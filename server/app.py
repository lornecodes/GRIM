"""GRIM Chat Server — FastAPI + WebSocket wrapping the LangGraph core.

Provides:
  GET  /              → Chat UI (Next.js static build or legacy HTML)
  GET  /health        → Health check
  WS   /ws/{sid}      → WebSocket chat (streaming-ready)
  POST /api/chat      → REST fallback (request/response)
  GET  /api/sessions  → List session thread IDs

Startup:
  uvicorn server.app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from core.config import GrimConfig, load_config
from core.graph import build_graph

# ---------------------------------------------------------------------------
# Monkey-patch: langchain-anthropic 1.3.4 context_management bug
# The Anthropic API returns context_management as a dict, but
# langchain-anthropic calls .model_dump() on it expecting a Pydantic model.
# Patch until upstream fix lands.
# ---------------------------------------------------------------------------
try:
    import langchain_anthropic.chat_models as _lcam

    _orig_make_chunk = _lcam._make_message_chunk_from_anthropic_event

    def _patched_make_chunk(*args, **kwargs):
        try:
            return _orig_make_chunk(*args, **kwargs)
        except AttributeError as e:
            if "model_dump" in str(e):
                # Retry with context_management stripped from the event
                event = args[0] if args else kwargs.get("event")
                if hasattr(event, "context_management"):
                    cm = event.context_management
                    # Replace with a dict that has model_dump
                    if isinstance(cm, dict):
                        class _DictWrapper(dict):
                            def model_dump(self):
                                return dict(self)
                        event.context_management = _DictWrapper(cm)
                        return _orig_make_chunk(*args, **kwargs)
            raise

    _lcam._make_message_chunk_from_anthropic_event = _patched_make_chunk
    logging.getLogger("grim.server").info("Patched langchain-anthropic context_management bug")
except Exception:
    pass  # don't crash on patch failure

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
for noisy in ("httpx", "httpcore", "anthropic", "tensorflow"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger("grim.server")

# ---------------------------------------------------------------------------
# Globals set during lifespan
# ---------------------------------------------------------------------------

_graph: Any = None
_config: GrimConfig | None = None
_mcp_cleanup: Any = None  # holds the MCP context manager for cleanup
_checkpointer: Any = None  # AsyncSqliteSaver — kept open for the server lifetime


def _grim_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Lifespan — boot Kronos MCP + build graph once
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start Kronos MCP, build graph, serve until shutdown."""
    global _graph, _config, _mcp_cleanup, _checkpointer

    grim_root = _grim_root()
    load_dotenv(grim_root / ".env")

    _config = load_config(grim_root=grim_root)

    # Set workspace root
    from core.tools.workspace import set_workspace_root
    set_workspace_root(grim_root.parent)

    logger.info("GRIM starting — env: %s, vault: %s", _config.env, _config.vault_path)

    # Boot Kronos MCP
    mcp_session = None
    try:
        from core.__main__ import kronos_mcp_session

        _mcp_cm = kronos_mcp_session(_config)
        mcp_session = await _mcp_cm.__aenter__()
        _mcp_cleanup = _mcp_cm
        if mcp_session:
            logger.info("Kronos MCP connected")
        else:
            logger.info("Running without Kronos MCP")
    except Exception as exc:
        logger.warning("Could not start Kronos MCP: %s", exc)

    # SQLite session persistence — open for the full server lifetime
    sessions_dir = grim_root / "local" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    db_path = sessions_dir / "grim.db"
    logger.info("SQLite checkpointer: %s", db_path)

    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as checkpointer:
        _checkpointer = checkpointer

        # Initialize reasoning cache (Redis, optional)
        from core.reasoning_cache import ReasoningCache
        reasoning_cache = await ReasoningCache.from_env()

        # Build graph (once, shared across all connections)
        _graph = build_graph(_config, mcp_session=mcp_session, checkpointer=checkpointer, reasoning_cache=reasoning_cache)
        logger.info("Graph built — server ready")

        yield

        # Cleanup MCP on shutdown
        if _mcp_cleanup:
            try:
                await _mcp_cleanup.__aexit__(None, None, None)
            except Exception:
                pass
        logger.info("GRIM server stopped")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="GRIM",
    description="General Recursive Intelligence Machine — Chat Interface",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow Next.js dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files — prefer Next.js build (ui/out/), fall back to legacy static/
_ui_dir = Path(__file__).resolve().parent.parent / "ui" / "out"
_static_dir = Path(__file__).parent / "static"

if _ui_dir.exists():
    _next_dir = _ui_dir / "_next"
    if _next_dir.exists():
        app.mount("/_next", StaticFiles(directory=str(_next_dir)), name="next-assets")
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the chat UI — Next.js build preferred, legacy fallback."""
    ui_index = _ui_dir / "index.html"
    if ui_index.exists():
        return FileResponse(str(ui_index), media_type="text/html")
    legacy_index = _static_dir / "index.html"
    if legacy_index.exists():
        return FileResponse(str(legacy_index), media_type="text/html")
    return HTMLResponse("<h1>GRIM</h1><p>Static files not found.</p>")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return JSONResponse({
        "status": "ok",
        "env": _config.env if _config else "unknown",
        "vault": str(_config.vault_path) if _config else None,
        "graph": _graph is not None,
    })


@app.get("/api/sessions")
async def list_sessions():
    """List unique session thread IDs from the checkpointer."""
    if not _checkpointer:
        return JSONResponse({"sessions": []})
    try:
        async with _checkpointer.conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id"
        ) as cursor:
            rows = await cursor.fetchall()
        sessions = [
            row[0].removeprefix("grim-web-")
            for row in rows
            if row[0].startswith("grim-web-")
        ]
        return JSONResponse({"sessions": sessions})
    except Exception as exc:
        logger.warning("Failed to list sessions: %s", exc)
        return JSONResponse({"sessions": []})


@app.get("/api/test-mcp")
async def test_mcp():
    """Direct MCP call — bypasses LangChain tools to isolate perf issues."""
    import time
    from core.tools.kronos_read import get_mcp_session

    session = get_mcp_session()
    if session is None:
        return JSONResponse({"error": "No MCP session"}, status_code=503)

    results = {}

    # Test 1: direct call_tool, no wrapper
    t0 = time.monotonic()
    try:
        raw = await session.call_tool("kronos_search", {"query": "PAC", "semantic": False})
        elapsed = time.monotonic() - t0
        text = raw.content[0].text if raw.content else "empty"
        results["direct_call"] = {"elapsed_ms": round(elapsed * 1000), "length": len(text)}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        results["direct_call"] = {"elapsed_ms": round(elapsed * 1000), "error": str(exc)}

    # Test 2: with asyncio.wait_for
    t0 = time.monotonic()
    try:
        raw = await asyncio.wait_for(
            session.call_tool("kronos_search", {"query": "SEC", "semantic": False}),
            timeout=10,
        )
        elapsed = time.monotonic() - t0
        text = raw.content[0].text if raw.content else "empty"
        results["wait_for_call"] = {"elapsed_ms": round(elapsed * 1000), "length": len(text)}
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        results["wait_for_call"] = {"elapsed_ms": round(elapsed * 1000), "error": "TIMEOUT"}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        results["wait_for_call"] = {"elapsed_ms": round(elapsed * 1000), "error": str(exc)}

    # Test 3: kronos_get
    t0 = time.monotonic()
    try:
        raw = await session.call_tool("kronos_get", {"id": "pac-framework"})
        elapsed = time.monotonic() - t0
        text = raw.content[0].text if raw.content else "empty"
        results["get_call"] = {"elapsed_ms": round(elapsed * 1000), "length": len(text)}
    except Exception as exc:
        elapsed = time.monotonic() - t0
        results["get_call"] = {"elapsed_ms": round(elapsed * 1000), "error": str(exc)}

    return JSONResponse(results)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    caller_id: str | None = None  # default: "peter" — services pass their own id


class ChatResponse(BaseModel):
    response: str
    session_id: str
    knowledge_count: int = 0
    mode: str = "companion"
    skills: list[str] = []


@app.post("/api/chat", response_model=ChatResponse)
async def chat_rest(req: ChatRequest):
    """REST endpoint — send a message, get a response."""
    if not _graph:
        return JSONResponse({"error": "Graph not ready"}, status_code=503)

    session_id = req.session_id or str(uuid.uuid4())[:8]
    graph_config = {"configurable": {"thread_id": f"grim-web-{session_id}"}}

    caller_id = req.caller_id or "peter"

    try:
        result = await _graph.ainvoke(
            {
                "messages": [HumanMessage(content=req.message)],
                "session_start": datetime.now(),
                "caller_id": caller_id,
            },
            config=graph_config,
        )
        return ChatResponse(
            response=_extract_response(result),
            session_id=session_id,
            knowledge_count=len(result.get("knowledge_context", [])),
            mode=result.get("mode", "companion"),
            skills=[s.name for s in result.get("matched_skills", [])],
        )
    except Exception as exc:
        logger.exception("Chat error")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# WebSocket — real-time chat with session persistence
# ---------------------------------------------------------------------------

@app.websocket("/ws/{session_id}")
async def websocket_chat(ws: WebSocket, session_id: str):
    """WebSocket chat endpoint with token streaming and full trace.

    Protocol:
      Client sends: {"message": "..."}
      Server sends:
        {"type": "trace", "cat": "...", "text": "...", ...}  — debug trace event
        {"type": "stream", "token": "..."}                    — each LLM token
        {"type": "response", "content": "...", "meta": {...}} — final response
        {"type": "error", "content": "..."}                   — error
    """
    await ws.accept()
    logger.info("WS connected: %s", session_id)

    graph_config = {"configurable": {"thread_id": f"grim-web-{session_id}"}}

    try:
        while True:
            data = await ws.receive_text()
            try:
                payload = json.loads(data)
                message = payload.get("message", data)
                caller_id = payload.get("caller_id", "peter")
            except json.JSONDecodeError:
                message = data
                caller_id = "peter"

            if not message:
                continue

            try:
                import time as _time
                _t0 = _time.monotonic()

                full_response = ""
                last_knowledge = []
                last_mode = "companion"
                last_skills = []
                streaming_started = False
                seen_nodes = set()
                _node_times: dict[str, float] = {}
                _current_node: str = ""
                _node_text: dict[str, str] = {}  # per-node LLM output
                _node_stream_text: dict[str, str] = {}  # per-node streamed tokens

                async def _trace(cat: str, text: str, **extra):
                    """Emit a trace event to the client."""
                    elapsed = round((_time.monotonic() - _t0) * 1000)
                    msg = {"type": "trace", "cat": cat, "text": text, "ms": elapsed}
                    msg.update(extra)
                    await ws.send_json(msg)

                await _trace("graph", "Graph invocation started")
                _event_count = 0

                async for event in _graph.astream_events(
                    {
                        "messages": [HumanMessage(content=message)],
                        "session_start": datetime.now(),
                        "caller_id": caller_id,
                    },
                    config=graph_config,
                    version="v2",
                ):
                    kind = event.get("event", "")
                    name = event.get("name", "")
                    _event_count += 1

                    # ── Node lifecycle ──
                    if kind == "on_chain_start" and name in (
                        "identity", "compress", "memory", "skill_match", "router",
                        "companion", "dispatch", "integrate", "evolve",
                    ):
                        if name not in seen_nodes:
                            seen_nodes.add(name)
                            _node_times[name] = _time.monotonic()
                            _current_node = name
                            await _trace("node", f"→ {name}", node=name, action="start")

                    elif kind == "on_chain_end" and name in seen_nodes:
                        node_elapsed = round((_time.monotonic() - _node_times.get(name, _t0)) * 1000)
                        output = event.get("data", {}).get("output")
                        detail = {}

                        if isinstance(output, dict):
                            # Capture key outputs from each node
                            if "knowledge_context" in output:
                                last_knowledge = output["knowledge_context"]
                                detail["fdo_count"] = len(last_knowledge)
                                detail["fdo_ids"] = [k.id for k in last_knowledge[:8]]
                            if "mode" in output:
                                last_mode = output["mode"]
                                detail["mode"] = last_mode
                            if "matched_skills" in output:
                                last_skills = [s.name for s in output["matched_skills"]]
                                detail["skills"] = last_skills
                            if "field_state" in output:
                                fs = output["field_state"]
                                if hasattr(fs, "coherence"):
                                    detail["field_state"] = {
                                        "coherence": round(fs.coherence, 3),
                                        "valence": round(fs.valence, 3),
                                        "uncertainty": round(fs.uncertainty, 3),
                                        "mode": fs.expression_mode() if hasattr(fs, "expression_mode") else "",
                                    }

                        # Include step_content for nodes that produced LLM output
                        step_text = _node_text.get(name) or _node_stream_text.get(name)
                        await _trace("node", f"✓ {name} ({node_elapsed}ms)",
                                     node=name, action="end", duration_ms=node_elapsed,
                                     detail=detail if detail else None,
                                     step_content=step_text if step_text else None)

                    # ── LLM lifecycle ──
                    elif kind == "on_chat_model_start":
                        model_name = ""
                        kwargs = event.get("data", {}).get("input", {})
                        if isinstance(kwargs, dict):
                            msgs_in = kwargs.get("messages", [])
                            if isinstance(msgs_in, list):
                                # Count by type
                                counts = {}
                                for m in (msgs_in[0] if msgs_in and isinstance(msgs_in[0], list) else msgs_in):
                                    t = getattr(m, "type", "unknown")
                                    counts[t] = counts.get(t, 0) + 1
                                detail_str = ", ".join(f"{v} {k}" for k, v in counts.items())
                                model_name = f" ({detail_str})"
                        await _trace("llm", f"LLM call started{model_name}", action="start")

                    elif kind == "on_chat_model_end":
                        resp = event.get("data", {}).get("output")
                        info = {}
                        if resp:
                            if hasattr(resp, "usage_metadata") and resp.usage_metadata:
                                info["tokens"] = dict(resp.usage_metadata)
                            has_tool_calls = hasattr(resp, "tool_calls") and resp.tool_calls
                            if has_tool_calls:
                                info["tool_calls"] = [tc["name"] for tc in resp.tool_calls]
                                # Tell the UI: the text just streamed was thinking
                                # (companion will make tool calls, then answer).
                                # UI should clear the bubble so the final answer
                                # starts fresh.
                                if _current_node == "companion":
                                    thinking = _node_stream_text.get("companion", "")
                                    await ws.send_json({
                                        "type": "stream_clear",
                                        "node": "companion",
                                        "thinking": thinking.strip() if thinking else "",
                                    })
                                    # Reset companion stream text and full_response
                                    # so the final answer starts fresh
                                    _node_stream_text["companion"] = ""
                                    full_response = ""
                            # Always capture the LAST non-tool-call AI response.
                            if not has_tool_calls and hasattr(resp, "content"):
                                text = _extract_text(resp.content)
                                if text:
                                    full_response = text
                                    streaming_started = True
                                    if _current_node:
                                        _node_text[_current_node] = text
                        await _trace("llm", "LLM call complete", action="end",
                                     detail=info if info else None)

                    # ── Token streaming ──
                    elif kind == "on_chat_model_stream":
                        chunk = event.get("data", {}).get("chunk")
                        if chunk and hasattr(chunk, "content") and chunk.content:
                            token = _extract_text(chunk.content)
                            if token:
                                if not streaming_started:
                                    streaming_started = True
                                full_response += token
                                if _current_node:
                                    _node_stream_text[_current_node] = _node_stream_text.get(_current_node, "") + token
                                await ws.send_json({"type": "stream", "token": token, "node": _current_node or None})

                    # ── Tool lifecycle ──
                    elif kind == "on_tool_start":
                        tool_name = event.get("name", "unknown")
                        tool_input = event.get("data", {}).get("input", {})
                        await _trace("tool", f"⚡ {tool_name}",
                                     tool=tool_name, action="start",
                                     input=_safe_truncate(tool_input))

                    elif kind == "on_tool_end":
                        tool_name = event.get("name", "unknown")
                        tool_output = event.get("data", {}).get("output", "")
                        output_str = str(tool_output)
                        await _trace("tool", f"✓ {tool_name}",
                                     tool=tool_name, action="end",
                                     output_preview=output_str[:200])

                total_ms = round((_time.monotonic() - _t0) * 1000)
                logger.info("WS turn complete: %d events, %d chars response, %dms",
                            _event_count, len(full_response), total_ms)

                if not full_response:
                    full_response = "I processed your message but have no response to show."

                await _trace("graph", f"Complete ({total_ms}ms)", duration_ms=total_ms)

                await ws.send_json({
                    "type": "response",
                    "content": full_response,
                    "meta": {
                        "mode": last_mode,
                        "knowledge_count": len(last_knowledge),
                        "skills": last_skills,
                        "fdo_ids": [k.id for k in last_knowledge[:5]] if last_knowledge else [],
                        "total_ms": total_ms,
                    },
                })

            except Exception as exc:
                import traceback
                tb = traceback.format_exc()
                logger.error("WS processing error: %s\n%s", exc, tb)
                # Send error with enough detail to debug
                exc_type = type(exc).__name__
                exc_loc = ""
                if exc.__traceback__:
                    frame = exc.__traceback__
                    while frame.tb_next:
                        frame = frame.tb_next
                    exc_loc = f" at {frame.tb_frame.f_code.co_filename}:{frame.tb_lineno}"
                await ws.send_json({
                    "type": "error",
                    "content": f"Error ({exc_type}{exc_loc}): {exc}",
                })

    except WebSocketDisconnect:
        logger.info("WS disconnected: %s", session_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_truncate(obj: Any, max_len: int = 200) -> Any:
    """Truncate tool input for trace display."""
    if isinstance(obj, str):
        return obj[:max_len]
    if isinstance(obj, dict):
        return {k: (v[:max_len] if isinstance(v, str) and len(v) > max_len else v)
                for k, v in obj.items()}
    return str(obj)[:max_len]


def _extract_text(content: Any) -> str:
    """Extract plain text from LLM content (handles Anthropic list blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return str(content) if content else ""


def _extract_response(result: dict) -> str:
    """Pull GRIM's response from the graph result."""
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "ai":
            text = _extract_text(msg.content if hasattr(msg, "content") else str(msg))
            if text:
                return text
    return "I processed your message but have no response to show."
