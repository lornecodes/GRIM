"""Tests for pool event bus — push notifications for job lifecycle.

Tests the PoolEventBus pub/sub system and verifies that ExecutionPool
emits events at the correct lifecycle points.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.pool.events import PoolEvent, PoolEventBus, PoolEventType
from core.pool.models import Job, JobType, JobPriority
from core.pool.queue import JobQueue


# ── PoolEvent tests ──────────────────────────────────────────────

class TestPoolEvent:
    def test_event_creation(self):
        event = PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id="job-abc12345",
            data={"cost_usd": 0.05},
        )
        assert event.type == PoolEventType.JOB_COMPLETE
        assert event.job_id == "job-abc12345"
        assert event.timestamp is not None

    def test_event_to_dict(self):
        event = PoolEvent(
            type=PoolEventType.JOB_SUBMITTED,
            job_id="job-abc12345",
            data={"job_type": "code", "priority": "normal"},
        )
        d = event.to_dict()
        assert d["type"] == "job_submitted"
        assert d["job_id"] == "job-abc12345"
        assert d["job_type"] == "code"
        assert "timestamp" in d

    def test_event_types(self):
        assert len(PoolEventType) == 6
        assert PoolEventType.JOB_SUBMITTED.value == "job_submitted"
        assert PoolEventType.JOB_STARTED.value == "job_started"
        assert PoolEventType.JOB_COMPLETE.value == "job_complete"
        assert PoolEventType.JOB_FAILED.value == "job_failed"
        assert PoolEventType.JOB_BLOCKED.value == "job_blocked"
        assert PoolEventType.JOB_CANCELLED.value == "job_cancelled"


# ── PoolEventBus tests ──────────────────────────────────────────

class TestPoolEventBus:
    @pytest.mark.asyncio
    async def test_subscribe_and_emit(self):
        bus = PoolEventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe(handler)
        assert bus.subscriber_count == 1

        event = PoolEvent(type=PoolEventType.JOB_COMPLETE, job_id="j1")
        await bus.emit(event)

        assert len(received) == 1
        assert received[0].job_id == "j1"

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        bus = PoolEventBus()
        a_received = []
        b_received = []

        async def handler_a(event):
            a_received.append(event)

        async def handler_b(event):
            b_received.append(event)

        bus.subscribe(handler_a)
        bus.subscribe(handler_b)
        assert bus.subscriber_count == 2

        await bus.emit(PoolEvent(type=PoolEventType.JOB_STARTED, job_id="j1"))

        assert len(a_received) == 1
        assert len(b_received) == 1

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        bus = PoolEventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe(handler)
        bus.unsubscribe(handler)
        assert bus.subscriber_count == 0

        await bus.emit(PoolEvent(type=PoolEventType.JOB_COMPLETE, job_id="j1"))
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_subscriber_error_doesnt_block_others(self):
        bus = PoolEventBus()
        received = []

        async def bad_handler(event):
            raise RuntimeError("boom")

        async def good_handler(event):
            received.append(event)

        bus.subscribe(bad_handler)
        bus.subscribe(good_handler)

        await bus.emit(PoolEvent(type=PoolEventType.JOB_COMPLETE, job_id="j1"))
        assert len(received) == 1  # good_handler still ran

    @pytest.mark.asyncio
    async def test_emit_no_subscribers(self):
        bus = PoolEventBus()
        # Should not raise
        await bus.emit(PoolEvent(type=PoolEventType.JOB_COMPLETE, job_id="j1"))


# ── ExecutionPool event emission tests ───────────────────────────

class TestPoolEventEmission:
    """Verify ExecutionPool emits events at the right lifecycle points."""

    @pytest.fixture
    def mock_config(self, tmp_path):
        @dataclass
        class MockConfig:
            pool_enabled: bool = True
            pool_num_slots: int = 1
            pool_poll_interval: float = 0.1
            pool_db_path: Path = tmp_path / "pool.db"
            pool_max_turns_per_job: int = 5
            pool_job_timeout_secs: int = 10
            kronos_mcp_command: str = ""
            vault_path: Optional[Path] = None
            skills_path: Optional[Path] = None
            workspace_root: Optional[Path] = None

        return MockConfig()

    @pytest.mark.asyncio
    async def test_submit_emits_event(self, tmp_path, mock_config):
        from core.pool.pool import ExecutionPool

        q = JobQueue(tmp_path / "pool.db")
        pool = ExecutionPool(q, mock_config)
        await pool.start()

        received = []
        pool.events.subscribe(lambda e: asyncio.coroutine(lambda: received.append(e))())

        # Use a proper async handler
        events = []

        async def capture(event):
            events.append(event)

        pool.events.subscribe(capture)

        job = Job(job_type=JobType.CODE, instructions="test")
        await pool.submit(job)

        assert len(events) == 1
        assert events[0].type == PoolEventType.JOB_SUBMITTED
        assert events[0].data["job_type"] == "code"

        await pool.stop()

    @pytest.mark.asyncio
    async def test_pool_has_event_bus(self, tmp_path, mock_config):
        from core.pool.pool import ExecutionPool

        q = JobQueue(tmp_path / "pool.db")
        pool = ExecutionPool(q, mock_config)

        assert hasattr(pool, "events")
        assert isinstance(pool.events, PoolEventBus)
        assert pool.events.subscriber_count == 0


# ── Server pool endpoints tests ──────────────────────────────────

class TestPoolEndpoints:
    """Test new pool API endpoints."""

    def test_clarify_endpoint_no_pool(self):
        from fastapi.testclient import TestClient
        import server.app as app_module

        with TestClient(app_module.app, raise_server_exceptions=False) as tc:
            original = app_module._execution_pool
            app_module._execution_pool = None
            try:
                resp = tc.post(
                    "/api/pool/jobs/job-test1234/clarify",
                    json={"answer": "main"},
                )
                # Debug: print(resp.status_code, resp.json())
                assert resp.status_code in (503, 422), f"Got {resp.status_code}: {resp.text}"
            finally:
                app_module._execution_pool = original

    def test_events_info_no_pool(self):
        from fastapi.testclient import TestClient
        import server.app as app_module

        with TestClient(app_module.app, raise_server_exceptions=False) as tc:
            original = app_module._execution_pool
            app_module._execution_pool = None
            try:
                resp = tc.get("/api/pool/events/info")
                assert resp.status_code == 503
            finally:
                app_module._execution_pool = original

    def test_events_info_with_pool(self):
        from fastapi.testclient import TestClient
        import server.app as app_module

        mock_pool = MagicMock()
        mock_pool.events = PoolEventBus()

        with TestClient(app_module.app, raise_server_exceptions=False) as tc:
            original = app_module._execution_pool
            app_module._execution_pool = mock_pool
            try:
                resp = tc.get("/api/pool/events/info")
                assert resp.status_code == 200
                data = resp.json()
                assert "subscribers" in data
                assert "active_ws" in data
            finally:
                app_module._execution_pool = original
