"""GRIM Chat Server — FastAPI + WebSocket interface to the LangGraph companion.

Bare-bones Mission Control: just a chat interface for now.
Serves a static HTML chat page and exposes a WebSocket endpoint
that streams GRIM responses in real-time.

Usage:
    python -m server              # Start on :8126
    python -m server --port 8080  # Custom port
    python -m server --debug      # Debug mode (test vault)
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
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, HumanMessage

from core.config import GrimConfig, load_config
from core.graph import build_graph
from core.tools.context import tool_context

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)

logger = logging.getLogger("grim.server")

# ---------------------------------------------------------------------------
# Globals (set during lifespan)
# ---------------------------------------------------------------------------

_graph: Any = None
_config: GrimConfig | None = None
_mcp_session: Any = None


# ---------------------------------------------------------------------------
# MCP lifecycle (reused from __main__)
# ---------------------------------------------------------------------------

async def _start_mcp(config: GrimConfig):
    """Start the Kronos MCP subprocess and return session."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        server_params = StdioServerParameters(
            command=config.kronos_mcp_command,
            args=config.kronos_mcp_args,
            env={
                "KRONOS_VAULT_PATH": str(config.vault_path),
                "KRONOS_SKILLS_PATH": str(config.skills_path),
                **os.environ,
            },
        )
        # We need to keep the context managers alive for the app's lifetime.
        # Use a manual enter/exit pattern.
        transport_cm = stdio_client(server_params)
        read, write = await transport_cm.__aenter__()

        session_cm = ClientSession(read, write)
        session = await session_cm.__aenter__()
        await session.initialize()

        logger.info("Kronos MCP connected (vault: %s)", config.vault_path)
        return session, transport_cm, session_cm

    except ImportError:
        logger.warning("MCP library not installed — running without Kronos")
        return None, None, None
    except Exception as exc:
        logger.warning("Failed to connect to Kronos MCP: %s", exc)
        return None, None, None


# ---------------------------------------------------------------------------
# FastAPI lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Boot GRIM graph + MCP on startup, tear down on shutdown."""
    global _graph, _config, _mcp_session

    grim_root = Path(__file__).resolve().parent
    load_dotenv(grim_root / ".env")

    if os.getenv("GRIM_ENV") == "debug":
        logger.info("Debug mode — using test vault")

    _config = load_config(grim_root=grim_root)

    workspace_root = grim_root.parent  # core_workspace root
    tool_context.configure(workspace_root=workspace_root)

    # Start MCP
    session, transport_cm, session_cm = await _start_mcp(_config)
    _mcp_session = session

    # Build graph
    _graph = build_graph(_config, mcp_session=_mcp_session)

    logger.info(
        "GRIM server ready — env: %s, vault: %s, mcp: %s",
        _config.env,
        _config.vault_path,
        "connected" if _mcp_session else "offline",
    )

    yield

    # Shutdown — close MCP session
    if session_cm:
        try:
            await session_cm.__aexit__(None, None, None)
        except Exception:
            pass
    if transport_cm:
        try:
            await transport_cm.__aexit__(None, None, None)
        except Exception:
            pass

    logger.info("GRIM server shutting down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="GRIM",
    description="General Recursive Intelligence Machine — Chat Interface",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files for the chat UI
STATIC_DIR = Path(__file__).parent / "ui" / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    """Serve the chat UI."""
    index_path = Path(__file__).parent / "ui" / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path), media_type="text/html")
    return HTMLResponse("<h1>GRIM</h1><p>UI not found. Use /ws for WebSocket.</p>")


@app.get("/health")
async def health():
    """Health check endpoint for Docker / load balancers."""
    return {
        "status": "healthy",
        "service": "grim",
        "version": "0.1.0",
        "mcp": "connected" if _mcp_session else "offline",
        "vault": str(_config.vault_path) if _config else None,
    }


@app.get("/api/status")
async def status():
    """Extended status for the UI."""
    return {
        "service": "grim",
        "version": "0.1.0",
        "env": _config.env if _config else "unknown",
        "model": _config.model if _config else "unknown",
        "mcp": "connected" if _mcp_session else "offline",
        "vault": str(_config.vault_path) if _config else None,
        "skills_path": str(_config.skills_path) if _config else None,
    }


# ---------------------------------------------------------------------------
# WebSocket chat
# ---------------------------------------------------------------------------

class SessionManager:
    """Track active WebSocket chat sessions."""

    def __init__(self):
        self.sessions: dict[str, dict] = {}

    def create(self, ws: WebSocket) -> str:
        sid = str(uuid.uuid4())[:8]
        self.sessions[sid] = {
            "ws": ws,
            "thread_id": f"grim-ws-{sid}",
            "created": datetime.now().isoformat(),
            "message_count": 0,
        }
        return sid

    def get(self, sid: str) -> dict | None:
        return self.sessions.get(sid)

    def remove(self, sid: str):
        self.sessions.pop(sid, None)

    @property
    def count(self) -> int:
        return len(self.sessions)


sessions = SessionManager()


@app.websocket("/ws")
async def websocket_chat(ws: WebSocket):
    """WebSocket endpoint for real-time chat with GRIM."""
    await ws.accept()
    sid = sessions.create(ws)
    session_info = sessions.get(sid)

    logger.info("WebSocket connected: session %s", sid)

    # Send welcome
    await ws.send_json({
        "type": "system",
        "content": f"Connected to GRIM. Session: {sid}",
        "session_id": sid,
    })

    try:
        while True:
            # Receive message
            data = await ws.receive_json()
            content = data.get("content", "").strip()

            if not content:
                continue

            session_info["message_count"] += 1
            logger.info("Session %s: message #%d", sid, session_info["message_count"])

            # Send "thinking" indicator
            await ws.send_json({"type": "thinking", "content": ""})

            try:
                # Invoke the GRIM graph
                graph_config = {"configurable": {"thread_id": session_info["thread_id"]}}

                # Default caller is Peter (web UI).
                # Services can override via message payload: {"caller_id": "ironclaw"}
                caller_id = data.get("caller_id", "peter")

                result = await _graph.ainvoke(
                    {
                        "messages": [HumanMessage(content=content)],
                        "session_start": datetime.now(),
                        "caller_id": caller_id,
                    },
                    config=graph_config,
                )

                # Extract GRIM's response
                response_text = _extract_response(result)
                mode = result.get("mode", "companion")
                delegation = result.get("delegation_type")
                kc_count = len(result.get("knowledge_context", []))

                await ws.send_json({
                    "type": "response",
                    "content": response_text,
                    "meta": {
                        "mode": mode,
                        "delegation": delegation,
                        "knowledge_context_count": kc_count,
                        "message_number": session_info["message_count"],
                    },
                })

            except Exception as exc:
                logger.exception("Error processing message in session %s", sid)
                await ws.send_json({
                    "type": "error",
                    "content": f"Something went wrong: {exc}",
                })

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: session %s", sid)
    except Exception as exc:
        logger.exception("WebSocket error in session %s", sid)
    finally:
        sessions.remove(sid)


def _extract_response(result: dict) -> str:
    """Extract GRIM's text response from graph result."""
    messages = result.get("messages", [])
    if not messages:
        return "I don't have a response for that."

    # Find the last AI message with content
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) or (hasattr(msg, "type") and msg.type == "ai"):
            content = msg.content if hasattr(msg, "content") else str(msg)
            if isinstance(content, str) and len(content) > 0:
                return content
            # Handle list content (tool calls return list)
            if isinstance(content, list):
                text_parts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                if text_parts:
                    return "\n".join(text_parts)

    return "I processed your message but don't have a text response."


# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------

def main():
    """Run the GRIM chat server."""
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="GRIM Chat Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8126, help="Bind port")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on changes")

    args = parser.parse_args()

    if args.debug:
        os.environ["GRIM_ENV"] = "debug"

    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
