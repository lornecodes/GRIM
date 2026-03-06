"""Tests for pool server endpoints (Phase 9).

Tests the metrics, workspace files, and WebSocket subscription endpoints
added in Phase 9 for Mission Control and Agent Studio.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


# ── Fixtures ──


@pytest.fixture
def mock_pool():
    """Create a mock ExecutionPool with queue and workspace manager."""
    pool = MagicMock()
    pool.status = {
        "running": True,
        "slots": [
            {"slot_id": "slot-0", "busy": True, "current_job_id": "job-001"},
            {"slot_id": "slot-1", "busy": False, "current_job_id": None},
        ],
        "active_jobs": 1,
        "active_workspaces": 1,
        "resource_locks": {},
    }
    pool.events = MagicMock()
    pool.events.subscribe = MagicMock()

    # Queue mock
    pool.queue = AsyncMock()
    pool.queue.list_jobs = AsyncMock(return_value=[
        {
            "id": "job-001",
            "status": "complete",
            "started_at": "2026-03-05T10:00:00+00:00",
            "completed_at": "2026-03-05T10:05:00+00:00",
            "cost_usd": 0.10,
        },
        {
            "id": "job-002",
            "status": "complete",
            "started_at": "2026-03-05T11:00:00+00:00",
            "completed_at": "2026-03-05T11:03:00+00:00",
            "cost_usd": 0.08,
        },
        {
            "id": "job-003",
            "status": "failed",
        },
        {
            "id": "job-004",
            "status": "running",
        },
        {
            "id": "job-005",
            "status": "queued",
        },
    ])

    # Workspace manager mock
    ws_mgr = MagicMock()
    pool.workspace_manager = ws_mgr
    pool.codebase = None

    return pool


@pytest.fixture
def app_with_pool(mock_pool):
    """Import app and inject mock pool."""
    import server.app as app_module
    original = app_module._execution_pool
    app_module._execution_pool = mock_pool
    yield app_module.app, mock_pool
    app_module._execution_pool = original


@pytest.fixture
def app_without_pool():
    """Import app with pool disabled."""
    import server.app as app_module
    original = app_module._execution_pool
    app_module._execution_pool = None
    yield app_module.app
    app_module._execution_pool = original


# ── Metrics endpoint tests ──


class TestPoolMetrics:
    """Test GET /api/pool/metrics."""

    @pytest.mark.asyncio
    async def test_metrics_returns_aggregated_data(self, app_with_pool):
        app, _ = app_with_pool
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/pool/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["completed_count"] == 2
        assert data["failed_count"] == 1
        assert data["running_count"] == 1
        assert data["queued_count"] == 1
        assert data["total_cost_usd"] == 0.18
        assert data["avg_duration_ms"] > 0
        assert "throughput_per_hour" in data

    @pytest.mark.asyncio
    async def test_metrics_returns_503_when_pool_disabled(self, app_without_pool):
        async with AsyncClient(transport=ASGITransport(app=app_without_pool), base_url="http://test") as client:
            resp = await client.get("/api/pool/metrics")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_metrics_custom_hours(self, app_with_pool):
        app, _ = app_with_pool
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/pool/metrics?hours=48")
        assert resp.status_code == 200
        data = resp.json()
        assert data["period_hours"] == 48


# ── Workspace files endpoint tests ──


class TestPoolWorkspaceFiles:
    """Test GET /api/pool/workspaces/{workspace_id}/files."""

    @pytest.mark.asyncio
    async def test_files_returns_503_when_pool_disabled(self, app_without_pool):
        async with AsyncClient(transport=ASGITransport(app=app_without_pool), base_url="http://test") as client:
            resp = await client.get("/api/pool/workspaces/ws-001/files")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_files_returns_404_for_unknown_workspace(self, app_with_pool):
        app, mock_pool = app_with_pool
        mock_pool.workspace_manager.get.return_value = None
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/pool/workspaces/ws-missing/files")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_files_returns_file_list(self, app_with_pool):
        app, mock_pool = app_with_pool
        mock_ws = MagicMock()
        mock_pool.workspace_manager.get.return_value = mock_ws
        mock_pool.workspace_manager.list_changed_files = AsyncMock(return_value=["src/main.py", "tests/test.py"])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/pool/workspaces/ws-001/files")
        assert resp.status_code == 200
        data = resp.json()
        assert data["workspace_id"] == "ws-001"
        assert len(data["files"]) == 2


# ── Workspace file read endpoint tests ──


class TestPoolWorkspaceFileRead:
    """Test GET /api/pool/workspaces/{workspace_id}/file."""

    @pytest.mark.asyncio
    async def test_file_read_requires_path(self, app_with_pool):
        app, mock_pool = app_with_pool
        mock_ws = MagicMock()
        mock_pool.workspace_manager.get.return_value = mock_ws
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/pool/workspaces/ws-001/file")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_file_read_blocks_path_traversal(self, app_with_pool):
        app, mock_pool = app_with_pool
        mock_ws = MagicMock()
        mock_pool.workspace_manager.get.return_value = mock_ws
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/pool/workspaces/ws-001/file?path=../../etc/passwd")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_file_read_returns_content(self, app_with_pool, tmp_path):
        app, mock_pool = app_with_pool
        # Create a real file
        test_file = tmp_path / "hello.py"
        test_file.write_text("print('hello')\n")
        mock_ws = MagicMock()
        mock_ws.worktree_path = tmp_path
        mock_pool.workspace_manager.get.return_value = mock_ws
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/pool/workspaces/ws-001/file?path=hello.py")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "print('hello')\n"
        assert data["path"] == "hello.py"

    @pytest.mark.asyncio
    async def test_file_read_404_for_missing_file(self, app_with_pool, tmp_path):
        app, mock_pool = app_with_pool
        mock_ws = MagicMock()
        mock_ws.worktree_path = tmp_path
        mock_pool.workspace_manager.get.return_value = mock_ws
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/pool/workspaces/ws-001/file?path=nonexistent.py")
        assert resp.status_code == 404


# ── Pool status endpoint tests ──


class TestPoolStatus:
    """Test GET /api/pool/status."""

    @pytest.mark.asyncio
    async def test_status_returns_503_when_disabled(self, app_without_pool):
        async with AsyncClient(transport=ASGITransport(app=app_without_pool), base_url="http://test") as client:
            resp = await client.get("/api/pool/status")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_status_returns_pool_state(self, app_with_pool):
        app, _ = app_with_pool
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/pool/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert len(data["slots"]) == 2


# ── WebSocket subscription model tests ──


class TestPoolWsSubscription:
    """Test the /ws-pool subscription-based event filtering."""

    def test_streaming_event_types_exist(self):
        """Verify the streaming event types are defined."""
        from core.pool.events import PoolEventType, STREAMING_EVENT_TYPES, is_streaming_event, PoolEvent
        assert PoolEventType.AGENT_OUTPUT in STREAMING_EVENT_TYPES
        assert PoolEventType.AGENT_TOOL_RESULT in STREAMING_EVENT_TYPES

        # Streaming events are correctly classified
        stream_event = PoolEvent(type=PoolEventType.AGENT_OUTPUT, job_id="j-1", data={"type": "text", "text": "hi"})
        assert is_streaming_event(stream_event) is True

        # Lifecycle events are not streaming
        lifecycle_event = PoolEvent(type=PoolEventType.JOB_COMPLETE, job_id="j-1")
        assert is_streaming_event(lifecycle_event) is False

    @pytest.mark.asyncio
    async def test_block_type_in_emitted_events(self):
        """Verify _on_agent_message adds block_type to event data."""
        from core.pool.pool import ExecutionPool

        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_config = MagicMock()
        mock_config.workspace_root = None

        pool = ExecutionPool(mock_queue, mock_config)
        emitted = []

        async def capture(e):
            emitted.append(e)

        pool.events.subscribe(capture)

        # Test text block
        await pool._on_agent_message("j-1", {
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
        })
        assert emitted[-1].data["block_type"] == "text"

        # Test tool_use block
        await pool._on_agent_message("j-1", {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "Bash", "input": {"cmd": "ls"}}],
        })
        assert emitted[-1].data["block_type"] == "tool_use"

        # Test result block
        await pool._on_agent_message("j-1", {
            "role": "result",
            "num_turns": 5,
        })
        assert emitted[-1].data["block_type"] == "result"
