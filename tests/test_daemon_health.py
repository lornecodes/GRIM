"""Unit tests for daemon config extension and health monitor."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from core.config import GrimConfig, load_config
from core.daemon.health import HealthMonitor
from core.daemon.models import PipelineStatus
from core.daemon.pipeline import PipelineStore


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    return tmp_path / "test_health.db"


@pytest.fixture
async def store(tmp_db) -> PipelineStore:
    s = PipelineStore(tmp_db)
    await s.initialize()
    return s


@pytest.fixture
def monitor() -> HealthMonitor:
    return HealthMonitor()


# ── Config tests ─────────────────────────────────────────────────


class TestDaemonConfig:
    """Test daemon config fields and YAML parsing."""

    def test_defaults(self):
        cfg = GrimConfig()
        assert cfg.daemon_enabled is False
        assert cfg.daemon_poll_interval == 30.0
        assert cfg.daemon_max_concurrent_jobs == 1
        assert cfg.daemon_project_filter == []
        assert cfg.daemon_auto_dispatch is True
        assert cfg.daemon_db_path == Path("local/daemon.db")

    def test_yaml_parsing(self, tmp_path):
        config_data = {
            "daemon": {
                "enabled": True,
                "poll_interval": 10.0,
                "max_concurrent_jobs": 3,
                "project_filter": ["proj-mewtwo", "proj-grim"],
                "auto_dispatch": False,
                "db_path": "local/custom_daemon.db",
            }
        }
        config_file = tmp_path / "config" / "grim.yaml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        cfg = load_config(config_path=config_file, grim_root=tmp_path)
        assert cfg.daemon_enabled is True
        assert cfg.daemon_poll_interval == 10.0
        assert cfg.daemon_max_concurrent_jobs == 3
        assert cfg.daemon_project_filter == ["proj-mewtwo", "proj-grim"]
        assert cfg.daemon_auto_dispatch is False

    def test_db_path_resolved(self, tmp_path):
        config_file = tmp_path / "config" / "grim.yaml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text("{}", encoding="utf-8")

        cfg = load_config(config_path=config_file, grim_root=tmp_path)
        assert cfg.daemon_db_path.is_absolute()

    def test_partial_yaml(self, tmp_path):
        """Only specified daemon keys are applied, rest stay default."""
        config_data = {"daemon": {"enabled": True}}
        config_file = tmp_path / "config" / "grim.yaml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        cfg = load_config(config_path=config_file, grim_root=tmp_path)
        assert cfg.daemon_enabled is True
        assert cfg.daemon_poll_interval == 30.0  # default


# ── Health monitor tests ─────────────────────────────────────────


class TestHealthMonitor:
    """Test HealthMonitor metrics and stuck detection."""

    def test_initial_state(self, monitor):
        assert monitor.scan_count == 0
        assert monitor.dispatch_count == 0
        assert monitor.last_scan_at is None
        assert monitor.last_dispatch_at is None

    def test_record_scan(self, monitor):
        monitor.record_scan()
        assert monitor.scan_count == 1
        assert monitor.last_scan_at is not None

    def test_record_dispatch(self, monitor):
        monitor.record_dispatch()
        assert monitor.dispatch_count == 1
        assert monitor.last_dispatch_at is not None

    def test_record_error(self, monitor):
        monitor.record_error("something broke")
        assert len(monitor.errors) == 1
        assert "something broke" in monitor.errors[0]

    def test_error_cap(self, monitor):
        for i in range(60):
            monitor.record_error(f"error {i}")
        assert len(monitor.errors) == 50

    def test_uptime(self, monitor):
        assert monitor.uptime_seconds >= 0

    @pytest.mark.asyncio
    async def test_status_dict(self, monitor, store):
        monitor.record_scan()
        monitor.record_dispatch()
        status = await monitor.status(store)
        assert status["running"] is True
        assert status["scan_count"] == 1
        assert status["dispatch_count"] == 1
        assert "pipeline" in status
        assert "recent_errors" in status

    @pytest.mark.asyncio
    async def test_stuck_items_none(self, monitor, store):
        stuck = await monitor.stuck_items(store)
        assert stuck == []

    @pytest.mark.asyncio
    async def test_stuck_items_detects_stale(self, monitor, store):
        item = await store.add("story-001", "proj-a")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-x")

        # With threshold_minutes=0, everything dispatched is "stuck"
        stuck = await monitor.stuck_items(store, threshold_minutes=0)
        assert len(stuck) == 1
        assert stuck[0]["story_id"] == "story-001"
        assert stuck[0]["job_id"] == "job-x"

    @pytest.mark.asyncio
    async def test_stuck_items_ignores_recent(self, monitor, store):
        item = await store.add("story-001", "proj-a")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-x")

        # With threshold=60 minutes, recently dispatched is NOT stuck
        stuck = await monitor.stuck_items(store, threshold_minutes=60)
        assert stuck == []
