"""Tests for Phase 5A: Ownership Model.

Tests the owner field across TaskEngine, Scanner, Pipeline, Engine, and Discord commands.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from core.daemon.models import PipelineItem, PipelineStatus
from core.daemon.pipeline import PipelineStore
from core.daemon.scanner import ProjectScanner, ScannedStory


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_project_fdo(vault_path: Path, proj_id: str, stories: list[dict]) -> None:
    """Write a minimal proj-* FDO file in the vault."""
    projects_dir = vault_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    fm = {
        "id": proj_id,
        "title": f"Project {proj_id}",
        "domain": "projects",
        "status": "developing",
        "confidence": 0.7,
        "tags": ["epic"],
        "stories": stories,
    }
    body = f"# {proj_id}\n\n## Summary\nTest project."
    fm_yaml = yaml.dump(fm, default_flow_style=False, sort_keys=False)
    fdo_path = projects_dir / f"{proj_id}.md"
    fdo_path.write_text(f"---\n{fm_yaml}---\n\n{body}", encoding="utf-8")


@pytest.fixture
def vault(tmp_path) -> Path:
    """Create a temp vault with ownership test stories."""
    vault_path = tmp_path / "vault"

    _make_project_fdo(vault_path, "proj-test", [
        {
            "id": "story-test-001",
            "title": "GRIM-owned code story",
            "status": "active",
            "priority": "high",
            "assignee": "code",
            "owner": "grim",
            "description": "Daemon handles this",
            "acceptance_criteria": ["Tests pass"],
            "estimate_days": 1.0,
            "tags": ["daemon"],
        },
        {
            "id": "story-test-002",
            "title": "Human-owned story",
            "status": "active",
            "priority": "medium",
            "assignee": "code",
            "owner": "human",
            "description": "Peter handles this",
            "acceptance_criteria": [],
            "estimate_days": 2.0,
            "tags": [],
        },
        {
            "id": "story-test-003",
            "title": "No explicit owner, has assignee",
            "status": "active",
            "priority": "low",
            "assignee": "research",
            "description": "Default owner logic",
            "acceptance_criteria": [],
            "estimate_days": 1.0,
            "tags": [],
        },
        {
            "id": "story-test-004",
            "title": "No owner, no assignee",
            "status": "active",
            "priority": "low",
            "description": "Should not be eligible (no assignee)",
            "acceptance_criteria": [],
            "estimate_days": 1.0,
            "tags": [],
        },
    ])

    return vault_path


@pytest.fixture
def pipeline_db(tmp_path) -> Path:
    return tmp_path / "test_daemon.db"


# ── ScannedStory Owner Tests ─────────────────────────────────────────────────

class TestScannedStoryOwner:
    """Test that ScannedStory correctly reads the owner field."""

    def test_owner_from_data(self):
        data = {"id": "s1", "status": "active", "assignee": "code", "owner": "grim"}
        story = ScannedStory(data, "proj-test")
        assert story.owner == "grim"

    def test_owner_human(self):
        data = {"id": "s2", "status": "active", "assignee": "code", "owner": "human"}
        story = ScannedStory(data, "proj-test")
        assert story.owner == "human"

    def test_owner_empty_default(self):
        data = {"id": "s3", "status": "active", "assignee": "code"}
        story = ScannedStory(data, "proj-test")
        assert story.owner == ""

    def test_eligibility_unaffected_by_owner(self):
        """Human-owned stories are still eligible — filtering happens at promote."""
        data = {"id": "s1", "status": "active", "assignee": "code", "owner": "human"}
        story = ScannedStory(data, "proj-test")
        assert story.is_eligible is True

    def test_no_assignee_not_eligible(self):
        data = {"id": "s1", "status": "active", "assignee": "", "owner": "grim"}
        story = ScannedStory(data, "proj-test")
        assert story.is_eligible is False


# ── Scanner Sync Tests ────────────────────────────────────────────────────────

class TestScannerOwnerSync:
    """Test that scanner passes owner through to pipeline."""

    def test_scan_reads_owner(self, vault):
        scanner = ProjectScanner(vault)
        stories = scanner.scan()
        by_id = {s.id: s for s in stories}

        assert by_id["story-test-001"].owner == "grim"
        assert by_id["story-test-002"].owner == "human"
        assert by_id["story-test-003"].owner == ""
        # story-test-004 has no assignee, should not be in eligible list
        assert "story-test-004" not in by_id

    @pytest.mark.asyncio
    async def test_sync_pipeline_sets_owner(self, vault, pipeline_db):
        store = PipelineStore(pipeline_db)
        await store.initialize()

        scanner = ProjectScanner(vault)
        result = await scanner.sync_pipeline(store)

        assert result["added"] == 3  # 001, 002, 003 (004 not eligible)

        item1 = await store.get_by_story("story-test-001")
        assert item1 is not None
        assert item1.owner == "grim"

        item2 = await store.get_by_story("story-test-002")
        assert item2 is not None
        assert item2.owner == "human"

        item3 = await store.get_by_story("story-test-003")
        assert item3 is not None
        assert item3.owner == ""


# ── PipelineStore Owner Tests ─────────────────────────────────────────────────

class TestPipelineStoreOwner:
    """Test pipeline store owner column operations."""

    @pytest.mark.asyncio
    async def test_add_with_owner(self, pipeline_db):
        store = PipelineStore(pipeline_db)
        await store.initialize()

        item = await store.add("s1", "proj-test", owner="human")
        assert item.owner == "human"

        fetched = await store.get(item.id)
        assert fetched is not None
        assert fetched.owner == "human"

    @pytest.mark.asyncio
    async def test_add_default_owner_empty(self, pipeline_db):
        store = PipelineStore(pipeline_db)
        await store.initialize()

        item = await store.add("s2", "proj-test")
        assert item.owner == ""

    @pytest.mark.asyncio
    async def test_update_owner(self, pipeline_db):
        store = PipelineStore(pipeline_db)
        await store.initialize()

        item = await store.add("s1", "proj-test", owner="grim")
        updated = await store.update_fields(item.id, owner="human")
        assert updated.owner == "human"

    @pytest.mark.asyncio
    async def test_list_with_owner_filter(self, pipeline_db):
        store = PipelineStore(pipeline_db)
        await store.initialize()

        await store.add("s1", "proj-test", owner="grim")
        await store.add("s2", "proj-test", owner="human")
        await store.add("s3", "proj-test", owner="grim")

        grim_items = await store.list_items(owner_filter="grim")
        assert len(grim_items) == 2

        human_items = await store.list_items(owner_filter="human")
        assert len(human_items) == 1

        all_items = await store.list_items()
        assert len(all_items) == 3

    @pytest.mark.asyncio
    async def test_advance_preserves_owner(self, pipeline_db):
        store = PipelineStore(pipeline_db)
        await store.initialize()

        item = await store.add("s1", "proj-test", owner="grim", assignee="code")
        advanced = await store.advance(item.id, PipelineStatus.READY)
        assert advanced.owner == "grim"


# ── Engine Ownership Tests ────────────────────────────────────────────────────

class TestEngineOwnership:
    """Test engine promote cycle respects ownership."""

    @pytest.mark.asyncio
    async def test_promote_skips_human_owned(self, pipeline_db):
        """Human-owned stories should NOT be promoted from BACKLOG to READY."""
        store = PipelineStore(pipeline_db)
        await store.initialize()

        # Add stories
        grim_item = await store.add("s1", "proj-test", owner="grim", assignee="code")
        human_item = await store.add("s2", "proj-test", owner="human", assignee="code")

        # Simulate engine promote cycle
        from core.daemon.engine import ManagementEngine

        config = _make_mock_config()
        engine = ManagementEngine(
            config=config,
            pool_queue=AsyncMock(),
            pool_events=MagicMock(),
            vault_path=Path("/fake/vault"),
        )
        # Replace store with our test store
        engine._store = store

        await engine._promote_cycle()

        # GRIM-owned should be READY
        grim_refreshed = await store.get(grim_item.id)
        assert grim_refreshed is not None
        assert grim_refreshed.status == PipelineStatus.READY

        # Human-owned should still be BACKLOG
        human_refreshed = await store.get(human_item.id)
        assert human_refreshed is not None
        assert human_refreshed.status == PipelineStatus.BACKLOG

    @pytest.mark.asyncio
    async def test_promote_empty_owner_with_assignee_defaults_grim(self, pipeline_db):
        """Empty owner with assignee should default to 'grim' and be promoted."""
        store = PipelineStore(pipeline_db)
        await store.initialize()

        item = await store.add("s1", "proj-test", owner="", assignee="code")

        from core.daemon.engine import ManagementEngine
        config = _make_mock_config()
        engine = ManagementEngine(
            config=config,
            pool_queue=AsyncMock(),
            pool_events=MagicMock(),
            vault_path=Path("/fake/vault"),
        )
        engine._store = store

        await engine._promote_cycle()

        refreshed = await store.get(item.id)
        assert refreshed is not None
        assert refreshed.status == PipelineStatus.READY

    def test_resolve_owner_grim(self):
        from core.daemon.engine import ManagementEngine

        config = _make_mock_config()
        engine = ManagementEngine(
            config=config,
            pool_queue=AsyncMock(),
            pool_events=MagicMock(),
            vault_path=Path("/fake/vault"),
        )

        item = PipelineItem(story_id="s1", project_id="p1", owner="grim", assignee="code")
        assert engine._resolve_owner(item) == "grim"

    def test_resolve_owner_human(self):
        from core.daemon.engine import ManagementEngine

        config = _make_mock_config()
        engine = ManagementEngine(
            config=config,
            pool_queue=AsyncMock(),
            pool_events=MagicMock(),
            vault_path=Path("/fake/vault"),
        )

        item = PipelineItem(story_id="s1", project_id="p1", owner="human", assignee="code")
        assert engine._resolve_owner(item) == "human"

    def test_resolve_owner_empty_with_assignee(self):
        from core.daemon.engine import ManagementEngine

        config = _make_mock_config()
        engine = ManagementEngine(
            config=config,
            pool_queue=AsyncMock(),
            pool_events=MagicMock(),
            vault_path=Path("/fake/vault"),
        )

        item = PipelineItem(story_id="s1", project_id="p1", owner="", assignee="code")
        assert engine._resolve_owner(item) == "grim"

    def test_resolve_owner_empty_no_assignee(self):
        from core.daemon.engine import ManagementEngine

        config = _make_mock_config()
        engine = ManagementEngine(
            config=config,
            pool_queue=AsyncMock(),
            pool_events=MagicMock(),
            vault_path=Path("/fake/vault"),
        )

        item = PipelineItem(story_id="s1", project_id="p1", owner="", assignee="")
        assert engine._resolve_owner(item) == "human"


# ── Nudge Cycle Tests ─────────────────────────────────────────────────────────

class TestNudgeCycle:
    """Test the nudge cycle for idle human stories."""

    @pytest.mark.asyncio
    async def test_nudge_emits_event_for_idle_human_story(self, pipeline_db):
        store = PipelineStore(pipeline_db)
        await store.initialize()

        # Add a human-owned story that's been idle
        item = await store.add("s1", "proj-test", owner="human", assignee="code")
        # Manually backdate updated_at to trigger nudge
        import aiosqlite
        old_time = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        async with aiosqlite.connect(str(pipeline_db)) as db:
            await db.execute(
                "UPDATE pipeline SET updated_at = ? WHERE id = ?",
                (old_time, item.id),
            )
            await db.commit()

        config = _make_mock_config(nudge_after_days=3)
        pool_events = MagicMock()
        pool_events.emit = AsyncMock()

        from core.daemon.engine import ManagementEngine
        engine = ManagementEngine(
            config=config,
            pool_queue=AsyncMock(),
            pool_events=pool_events,
            vault_path=Path("/fake/vault"),
        )
        engine._store = store
        engine._last_nudge_check = 0  # force nudge check

        await engine._nudge_cycle()

        pool_events.emit.assert_called_once()
        event = pool_events.emit.call_args[0][0]
        assert event.type.value == "daemon_nudge"
        assert event.data["story_id"] == "s1"

    @pytest.mark.asyncio
    async def test_nudge_skips_grim_owned(self, pipeline_db):
        store = PipelineStore(pipeline_db)
        await store.initialize()

        item = await store.add("s1", "proj-test", owner="grim", assignee="code")
        # Backdate
        import aiosqlite
        old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        async with aiosqlite.connect(str(pipeline_db)) as db:
            await db.execute(
                "UPDATE pipeline SET updated_at = ? WHERE id = ?",
                (old_time, item.id),
            )
            await db.commit()

        config = _make_mock_config(nudge_after_days=3)
        pool_events = MagicMock()
        pool_events.emit = AsyncMock()

        from core.daemon.engine import ManagementEngine
        engine = ManagementEngine(
            config=config,
            pool_queue=AsyncMock(),
            pool_events=pool_events,
            vault_path=Path("/fake/vault"),
        )
        engine._store = store
        engine._last_nudge_check = 0

        await engine._nudge_cycle()

        pool_events.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_nudge_rate_limited(self, pipeline_db):
        """Nudge should only run once per hour."""
        import time

        store = PipelineStore(pipeline_db)
        await store.initialize()

        config = _make_mock_config(nudge_after_days=3)
        pool_events = MagicMock()
        pool_events.emit = AsyncMock()

        from core.daemon.engine import ManagementEngine
        engine = ManagementEngine(
            config=config,
            pool_queue=AsyncMock(),
            pool_events=pool_events,
            vault_path=Path("/fake/vault"),
        )
        engine._store = store
        engine._last_nudge_check = time.monotonic()  # just ran

        await engine._nudge_cycle()

        # Should not have run (rate limited)
        pool_events.emit.assert_not_called()


# ── TaskEngine Owner Tests ────────────────────────────────────────────────────

class TestTaskEngineOwner:
    """Test TaskEngine CRUD operations for the owner field."""

    def test_create_with_owner(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        result = engine.create_story(
            "proj-test", "Test with owner", owner="human",
        )
        assert "error" not in result
        assert result["story"]["owner"] == "human"

    def test_create_default_empty_owner(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        result = engine.create_story("proj-test", "Test no owner")
        assert "error" not in result
        assert result["story"]["owner"] == ""

    def test_create_invalid_owner(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        result = engine.create_story(
            "proj-test", "Invalid owner", owner="bot",
        )
        assert "error" in result

    def test_update_owner(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        result = engine.update_item("story-test-001", {"owner": "human"})
        assert "error" not in result
        assert "owner" in result.get("fields_changed", [])

    def test_update_invalid_owner(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        result = engine.update_item("story-test-001", {"owner": "invalid"})
        assert "error" in result

    def test_list_with_owner_filter(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))

        grim_items = engine.list_items(owner="grim")
        assert all(i.get("owner") == "grim" for i in grim_items)

        human_items = engine.list_items(owner="human")
        assert all(i.get("owner") == "human" for i in human_items)

    def test_list_includes_owner_field(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        items = engine.list_items()
        for item in items:
            assert "owner" in item

    def test_get_items_batch_includes_owner(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        batch = engine.get_items_batch(["story-test-001", "story-test-002"])
        assert batch["story-test-001"]["owner"] == "grim"
        assert batch["story-test-002"]["owner"] == "human"


# ── Discord Command Tests ─────────────────────────────────────────────────────

class TestDaemonCommands:
    """Test daemon command pattern matching."""

    def test_status_pattern(self):
        from clients.daemon_commands import STATUS_PATTERN
        assert STATUS_PATTERN.search("status")
        assert STATUS_PATTERN.search("what is the status")
        assert not STATUS_PATTERN.search("foobar")

    def test_backlog_pattern_all(self):
        from clients.daemon_commands import BACKLOG_PATTERN
        m = BACKLOG_PATTERN.search("backlog")
        assert m is not None
        assert m.group(1) is None  # defaults to "all"

    def test_backlog_pattern_mine(self):
        from clients.daemon_commands import BACKLOG_PATTERN
        m = BACKLOG_PATTERN.search("backlog mine")
        assert m is not None
        assert m.group(1) == "mine"

    def test_backlog_pattern_grim(self):
        from clients.daemon_commands import BACKLOG_PATTERN
        m = BACKLOG_PATTERN.search("backlog grim")
        assert m is not None
        assert m.group(1) == "grim"

    def test_own_pattern(self):
        from clients.daemon_commands import OWN_PATTERN
        m = OWN_PATTERN.search("own story-mewtwo-010 human")
        assert m is not None
        assert m.group(1) == "story-mewtwo-010"
        assert m.group(2) == "human"

    def test_own_pattern_grim(self):
        from clients.daemon_commands import OWN_PATTERN
        m = OWN_PATTERN.search("own story-grim-001 grim")
        assert m is not None
        assert m.group(1) == "story-grim-001"
        assert m.group(2) == "grim"


# ── Daemon Event Formatting Tests ────────────────────────────────────────────

class TestDaemonEventFormatting:
    """Test daemon event formatting for Discord."""

    def test_format_nudge(self):
        from clients.daemon_commands import format_daemon_event
        event = {
            "type": "daemon_nudge",
            "job_id": "",
            "data": {"story_id": "story-test-002", "idle_days": 5},
        }
        result = format_daemon_event(event)
        assert result is not None
        assert "story-test-002" in result
        assert "5 days" in result

    def test_format_escalation(self):
        from clients.daemon_commands import format_daemon_event
        event = {
            "type": "daemon_escalation",
            "job_id": "job-abc123",
            "data": {"story_id": "s1", "question": "What DB?", "reason": "Low confidence"},
        }
        result = format_daemon_event(event)
        assert result is not None
        assert "Escalation" in result
        assert "What DB?" in result

    def test_format_auto_resolved(self):
        from clients.daemon_commands import format_daemon_event
        event = {
            "type": "daemon_auto_resolved",
            "job_id": "job-abc123",
            "data": {"story_id": "s1", "source": "mechanical", "confidence": 0.85},
        }
        result = format_daemon_event(event)
        assert result is not None
        assert "Auto-Resolved" in result

    def test_is_daemon_event(self):
        from clients.daemon_commands import is_daemon_event
        assert is_daemon_event({"type": "daemon_nudge"})
        assert is_daemon_event({"type": "daemon_escalation"})
        assert is_daemon_event({"event_type": "daemon_approved"})
        assert not is_daemon_event({"type": "job_complete"})
        assert not is_daemon_event({"type": "agent_output"})


# ── Event Type Tests ──────────────────────────────────────────────────────────

class TestDaemonNudgeEventType:
    """Test the new DAEMON_NUDGE event type."""

    def test_event_type_exists(self):
        from core.pool.events import PoolEventType
        assert hasattr(PoolEventType, "DAEMON_NUDGE")
        assert PoolEventType.DAEMON_NUDGE.value == "daemon_nudge"


# ── Config Tests ──────────────────────────────────────────────────────────────

class TestDaemonConfig:
    """Test new daemon config fields."""

    def test_default_values(self):
        from core.config import GrimConfig
        cfg = GrimConfig()
        assert cfg.daemon_default_owner == "grim"
        assert cfg.daemon_nudge_after_days == 3
        assert cfg.daemon_discord_channel_id == 0


# ── Mock config helper ────────────────────────────────────────────────────────

def _make_mock_config(
    poll_interval: float = 999,
    nudge_after_days: int = 3,
    **overrides,
) -> MagicMock:
    config = MagicMock()
    config.daemon_poll_interval = poll_interval
    config.daemon_max_concurrent_jobs = 1
    config.daemon_project_filter = []
    config.daemon_auto_dispatch = True
    config.daemon_db_path = Path("local/daemon.db")
    config.daemon_auto_resolve = False
    config.daemon_validate_output = False
    config.daemon_max_daemon_retries = 1
    config.daemon_resolve_model = "test"
    config.daemon_validate_model = "test"
    config.daemon_resolve_confidence_threshold = 0.7
    config.daemon_auto_pr = False
    config.daemon_github_repo = ""
    config.daemon_pr_poll_interval = 300
    config.daemon_default_owner = "grim"
    config.daemon_nudge_after_days = nudge_after_days
    config.daemon_discord_channel_id = 0
    config.vault_path = Path("/fake/vault")
    config.workspace_root = Path("/fake/workspace")
    for k, v in overrides.items():
        setattr(config, k, v)
    return config
