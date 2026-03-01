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
_ironclaw_bridge: Any = None  # IronClawBridge instance (optional)


def _grim_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Lifespan — boot Kronos MCP + build graph once
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start Kronos MCP, build graph, serve until shutdown."""
    global _graph, _config, _mcp_cleanup, _checkpointer, _ironclaw_bridge

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

        # Initialize IronClaw bridge (optional — graceful if unavailable)
        ironclaw_bridge = None
        ironclaw_url = os.environ.get("IRONCLAW_URL", "http://localhost:3100")
        ironclaw_api_key = os.environ.get("IRONCLAW_API_KEY", "grim-internal-key")
        try:
            from core.bridge.ironclaw import IronClawBridge
            bridge = IronClawBridge(base_url=ironclaw_url, api_key=ironclaw_api_key)
            if await bridge.is_available():
                ironclaw_bridge = bridge
                _ironclaw_bridge = bridge
                health = await bridge.health()
                logger.info(
                    "IronClaw connected: %s (v%s, uptime %.0fs)",
                    ironclaw_url, health.version, health.uptime_secs,
                )
            else:
                logger.info("IronClaw not available at %s — running without sandbox", ironclaw_url)
                await bridge.close()
        except Exception as exc:
            logger.info("IronClaw bridge init failed: %s — running without sandbox", exc)

        # Build graph (once, shared across all connections)
        _graph = build_graph(
            _config,
            mcp_session=mcp_session,
            checkpointer=checkpointer,
            reasoning_cache=reasoning_cache,
            ironclaw_bridge=ironclaw_bridge,
        )
        logger.info("Graph built — server ready")

        yield

        # Cleanup IronClaw bridge on shutdown
        if _ironclaw_bridge:
            try:
                await _ironclaw_bridge.close()
            except Exception:
                pass

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


@app.get("/api/config")
async def get_config():
    """Expose resolved GRIM configuration for the Settings UI."""
    if not _config:
        return JSONResponse({"error": "Config not loaded"}, status_code=503)

    return JSONResponse({
        "env": _config.env,
        "vault_path": str(_config.vault_path),
        "model": _config.model,
        "temperature": _config.temperature,
        "max_tokens": _config.max_tokens,
        "routing": {
            "enabled": _config.routing_enabled,
            "default_tier": _config.routing_default_tier,
            "classifier_enabled": _config.routing_classifier_enabled,
            "confidence_threshold": _config.routing_confidence_threshold,
        },
        "context": {
            "max_tokens": _config.context_max_tokens,
            "keep_recent": _config.context_keep_recent,
        },
        "identity": {
            "system_prompt_path": str(_config.identity_prompt_path),
            "personality_path": str(_config.identity_personality_path),
            "personality_cache_path": str(_config.personality_cache_path),
            "skills_path": str(_config.skills_path),
        },
        "skills": {
            "auto_load": _config.skills_auto_load,
            "match_per_turn": _config.skills_match_per_turn,
        },
        "persistence": {
            "checkpoint_backend": _config.checkpoint_backend,
            "checkpoint_path": str(_config.checkpoint_path),
        },
        "evolution": {
            "frequency": _config.evolution_frequency,
            "directory": str(_config.evolution_dir),
        },
        "objectives_max_active": _config.objectives_max_active,
        "redis_url": bool(_config.redis_url),  # expose presence, not the URL
    })


class ConfigUpdateRequest(BaseModel):
    env: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    vault_path: str | None = None
    routing: dict | None = None
    context: dict | None = None
    skills: dict | None = None
    objectives_max_active: int | None = None


@app.post("/api/config")
async def update_config(req: ConfigUpdateRequest):
    """Update GRIM configuration — writes to grim.yaml and reloads."""
    global _config

    if not _config:
        return JSONResponse({"error": "Config not loaded"}, status_code=503)

    try:
        from core.config import save_config_updates

        # Build updates dict from non-None fields
        updates = {k: v for k, v in req.model_dump().items() if v is not None}
        if not updates:
            return JSONResponse({"error": "No updates provided"}, status_code=400)

        grim_root = _grim_root()
        _config = save_config_updates(updates, grim_root=grim_root)
        logger.info("Config updated: %s", list(updates.keys()))

        # Return the updated config (same format as GET)
        return await get_config()

    except Exception as exc:
        logger.exception("Config update failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/ironclaw/status")
async def ironclaw_status():
    """IronClaw engine status — gateway health, tools, metrics."""
    if not _ironclaw_bridge:
        return JSONResponse({
            "available": False,
            "message": "IronClaw bridge not initialized",
        })

    health = await _ironclaw_bridge.health()
    tools = await _ironclaw_bridge.list_tools() if health.healthy else []
    metrics = await _ironclaw_bridge.get_metrics() if health.healthy else None

    return JSONResponse({
        "available": health.healthy,
        "version": health.version,
        "uptime_secs": health.uptime_secs,
        "tools": [{"name": t.name, "description": t.description, "risk_level": t.risk_level} for t in tools],
        "metrics": {
            "requests_total": metrics.requests_total,
            "requests_failed": metrics.requests_failed,
            "active_sessions": metrics.active_sessions,
            "uptime_seconds": metrics.uptime_seconds,
        } if metrics else None,
    })


# ---------------------------------------------------------------------------
# Agent Team endpoints
# ---------------------------------------------------------------------------

# Static GRIM agent metadata — matches the LangGraph node structure
GRIM_AGENTS = [
    {
        "id": "companion",
        "name": "Companion",
        "role": "thinker",
        "description": "Primary conversational agent — reasoning, identity, natural language",
        "tools": ["reasoning_cache", "identity_reflect", "model_select"],
        "color": "#7c6fef",
    },
    {
        "id": "memory",
        "name": "Memory",
        "role": "vault_ops",
        "description": "Kronos vault operations — search, retrieve, create, update FDOs",
        "tools": ["kronos_search", "kronos_get", "kronos_create", "kronos_update", "kronos_graph"],
        "color": "#8b5cf6",
    },
    {
        "id": "coder",
        "name": "Coder",
        "role": "code_files",
        "description": "Code generation, file operations, refactoring, debugging",
        "tools": ["file_read", "file_write", "file_search", "directory_list", "shell", "git_status", "git_diff", "git_commit", "code_search", "code_analyze"],
        "color": "#34d399",
    },
    {
        "id": "research",
        "name": "Researcher",
        "role": "analysis",
        "description": "Web research, document analysis, information synthesis",
        "tools": ["web_search", "web_fetch", "document_analyze", "summarize", "citation_extract", "data_transform", "compare_sources", "timeline_build", "deep_dive", "kronos_deep_dive", "navigate"],
        "color": "#3b82f6",
    },
    {
        "id": "operator",
        "name": "Operator",
        "role": "git_shell",
        "description": "System operations — git, shell, Docker, CI/CD, deployments",
        "tools": ["shell", "git_status", "git_diff", "git_commit", "git_push", "git_branch", "docker_build", "docker_compose", "ci_trigger", "env_check", "process_list", "port_check", "disk_usage", "system_info"],
        "color": "#f59e0b",
    },
    {
        "id": "ironclaw",
        "name": "IronClaw",
        "role": "sandbox",
        "description": "Sandboxed execution via IronClaw engine — secure tool runs",
        "tools": ["ic_file_read", "ic_file_write", "ic_shell", "ic_http_request", "ic_directory_list", "ic_search", "ic_health", "ic_metrics"],
        "color": "#ef4444",
    },
]


@app.get("/api/agents")
async def list_agents():
    """GRIM agent roster — static metadata for the brain tier."""
    return JSONResponse({"agents": GRIM_AGENTS})


@app.get("/api/ironclaw/agents")
async def ironclaw_agents():
    """IronClaw agent roster — limbs tier via bridge."""
    if not _ironclaw_bridge:
        return JSONResponse({
            "enabled": False,
            "roles": [],
            "active_sessions": 0,
            "max_concurrent_sessions": 0,
            "message": "IronClaw bridge not initialized",
        })

    try:
        data = await _ironclaw_bridge.list_agents()
        return JSONResponse(data)
    except Exception as exc:
        logger.warning("Failed to list IronClaw agents: %s", exc)
        return JSONResponse({
            "enabled": False,
            "roles": [],
            "active_sessions": 0,
            "max_concurrent_sessions": 0,
            "error": str(exc),
        })


class WorkflowRequest(BaseModel):
    task: str
    pattern: dict


@app.post("/api/ironclaw/workflow")
async def ironclaw_workflow(req: WorkflowRequest):
    """Run an IronClaw agent workflow — proxy to bridge."""
    if not _ironclaw_bridge:
        return JSONResponse(
            {"error": "IronClaw bridge not initialized"},
            status_code=503,
        )

    try:
        result = await _ironclaw_bridge.run_workflow(req.task, req.pattern)
        return JSONResponse(result)
    except Exception as exc:
        logger.error("IronClaw workflow failed: %s", exc)
        return JSONResponse(
            {"error": str(exc)},
            status_code=500,
        )


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


# ---------------------------------------------------------------------------
# Memory endpoints — GRIM's persistent working memory (kronos-vault/memory.md)
# ---------------------------------------------------------------------------

class MemoryUpdateRequest(BaseModel):
    content: str


@app.get("/api/memory")
async def get_memory():
    """Read GRIM's persistent working memory via MCP (persists on host)."""
    if not _config:
        return JSONResponse({"error": "Config not loaded"}, status_code=503)

    from core.tools.kronos_read import get_mcp_session

    session = get_mcp_session()
    if session is not None:
        try:
            result = await session.call_tool("kronos_memory_read", {})
            if hasattr(result, "content") and result.content:
                import json as _json
                data = _json.loads(result.content[0].text)
                content = data.get("content", "")
                # Parse sections from content for backward compat
                from core.memory_store import parse_memory_sections
                sections = parse_memory_sections(content)
                return JSONResponse({"content": content, "sections": sections})
        except Exception:
            pass  # fall through to direct file read

    # Fallback to direct file read
    from core.memory_store import parse_memory_sections, read_memory
    content = read_memory(_config.vault_path)
    sections = parse_memory_sections(content)
    return JSONResponse({"content": content, "sections": sections})


@app.post("/api/memory")
async def post_memory(req: MemoryUpdateRequest):
    """Update GRIM's persistent working memory via MCP (persists on host)."""
    if not _config:
        return JSONResponse({"error": "Config not loaded"}, status_code=503)

    from core.tools.kronos_read import get_mcp_session

    session = get_mcp_session()
    if session is not None:
        try:
            await session.call_tool(
                "kronos_memory_update",
                {"full_content": req.content},
            )
        except Exception:
            # Fallback to direct file write
            from core.memory_store import write_memory
            write_memory(_config.vault_path, req.content)
    else:
        from core.memory_store import write_memory
        write_memory(_config.vault_path, req.content)

    from core.memory_store import parse_memory_sections
    sections = parse_memory_sections(req.content)
    return JSONResponse({"content": req.content, "sections": sections})


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------

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

                # Agent live-monitoring queue — agents push events here,
                # we drain them concurrently alongside graph events.
                import asyncio as _asyncio
                _agent_queue: _asyncio.Queue = _asyncio.Queue()

                async def _drain_agent_events():
                    """Drain agent events and emit them as traces."""
                    while True:
                        event = await _agent_queue.get()
                        if event is None:  # sentinel = graph done
                            break
                        cat = event.pop("cat", "agent")
                        text = event.pop("text", "")
                        await _trace(cat, text, **event)

                _drain_task = _asyncio.create_task(_drain_agent_events())

                # Pass queue via config (not state) — Queue is not serializable
                graph_config["configurable"]["agent_event_queue"] = _agent_queue

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
                        is_claw = tool_name.startswith("claw_")
                        cat = "claw" if is_claw else "tool"
                        await _trace(cat, f"⚡ {tool_name}",
                                     tool=tool_name, action="start",
                                     sandboxed=is_claw,
                                     input=_safe_truncate(tool_input))

                    elif kind == "on_tool_end":
                        tool_name = event.get("name", "unknown")
                        tool_output = event.get("data", {}).get("output", "")
                        output_str = str(tool_output)
                        is_claw = tool_name.startswith("claw_")
                        cat = "claw" if is_claw else "tool"
                        await _trace(cat, f"✓ {tool_name}",
                                     tool=tool_name, action="end",
                                     sandboxed=is_claw,
                                     output_preview=output_str[:200])

                # Signal the agent event drainer to stop and wait for it
                _agent_queue.put_nowait(None)
                await _drain_task

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
