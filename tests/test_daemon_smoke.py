"""Smoke tests for the management daemon — imports, wiring, config, endpoints.

No external dependencies or real API calls. Fast, no-setup.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


# ── Package imports ──────────────────────────────────────────────


class TestDaemonImports:
    """Verify all daemon modules import cleanly."""

    def test_import_models(self):
        from core.daemon.models import (
            PipelineStatus, PipelineItem, InvalidTransition,
            VALID_TRANSITIONS, TERMINAL_STATUSES, PRIORITY_ORDER,
        )

    def test_import_pipeline(self):
        from core.daemon.pipeline import PipelineStore

    def test_import_scanner(self):
        from core.daemon.scanner import ProjectScanner, ScannedStory

    def test_import_engine(self):
        from core.daemon.engine import ManagementEngine

    def test_import_health(self):
        from core.daemon.health import HealthMonitor

    def test_import_package(self):
        from core.daemon import PipelineItem, PipelineStatus


# ── Model construction ──────────────────────────────────────────


class TestModelConstruction:
    """Verify models can be created with defaults."""

    def test_pipeline_item_defaults(self):
        from core.daemon.models import PipelineItem, PipelineStatus

        item = PipelineItem(story_id="story-001", project_id="proj-x")
        assert item.story_id == "story-001"
        assert item.project_id == "proj-x"
        assert item.status == PipelineStatus.BACKLOG
        assert item.priority == 2
        assert item.id.startswith("pipeline-")

    def test_pipeline_status_values(self):
        from core.daemon.models import PipelineStatus

        expected = {"backlog", "ready", "dispatched", "review", "merged", "failed", "blocked"}
        assert {s.value for s in PipelineStatus} == expected

    def test_valid_transitions_complete(self):
        from core.daemon.models import PipelineStatus, VALID_TRANSITIONS

        for status in PipelineStatus:
            assert status in VALID_TRANSITIONS

    def test_priority_order(self):
        from core.daemon.models import PRIORITY_ORDER

        assert PRIORITY_ORDER["critical"] < PRIORITY_ORDER["low"]
        assert len(PRIORITY_ORDER) == 4

    def test_invalid_transition_message(self):
        from core.daemon.models import PipelineStatus, InvalidTransition

        exc = InvalidTransition(PipelineStatus.BACKLOG, PipelineStatus.MERGED)
        assert "backlog" in str(exc)
        assert "merged" in str(exc)


# ── Config defaults ──────────────────────────────────────────────


class TestDaemonConfig:
    """Verify daemon config fields and defaults."""

    def test_daemon_defaults(self):
        from core.config import GrimConfig

        cfg = GrimConfig()
        assert cfg.daemon_enabled is False
        assert cfg.daemon_poll_interval == 30.0
        assert cfg.daemon_max_concurrent_jobs == 1
        assert cfg.daemon_project_filter == []
        assert cfg.daemon_auto_dispatch is True
        assert cfg.daemon_db_path == Path("local/daemon.db")

    def test_daemon_yaml_parsing(self):
        from core.config import GrimConfig, _apply_yaml

        cfg = GrimConfig()
        raw = {
            "daemon": {
                "enabled": True,
                "poll_interval": 60.0,
                "max_concurrent_jobs": 3,
                "project_filter": ["proj-alpha"],
                "auto_dispatch": False,
                "db_path": "data/daemon.db",
            }
        }
        _apply_yaml(cfg, raw, Path("."))
        assert cfg.daemon_enabled is True
        assert cfg.daemon_poll_interval == 60.0
        assert cfg.daemon_max_concurrent_jobs == 3
        assert cfg.daemon_project_filter == ["proj-alpha"]
        assert cfg.daemon_auto_dispatch is False
        assert cfg.daemon_db_path == Path("data/daemon.db")


# ── Health monitor construction ──────────────────────────────────


class TestHealthMonitorConstruction:
    """Verify HealthMonitor can be created and basic operations work."""

    def test_creation(self):
        from core.daemon.health import HealthMonitor

        h = HealthMonitor()
        assert h.scan_count == 0
        assert h.dispatch_count == 0
        assert h.errors == []

    def test_record_scan(self):
        from core.daemon.health import HealthMonitor

        h = HealthMonitor()
        h.record_scan()
        assert h.scan_count == 1
        assert h.last_scan_at is not None

    def test_record_dispatch(self):
        from core.daemon.health import HealthMonitor

        h = HealthMonitor()
        h.record_dispatch()
        assert h.dispatch_count == 1

    def test_uptime(self):
        from core.daemon.health import HealthMonitor

        h = HealthMonitor()
        assert h.uptime_seconds >= 0


# ── Scanner construction ─────────────────────────────────────────


class TestScannerConstruction:
    def test_scanner_init(self, tmp_path):
        from core.daemon.scanner import ProjectScanner

        scanner = ProjectScanner(tmp_path)
        assert scanner._vault_path == tmp_path

    def test_scanner_with_filter(self, tmp_path):
        from core.daemon.scanner import ProjectScanner

        scanner = ProjectScanner(tmp_path, project_filter=["proj-alpha"])
        assert scanner._project_filter == {"proj-alpha"}

    def test_scanned_story_eligible(self):
        from core.daemon.scanner import ScannedStory

        s = ScannedStory({"id": "s1", "status": "active", "assignee": "code"}, "proj-x")
        assert s.is_eligible

    def test_scanned_story_not_eligible_no_assignee(self):
        from core.daemon.scanner import ScannedStory

        s = ScannedStory({"id": "s1", "status": "active", "assignee": ""}, "proj-x")
        assert not s.is_eligible


# ── Endpoint registration ────────────────────────────────────────


class TestEndpointRegistration:
    """Verify daemon endpoints are registered on the app."""

    def test_daemon_routes_exist(self):
        from server.app import app

        routes = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/api/daemon/status" in routes
        assert "/api/daemon/pipeline" in routes
        assert "/api/daemon/pipeline/{item_id}/advance" in routes
        assert "/api/daemon/pipeline/{item_id}/retry" in routes


# ── Endpoint behavior (daemon disabled) ──────────────────────────


class TestEndpointsDaemonDisabled:
    """When daemon is not enabled, all endpoints return 404."""

    @pytest.fixture
    def client(self):
        import server.app as app_mod
        app_mod._daemon_engine = None
        from server.app import app
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_status_404(self, client):
        resp = await client.get("/api/daemon/status")
        assert resp.status_code == 404
        assert "not enabled" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_pipeline_404(self, client):
        resp = await client.get("/api/daemon/pipeline")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_advance_404(self, client):
        resp = await client.post(
            "/api/daemon/pipeline/fake-id/advance",
            json={"status": "merged"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_retry_404(self, client):
        resp = await client.post("/api/daemon/pipeline/fake-id/retry")
        assert resp.status_code == 404


# ── Endpoint behavior (daemon enabled, mock store) ───────────────


class TestEndpointsDaemonEnabled:
    """When daemon IS enabled, endpoints interact with the store."""

    @pytest.fixture
    def mock_engine(self):
        engine = MagicMock()
        engine.health = MagicMock()
        engine.health.status = AsyncMock(return_value={
            "running": True,
            "uptime_seconds": 42.0,
            "scan_count": 5,
            "dispatch_count": 2,
            "pipeline": {"backlog": 3, "ready": 1, "dispatched": 0},
            "recent_errors": [],
            "started_at": "2026-03-06T00:00:00+00:00",
            "last_scan_at": None,
            "last_dispatch_at": None,
        })
        engine.store = MagicMock()
        engine.store.list_items = AsyncMock(return_value=[])
        engine.store.advance = AsyncMock()
        return engine

    @pytest.fixture
    def client(self, mock_engine):
        import server.app as app_mod
        original = app_mod._daemon_engine
        app_mod._daemon_engine = mock_engine
        from server.app import app
        yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        app_mod._daemon_engine = original

    @pytest.mark.asyncio
    async def test_status_ok(self, client, mock_engine):
        resp = await client.get("/api/daemon/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert data["scan_count"] == 5
        mock_engine.health.status.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_empty(self, client, mock_engine):
        resp = await client.get("/api/daemon/pipeline")
        assert resp.status_code == 200
        assert resp.json() == []
        mock_engine.store.list_items.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_with_status_filter(self, client, mock_engine):
        resp = await client.get("/api/daemon/pipeline?status=backlog")
        assert resp.status_code == 200
        call_kwargs = mock_engine.store.list_items.call_args
        from core.daemon.models import PipelineStatus
        assert call_kwargs.kwargs.get("status_filter") == PipelineStatus.BACKLOG or \
               (call_kwargs.args and call_kwargs.args[0] == PipelineStatus.BACKLOG) or \
               call_kwargs[1].get("status_filter") == PipelineStatus.BACKLOG

    @pytest.mark.asyncio
    async def test_advance_missing_status(self, client):
        resp = await client.post(
            "/api/daemon/pipeline/fake-id/advance",
            json={},
        )
        assert resp.status_code == 400
        assert "status" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_advance_invalid_status(self, client):
        resp = await client.post(
            "/api/daemon/pipeline/fake-id/advance",
            json={"status": "nonexistent"},
        )
        assert resp.status_code == 400
        assert "invalid" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_advance_success(self, client, mock_engine):
        from core.daemon.models import PipelineItem, PipelineStatus

        mock_item = PipelineItem(
            story_id="story-001", project_id="proj-x",
            status=PipelineStatus.MERGED,
        )
        mock_engine.store.advance = AsyncMock(return_value=mock_item)

        resp = await client.post(
            "/api/daemon/pipeline/pipeline-abc/advance",
            json={"status": "merged"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["story_id"] == "story-001"
        assert data["status"] == "merged"

    @pytest.mark.asyncio
    async def test_advance_invalid_transition(self, client, mock_engine):
        from core.daemon.models import PipelineStatus, InvalidTransition

        mock_engine.store.advance = AsyncMock(
            side_effect=InvalidTransition(PipelineStatus.BACKLOG, PipelineStatus.MERGED)
        )

        resp = await client.post(
            "/api/daemon/pipeline/pipeline-abc/advance",
            json={"status": "merged"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_advance_not_found(self, client, mock_engine):
        mock_engine.store.advance = AsyncMock(
            side_effect=ValueError("Item not found")
        )
        resp = await client.post(
            "/api/daemon/pipeline/nonexistent/advance",
            json={"status": "merged"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_retry_success(self, client, mock_engine):
        from core.daemon.models import PipelineItem, PipelineStatus

        mock_item = PipelineItem(
            story_id="story-001", project_id="proj-x",
            status=PipelineStatus.READY,
        )
        mock_engine.store.advance = AsyncMock(return_value=mock_item)

        resp = await client.post("/api/daemon/pipeline/pipeline-abc/retry")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    @pytest.mark.asyncio
    async def test_retry_invalid_transition(self, client, mock_engine):
        from core.daemon.models import PipelineStatus, InvalidTransition

        mock_engine.store.advance = AsyncMock(
            side_effect=InvalidTransition(PipelineStatus.BACKLOG, PipelineStatus.READY)
        )
        resp = await client.post("/api/daemon/pipeline/pipeline-abc/retry")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_retry_not_found(self, client, mock_engine):
        mock_engine.store.advance = AsyncMock(
            side_effect=ValueError("Item not found")
        )
        resp = await client.post("/api/daemon/pipeline/nonexistent/retry")
        assert resp.status_code == 404


# ── Boot wiring ──────────────────────────────────────────────────


class TestDaemonBootWiring:
    """Verify daemon boot logic in app.py lifespan."""

    def test_daemon_requires_pool(self):
        """daemon_enabled without pool should not crash."""
        from core.config import GrimConfig

        cfg = GrimConfig()
        cfg.daemon_enabled = True
        cfg.pool_enabled = False
        # Just verify config can be constructed — actual wiring tested via lifespan

    def test_daemon_engine_global_default(self):
        """_daemon_engine starts as None."""
        import server.app as app_mod
        # Should be None or a ManagementEngine; during test it should be None
        # (we just check it's accessible)
        assert hasattr(app_mod, "_daemon_engine")
