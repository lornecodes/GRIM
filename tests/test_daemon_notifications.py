"""Tests for Phase 5E: Proactive Notifications + Daily Summary.

Tests DaemonNotifier (daily summary, stuck detection, formatting),
engine notification cycle, Discord commands/formatting, config fields,
and event types.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.daemon.models import PipelineItem, PipelineStatus
from core.daemon.notifier import DaemonNotifier, DailySummary, StuckItem
from core.daemon.pipeline import PipelineStore
from core.pool.events import PoolEvent, PoolEventBus, PoolEventType


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_store(tmp_path: Path) -> PipelineStore:
    return PipelineStore(tmp_path / "test.db")


async def _init_store(tmp_path: Path) -> PipelineStore:
    store = _make_store(tmp_path)
    await store.initialize()
    return store


def _make_item(**overrides) -> PipelineItem:
    defaults = {
        "id": "item-1",
        "story_id": "story-test-001",
        "project_id": "proj-test",
        "status": PipelineStatus.BACKLOG,
        "priority": "medium",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return PipelineItem(**defaults)


# ── TestDaemonNotifier ────────────────────────────────────────────────────────

class TestDaemonNotifierStuck:
    """Test stuck detection."""

    @pytest.mark.asyncio
    async def test_no_stuck_items(self, tmp_path):
        store = await _init_store(tmp_path)
        notifier = DaemonNotifier(stuck_threshold_hours=2)
        stuck = await notifier.detect_stuck(store)
        assert stuck == []

    @pytest.mark.asyncio
    async def test_detects_stuck_dispatched(self, tmp_path):
        store = await _init_store(tmp_path)
        await store.add(
            story_id="story-stuck-001", project_id="proj-test",
            priority=1, assignee="code",
        )
        item = await store.get_by_story("story-stuck-001")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-1")
        # Backdate updated_at to simulate being stuck
        import aiosqlite
        async with aiosqlite.connect(str(store._db_path)) as db:
            await db.execute(
                "UPDATE pipeline SET updated_at = ? WHERE id = ?",
                ((datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(), item.id),
            )
            await db.commit()
        notifier = DaemonNotifier(stuck_threshold_hours=2)
        stuck = await notifier.detect_stuck(store)
        assert len(stuck) == 1
        assert stuck[0].story_id == "story-stuck-001"
        assert stuck[0].hours_dispatched >= 4.9

    @pytest.mark.asyncio
    async def test_not_stuck_if_recent(self, tmp_path):
        store = await _init_store(tmp_path)
        await store.add(
            story_id="story-fresh-001", project_id="proj-test",
            priority=2, assignee="code",
        )
        item = await store.get_by_story("story-fresh-001")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-2")
        notifier = DaemonNotifier(stuck_threshold_hours=2)
        stuck = await notifier.detect_stuck(store)
        assert stuck == []

    @pytest.mark.asyncio
    async def test_zero_threshold_disables(self, tmp_path):
        store = await _init_store(tmp_path)
        notifier = DaemonNotifier(stuck_threshold_hours=0)
        stuck = await notifier.detect_stuck(store)
        assert stuck == []


class TestDaemonNotifierDailySummary:
    """Test daily summary generation."""

    @pytest.mark.asyncio
    async def test_empty_pipeline(self, tmp_path):
        store = await _init_store(tmp_path)
        notifier = DaemonNotifier()
        summary = await notifier.daily_summary(store)
        assert summary.total_items == 0
        assert summary.completed_today == 0
        assert summary.stuck_items == []
        assert summary.human_idle == []

    @pytest.mark.asyncio
    async def test_counts_by_status(self, tmp_path):
        store = await _init_store(tmp_path)
        await store.add(story_id="s1", project_id="proj-test", priority=1, assignee="code")
        await store.add(story_id="s2", project_id="proj-test", priority=2, assignee="code")
        item2 = await store.get_by_story("s2")
        await store.advance(item2.id, PipelineStatus.READY)
        notifier = DaemonNotifier()
        summary = await notifier.daily_summary(store)
        assert summary.total_items == 2
        assert summary.counts_by_status.get("backlog", 0) == 1
        assert summary.counts_by_status.get("ready", 0) == 1

    @pytest.mark.asyncio
    async def test_human_idle_detection(self, tmp_path):
        store = await _init_store(tmp_path)
        await store.add(
            story_id="s-human-1", project_id="proj-test",
            priority=2, assignee="code", owner="human",
        )
        item = await store.get_by_story("s-human-1")
        # Backdate to make it idle
        import aiosqlite
        async with aiosqlite.connect(str(store._db_path)) as db:
            await db.execute(
                "UPDATE pipeline SET updated_at = ? WHERE id = ?",
                ((datetime.now(timezone.utc) - timedelta(days=5)).isoformat(), item.id),
            )
            await db.commit()
        notifier = DaemonNotifier()
        summary = await notifier.daily_summary(store)
        assert len(summary.human_idle) == 1
        assert summary.human_idle[0]["story_id"] == "s-human-1"
        assert summary.human_idle[0]["idle_days"] >= 4


class TestDaemonNotifierFormat:
    """Test daily summary formatting."""

    def test_empty_summary(self):
        notifier = DaemonNotifier()
        summary = DailySummary()
        text = notifier.format_daily_summary(summary)
        assert "Daily Daemon Summary" in text
        assert "empty" in text.lower()

    def test_with_counts(self):
        notifier = DaemonNotifier()
        summary = DailySummary(
            counts_by_status={"backlog": 3, "ready": 2, "dispatched": 1},
            completed_today=2,
            total_items=6,
        )
        text = notifier.format_daily_summary(summary)
        assert "backlog" in text.lower()
        assert "**2**" in text  # completed count
        assert "No stuck items" in text

    def test_with_stuck(self):
        notifier = DaemonNotifier()
        summary = DailySummary(
            counts_by_status={"dispatched": 1},
            stuck_items=[{"story_id": "story-stuck-1", "hours": 3.5}],
            total_items=1,
        )
        text = notifier.format_daily_summary(summary)
        assert "Stuck" in text
        assert "story-stuck-1" in text


# ── TestEventTypes ────────────────────────────────────────────────────────────

class TestNotificationEventTypes:
    """Test that 5E event types exist and work."""

    def test_stuck_warning_exists(self):
        assert PoolEventType.DAEMON_STUCK_WARNING.value == "daemon_stuck_warning"

    def test_daily_summary_exists(self):
        assert PoolEventType.DAEMON_DAILY_SUMMARY.value == "daemon_daily_summary"

    def test_stuck_warning_event_creation(self):
        event = PoolEvent(
            type=PoolEventType.DAEMON_STUCK_WARNING,
            job_id="job-1",
            data={"story_id": "s1", "hours_dispatched": 3.5},
        )
        d = event.to_dict()
        assert d["event_type"] == "daemon_stuck_warning"
        assert d["story_id"] == "s1"


# ── TestEngineNotificationCycle ───────────────────────────────────────────────

class TestEngineNotificationCycle:
    """Test engine notification cycle integration."""

    def _make_engine(self, tmp_path, **config_overrides):
        from core.daemon.engine import ManagementEngine
        from core.pool.queue import JobQueue

        vault_path = tmp_path / "vault"
        vault_path.mkdir(parents=True, exist_ok=True)
        db_path = tmp_path / "test.db"

        config = MagicMock()
        config.daemon_poll_interval = 999
        config.daemon_auto_resolve = False
        config.daemon_validate_output = False
        config.daemon_max_daemon_retries = 0
        config.daemon_auto_approve_threshold = 0
        config.daemon_auto_pr = False
        config.daemon_nudge_after_days = 3
        config.daemon_daily_summary_hour = 0  # always eligible
        config.daemon_stuck_threshold_hours = 2
        config.daemon_default_owner = "grim"
        config.daemon_db_path = db_path
        config.vault_path = vault_path
        config.workspace_root = tmp_path
        for k, v in config_overrides.items():
            setattr(config, k, v)

        bus = PoolEventBus()
        pool_queue = MagicMock(spec=JobQueue)

        engine = ManagementEngine(
            config=config,
            pool_queue=pool_queue,
            pool_events=bus,
            vault_path=vault_path,
        )
        return engine, bus, engine._store

    @pytest.mark.asyncio
    async def test_notification_cycle_runs(self, tmp_path):
        engine, bus, store = self._make_engine(tmp_path)
        await store.initialize()
        # Force first check by setting last check to 0
        engine._last_notification_check = 0.0
        events = []
        bus.subscribe(lambda e: events.append(e) or asyncio.sleep(0))
        await engine._notification_cycle()
        # No stuck items, so only daily summary event
        assert any(e.type == PoolEventType.DAEMON_DAILY_SUMMARY for e in events)

    @pytest.mark.asyncio
    async def test_notification_rate_limited(self, tmp_path):
        engine, bus, store = self._make_engine(tmp_path)
        await store.initialize()
        engine._last_notification_check = time.monotonic()  # just checked
        events = []
        bus.subscribe(lambda e: events.append(e) or asyncio.sleep(0))
        await engine._notification_cycle()
        assert events == []  # rate-limited, no events

    @pytest.mark.asyncio
    async def test_stuck_warning_emitted(self, tmp_path):
        engine, bus, store = self._make_engine(tmp_path)
        await store.initialize()
        engine._last_notification_check = 0.0

        # Add a stuck item
        await store.add(
            story_id="story-stuck-eng", project_id="proj-test",
            priority=1, assignee="code",
        )
        item = await store.get_by_story("story-stuck-eng")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-stuck")
        # Backdate
        import aiosqlite
        async with aiosqlite.connect(str(store._db_path)) as db:
            await db.execute(
                "UPDATE pipeline SET updated_at = ? WHERE id = ?",
                ((datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(), item.id),
            )
            await db.commit()

        events = []
        bus.subscribe(lambda e: events.append(e) or asyncio.sleep(0))
        await engine._notification_cycle()
        stuck_events = [e for e in events if e.type == PoolEventType.DAEMON_STUCK_WARNING]
        assert len(stuck_events) == 1
        assert stuck_events[0].data["story_id"] == "story-stuck-eng"

    @pytest.mark.asyncio
    async def test_daily_summary_once_per_day(self, tmp_path):
        engine, bus, store = self._make_engine(tmp_path)
        await store.initialize()
        engine._last_notification_check = 0.0

        events = []
        bus.subscribe(lambda e: events.append(e) or asyncio.sleep(0))

        await engine._notification_cycle()
        first_count = len([e for e in events if e.type == PoolEventType.DAEMON_DAILY_SUMMARY])
        assert first_count == 1

        # Second call — same day, should not emit again
        engine._last_notification_check = 0.0  # reset rate limit
        await engine._notification_cycle()
        second_count = len([e for e in events if e.type == PoolEventType.DAEMON_DAILY_SUMMARY])
        assert second_count == 1  # still 1

    @pytest.mark.asyncio
    async def test_on_demand_daily_summary(self, tmp_path):
        engine, bus, store = self._make_engine(tmp_path)
        await store.initialize()
        result = await engine.daily_summary()
        assert "formatted" in result
        assert "total_items" in result
        assert result["total_items"] == 0

    @pytest.mark.asyncio
    async def test_notifier_unavailable(self, tmp_path):
        engine, bus, store = self._make_engine(tmp_path)
        engine._notifier = None
        result = await engine.daily_summary()
        assert "error" in result


# ── TestDiscordCommands ───────────────────────────────────────────────────────

class TestDiscordDaemonCommands:
    """Test Discord command parsing and event formatting for 5E."""

    def test_daily_pattern_matches(self):
        from clients.daemon_commands import DAILY_PATTERN
        assert DAILY_PATTERN.search("daily")
        assert DAILY_PATTERN.search("@GRIM daily")
        assert not DAILY_PATTERN.search("everyday")

    @pytest.mark.asyncio
    async def test_try_handle_daily(self):
        from clients.daemon_commands import DaemonCommandHandler
        handler = DaemonCommandHandler("http://localhost:8080")
        with patch.object(handler, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"formatted": "**Summary**\nAll good."}
            result = await handler.try_handle("daily")
            assert result == "**Summary**\nAll good."
            mock_req.assert_called_once_with("get", "/api/daemon/daily")

    @pytest.mark.asyncio
    async def test_try_handle_daily_no_data(self):
        from clients.daemon_commands import DaemonCommandHandler
        handler = DaemonCommandHandler("http://localhost:8080")
        with patch.object(handler, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {}
            result = await handler.try_handle("daily")
            assert "No daily summary" in result

    @pytest.mark.asyncio
    async def test_try_handle_daily_daemon_down(self):
        from clients.daemon_commands import DaemonCommandHandler
        handler = DaemonCommandHandler("http://localhost:8080")
        with patch.object(handler, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = None
            result = await handler.try_handle("daily")
            assert "not running" in result.lower()

    def test_format_stuck_warning(self):
        from clients.daemon_commands import format_daemon_event
        result = format_daemon_event({
            "type": "daemon_stuck_warning",
            "data": {"story_id": "story-x", "hours_dispatched": 3},
        })
        assert "Stuck" in result
        assert "story-x" in result
        assert "3" in result

    def test_format_daily_summary(self):
        from clients.daemon_commands import format_daemon_event
        result = format_daemon_event({
            "type": "daemon_daily_summary",
            "data": {"formatted": "**Summary here**"},
        })
        assert result == "**Summary here**"

    def test_format_daily_summary_no_data(self):
        from clients.daemon_commands import format_daemon_event
        result = format_daemon_event({
            "type": "daemon_daily_summary",
            "data": {},
        })
        assert "no data" in result.lower()

    def test_daemon_event_types_include_5e(self):
        from clients.daemon_commands import DAEMON_EVENT_TYPES
        assert "daemon_stuck_warning" in DAEMON_EVENT_TYPES
        assert "daemon_daily_summary" in DAEMON_EVENT_TYPES


# ── TestConfig ────────────────────────────────────────────────────────────────

class TestNotificationConfig:
    """Test config fields for Phase 5E."""

    def test_default_values(self):
        from core.config import GrimConfig
        config = GrimConfig()
        assert config.daemon_daily_summary_hour == 14
        assert config.daemon_stuck_threshold_hours == 2
