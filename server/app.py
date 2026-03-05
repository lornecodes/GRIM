"""GRIM Chat Server — FastAPI + WebSocket wrapping the LangGraph core.

Provides:
  GET  /              → Chat UI (Next.js static build or legacy HTML)
  GET  /health        → Health check
  WS   /ws/{sid}      → WebSocket chat (streaming-ready, LangGraph v1)
  WS   /ws/v2/{sid}   → WebSocket chat (GrimClient SDK sessions)
  POST /api/chat      → REST fallback (request/response)
  GET  /api/sessions  → List session thread IDs
  GET  /api/v2/sessions → List active SDK sessions

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
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
_skill_registry: Any = None  # SkillRegistry — loaded at boot for /api/skills
_agent_metadata: list[dict] | None = None  # dynamic agent roster (populated at boot)
_execution_pool: Any = None  # ExecutionPool instance (Project Charizard)
_active_ws_sessions: set[str] = set()  # live WebSocket session IDs (for Graph Studio)
_session_knowledge: dict[str, list] = {}  # session_id → list of KnowledgeEntry.to_dict()
_session_manager: Any = None  # SessionManager for v2 SDK sessions


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
    from core.tools.context import tool_context
    tool_context.configure(workspace_root=grim_root.parent)

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

        # Load skill registry for /api/skills endpoint
        from core.skills.loader import load_skills
        global _skill_registry
        _skill_registry = load_skills(_config.skills_path)

        # Build graph (once, shared across all connections)
        _graph = build_graph(
            _config,
            mcp_session=mcp_session,
            checkpointer=checkpointer,
            reasoning_cache=reasoning_cache,
        )
        logger.info("Graph built — server ready")

        # Build dynamic agent roster metadata
        global _agent_metadata
        try:
            from core.nodes.metadata import GRAPH_NODE_METADATA
            from core.agents.registry import AgentRegistry
            # Discover ALL agents (including disabled) for roster display
            roster_registry = AgentRegistry.discover(_config, disabled=[])
            agent_meta = roster_registry.build_metadata(_config)
            _agent_metadata = list(GRAPH_NODE_METADATA) + agent_meta
            logger.info("Agent roster: %d entries (dynamic)", len(_agent_metadata))
        except Exception as exc:
            logger.warning("Failed to build agent roster metadata: %s", exc)

        # Boot Execution Pool (Project Charizard) if enabled
        global _execution_pool
        if _config.pool_enabled:
            try:
                from core.pool import ExecutionPool, JobQueue
                from pathlib import Path as _Path
                import core.tools.pool_tools  # triggers tool registration

                queue = JobQueue(_config.pool_db_path)
                _execution_pool = ExecutionPool(queue, _config)
                await _execution_pool.start()
                tool_context.execution_pool = _execution_pool
                logger.info("Execution pool started: %d slots", _config.pool_num_slots)
            except Exception as exc:
                logger.warning("Could not start execution pool: %s", exc)
                _execution_pool = None
        else:
            logger.info("Execution pool disabled (pool.enabled: false)")

        # Boot SessionManager for v2 SDK sessions
        global _session_manager
        try:
            from server.sessions import SessionManager
            _session_manager = SessionManager(_config)
            await _session_manager.start()
        except Exception as exc:
            logger.warning("Could not start SessionManager: %s", exc)
            _session_manager = None

        yield

        # Shutdown SessionManager
        if _session_manager:
            try:
                await _session_manager.stop()
            except Exception:
                pass

        # Shutdown Execution Pool
        if _execution_pool:
            try:
                await _execution_pool.stop()
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


@app.get("/icon.svg")
async def favicon():
    """Serve Next.js generated favicon."""
    icon = _ui_dir / "icon.svg"
    if icon.exists():
        return FileResponse(str(icon), media_type="image/svg+xml")
    return HTMLResponse("", status_code=404)


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


# ---------------------------------------------------------------------------
# Agent Team endpoints
# ---------------------------------------------------------------------------

@app.get("/api/agents")
async def list_agents():
    """GRIM agent roster — dynamic metadata from agent classes and graph nodes."""
    disabled = set(_config.agents_disabled) if _config else set()
    agents = []
    for meta in (_agent_metadata or []):
        agents.append({
            **meta,
            "enabled": meta["id"] not in disabled,
        })
    return JSONResponse({"agents": agents})


# ---------------------------------------------------------------------------
# Skills endpoints
# ---------------------------------------------------------------------------

@app.get("/api/skills")
async def list_skills():
    """List all skills with enabled/disabled status."""
    if not _skill_registry:
        return JSONResponse({"skills": [], "error": "Skills not loaded"}, status_code=503)
    if not _config:
        return JSONResponse({"skills": []}, status_code=503)

    disabled = set(_config.skills_disabled)
    skills = []
    for s in _skill_registry.all():
        skills.append({
            "name": s.name,
            "version": s.version,
            "description": s.description,
            "type": s.skill_type,
            "permissions": s.permissions,
            "phases": [g for g in s.quality_gates] if s.quality_gates else [],
            "enabled": s.name not in disabled,
        })

    return JSONResponse({"skills": skills, "total": len(skills), "disabled_count": len(disabled)})


@app.post("/api/skills/{name}/toggle")
async def toggle_skill(name: str):
    """Toggle a skill's enabled/disabled state."""
    global _config

    if not _config:
        return JSONResponse({"error": "Config not loaded"}, status_code=503)

    if _skill_registry and name not in _skill_registry:
        return JSONResponse({"error": f"Unknown skill: {name}"}, status_code=404)

    disabled = list(_config.skills_disabled)
    if name in disabled:
        disabled.remove(name)
        enabled = True
    else:
        disabled.append(name)
        enabled = False

    from core.config import save_config_updates
    _config = save_config_updates({"skills": {"disabled": disabled}}, grim_root=_grim_root())
    logger.info("Skill %s %s", name, "enabled" if enabled else "disabled")

    return JSONResponse({"name": name, "enabled": enabled})


# ---------------------------------------------------------------------------
# Models endpoints
# ---------------------------------------------------------------------------

ANTHROPIC_MODELS = [
    {
        "id": "claude-opus-4-6",
        "name": "Claude Opus 4.6",
        "tier": "opus",
        "context_window": 200_000,
        "max_output": 32_000,
    },
    {
        "id": "claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6",
        "tier": "sonnet",
        "context_window": 200_000,
        "max_output": 16_000,
    },
    {
        "id": "claude-haiku-4-5-20251001",
        "name": "Claude Haiku 4.5",
        "tier": "haiku",
        "context_window": 200_000,
        "max_output": 8_192,
    },
]


@app.get("/api/models")
async def list_models():
    """List available models with enabled/disabled status and routing config."""
    if not _config:
        return JSONResponse({"models": []}, status_code=503)

    disabled = set(_config.models_disabled)
    models = []
    for m in ANTHROPIC_MODELS:
        models.append({
            **m,
            "enabled": m["tier"] not in disabled,
            "is_default": m["tier"] == _config.routing_default_tier,
        })

    return JSONResponse({
        "provider": "anthropic",
        "models": models,
        "routing": {
            "enabled": _config.routing_enabled,
            "default_tier": _config.routing_default_tier,
            "classifier_enabled": _config.routing_classifier_enabled,
            "confidence_threshold": _config.routing_confidence_threshold,
        },
    })


@app.post("/api/models/{tier}/toggle")
async def toggle_model(tier: str):
    """Toggle a model tier's enabled/disabled state."""
    global _config

    if not _config:
        return JSONResponse({"error": "Config not loaded"}, status_code=503)

    valid_tiers = {m["tier"] for m in ANTHROPIC_MODELS}
    if tier not in valid_tiers:
        return JSONResponse({"error": f"Unknown tier: {tier}"}, status_code=404)

    disabled = list(_config.models_disabled)
    if tier in disabled:
        disabled.remove(tier)
        enabled = True
    else:
        disabled.append(tier)
        enabled = False

    from core.config import save_config_updates
    _config = save_config_updates({"models": {"disabled": disabled}}, grim_root=_grim_root())
    logger.info("Model tier %s %s", tier, "enabled" if enabled else "disabled")

    return JSONResponse({"tier": tier, "enabled": enabled})


# ---------------------------------------------------------------------------
# Agent toggle endpoint
# ---------------------------------------------------------------------------

@app.post("/api/agents/{agent_id}/toggle")
async def toggle_agent(agent_id: str):
    """Toggle an agent's enabled/disabled state (IronClaw-tier only)."""
    global _config

    if not _config:
        return JSONResponse({"error": "Config not loaded"}, status_code=503)

    # Only agents with toggleable=True can be toggled (from metadata)
    toggleable = {m["id"] for m in (_agent_metadata or []) if m.get("toggleable")}
    if agent_id not in toggleable:
        return JSONResponse(
            {"error": f"Agent '{agent_id}' is not toggleable (only IronClaw-tier agents can be toggled)"},
            status_code=400,
        )

    disabled = list(_config.agents_disabled)
    if agent_id in disabled:
        disabled.remove(agent_id)
        enabled = True
    else:
        disabled.append(agent_id)
        enabled = False

    from core.config import save_config_updates
    _config = save_config_updates({"agents": {"disabled": disabled}}, grim_root=_grim_root())
    logger.info("Agent %s %s", agent_id, "enabled" if enabled else "disabled")

    return JSONResponse({"id": agent_id, "enabled": enabled})


# ---------------------------------------------------------------------------
# Identity / personality endpoints
# ---------------------------------------------------------------------------

@app.get("/api/identity")
async def get_identity():
    """Get personality field state and system prompt for editing."""
    if not _config:
        return JSONResponse({"error": "Config not loaded"}, status_code=503)

    result: dict[str, Any] = {
        "field_state": {"coherence": 0.8, "valence": 0.3, "uncertainty": 0.2},
        "system_prompt": "",
    }

    # Read personality.yaml
    try:
        personality_path = _config.identity_personality_path
        if personality_path.exists():
            raw = yaml.safe_load(personality_path.read_text(encoding="utf-8")) or {}
            fs = raw.get("field_state", {})
            result["field_state"] = {
                "coherence": fs.get("coherence", 0.8),
                "valence": fs.get("valence", 0.3),
                "uncertainty": fs.get("uncertainty", 0.2),
            }
    except Exception as exc:
        logger.warning("Failed to read personality.yaml: %s", exc)

    # Read system_prompt.md
    try:
        prompt_path = _config.identity_prompt_path
        if prompt_path.exists():
            result["system_prompt"] = prompt_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to read system_prompt.md: %s", exc)

    return JSONResponse(result)


class IdentityUpdateRequest(BaseModel):
    field_state: dict | None = None  # {coherence, valence, uncertainty}
    system_prompt: str | None = None


@app.post("/api/identity")
async def update_identity(req: IdentityUpdateRequest):
    """Update personality field state and/or system prompt."""
    if not _config:
        return JSONResponse({"error": "Config not loaded"}, status_code=503)

    try:
        # Update personality.yaml
        if req.field_state:
            personality_path = _config.identity_personality_path
            if personality_path.exists():
                raw = yaml.safe_load(personality_path.read_text(encoding="utf-8")) or {}
            else:
                raw = {}

            fs = raw.setdefault("field_state", {})
            for key in ("coherence", "valence", "uncertainty"):
                if key in req.field_state:
                    fs[key] = float(req.field_state[key])

            personality_path.write_text(
                yaml.dump(raw, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
            logger.info("Personality updated: %s", req.field_state)

        # Update system_prompt.md
        if req.system_prompt is not None:
            prompt_path = _config.identity_prompt_path
            prompt_path.write_text(req.system_prompt, encoding="utf-8")
            logger.info("System prompt updated (%d chars)", len(req.system_prompt))

        return await get_identity()

    except Exception as exc:
        logger.exception("Identity update failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


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
# Task Management endpoints — board, stories, calendar
# ---------------------------------------------------------------------------

async def _mcp_task_call(tool_name: str, args: dict) -> dict:
    """Call a kronos task/board/calendar MCP tool, return parsed JSON."""
    from core.tools.kronos_read import get_mcp_session
    session = get_mcp_session()
    if session is None:
        return {"error": "No MCP session"}
    try:
        raw = await asyncio.wait_for(
            session.call_tool(tool_name, args), timeout=30
        )
        text = raw.content[0].text if raw.content else "{}"
        return json.loads(text)
    except asyncio.TimeoutError:
        return {"error": f"MCP call {tool_name} timed out"}
    except Exception as exc:
        return {"error": str(exc)}


def _check_mcp_error(data: dict) -> JSONResponse | None:
    """If data has an 'error' key, return a 500 JSONResponse. Otherwise None."""
    if "error" in data:
        return JSONResponse(data, status_code=500)
    return None


@app.get("/api/projects")
async def api_projects():
    """List projects (epics) for the task board dropdown."""
    data = await _mcp_task_call("kronos_task_list", {})
    err = _check_mcp_error(data)
    if err:
        return err
    # Extract unique projects from stories
    projects: dict[str, dict] = {}
    for story in data.get("stories", []):
        pid = story.get("project", "")
        if pid and pid not in projects:
            projects[pid] = {
                "id": pid,
                "title": pid.replace("proj-", "").replace("-", " ").title(),
            }
    return JSONResponse({"projects": list(projects.values())})


@app.get("/api/tasks/board")
async def api_board_view(project_id: str | None = None):
    """Kanban board — columns with full story+task data."""
    args: dict = {}
    if project_id:
        args["project_id"] = project_id
    data = await _mcp_task_call("kronos_board_view", args)
    return _check_mcp_error(data) or JSONResponse(data)


@app.get("/api/tasks/backlog")
async def api_backlog_view(project_id: str | None = None, feat_id: str | None = None,
                           priority: str | None = None):
    """Stories not on the board."""
    args: dict = {}
    if project_id:
        args["project_id"] = project_id
    if feat_id:
        args["feat_id"] = feat_id
    if priority:
        args["priority"] = priority
    data = await _mcp_task_call("kronos_backlog_view", args)
    return _check_mcp_error(data) or JSONResponse(data)


@app.get("/api/tasks/list")
async def api_task_list(status: str | None = None, priority: str | None = None,
                        feat_id: str | None = None, project_id: str | None = None):
    """List stories/tasks with optional filters."""
    args: dict = {}
    if status:
        args["status"] = status
    if priority:
        args["priority"] = priority
    if feat_id:
        args["feat_id"] = feat_id
    if project_id:
        args["project_id"] = project_id
    data = await _mcp_task_call("kronos_task_list", args)
    return _check_mcp_error(data) or JSONResponse(data)


@app.get("/api/tasks/{item_id}")
async def api_task_get(item_id: str):
    """Get a single story or task by ID."""
    data = await _mcp_task_call("kronos_task_get", {"item_id": item_id})
    return _check_mcp_error(data) or JSONResponse(data)


class TaskCreateRequest(BaseModel):
    type: str  # "story" or "task"
    title: str
    feat_id: str | None = None
    story_id: str | None = None
    priority: str | None = None
    estimate_days: float | None = None
    description: str | None = None
    acceptance_criteria: list[str] | None = None
    tags: list[str] | None = None
    assignee: str | None = None
    notes: str | None = None


@app.post("/api/tasks")
async def api_task_create(req: TaskCreateRequest):
    """Create a story or task."""
    args = req.model_dump(exclude_none=True)
    data = await _mcp_task_call("kronos_task_create", args)
    return _check_mcp_error(data) or JSONResponse(data)


class TaskUpdateRequest(BaseModel):
    item_id: str
    title: str | None = None
    status: str | None = None
    priority: str | None = None
    estimate_days: float | None = None
    description: str | None = None
    notes: str | None = None


@app.put("/api/tasks/{item_id}")
async def api_task_update(item_id: str, req: TaskUpdateRequest):
    """Update a story or task."""
    fields = req.model_dump(exclude_none=True)
    fields.pop("item_id", None)
    data = await _mcp_task_call("kronos_task_update", {
        "item_id": item_id,
        "fields": fields,
    })
    return _check_mcp_error(data) or JSONResponse(data)


class TaskMoveRequest(BaseModel):
    column: str  # new, active, in_progress, resolved, closed


@app.post("/api/tasks/{story_id}/move")
async def api_task_move(story_id: str, req: TaskMoveRequest):
    """Move a story to a board column."""
    data = await _mcp_task_call("kronos_task_move", {
        "story_id": story_id, "column": req.column
    })
    return _check_mcp_error(data) or JSONResponse(data)


@app.post("/api/tasks/archive")
async def api_task_archive(feat_id: str | None = None):
    """Archive closed stories."""
    args: dict = {}
    if feat_id:
        args["feat_id"] = feat_id
    data = await _mcp_task_call("kronos_task_archive", args)
    return _check_mcp_error(data) or JSONResponse(data)


@app.get("/api/calendar")
async def api_calendar_view(start_date: str, end_date: str):
    """Calendar entries for a date range."""
    data = await _mcp_task_call("kronos_calendar_view", {
        "start_date": start_date, "end_date": end_date
    })
    return JSONResponse(data)


class CalendarAddRequest(BaseModel):
    title: str
    date: str
    time: str | None = None
    duration_hours: float | None = None
    recurring: bool | None = None
    notes: str | None = None


@app.post("/api/calendar")
async def api_calendar_add(req: CalendarAddRequest):
    """Add a personal calendar event."""
    args = req.model_dump(exclude_none=True)
    data = await _mcp_task_call("kronos_calendar_add", args)
    return JSONResponse(data)


class CalendarUpdateRequest(BaseModel):
    action: str = "update"  # update or delete
    title: str | None = None
    date: str | None = None
    time: str | None = None
    duration_hours: float | None = None
    notes: str | None = None


@app.put("/api/calendar/{event_id}")
async def api_calendar_update(event_id: str, req: CalendarUpdateRequest):
    """Update or delete a personal calendar event."""
    args = req.model_dump(exclude_none=True)
    args["event_id"] = event_id
    data = await _mcp_task_call("kronos_calendar_update", args)
    return JSONResponse(data)


@app.post("/api/calendar/sync")
async def api_calendar_sync():
    """Sync schedule from board state."""
    data = await _mcp_task_call("kronos_calendar_sync", {})
    return JSONResponse(data)


# ---------------------------------------------------------------------------
# Vault Explorer endpoints
# ---------------------------------------------------------------------------

@app.get("/api/vault/list")
async def api_vault_list(domain: str | None = None):
    """List FDOs, optionally filtered by domain."""
    args: dict = {}
    if domain:
        args["domain"] = domain
    data = await _mcp_task_call("kronos_list", args)
    return _check_mcp_error(data) or JSONResponse(data)


@app.get("/api/vault/search")
async def api_vault_search(q: str, semantic: bool = True):
    """Search FDOs via hybrid search."""
    data = await _mcp_task_call("kronos_search", {
        "query": q, "semantic": semantic,
    })
    return _check_mcp_error(data) or JSONResponse(data)


@app.get("/api/vault/tags")
async def api_vault_tags(domain: str | None = None):
    """Get all tags with counts, optionally filtered by domain."""
    args: dict = {}
    if domain:
        args["domain"] = domain
    data = await _mcp_task_call("kronos_tags", args)
    return _check_mcp_error(data) or JSONResponse(data)


@app.get("/api/vault/graph")
async def api_vault_graph(id: str | None = None, depth: int = 1, scope: str = "all"):
    """Get graph data. Without id, builds full vault graph."""
    if id:
        data = await _mcp_task_call("kronos_graph", {
            "id": id, "depth": depth, "scope": scope,
        })
        return _check_mcp_error(data) or JSONResponse(data)

    # Full graph: list all FDOs, then batch graph calls for edges
    list_data = await _mcp_task_call("kronos_list", {})
    err = _check_mcp_error(list_data)
    if err:
        return err

    all_nodes: dict[str, dict] = {}
    for fdo in list_data.get("fdos", []):
        all_nodes[fdo["id"]] = {
            "id": fdo["id"], "title": fdo["title"],
            "domain": fdo["domain"], "status": fdo.get("status", "seed"),
            "confidence": fdo.get("confidence", 0.5),
            "tags": fdo.get("tags", []),
        }

    all_edges: list[dict] = []
    edge_set: set[tuple] = set()
    sem = asyncio.Semaphore(10)

    async def fetch_edges(fdo_id: str) -> list[dict]:
        async with sem:
            g = await _mcp_task_call("kronos_graph", {
                "id": fdo_id, "depth": 1, "scope": scope,
            })
            return g.get("edges", [])

    tasks = [fetch_edges(fid) for fid in all_nodes]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for edge_list in results:
        if isinstance(edge_list, list):
            for e in edge_list:
                key = (e["from"], e["to"], e.get("type", "related"))
                if key not in edge_set:
                    edge_set.add(key)
                    all_edges.append(e)

    return JSONResponse({
        "nodes": all_nodes,
        "edges": all_edges,
        "count": len(all_nodes),
    })


@app.get("/api/vault/stats")
async def api_vault_stats():
    """Aggregate vault stats for dashboard widget."""
    data = await _mcp_task_call("kronos_validate", {})
    return _check_mcp_error(data) or JSONResponse(data)


@app.get("/api/vault/{fdo_id}")
async def api_vault_get(fdo_id: str):
    """Get full FDO by ID."""
    data = await _mcp_task_call("kronos_get", {"id": fdo_id})
    return _check_mcp_error(data) or JSONResponse(data)


class VaultCreateRequest(BaseModel):
    id: str
    title: str
    domain: str
    confidence: float
    body: str
    status: str | None = "seed"
    tags: list[str] | None = None
    related: list[str] | None = None
    confidence_basis: str | None = None
    pac_parent: str | None = None
    source_repos: list[str] | None = None


@app.post("/api/vault")
async def api_vault_create(req: VaultCreateRequest):
    """Create a new FDO."""
    args = req.model_dump(exclude_none=True)
    data = await _mcp_task_call("kronos_create", args)
    return _check_mcp_error(data) or JSONResponse(data)


class VaultUpdateRequest(BaseModel):
    title: str | None = None
    status: str | None = None
    confidence: float | None = None
    tags: list[str] | None = None
    related: list[str] | None = None
    body: str | None = None
    confidence_basis: str | None = None
    pac_parent: str | None = None


@app.put("/api/vault/{fdo_id}")
async def api_vault_update(fdo_id: str, req: VaultUpdateRequest):
    """Update FDO fields."""
    fields = req.model_dump(exclude_none=True)
    if not fields:
        return JSONResponse({"error": "No fields to update"}, status_code=400)
    data = await _mcp_task_call("kronos_update", {
        "id": fdo_id, "fields": fields,
    })
    return _check_mcp_error(data) or JSONResponse(data)


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    caller_id: str | None = None  # default: "peter" — services pass their own id
    sandbox: bool = False  # sandbox mode — blocks vault/memory writes for eval


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
                "sandbox": req.sandbox,
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
    _active_ws_sessions.add(session_id)
    logger.info("WS connected: %s", session_id)

    graph_config = {"configurable": {"thread_id": f"grim-web-{session_id}"}}

    try:
        while True:
            data = await ws.receive_text()
            try:
                payload = json.loads(data)
                message = payload.get("message", data)
                caller_id = payload.get("caller_id", "peter")
                sandbox_mode = payload.get("sandbox", False)
            except json.JSONDecodeError:
                message = data
                caller_id = "peter"
                sandbox_mode = False

            if not message:
                continue

            try:
                import time as _time
                _t0 = _time.monotonic()

                full_response = ""
                last_knowledge = []
                last_session_knowledge_count = 0
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
                        "sandbox": sandbox_mode,
                    },
                    config=graph_config,
                    version="v2",
                ):
                    kind = event.get("event", "")
                    name = event.get("name", "")
                    _event_count += 1

                    # ── Node lifecycle ──
                    # v0.0.6 nodes + v0.10 subgraph nodes
                    if kind == "on_chain_start" and name in (
                        "identity", "compress", "memory", "skill_match",
                        "router", "companion", "integrate", "evolve",
                        "graph_router", "personal_companion", "planning_companion",
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
                            if "session_knowledge" in output:
                                sk = output["session_knowledge"]
                                last_session_knowledge_count = len(sk) if sk else 0
                                detail["session_knowledge_count"] = last_session_knowledge_count
                                # Store for API access — serialize entries
                                if sk:
                                    _session_knowledge[session_id] = [
                                        e.to_dict() if hasattr(e, "to_dict") else e
                                        for e in sk
                                    ]
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

                        # Capture integrate node's formatted response.
                        # The integrate node returns {"messages": [AIMessage(...)]}
                        # with the agent summary + audit verdict + file listing,
                        # but it doesn't call an LLM, so on_chat_model_end never
                        # fires for it. We must extract the text here.
                        if name == "integrate" and isinstance(output, dict):
                            integrate_msgs = output.get("messages", [])
                            for im in integrate_msgs:
                                if hasattr(im, "content"):
                                    itext = _extract_text(im.content)
                                    if itext:
                                        full_response = itext
                                        _node_text["integrate"] = itext
                                        logger.debug("Captured integrate response (%d chars)", len(itext))

                        # Emit compact memory notification when evolve completes
                        # (instead of streaming full memory content to the UI)
                        if name == "evolve":
                            await ws.send_json({
                                "type": "memory_notification",
                                "updated": True,
                                "summary": "Working memory updated",
                                "duration_ms": node_elapsed,
                            })

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
                                # Applies to companion (v0.0.6) and conversation (v0.10)
                                if _current_node in ("companion", "conversation"):
                                    thinking = _node_stream_text.get(_current_node, "")
                                    await ws.send_json({
                                        "type": "stream_clear",
                                        "node": _current_node,
                                        "thinking": thinking.strip() if thinking else "",
                                    })
                                    # Reset stream text and full_response
                                    # so the final answer starts fresh
                                    _node_stream_text[_current_node] = ""
                                    full_response = ""
                            # Capture the LAST non-tool-call AI response —
                            # but NOT from the evolve node, whose LLM calls
                            # (objective extraction, memory update) are internal
                            # and must not overwrite the user-facing response.
                            if not has_tool_calls and hasattr(resp, "content") and _current_node != "evolve":
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
                        # Skip streaming tokens from evolve node — its internal
                        # LLM calls (memory update, objective extraction) should
                        # not dump full content to the chat UI.
                        if _current_node == "evolve":
                            continue
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
                        "session_knowledge_count": last_session_knowledge_count,
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
    finally:
        _active_ws_sessions.discard(session_id)


# ---------------------------------------------------------------------------
# v2 WebSocket — GrimClient SDK sessions
# ---------------------------------------------------------------------------

@app.websocket("/ws/v2/{session_id}")
async def websocket_chat_v2(ws: WebSocket, session_id: str):
    """v2 WebSocket chat — uses GrimClient SDK sessions.

    Same client protocol as /ws/{session_id}:
      Client sends: {"message": "...", "caller_id": "..."}
      Server sends:
        {"type": "trace", "cat": "...", "text": "..."}  — trace events
        {"type": "stream", "token": "..."}               — text tokens
        {"type": "response", "content": "...", "meta": {...}} — final
        {"type": "error", "content": "..."}              — error
    """
    if _session_manager is None:
        await ws.accept()
        await ws.send_json({"type": "error", "content": "SDK sessions not available"})
        await ws.close()
        return

    await ws.accept()
    logger.info("WS v2 connected: %s", session_id)

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

                # Get or create the GrimClient for this session
                client = await _session_manager.get_or_create(
                    session_id, caller_id=caller_id,
                )

                await ws.send_json({
                    "type": "trace", "cat": "sdk", "text": "Processing...",
                    "ms": 0,
                })

                full_response = ""
                tool_trace: list[str] = []

                async for event in client.send_streaming(message):
                    elapsed = round((_time.monotonic() - _t0) * 1000)

                    if event.type == "text":
                        token = event.data.get("text", "")
                        if token:
                            full_response += token
                            await ws.send_json({
                                "type": "stream",
                                "token": token,
                                "node": "sdk",
                            })

                    elif event.type == "tool_use":
                        tool_name = event.data.get("name", "unknown")
                        tool_input = event.data.get("input", {})
                        tool_trace.append(tool_name)
                        await ws.send_json({
                            "type": "trace",
                            "cat": "tool",
                            "text": f"⚡ {tool_name}",
                            "tool": tool_name,
                            "action": "call",
                            "ms": elapsed,
                            "input": _safe_truncate(tool_input),
                        })

                    elif event.type == "result":
                        cost = event.data.get("cost_usd")
                        turns = event.data.get("num_turns")
                        total_ms = round((_time.monotonic() - _t0) * 1000)

                        await ws.send_json({
                            "type": "trace",
                            "cat": "sdk",
                            "text": f"Complete ({total_ms}ms)",
                            "ms": total_ms,
                        })

                # Mark session active
                _session_manager.touch(session_id)

                total_ms = round((_time.monotonic() - _t0) * 1000)

                if not full_response:
                    full_response = "I processed your message but have no response to show."

                # Get cost/turn data from session
                session_info = client.session_info

                await ws.send_json({
                    "type": "response",
                    "content": full_response,
                    "meta": {
                        "mode": "sdk",
                        "tools": tool_trace,
                        "total_ms": total_ms,
                        "cost_usd": session_info.get("total_cost_usd", 0),
                        "turn_count": session_info.get("turn_count", 0),
                    },
                })

                logger.info("WS v2 turn: %s — %d chars, %d tools, %dms",
                            session_id, len(full_response), len(tool_trace), total_ms)

            except Exception as exc:
                import traceback
                tb = traceback.format_exc()
                logger.error("WS v2 error: %s\n%s", exc, tb)
                exc_type = type(exc).__name__
                await ws.send_json({
                    "type": "error",
                    "content": f"Error ({exc_type}): {exc}",
                })

    except WebSocketDisconnect:
        logger.info("WS v2 disconnected: %s", session_id)


# ---------------------------------------------------------------------------
# v2 Session API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/v2/sessions")
async def list_v2_sessions():
    """List active SDK sessions."""
    if _session_manager is None:
        return JSONResponse({"sessions": [], "error": "SDK sessions not available"}, status_code=503)
    return JSONResponse({
        "sessions": _session_manager.list_sessions(),
        "count": _session_manager.active_count,
    })


@app.delete("/api/v2/sessions/{session_id}")
async def destroy_v2_session(session_id: str):
    """Destroy an SDK session."""
    if _session_manager is None:
        return JSONResponse({"error": "SDK sessions not available"}, status_code=503)
    existed = await _session_manager.destroy(session_id)
    if existed:
        return JSONResponse({"ok": True, "session_id": session_id})
    return JSONResponse({"error": "Session not found"}, status_code=404)


@app.post("/api/v2/chat")
async def chat_v2_rest(req: ChatRequest):
    """REST endpoint for v2 SDK sessions — send a message, get a response."""
    if _session_manager is None:
        return JSONResponse({"error": "SDK sessions not available"}, status_code=503)

    session_id = req.session_id or str(uuid.uuid4())[:8]
    caller_id = req.caller_id or "peter"

    try:
        client = await _session_manager.get_or_create(session_id, caller_id=caller_id)
        resp = await client.send(req.message)

        _session_manager.touch(session_id)

        return JSONResponse({
            "response": resp.text or "No response.",
            "session_id": session_id,
            "tool_calls": resp.tool_calls,
            "cost_usd": resp.cost_usd,
            "num_turns": resp.num_turns,
        })
    except Exception as exc:
        logger.exception("v2 chat error")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Graph Studio endpoints
# ---------------------------------------------------------------------------

@app.get("/api/graph/topology")
async def graph_topology():
    """Serialize the GRIM LangGraph topology for the Graph Studio UI.

    Merges static infrastructure node metadata with live agent-registry
    metadata (companion nodes + discovered agents).  Returns nodes with
    layout positions and enabled state, plus all edges.
    """
    from core.graph_topology import INFRA_NODE_METADATA, STATIC_EDGES, NODE_POSITIONS

    disabled = set(_config.agents_disabled) if _config else set()
    nodes: dict[str, dict] = {}

    # 1. Infrastructure nodes (always present)
    for node_id, meta in INFRA_NODE_METADATA.items():
        pos = NODE_POSITIONS.get(node_id, (0, 0))
        nodes[node_id] = {**meta, "enabled": True, "col": pos[0], "row": pos[1]}

    # 2. Companion + agent nodes from the dynamic roster
    model_name = _config.model if _config else "claude-sonnet-4-6"
    if _agent_metadata:
        for meta in _agent_metadata:
            node_id = meta["id"]
            pos = NODE_POSITIONS.get(node_id)
            if pos is None:
                continue  # skip agents not in the graph topology (e.g. sub-agents)
            _companion_nodes = {"companion", "conversation", "planning"}
            node_type = "companion" if (
                node_id.endswith("_companion") or node_id in _companion_nodes
            ) else "agent"
            node = {
                **meta,
                "node_type": node_type,
                "enabled": node_id not in disabled,
                "col": pos[0],
                "row": pos[1],
            }
            # Ensure companion nodes get model info (agents get it from metadata())
            if "model" not in node:
                node["model"] = model_name
            nodes[node_id] = node

    return JSONResponse({
        "nodes": nodes,
        "edges": STATIC_EDGES,
        "node_count": len(nodes),
        "edge_count": len(STATIC_EDGES),
    })


@app.get("/api/graph/sessions")
async def graph_sessions():
    """Active WebSocket session count for Graph Studio status bar."""
    return JSONResponse({
        "active": len(_active_ws_sessions),
        "session_ids": sorted(_active_ws_sessions),
    })


# ---------------------------------------------------------------------------
# Session Knowledge endpoints
# ---------------------------------------------------------------------------

@app.get("/api/session/knowledge")
async def api_session_knowledge(session_id: str | None = None):
    """Return accumulated session knowledge entries for a session.

    If no session_id is provided, returns entries for the most recent active session.
    """
    if session_id and session_id in _session_knowledge:
        entries = _session_knowledge[session_id]
    elif not session_id and _session_knowledge:
        # Most recently updated session
        entries = list(_session_knowledge.values())[-1]
    else:
        entries = []

    return JSONResponse({
        "entries": entries,
        "count": len(entries),
    })


@app.get("/api/session/knowledge/graph")
async def api_session_knowledge_graph(session_id: str | None = None):
    """Build a force-graph from session knowledge entries.

    Nodes = accumulated FDOs, sized by hit_count.
    Edges = related links between FDOs present in the session.
    """
    if session_id and session_id in _session_knowledge:
        entries = _session_knowledge[session_id]
    elif not session_id and _session_knowledge:
        entries = list(_session_knowledge.values())[-1]
    else:
        return JSONResponse({"nodes": [], "edges": [], "node_count": 0, "edge_count": 0})

    # Build node set
    node_map: dict[str, dict] = {}
    for e in entries:
        fdo_id = e.get("fdo_id", "")
        if not fdo_id:
            continue
        node_map[fdo_id] = {
            "id": fdo_id,
            "title": e.get("fdo_title", fdo_id),
            "domain": e.get("fdo_domain", ""),
            "confidence": e.get("fdo_confidence", 0),
            "hit_count": e.get("hit_count", 1),
            "fetched_turn": e.get("fetched_turn", 0),
            "fetched_by": e.get("fetched_by", ""),
        }

    # Build edges from related links (only between nodes in the session)
    edges: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()
    for e in entries:
        fdo_id = e.get("fdo_id", "")
        for related_id in e.get("related", []):
            if related_id in node_map and fdo_id != related_id:
                edge_key = tuple(sorted((fdo_id, related_id)))
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({
                        "source": edge_key[0],
                        "target": edge_key[1],
                        "type": "related",
                    })

    nodes = list(node_map.values())
    return JSONResponse({
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    })


@app.get("/api/memory/graph")
async def api_memory_graph():
    """Build a force-graph from GRIM's working memory FDO references.

    Parses wikilinks [[fdo-id]] from memory.md sections, builds nodes
    from referenced FDOs, and creates edges from co-section membership
    + related links.
    """
    import re

    # Read memory via MCP or fallback
    memory_content = ""
    try:
        if _config:
            from core.memory_store import read_memory
            memory_content = read_memory(_config.vault_path)
    except Exception:
        pass

    if not memory_content:
        return JSONResponse({"nodes": [], "edges": [], "sections": [], "node_count": 0, "edge_count": 0})

    # Parse sections and extract wikilinks
    sections: dict[str, list[str]] = {}
    current_section = "root"
    for line in memory_content.split("\n"):
        if line.startswith("## "):
            current_section = line[3:].strip()
            sections.setdefault(current_section, [])
        else:
            # Extract [[fdo-id]] wikilinks
            refs = re.findall(r"\[\[([a-z0-9-]+)\]\]", line)
            for ref in refs:
                sections.setdefault(current_section, [])
                if ref not in sections[current_section]:
                    sections[current_section].append(ref)

    # Build nodes from all referenced FDOs
    all_fdo_ids: set[str] = set()
    for fdo_list in sections.values():
        all_fdo_ids.update(fdo_list)

    nodes: list[dict] = []
    section_membership: dict[str, list[str]] = {}  # fdo_id → [sections]

    for fdo_id in all_fdo_ids:
        member_sections = [s for s, ids in sections.items() if fdo_id in ids]
        section_membership[fdo_id] = member_sections
        nodes.append({
            "id": fdo_id,
            "title": fdo_id,  # UI can enrich via kronos_get
            "sections": member_sections,
            "reference_count": len(member_sections),
        })

    # Edges: co-section membership (FDOs referenced in the same section)
    edges: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()
    for section_name, fdo_ids in sections.items():
        for i, a in enumerate(fdo_ids):
            for b in fdo_ids[i + 1:]:
                edge_key = tuple(sorted((a, b)))
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({
                        "source": edge_key[0],
                        "target": edge_key[1],
                        "type": "co_section",
                        "section": section_name,
                    })

    return JSONResponse({
        "nodes": nodes,
        "edges": edges,
        "sections": list(sections.keys()),
        "node_count": len(nodes),
        "edge_count": len(edges),
    })


# ---------------------------------------------------------------------------
# Evaluation endpoints
# ---------------------------------------------------------------------------

_eval_runs: dict[str, dict] = {}  # run_id → {status, results, ws_clients, error}


class EvalRunRequest(BaseModel):
    tier: int | str = "all"
    categories: list[str] | None = None


class EvalCaseAppendRequest(BaseModel):
    case: dict


@app.post("/api/eval/run")
async def api_eval_start(req: EvalRunRequest):
    """Start an eval run as a background task."""
    from eval.config import EvalConfig
    from eval.engine.runner import EvalRunner

    run_id = str(uuid.uuid4())[:8]
    _eval_runs[run_id] = {
        "status": "running",
        "results": None,
        "ws_clients": [],
        "error": None,
    }

    async def _broadcast(event: dict):
        for ws_ref in list(_eval_runs.get(run_id, {}).get("ws_clients", [])):
            try:
                await ws_ref.send_json(event)
            except Exception:
                pass

    def _progress(event: dict):
        # Schedule broadcast on the event loop (callback runs sync)
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(_broadcast(event))
        except Exception:
            pass

    if req.tier == 3:
        # Tier 3 — live integration eval via Tier3Executor
        async def _run_tier3():
            try:
                from eval.tier3.executor import Tier3Executor
                from eval.tier3.judges import create_default_judges

                config = EvalConfig.from_env()
                judges = create_default_judges()
                executor = Tier3Executor(config=config, judges=judges, progress_callback=_progress)
                results = await executor.run(categories=req.categories)

                # Wrap into standard result shape for UI compatibility
                wrapped = _wrap_tier3_results(run_id, results)
                _eval_runs[run_id]["results"] = wrapped
                _eval_runs[run_id]["status"] = "completed"

                # Persist to disk
                from eval.engine.comparator import save_run
                from eval.schema import EvalRun
                persist_path = config.results_dir / f"{run_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
                config.results_dir.mkdir(parents=True, exist_ok=True)
                persist_path.write_text(json.dumps(wrapped, default=str), encoding="utf-8")

                await _broadcast({"type": "complete", "run_id": run_id})
            except Exception as exc:
                logger.exception("Tier 3 eval run %s failed", run_id)
                _eval_runs[run_id]["status"] = "failed"
                _eval_runs[run_id]["error"] = str(exc)
                await _broadcast({"type": "error", "run_id": run_id, "message": str(exc)})

        asyncio.create_task(_run_tier3())
    else:
        # Tier 1/2/all — existing EvalRunner
        async def _run():
            try:
                runner = EvalRunner(EvalConfig(), progress_callback=_progress)
                result = await runner.run(tier=req.tier, categories=req.categories)
                _eval_runs[run_id]["results"] = result.model_dump()
                _eval_runs[run_id]["status"] = "completed"
                await _broadcast({"type": "complete", "run_id": run_id})
            except Exception as exc:
                logger.exception("Eval run %s failed", run_id)
                _eval_runs[run_id]["status"] = "failed"
                _eval_runs[run_id]["error"] = str(exc)
                await _broadcast({"type": "error", "run_id": run_id, "message": str(exc)})

        asyncio.create_task(_run())
    return JSONResponse({"run_id": run_id, "status": "running"})


def _wrap_tier3_results(run_id: str, results: list) -> dict:
    """Wrap Tier3CaseResults into the standard EvalRun-like dict shape."""
    from collections import defaultdict

    by_cat: dict[str, list] = defaultdict(list)
    for r in results:
        by_cat[r.category].append(r)

    suites = []
    total_passed = 0
    total_cases = 0
    total_dur = 0

    for cat, cat_results in sorted(by_cat.items()):
        cases = []
        for r in cat_results:
            last_response = ""
            if r.turn_results:
                last_response = r.turn_results[-1].response_text or ""
            cases.append({
                "case_id": r.case_id,
                "tier": 3,
                "category": r.category,
                "passed": r.passed,
                "score": r.overall_score,
                "duration_ms": r.duration_ms or 0,
                "tags": r.tags or [],
                "checks": [],
                "dimensions": [
                    {"name": j.judge, "score": j.score, "rationale": j.rationale or ""}
                    for j in (r.judgments or [])
                ],
                "tool_trace": r.tools_called or [],
                "response_text": last_response,
                "error": r.error,
                # Tier 3 extras
                "judgments": [j.model_dump() for j in (r.judgments or [])],
                "routing_path": r.routing_path or [],
                "subgraph_history": r.subgraph_history or [],
                "metrics": r.metrics.model_dump() if r.metrics else None,
                "turn_results": [tr.model_dump() for tr in (r.turn_results or [])],
            })

        cat_passed = sum(1 for c in cases if c["passed"])
        cat_total = len(cases)
        cat_score = sum(c["score"] for c in cases) / cat_total if cat_total else 0
        suites.append({
            "tier": 3,
            "category": cat,
            "cases": cases,
            "passed": cat_passed,
            "total": cat_total,
            "score": round(cat_score, 4),
        })
        total_passed += cat_passed
        total_cases += cat_total
        total_dur += sum(c["duration_ms"] for c in cases)

    overall = sum(s["score"] for s in suites) / len(suites) if suites else 0

    return {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tier": 3,
        "status": "completed",
        "suites": suites,
        "overall_score": round(overall, 4),
        "pass_rate": round(total_passed / total_cases, 4) if total_cases else 0,
        "total_cases": total_cases,
        "total_passed": total_passed,
        "duration_ms": total_dur,
    }


@app.get("/api/eval/run/{run_id}")
async def api_eval_status(run_id: str):
    """Poll eval run status."""
    entry = _eval_runs.get(run_id)
    if not entry:
        return JSONResponse({"error": "Unknown run_id"}, status_code=404)
    return JSONResponse({
        "run_id": run_id,
        "status": entry["status"],
        "error": entry.get("error"),
        "results": entry.get("results"),
    })


@app.get("/api/eval/runs")
async def api_eval_runs():
    """List all saved eval runs (from disk)."""
    from eval.config import EvalConfig

    config = EvalConfig()
    runs = []
    if config.results_dir.exists():
        for f in sorted(config.results_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                # Extract per-suite scores for history chart
                suite_scores: dict[str, float] = {}
                for s in data.get("suites", []):
                    cat = s.get("category", "")
                    if cat:
                        suite_scores[cat] = s.get("score", 0)
                runs.append({
                    "run_id": data.get("run_id", ""),
                    "timestamp": data.get("timestamp", ""),
                    "status": data.get("status", ""),
                    "tier": data.get("tier", ""),
                    "total_cases": data.get("total_cases", 0),
                    "passed_cases": data.get("passed_cases", 0),
                    "overall_score": data.get("overall_score", 0),
                    "git_sha": data.get("git_sha", ""),
                    "duration_ms": data.get("duration_ms", 0),
                    "suite_scores": suite_scores,
                    "file": f.name,
                })
            except Exception:
                pass
    return JSONResponse(runs)


@app.get("/api/eval/results/{run_id}")
async def api_eval_results(run_id: str):
    """Get full results for a saved run."""
    from eval.config import EvalConfig

    config = EvalConfig()
    if config.results_dir.exists():
        for f in config.results_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("run_id", "").startswith(run_id):
                    return JSONResponse(data)
            except Exception:
                pass
    # Also check in-memory runs
    entry = _eval_runs.get(run_id)
    if entry and entry.get("results"):
        return JSONResponse(entry["results"])
    return JSONResponse({"error": "Run not found"}, status_code=404)


@app.get("/api/eval/compare")
async def api_eval_compare(base: str, target: str):
    """Compare two eval runs for regressions."""
    from eval.config import EvalConfig
    from eval.engine.comparator import compare_runs
    from eval.schema import EvalRun

    config = EvalConfig()
    base_data = target_data = None

    if config.results_dir.exists():
        for f in config.results_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                rid = data.get("run_id", "")
                if rid.startswith(base):
                    base_data = data
                if rid.startswith(target):
                    target_data = data
            except Exception:
                pass

    if not base_data or not target_data:
        return JSONResponse({"error": "One or both runs not found"}, status_code=404)

    base_run = EvalRun(**base_data)
    target_run = EvalRun(**target_data)
    base_run.compute_stats()
    target_run.compute_stats()
    result = compare_runs(base_run, target_run)
    return JSONResponse(result.model_dump())


@app.get("/api/eval/datasets")
async def api_eval_datasets():
    """List all eval datasets with case counts."""
    from eval.config import EvalConfig
    from eval.engine.runner import EvalRunner

    runner = EvalRunner(EvalConfig())
    return JSONResponse(runner.list_datasets())


@app.get("/api/eval/cases/{tier}")
async def api_eval_cases(tier: int, category: str | None = None):
    """Get test case listing for a tier (id, category, tags, description, turn_count)."""
    from eval.config import EvalConfig

    config = EvalConfig()
    cases: list[dict] = []

    if tier == 3:
        from eval.tier3.executor import Tier3Executor
        executor = Tier3Executor(config)
        categories = [category] if category else None
        datasets = executor.load_datasets(categories)
        for ds in datasets.values():
            for c in ds.cases:
                cases.append({
                    "id": c.id,
                    "tier": 3,
                    "category": c.category.value,
                    "description": c.description or "",
                    "tags": c.tags or [],
                    "turn_count": len(c.turns),
                })
    else:
        tier_dir = config.datasets_dir / f"tier{tier}"
        if tier_dir.exists():
            for path in sorted(tier_dir.glob("*_cases.yaml")):
                try:
                    data = yaml.safe_load(path.read_text(encoding="utf-8"))
                    cat = data.get("category", path.stem)
                    if category and cat != category:
                        continue
                    for c in data.get("cases", []):
                        cases.append({
                            "id": c.get("id", ""),
                            "tier": tier,
                            "category": cat,
                            "description": c.get("description", ""),
                            "tags": c.get("tags", []),
                            "turn_count": len(c.get("turns", [c])),
                        })
                except Exception:
                    pass

    return JSONResponse({"total": len(cases), "cases": cases})


@app.get("/api/eval/datasets/{tier}/{category}")
async def api_eval_dataset(tier: int, category: str):
    """Get a full dataset as JSON."""
    from eval.config import EvalConfig

    config = EvalConfig()
    if tier == 3:
        # Tier 3 uses {category}.yaml naming
        tier_dir = config.datasets_dir / "tier3"
        path = tier_dir / f"{category}.yaml"
    else:
        tier_dir = config.datasets_dir / f"tier{tier}"
        path = tier_dir / f"{category}_cases.yaml"
    if not path.exists():
        return JSONResponse({"error": "Dataset not found"}, status_code=404)
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/eval/datasets/{tier}/{category}/cases")
async def api_eval_append_case(tier: int, category: str, req: EvalCaseAppendRequest):
    """Append a single test case to a dataset."""
    from eval.config import EvalConfig

    config = EvalConfig()
    tier_dir = config.datasets_dir / f"tier{tier}"
    path = tier_dir / f"{category}_cases.yaml"
    if not path.exists():
        return JSONResponse({"error": "Dataset not found"}, status_code=404)

    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        cases = data.get("cases", [])

        # Check for duplicate ID
        new_id = req.case.get("id", "")
        if any(c.get("id") == new_id for c in cases):
            return JSONResponse({"error": f"Case ID '{new_id}' already exists"}, status_code=409)

        cases.append(req.case)
        data["cases"] = cases
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")
        return JSONResponse({"ok": True, "total_cases": len(cases)})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.put("/api/eval/datasets/{tier}/{category}/cases/{case_id}")
async def api_eval_update_case(tier: int, category: str, case_id: str, req: EvalCaseAppendRequest):
    """Update an existing test case in a dataset."""
    from eval.config import EvalConfig

    config = EvalConfig()
    tier_dir = config.datasets_dir / f"tier{tier}"
    path = tier_dir / f"{category}_cases.yaml"
    if not path.exists():
        return JSONResponse({"error": "Dataset not found"}, status_code=404)

    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        cases = data.get("cases", [])

        idx = next((i for i, c in enumerate(cases) if c.get("id") == case_id), None)
        if idx is None:
            return JSONResponse({"error": f"Case '{case_id}' not found"}, status_code=404)

        cases[idx] = req.case
        data["cases"] = cases
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")
        return JSONResponse({"ok": True, "case_id": case_id})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.delete("/api/eval/datasets/{tier}/{category}/cases/{case_id}")
async def api_eval_delete_case(tier: int, category: str, case_id: str):
    """Delete a test case from a dataset."""
    from eval.config import EvalConfig

    config = EvalConfig()
    tier_dir = config.datasets_dir / f"tier{tier}"
    path = tier_dir / f"{category}_cases.yaml"
    if not path.exists():
        return JSONResponse({"error": "Dataset not found"}, status_code=404)

    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        cases = data.get("cases", [])

        original_len = len(cases)
        cases = [c for c in cases if c.get("id") != case_id]
        if len(cases) == original_len:
            return JSONResponse({"error": f"Case '{case_id}' not found"}, status_code=404)

        data["cases"] = cases
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")
        return JSONResponse({"ok": True, "total_cases": len(cases)})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.websocket("/ws/eval/{run_id}")
async def ws_eval_progress(ws: WebSocket, run_id: str):
    """Stream eval progress events for a running eval."""
    await ws.accept()
    entry = _eval_runs.get(run_id)
    if not entry:
        await ws.send_json({"type": "error", "message": "Unknown run_id"})
        await ws.close()
        return

    entry.setdefault("ws_clients", []).append(ws)
    logger.info("Eval WS connected: %s", run_id)
    try:
        while True:
            await ws.receive_text()  # keep-alive
    except WebSocketDisconnect:
        entry.get("ws_clients", []).remove(ws) if ws in entry.get("ws_clients", []) else None
        logger.info("Eval WS disconnected: %s", run_id)


# ---------------------------------------------------------------------------
# Pool API (Project Charizard)
# ---------------------------------------------------------------------------

@app.get("/api/pool/status")
async def api_pool_status():
    """Get execution pool status."""
    if _execution_pool is None:
        return JSONResponse({"error": "Pool not enabled"}, status_code=503)
    return JSONResponse(_execution_pool.status)


@app.get("/api/pool/jobs")
async def api_pool_jobs(status: str | None = None, job_type: str | None = None, limit: int = 50):
    """List pool jobs, optionally filtered by status and/or job type."""
    if _execution_pool is None:
        return JSONResponse({"error": "Pool not enabled"}, status_code=503)

    from core.pool.models import JobStatus as JS, JobType as JT
    sf = None
    if status:
        try:
            sf = JS(status)
        except ValueError:
            return JSONResponse({"error": f"Invalid status: {status}"}, status_code=400)
    tf = None
    if job_type:
        try:
            tf = JT(job_type)
        except ValueError:
            return JSONResponse({"error": f"Invalid job_type: {job_type}"}, status_code=400)

    jobs = await _execution_pool.queue.list_jobs(status_filter=sf, type_filter=tf, limit=limit)
    return JSONResponse([j.model_dump(mode="json") for j in jobs])


@app.get("/api/pool/jobs/{job_id}")
async def api_pool_job(job_id: str):
    """Get a specific pool job by ID."""
    if _execution_pool is None:
        return JSONResponse({"error": "Pool not enabled"}, status_code=503)

    job = await _execution_pool.queue.get(job_id)
    if job is None:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return JSONResponse(job.model_dump(mode="json"))


@app.post("/api/pool/jobs")
async def api_pool_submit(request: Request):
    """Submit a new pool job."""
    if _execution_pool is None:
        return JSONResponse({"error": "Pool not enabled"}, status_code=503)

    from core.pool.models import Job, JobPriority, JobType

    body = await request.json()
    try:
        job = Job(
            job_type=JobType(body["job_type"]),
            instructions=body["instructions"],
            priority=JobPriority(body.get("priority", "normal")),
            plan=body.get("plan"),
            workspace_id=body.get("workspace_id"),
            kronos_domains=body.get("kronos_domains", []),
            kronos_fdo_ids=body.get("kronos_fdo_ids", []),
        )
    except (KeyError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    job_id = await _execution_pool.submit(job)
    return JSONResponse({"job_id": job_id, "status": "queued"})


@app.post("/api/pool/jobs/{job_id}/cancel")
async def api_pool_cancel(job_id: str):
    """Cancel a queued or blocked pool job."""
    if _execution_pool is None:
        return JSONResponse({"error": "Pool not enabled"}, status_code=503)

    success = await _execution_pool.queue.cancel(job_id)
    if success:
        return JSONResponse({"ok": True, "job_id": job_id})
    return JSONResponse({"error": "Cannot cancel — job may be running or finished"}, status_code=409)


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
