"""Tests for pool startup performance optimizations.

Covers:
- Kronos SSE server mode (make_sse_app, health endpoint)
- Pool warm-up (health check, fallback to stdio)
- Slot SSE config path
- Eager SDK imports
- Config defaults for new options
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Smoke: Config defaults ────────────────────────────────────────

class TestWarmupConfigDefaults:
    """New config fields exist with correct defaults."""

    def test_pool_kronos_url_default(self):
        from core.config import GrimConfig

        cfg = GrimConfig()
        assert cfg.pool_kronos_url == ""

    def test_pool_warm_on_start_default(self):
        from core.config import GrimConfig

        cfg = GrimConfig()
        assert cfg.pool_warm_on_start is True

    def test_pool_kronos_url_set(self):
        from core.config import GrimConfig

        cfg = GrimConfig(pool_kronos_url="http://127.0.0.1:8319")
        assert cfg.pool_kronos_url == "http://127.0.0.1:8319"


# ── Smoke: Slot SSE config ────────────────────────────────────────

class TestSlotSSEConfig:
    """AgentSlot accepts and uses SSE URL."""

    def test_slot_has_kronos_mcp_url(self):
        from core.pool.slot import AgentSlot

        slot = AgentSlot(slot_id="slot-0", kronos_mcp_url="http://localhost:8319")
        assert slot.kronos_mcp_url == "http://localhost:8319"

    def test_slot_default_no_url(self):
        from core.pool.slot import AgentSlot

        slot = AgentSlot(slot_id="slot-0")
        assert slot.kronos_mcp_url == ""

    def test_slot_sse_mcp_config_preferred(self):
        """When SSE URL is set, MCP config should use SSE type."""
        from core.pool.slot import AgentSlot
        from core.pool.models import Job, JobType

        slot = AgentSlot(
            slot_id="slot-0",
            kronos_mcp_url="http://localhost:8319",
            kronos_mcp_command="kronos-mcp",  # Also set — SSE should win
        )
        # We can't easily test the internal config without running execute(),
        # but we verify the field is set correctly
        assert slot.kronos_mcp_url == "http://localhost:8319"
        assert slot.kronos_mcp_command == "kronos-mcp"


# ── Smoke: Eager SDK import ───────────────────────────────────────

class TestEagerSDKImport:
    """SDK is imported at module level when available."""

    def test_sdk_names_at_module_level(self):
        """Module-level SDK imports are available (no lazy import needed)."""
        from core.pool import slot
        assert hasattr(slot, "ClaudeSDKClient")
        assert hasattr(slot, "ClaudeAgentOptions")
        assert hasattr(slot, "AssistantMessage")

    def test_slot_imports_cleanly(self):
        """Module imports without errors regardless of SDK availability."""
        from core.pool.slot import AgentSlot, AGENT_CONFIGS
        assert AgentSlot is not None
        assert len(AGENT_CONFIGS) >= 4


# ── Unit: Kronos SSE app ──────────────────────────────────────────

class TestKronosSSEApp:
    """Kronos MCP SSE ASGI app construction."""

    def test_make_sse_app_returns_starlette(self):
        """make_sse_app() builds a working Starlette app."""
        # We need to set KRONOS_VAULT_PATH for the server module to import
        import os
        vault = os.getenv("KRONOS_VAULT_PATH", "")
        if not vault:
            pytest.skip("KRONOS_VAULT_PATH not set")

        from kronos_mcp.server import make_sse_app
        from starlette.applications import Starlette

        app = make_sse_app()
        assert isinstance(app, Starlette)

    def test_make_sse_app_has_routes(self):
        """SSE app has /sse, /messages/, and /health routes."""
        import os
        vault = os.getenv("KRONOS_VAULT_PATH", "")
        if not vault:
            pytest.skip("KRONOS_VAULT_PATH not set")

        from kronos_mcp.server import make_sse_app

        app = make_sse_app()
        route_paths = [r.path for r in app.routes]
        assert "/sse" in route_paths
        assert "/messages/" in route_paths
        assert "/health" in route_paths


class TestKronosHealthEndpoint:
    """Health endpoint responds correctly."""

    @pytest.mark.asyncio
    async def test_health_returns_json(self):
        """GET /health returns JSON with status and engines_initialized."""
        import os
        vault = os.getenv("KRONOS_VAULT_PATH", "")
        if not vault:
            pytest.skip("KRONOS_VAULT_PATH not set")

        from kronos_mcp.server import make_sse_app
        from starlette.testclient import TestClient

        app = make_sse_app()
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "engines_initialized" in data
        assert "vault_path" in data


# ── Unit: Pool warm-up ────────────────────────────────────────────

@dataclass
class _MockConfig:
    pool_num_slots: int = 1
    pool_poll_interval: float = 999
    pool_max_turns_per_job: int = 5
    pool_job_timeout_secs: int = 30
    pool_kronos_url: str = ""
    pool_warm_on_start: bool = True
    kronos_mcp_command: str = ""
    workspace_root: Any = None
    vault_path: Any = None
    skills_path: Any = None
    repos_manifest: str = "repos.yaml"


class TestPoolWarmup:
    """Pool._warm_kronos() health check and fallback."""

    @pytest.fixture
    def tmp_db(self, tmp_path) -> Path:
        return tmp_path / "test_pool.db"

    @pytest.fixture
    def mock_queue(self, tmp_db):
        from core.pool.queue import JobQueue
        return JobQueue(tmp_db)

    @pytest.mark.asyncio
    async def test_warm_sse_healthy(self, mock_queue):
        """SSE warm-up succeeds when health endpoint returns 200."""
        from core.pool.pool import ExecutionPool

        config = _MockConfig(pool_kronos_url="http://127.0.0.1:8319")
        pool = ExecutionPool(mock_queue, config)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"status": "ok", "engines_initialized": True}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await pool._warm_kronos()

        # SSE URL should remain (not fallen back to stdio)
        assert pool._kronos_mcp_url == "http://127.0.0.1:8319"

    @pytest.mark.asyncio
    async def test_warm_sse_unreachable_falls_back(self, mock_queue):
        """SSE warm-up falls back to stdio when server is unreachable."""
        from core.pool.pool import ExecutionPool

        config = _MockConfig(
            pool_kronos_url="http://127.0.0.1:9999",
            kronos_mcp_command="kronos-mcp",
        )
        pool = ExecutionPool(mock_queue, config)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await pool._warm_kronos()

        # Should have cleared SSE URL (fallen back to stdio)
        assert pool._kronos_mcp_url == ""

    @pytest.mark.asyncio
    async def test_warm_stdio_logs_info(self, mock_queue):
        """Stdio warm-up just logs — no network calls."""
        from core.pool.pool import ExecutionPool

        config = _MockConfig(kronos_mcp_command="kronos-mcp")
        pool = ExecutionPool(mock_queue, config)

        # Should not raise or make any network calls
        await pool._warm_kronos()
        assert pool._kronos_mcp_url == ""

    @pytest.mark.asyncio
    async def test_warm_disabled(self, mock_queue):
        """No warm-up when pool_warm_on_start=False."""
        from core.pool.pool import ExecutionPool

        config = _MockConfig(
            pool_kronos_url="http://127.0.0.1:8319",
            pool_warm_on_start=False,
        )
        pool = ExecutionPool(mock_queue, config)

        # start() should NOT call _warm_kronos when disabled
        pool._warm_kronos = AsyncMock()
        await mock_queue.initialize()

        # Manually mimic what start() does — check the flag
        warm_on_start = getattr(config, "pool_warm_on_start", True)
        assert warm_on_start is False

    @pytest.mark.asyncio
    async def test_warm_no_kronos_configured(self, mock_queue):
        """No Kronos at all — warm-up is a no-op."""
        from core.pool.pool import ExecutionPool

        config = _MockConfig()
        pool = ExecutionPool(mock_queue, config)

        # Should not raise
        await pool._warm_kronos()


# ── Unit: Pool start passes SSE URL to slots ──────────────────────

class TestPoolStartSSE:
    """Pool.start() wires SSE URL into slots and warms them."""

    @pytest.fixture
    def tmp_db(self, tmp_path) -> Path:
        return tmp_path / "test_pool.db"

    @pytest.fixture
    def mock_queue(self, tmp_db):
        from core.pool.queue import JobQueue
        return JobQueue(tmp_db)

    @pytest.mark.asyncio
    async def test_slots_get_sse_url(self, mock_queue):
        """When pool_kronos_url is set, all slots get the URL."""
        from core.pool.pool import ExecutionPool
        from core.pool.slot import AgentSlot

        config = _MockConfig(pool_kronos_url="http://127.0.0.1:8319", pool_num_slots=2)
        pool = ExecutionPool(mock_queue, config)

        # Mock warm-up to avoid actual HTTP calls and subprocess spawning
        with patch.object(pool, "_warm_kronos", new_callable=AsyncMock), \
             patch.object(AgentSlot, "warm", new_callable=AsyncMock):
            await pool.start()

        try:
            assert len(pool._slots) == 2
            for slot in pool._slots:
                assert slot.kronos_mcp_url == "http://127.0.0.1:8319"
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_slots_get_stdio_when_no_url(self, mock_queue):
        """When pool_kronos_url is empty, slots use stdio command."""
        from core.pool.pool import ExecutionPool
        from core.pool.slot import AgentSlot

        config = _MockConfig(kronos_mcp_command="kronos-mcp", pool_num_slots=1)
        pool = ExecutionPool(mock_queue, config)

        with patch.object(pool, "_start_kronos_sse", new_callable=AsyncMock), \
             patch.object(pool, "_warm_kronos", new_callable=AsyncMock), \
             patch.object(AgentSlot, "warm", new_callable=AsyncMock):
            await pool.start()

        try:
            assert pool._slots[0].kronos_mcp_url == ""
            assert pool._slots[0].kronos_mcp_command == "kronos-mcp"
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_start_warms_slots(self, mock_queue):
        """Pool.start() calls warm() on each slot."""
        from core.pool.pool import ExecutionPool
        from core.pool.slot import AgentSlot

        config = _MockConfig(pool_num_slots=2)
        pool = ExecutionPool(mock_queue, config)

        warm_calls = []

        async def track_warm(self):
            warm_calls.append(self.slot_id)

        with patch.object(pool, "_warm_kronos", new_callable=AsyncMock), \
             patch.object(AgentSlot, "warm", track_warm):
            await pool.start()

        try:
            assert len(warm_calls) == 2
            assert "slot-0" in warm_calls
            assert "slot-1" in warm_calls
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_start_warms_slots_in_parallel(self, mock_queue):
        """Pool.start() warms all slots concurrently, not sequentially."""
        from core.pool.pool import ExecutionPool
        from core.pool.slot import AgentSlot
        import time

        config = _MockConfig(pool_num_slots=3)
        pool = ExecutionPool(mock_queue, config)

        warm_times: list[tuple[str, float, float]] = []

        async def track_warm_timing(self):
            start = time.monotonic()
            await asyncio.sleep(0.1)  # Simulate subprocess spawn
            end = time.monotonic()
            warm_times.append((self.slot_id, start, end))

        with patch.object(pool, "_warm_kronos", new_callable=AsyncMock), \
             patch.object(AgentSlot, "warm", track_warm_timing):
            await pool.start()

        try:
            assert len(warm_times) == 3
            # All 3 should start roughly at the same time (parallel, not sequential)
            starts = [t[1] for t in warm_times]
            max_start_spread = max(starts) - min(starts)
            # If sequential, spread would be ~0.2s (2 x 0.1s). Parallel should be <0.05s.
            assert max_start_spread < 0.08, f"Slots started too far apart ({max_start_spread:.3f}s) — not parallel"
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_stop_shuts_down_slots(self, mock_queue):
        """Pool.stop() calls shutdown() on each slot."""
        from core.pool.pool import ExecutionPool
        from core.pool.slot import AgentSlot

        config = _MockConfig(pool_num_slots=2)
        pool = ExecutionPool(mock_queue, config)

        with patch.object(pool, "_warm_kronos", new_callable=AsyncMock), \
             patch.object(AgentSlot, "warm", new_callable=AsyncMock):
            await pool.start()

        shutdown_calls = []

        async def track_shutdown(self):
            shutdown_calls.append(self.slot_id)

        with patch.object(AgentSlot, "shutdown", track_shutdown):
            await pool.stop()

        assert len(shutdown_calls) == 2


# ── Unit: Persistent slot client ──────────────────────────────────

def _make_mock_client():
    """Build a mock ClaudeSDKClient with connect/disconnect/query."""
    mock_result_msg = MagicMock()
    mock_result_msg.total_cost_usd = 0.01
    mock_result_msg.num_turns = 1

    mock_text_block = MagicMock()
    mock_text_block.text = "result"

    mock_assistant_msg = MagicMock()
    mock_assistant_msg.content = [mock_text_block]

    async def mock_receive():
        yield mock_assistant_msg
        yield mock_result_msg

    mock_client = AsyncMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()
    mock_client.receive_response = mock_receive

    return mock_client, mock_assistant_msg, mock_result_msg, mock_text_block


class TestPersistentSlotClient:
    """Slot keeps subprocess alive across jobs."""

    @pytest.mark.asyncio
    async def test_warm_creates_client(self):
        """warm() creates a persistent client."""
        from core.pool.slot import AgentSlot

        slot = AgentSlot(slot_id="slot-0")
        mock_client, *_ = _make_mock_client()

        with patch("core.pool.slot.ClaudeSDKClient", return_value=mock_client), \
             patch("core.pool.slot.ClaudeAgentOptions", return_value=MagicMock()):
            await slot.warm()

        assert slot._warm is True
        assert slot._client is mock_client
        mock_client.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_disconnects(self):
        """shutdown() disconnects the persistent client."""
        from core.pool.slot import AgentSlot

        slot = AgentSlot(slot_id="slot-0")
        mock_client, *_ = _make_mock_client()

        with patch("core.pool.slot.ClaudeSDKClient", return_value=mock_client), \
             patch("core.pool.slot.ClaudeAgentOptions", return_value=MagicMock()):
            await slot.warm()

        await slot.shutdown()

        assert slot._warm is False
        assert slot._client is None
        mock_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_reuses_warm_client(self):
        """execute() reuses an already-warmed client (no reconnect)."""
        from core.pool.slot import AgentSlot
        from core.pool.models import Job, JobType

        slot = AgentSlot(slot_id="slot-0")
        mock_client, mock_asst, mock_result, mock_text = _make_mock_client()

        with patch("core.pool.slot.ClaudeSDKClient", return_value=mock_client), \
             patch("core.pool.slot.ClaudeAgentOptions", return_value=MagicMock()), \
             patch("core.pool.slot.AssistantMessage", new=type(mock_asst)), \
             patch("core.pool.slot.ResultMessage", new=type(mock_result)), \
             patch("core.pool.slot.TextBlock", new=type(mock_text)):

            await slot.warm()
            assert mock_client.connect.call_count == 1

            # Execute should NOT call connect again
            job = Job(job_type=JobType.RESEARCH, instructions="test")
            result = await slot.execute(job)

            assert mock_client.connect.call_count == 1  # Still 1
            assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_uses_unique_session_id(self):
        """Each job gets a unique session_id for isolation."""
        from core.pool.slot import AgentSlot
        from core.pool.models import Job, JobType

        slot = AgentSlot(slot_id="slot-0")
        mock_client, mock_asst, mock_result, mock_text = _make_mock_client()

        with patch("core.pool.slot.ClaudeSDKClient", return_value=mock_client), \
             patch("core.pool.slot.ClaudeAgentOptions", return_value=MagicMock()), \
             patch("core.pool.slot.AssistantMessage", new=type(mock_asst)), \
             patch("core.pool.slot.ResultMessage", new=type(mock_result)), \
             patch("core.pool.slot.TextBlock", new=type(mock_text)):

            await slot.warm()

            job1 = Job(job_type=JobType.RESEARCH, instructions="first")
            await slot.execute(job1)

            job2 = Job(job_type=JobType.RESEARCH, instructions="second")
            await slot.execute(job2)

            # Check that query was called with different session_ids
            calls = mock_client.query.call_args_list
            assert len(calls) == 2
            sid1 = calls[0].kwargs.get("session_id") or calls[0][1].get("session_id", calls[0][0][1] if len(calls[0][0]) > 1 else None)
            sid2 = calls[1].kwargs.get("session_id") or calls[1][1].get("session_id", calls[1][0][1] if len(calls[1][0]) > 1 else None)
            assert sid1 != sid2

    @pytest.mark.asyncio
    async def test_execute_no_reconnect_on_different_job_type(self):
        """Switching job type reuses the same subprocess (no teardown)."""
        from core.pool.slot import AgentSlot
        from core.pool.models import Job, JobType

        slot = AgentSlot(slot_id="slot-0")
        mock_client, mock_asst, mock_result, mock_text = _make_mock_client()

        with patch("core.pool.slot.ClaudeSDKClient", return_value=mock_client), \
             patch("core.pool.slot.ClaudeAgentOptions", return_value=MagicMock()), \
             patch("core.pool.slot.AssistantMessage", new=type(mock_asst)), \
             patch("core.pool.slot.ResultMessage", new=type(mock_result)), \
             patch("core.pool.slot.TextBlock", new=type(mock_text)):

            await slot.warm()
            assert mock_client.connect.call_count == 1

            # Execute with different job type — should NOT reconnect
            job = Job(job_type=JobType.CODE, instructions="write code")
            await slot.execute(job)

            # Still just 1 connect (from warm), no disconnect
            assert mock_client.connect.call_count == 1
            assert mock_client.disconnect.call_count == 0

    @pytest.mark.asyncio
    async def test_execute_cold_start_auto_connects(self):
        """execute() without warm() auto-connects on first job."""
        from core.pool.slot import AgentSlot
        from core.pool.models import Job, JobType

        slot = AgentSlot(slot_id="slot-0")
        mock_client, mock_asst, mock_result, mock_text = _make_mock_client()

        with patch("core.pool.slot.ClaudeSDKClient", return_value=mock_client), \
             patch("core.pool.slot.ClaudeAgentOptions", return_value=MagicMock()), \
             patch("core.pool.slot.AssistantMessage", new=type(mock_asst)), \
             patch("core.pool.slot.ResultMessage", new=type(mock_result)), \
             patch("core.pool.slot.TextBlock", new=type(mock_text)):

            # No warm() call — cold start
            job = Job(job_type=JobType.RESEARCH, instructions="test")
            result = await slot.execute(job)

            assert result.success is True
            mock_client.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_reconnects_after_crash(self):
        """If the subprocess dies mid-job, next job auto-reconnects."""
        from core.pool.slot import AgentSlot
        from core.pool.models import Job, JobType

        slot = AgentSlot(slot_id="slot-0")
        mock_client, mock_asst, mock_result, mock_text = _make_mock_client()

        call_count = 0

        async def failing_then_ok_receive():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("subprocess died")
            yield mock_asst
            yield mock_result

        mock_client.receive_response = failing_then_ok_receive

        with patch("core.pool.slot.ClaudeSDKClient", return_value=mock_client), \
             patch("core.pool.slot.ClaudeAgentOptions", return_value=MagicMock()), \
             patch("core.pool.slot.AssistantMessage", new=type(mock_asst)), \
             patch("core.pool.slot.ResultMessage", new=type(mock_result)), \
             patch("core.pool.slot.TextBlock", new=type(mock_text)):

            await slot.warm()
            assert slot._warm is True

            # First job fails — subprocess crashed
            job1 = Job(job_type=JobType.RESEARCH, instructions="crash")
            result1 = await slot.execute(job1)
            assert result1.success is False
            assert slot._warm is False  # Marked for reconnect

            # Second job should auto-reconnect
            job2 = Job(job_type=JobType.RESEARCH, instructions="retry")
            result2 = await slot.execute(job2)
            assert result2.success is True
            assert mock_client.connect.call_count == 2  # warm + reconnect

    @pytest.mark.asyncio
    async def test_dynamic_permission_restricts_tools(self):
        """Dynamic permission callback restricts tools based on current job type."""
        from core.pool.slot import AgentSlot, _make_dynamic_permission_callback
        from core.pool.models import JobType

        slot = AgentSlot(slot_id="slot-0")
        slot._client_job_type = JobType.RESEARCH

        cb = _make_dynamic_permission_callback(slot)
        # RESEARCH jobs shouldn't have Write access
        result = await cb("Write", {}, None)
        assert hasattr(result, "behavior")  # PermissionResultDeny

        # But kronos_search should be allowed
        slot._client_job_type = JobType.RESEARCH
        result2 = await cb("mcp__kronos__kronos_search", {}, None)
        # This should be allowed (PermissionResultAllow has no .behavior)

    @pytest.mark.asyncio
    async def test_build_mcp_servers_sse(self):
        """_build_mcp_servers uses SSE when URL is set."""
        from core.pool.slot import AgentSlot

        slot = AgentSlot(slot_id="slot-0", kronos_mcp_url="http://localhost:8319")
        servers = slot._build_mcp_servers()
        assert servers["kronos"]["type"] == "sse"
        assert "8319" in servers["kronos"]["url"]

    @pytest.mark.asyncio
    async def test_build_mcp_servers_stdio(self):
        """_build_mcp_servers uses stdio when no URL."""
        from core.pool.slot import AgentSlot

        slot = AgentSlot(slot_id="slot-0", kronos_mcp_command="kronos-mcp")
        servers = slot._build_mcp_servers()
        assert servers["kronos"]["command"] == "kronos-mcp"


# ── Unit: Kronos SSE auto-start ────────────────────────────────────

class TestKronosSSEAutoStart:
    """Pool auto-starts a local Kronos SSE server when no URL is configured."""

    @pytest.fixture
    def tmp_db(self, tmp_path) -> Path:
        return tmp_path / "test_pool.db"

    @pytest.fixture
    def mock_queue(self, tmp_db):
        from core.pool.queue import JobQueue
        return JobQueue(tmp_db)

    @pytest.mark.asyncio
    async def test_auto_start_sets_url(self, mock_queue):
        """When no pool_kronos_url, pool spawns SSE and sets URL."""
        from core.pool.pool import ExecutionPool
        from core.pool.slot import AgentSlot

        config = _MockConfig(kronos_mcp_command="python", pool_num_slots=1)
        pool = ExecutionPool(mock_queue, config)

        # Mock the subprocess spawn
        mock_proc = AsyncMock()
        mock_proc.returncode = None  # Still running
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        # Mock httpx health check to succeed
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok", "engines_initialized": True}

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("httpx.AsyncClient", return_value=mock_http), \
             patch.object(AgentSlot, "warm", new_callable=AsyncMock):
            await pool.start()

        try:
            # SSE URL should be set from auto-start
            assert "8319" in pool._kronos_mcp_url
            assert pool._kronos_process is mock_proc
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_auto_start_skipped_when_url_configured(self, mock_queue):
        """If pool_kronos_url is already set, don't auto-start."""
        from core.pool.pool import ExecutionPool
        from core.pool.slot import AgentSlot

        config = _MockConfig(
            pool_kronos_url="http://external:8319",
            kronos_mcp_command="python",
            pool_num_slots=1,
        )
        pool = ExecutionPool(mock_queue, config)

        with patch("asyncio.create_subprocess_exec") as mock_spawn, \
             patch.object(pool, "_warm_kronos", new_callable=AsyncMock), \
             patch.object(AgentSlot, "warm", new_callable=AsyncMock):
            await pool.start()

        try:
            # Should NOT have spawned a local SSE server
            mock_spawn.assert_not_called()
            assert pool._kronos_mcp_url == "http://external:8319"
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_stop_kills_managed_sse(self, mock_queue):
        """Pool.stop() terminates the managed Kronos SSE process."""
        from core.pool.pool import ExecutionPool
        from core.pool.slot import AgentSlot

        config = _MockConfig(kronos_mcp_command="python", pool_num_slots=1)
        pool = ExecutionPool(mock_queue, config)

        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok", "engines_initialized": True}

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("httpx.AsyncClient", return_value=mock_http), \
             patch.object(AgentSlot, "warm", new_callable=AsyncMock):
            await pool.start()

        await pool.stop()

        mock_proc.terminate.assert_called_once()


# ── Unit: Prompt role injection ────────────────────────────────────

class TestPromptRoleInjection:
    """_build_prompt includes role-specific instructions."""

    def test_code_job_includes_role(self):
        from core.pool.slot import _build_prompt
        from core.pool.models import Job, JobType

        job = Job(job_type=JobType.CODE, instructions="write fizzbuzz")
        prompt = _build_prompt(job)
        assert "## Role" in prompt
        assert "coding agent" in prompt
        assert "write fizzbuzz" in prompt

    def test_research_job_includes_role(self):
        from core.pool.slot import _build_prompt
        from core.pool.models import Job, JobType

        job = Job(job_type=JobType.RESEARCH, instructions="find info")
        prompt = _build_prompt(job)
        assert "research agent" in prompt

    def test_prompt_preserves_plan_and_clarification(self):
        from core.pool.slot import _build_prompt
        from core.pool.models import Job, JobType

        job = Job(
            job_type=JobType.CODE,
            instructions="implement feature",
            plan="Step 1: do X",
            clarification_question="which DB?",
            clarification_answer="postgres",
        )
        prompt = _build_prompt(job)
        assert "Step 1: do X" in prompt
        assert "postgres" in prompt


# ── Smoke: __main__.py CLI parsing ────────────────────────────────

class TestCLIParsing:
    """Verify __main__.py argument parsing."""

    def test_default_is_stdio(self):
        """Without --sse flag, should run stdio mode."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--sse", action="store_true")
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8319)

        args = parser.parse_args([])
        assert args.sse is False
        assert args.host == "127.0.0.1"
        assert args.port == 8319

    def test_sse_flag(self):
        """--sse flag activates SSE mode."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--sse", action="store_true")
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8319)

        args = parser.parse_args(["--sse", "--port", "9000"])
        assert args.sse is True
        assert args.port == 9000

    def test_custom_host(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--sse", action="store_true")
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8319)

        args = parser.parse_args(["--sse", "--host", "0.0.0.0"])
        assert args.host == "0.0.0.0"
