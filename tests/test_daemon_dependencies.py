"""Tests for Phase 5B: Story Dependencies.

Tests depends_on across TaskEngine, Scanner, Pipeline, Engine, and Discord commands.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from core.daemon.models import PipelineItem, PipelineStatus
from core.daemon.pipeline import PipelineStore
from core.daemon.scanner import (
    ProjectScanner,
    ScannedStory,
    check_dependencies,
    detect_dependency_cycle,
)


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
    """Create a temp vault with dependency test stories."""
    vault_path = tmp_path / "vault"

    _make_project_fdo(vault_path, "proj-test", [
        {
            "id": "story-test-001",
            "title": "Research story (no deps)",
            "status": "active",
            "priority": "high",
            "assignee": "research",
            "owner": "grim",
            "description": "Research first",
            "acceptance_criteria": ["Research complete"],
            "estimate_days": 1.0,
            "tags": ["research"],
        },
        {
            "id": "story-test-002",
            "title": "Code story (depends on research)",
            "status": "active",
            "priority": "medium",
            "assignee": "code",
            "owner": "grim",
            "depends_on": ["story-test-001"],
            "description": "Implement after research",
            "acceptance_criteria": ["Tests pass"],
            "estimate_days": 2.0,
            "tags": ["code"],
        },
        {
            "id": "story-test-003",
            "title": "Audit story (depends on code)",
            "status": "active",
            "priority": "low",
            "assignee": "audit",
            "owner": "grim",
            "depends_on": ["story-test-002"],
            "description": "Audit after code",
            "acceptance_criteria": [],
            "estimate_days": 0.5,
            "tags": ["audit"],
        },
        {
            "id": "story-test-004",
            "title": "Independent story",
            "status": "active",
            "priority": "medium",
            "assignee": "code",
            "owner": "grim",
            "description": "No deps",
            "acceptance_criteria": [],
            "estimate_days": 1.0,
            "tags": [],
        },
    ])

    return vault_path


@pytest.fixture
def pipeline_db(tmp_path) -> Path:
    return tmp_path / "daemon.db"


# ── ScannedStory depends_on ──────────────────────────────────────────────────


class TestScannedStoryDependsOn:
    """ScannedStory reads depends_on from vault data."""

    def test_reads_depends_on(self):
        story = ScannedStory(
            {"id": "story-x", "status": "active", "assignee": "code",
             "depends_on": ["story-y", "story-z"]},
            "proj-test",
        )
        assert story.depends_on == ["story-y", "story-z"]

    def test_empty_depends_on_default(self):
        story = ScannedStory(
            {"id": "story-x", "status": "active", "assignee": "code"},
            "proj-test",
        )
        assert story.depends_on == []

    def test_none_depends_on_becomes_empty(self):
        story = ScannedStory(
            {"id": "story-x", "status": "active", "assignee": "code",
             "depends_on": None},
            "proj-test",
        )
        assert story.depends_on == []


# ── Scanner reads depends_on ─────────────────────────────────────────────────


class TestScannerDependsOn:
    """Scanner reads depends_on from vault and passes to pipeline."""

    def test_scan_reads_depends_on(self, vault):
        scanner = ProjectScanner(vault)
        stories = scanner.scan()
        by_id = {s.id: s for s in stories}
        assert by_id["story-test-002"].depends_on == ["story-test-001"]
        assert by_id["story-test-001"].depends_on == []

    @pytest.mark.asyncio
    async def test_sync_passes_depends_on_to_pipeline(self, vault, pipeline_db):
        store = PipelineStore(pipeline_db)
        await store.initialize()

        scanner = ProjectScanner(vault)
        await scanner.sync_pipeline(store)

        item = await store.get_by_story("story-test-002")
        assert item is not None
        assert item.depends_on == json.dumps(["story-test-001"])

    @pytest.mark.asyncio
    async def test_sync_updates_depends_on_change(self, vault, pipeline_db):
        """When depends_on changes in vault, scanner updates pipeline."""
        store = PipelineStore(pipeline_db)
        await store.initialize()

        # Initial sync
        scanner = ProjectScanner(vault)
        await scanner.sync_pipeline(store)

        # Modify vault — add a dependency
        _make_project_fdo(vault, "proj-test", [
            {"id": "story-test-001", "title": "Research story (no deps)",
             "status": "active", "assignee": "research", "owner": "grim",
             "estimate_days": 1.0},
            {"id": "story-test-002", "title": "Code story (now depends on 001 + 004)",
             "status": "active", "assignee": "code", "owner": "grim",
             "depends_on": ["story-test-001", "story-test-004"],
             "estimate_days": 2.0},
            {"id": "story-test-004", "title": "Independent story",
             "status": "active", "assignee": "code", "owner": "grim",
             "estimate_days": 1.0},
        ])

        result = await scanner.sync_pipeline(store)
        assert result["updated"] >= 1

        item = await store.get_by_story("story-test-002")
        assert item is not None
        assert json.loads(item.depends_on) == ["story-test-001", "story-test-004"]


# ── Pipeline Store depends_on ────────────────────────────────────────────────


class TestPipelineStoreDependsOn:
    """Pipeline store stores and retrieves depends_on/blocked_by."""

    @pytest.mark.asyncio
    async def test_add_with_depends_on(self, pipeline_db):
        store = PipelineStore(pipeline_db)
        await store.initialize()

        deps = json.dumps(["story-dep-001"])
        item = await store.add("story-x", "proj-test", depends_on=deps)
        assert item.depends_on == deps

        retrieved = await store.get(item.id)
        assert retrieved.depends_on == deps

    @pytest.mark.asyncio
    async def test_add_without_depends_on(self, pipeline_db):
        store = PipelineStore(pipeline_db)
        await store.initialize()

        item = await store.add("story-x", "proj-test")
        assert item.depends_on == ""

    @pytest.mark.asyncio
    async def test_update_blocked_by(self, pipeline_db):
        store = PipelineStore(pipeline_db)
        await store.initialize()

        item = await store.add("story-x", "proj-test",
                               depends_on=json.dumps(["story-dep-001"]))

        blocked = json.dumps(["story-dep-001"])
        updated = await store.update_fields(item.id, blocked_by=blocked)
        assert updated.blocked_by == blocked

    @pytest.mark.asyncio
    async def test_advance_with_depends_on_preserved(self, pipeline_db):
        store = PipelineStore(pipeline_db)
        await store.initialize()

        deps = json.dumps(["story-dep-001"])
        item = await store.add("story-x", "proj-test", depends_on=deps)
        advanced = await store.advance(item.id, PipelineStatus.READY)
        assert advanced.depends_on == deps

    @pytest.mark.asyncio
    async def test_clear_blocked_by(self, pipeline_db):
        store = PipelineStore(pipeline_db)
        await store.initialize()

        item = await store.add("story-x", "proj-test")
        await store.update_fields(item.id, blocked_by=json.dumps(["story-dep"]))
        cleared = await store.update_fields(item.id, blocked_by="")
        assert cleared.blocked_by == ""


# ── check_dependencies ───────────────────────────────────────────────────────


class TestCheckDependencies:
    """Unit tests for the check_dependencies function."""

    def test_no_deps_is_satisfied(self):
        satisfied, blocking = check_dependencies("", {})
        assert satisfied is True
        assert blocking == []

    def test_empty_json_is_satisfied(self):
        satisfied, blocking = check_dependencies("[]", {})
        assert satisfied is True
        assert blocking == []

    def test_all_resolved_is_satisfied(self):
        deps = json.dumps(["story-a", "story-b"])
        statuses = {"story-a": "resolved", "story-b": "closed"}
        satisfied, blocking = check_dependencies(deps, statuses)
        assert satisfied is True
        assert blocking == []

    def test_one_pending_blocks(self):
        deps = json.dumps(["story-a", "story-b"])
        statuses = {"story-a": "resolved", "story-b": "active"}
        satisfied, blocking = check_dependencies(deps, statuses)
        assert satisfied is False
        assert blocking == ["story-b"]

    def test_unknown_story_blocks(self):
        deps = json.dumps(["story-missing"])
        statuses = {}
        satisfied, blocking = check_dependencies(deps, statuses)
        assert satisfied is False
        assert blocking == ["story-missing"]

    def test_malformed_json_treated_as_no_deps(self):
        satisfied, blocking = check_dependencies("not-json", {})
        assert satisfied is True
        assert blocking == []

    def test_multiple_blockers(self):
        deps = json.dumps(["story-a", "story-b", "story-c"])
        statuses = {"story-a": "active", "story-b": "in_progress", "story-c": "resolved"}
        satisfied, blocking = check_dependencies(deps, statuses)
        assert satisfied is False
        assert set(blocking) == {"story-a", "story-b"}


# ── detect_dependency_cycle ──────────────────────────────────────────────────


class TestDetectDependencyCycle:
    """Cycle detection in story dependency graphs."""

    def test_no_cycles(self):
        stories = [
            ScannedStory({"id": "s1", "status": "active", "assignee": "code"}, "proj"),
            ScannedStory({"id": "s2", "status": "active", "assignee": "code",
                          "depends_on": ["s1"]}, "proj"),
        ]
        cycles = detect_dependency_cycle(stories)
        assert cycles == []

    def test_simple_cycle(self):
        stories = [
            ScannedStory({"id": "s1", "status": "active", "assignee": "code",
                          "depends_on": ["s2"]}, "proj"),
            ScannedStory({"id": "s2", "status": "active", "assignee": "code",
                          "depends_on": ["s1"]}, "proj"),
        ]
        cycles = detect_dependency_cycle(stories)
        assert len(cycles) >= 1

    def test_self_cycle(self):
        stories = [
            ScannedStory({"id": "s1", "status": "active", "assignee": "code",
                          "depends_on": ["s1"]}, "proj"),
        ]
        cycles = detect_dependency_cycle(stories)
        assert len(cycles) >= 1

    def test_no_stories_no_cycles(self):
        assert detect_dependency_cycle([]) == []


# ── Engine promote with dependencies ─────────────────────────────────────────


class TestEnginePromoteWithDeps:
    """Engine._promote_cycle respects dependency satisfaction."""

    @pytest.mark.asyncio
    async def test_promote_skips_unsatisfied_deps(self, pipeline_db):
        """Stories with unsatisfied deps stay in BACKLOG."""
        from core.daemon.engine import ManagementEngine

        store = PipelineStore(pipeline_db)
        await store.initialize()

        # Add a story with deps on an active story
        deps = json.dumps(["story-dep-001"])
        await store.add("story-test-002", "proj-test", assignee="code",
                         owner="grim", depends_on=deps)

        # Mock engine
        config = MagicMock()
        config.daemon_poll_interval = 999
        config.daemon_max_concurrent_jobs = 1
        config.daemon_auto_dispatch = True
        config.daemon_auto_resolve = False
        config.daemon_validate_output = False
        config.daemon_max_daemon_retries = 0
        config.daemon_default_owner = "grim"
        config.daemon_nudge_after_days = 3
        config.daemon_auto_pr = False

        pool_queue = AsyncMock()
        pool_events = MagicMock()
        pool_events.subscribe = MagicMock()
        pool_events.unsubscribe = MagicMock()
        pool_events.emit = AsyncMock()

        engine = ManagementEngine(config, pool_queue, pool_events,
                                   vault_path=pipeline_db.parent / "vault")
        engine._store = store

        # Mock task engine to return dep story as "active" (not satisfied)
        mock_task_engine = MagicMock()
        mock_task_engine.get_items_batch.return_value = {
            "story-dep-001": {"status": "active", "title": "Dep story"},
        }
        engine._task_engine = mock_task_engine

        await engine._promote_cycle()

        # Story should still be in BACKLOG
        item = await store.get_by_story("story-test-002")
        assert item.status == PipelineStatus.BACKLOG

    @pytest.mark.asyncio
    async def test_promote_succeeds_when_deps_satisfied(self, pipeline_db):
        """Stories with all deps resolved get promoted."""
        from core.daemon.engine import ManagementEngine

        store = PipelineStore(pipeline_db)
        await store.initialize()

        deps = json.dumps(["story-dep-001"])
        await store.add("story-test-002", "proj-test", assignee="code",
                         owner="grim", depends_on=deps)

        config = MagicMock()
        config.daemon_poll_interval = 999
        config.daemon_max_concurrent_jobs = 1
        config.daemon_auto_dispatch = True
        config.daemon_auto_resolve = False
        config.daemon_validate_output = False
        config.daemon_max_daemon_retries = 0
        config.daemon_default_owner = "grim"
        config.daemon_nudge_after_days = 3
        config.daemon_auto_pr = False

        pool_queue = AsyncMock()
        pool_events = MagicMock()
        pool_events.subscribe = MagicMock()
        pool_events.unsubscribe = MagicMock()
        pool_events.emit = AsyncMock()

        engine = ManagementEngine(config, pool_queue, pool_events,
                                   vault_path=pipeline_db.parent / "vault")
        engine._store = store

        # Dep is resolved
        mock_task_engine = MagicMock()
        mock_task_engine.get_items_batch.return_value = {
            "story-dep-001": {"status": "resolved", "title": "Dep story"},
        }
        engine._task_engine = mock_task_engine

        await engine._promote_cycle()

        item = await store.get_by_story("story-test-002")
        assert item.status == PipelineStatus.READY

    @pytest.mark.asyncio
    async def test_promote_no_deps_works_normally(self, pipeline_db):
        """Stories without deps get promoted as usual."""
        from core.daemon.engine import ManagementEngine

        store = PipelineStore(pipeline_db)
        await store.initialize()

        await store.add("story-test-001", "proj-test", assignee="code",
                         owner="grim")

        config = MagicMock()
        config.daemon_poll_interval = 999
        config.daemon_max_concurrent_jobs = 1
        config.daemon_auto_dispatch = True
        config.daemon_auto_resolve = False
        config.daemon_validate_output = False
        config.daemon_max_daemon_retries = 0
        config.daemon_default_owner = "grim"
        config.daemon_nudge_after_days = 3
        config.daemon_auto_pr = False

        pool_queue = AsyncMock()
        pool_events = MagicMock()
        pool_events.subscribe = MagicMock()
        pool_events.unsubscribe = MagicMock()
        pool_events.emit = AsyncMock()

        engine = ManagementEngine(config, pool_queue, pool_events,
                                   vault_path=pipeline_db.parent / "vault")
        engine._store = store

        await engine._promote_cycle()

        item = await store.get_by_story("story-test-001")
        assert item.status == PipelineStatus.READY

    @pytest.mark.asyncio
    async def test_blocked_by_updated_when_deps_pending(self, pipeline_db):
        """blocked_by field gets updated with blocking story IDs."""
        from core.daemon.engine import ManagementEngine

        store = PipelineStore(pipeline_db)
        await store.initialize()

        deps = json.dumps(["story-dep-001", "story-dep-002"])
        await store.add("story-x", "proj-test", assignee="code",
                         owner="grim", depends_on=deps)

        config = MagicMock()
        config.daemon_poll_interval = 999
        config.daemon_max_concurrent_jobs = 1
        config.daemon_auto_dispatch = True
        config.daemon_auto_resolve = False
        config.daemon_validate_output = False
        config.daemon_max_daemon_retries = 0
        config.daemon_default_owner = "grim"
        config.daemon_nudge_after_days = 3
        config.daemon_auto_pr = False

        pool_queue = AsyncMock()
        pool_events = MagicMock()
        pool_events.subscribe = MagicMock()
        pool_events.unsubscribe = MagicMock()
        pool_events.emit = AsyncMock()

        engine = ManagementEngine(config, pool_queue, pool_events,
                                   vault_path=pipeline_db.parent / "vault")
        engine._store = store

        mock_task_engine = MagicMock()
        mock_task_engine.get_items_batch.return_value = {
            "story-dep-001": {"status": "resolved"},
            "story-dep-002": {"status": "active"},
        }
        engine._task_engine = mock_task_engine

        await engine._promote_cycle()

        item = await store.get_by_story("story-x")
        assert item.status == PipelineStatus.BACKLOG
        assert json.loads(item.blocked_by) == ["story-dep-002"]

    @pytest.mark.asyncio
    async def test_dependency_satisfied_event_emitted(self, pipeline_db):
        """DAEMON_DEPENDENCY_SATISFIED event emitted when deps clear."""
        from core.daemon.engine import ManagementEngine

        store = PipelineStore(pipeline_db)
        await store.initialize()

        deps = json.dumps(["story-dep-001"])
        item = await store.add("story-x", "proj-test", assignee="code",
                                owner="grim", depends_on=deps)
        # Set blocked_by to simulate previously blocked state
        await store.update_fields(item.id, blocked_by=json.dumps(["story-dep-001"]))

        config = MagicMock()
        config.daemon_poll_interval = 999
        config.daemon_max_concurrent_jobs = 1
        config.daemon_auto_dispatch = True
        config.daemon_auto_resolve = False
        config.daemon_validate_output = False
        config.daemon_max_daemon_retries = 0
        config.daemon_default_owner = "grim"
        config.daemon_nudge_after_days = 3
        config.daemon_auto_pr = False

        pool_queue = AsyncMock()
        pool_events = MagicMock()
        pool_events.subscribe = MagicMock()
        pool_events.unsubscribe = MagicMock()
        pool_events.emit = AsyncMock()

        engine = ManagementEngine(config, pool_queue, pool_events,
                                   vault_path=pipeline_db.parent / "vault")
        engine._store = store

        # Now dep is resolved
        mock_task_engine = MagicMock()
        mock_task_engine.get_items_batch.return_value = {
            "story-dep-001": {"status": "resolved"},
        }
        engine._task_engine = mock_task_engine

        await engine._promote_cycle()

        # Should have emitted DEPENDENCY_SATISFIED
        from core.pool.events import PoolEventType
        emit_calls = pool_events.emit.call_args_list
        dep_events = [c for c in emit_calls
                      if c[0][0].type == PoolEventType.DAEMON_DEPENDENCY_SATISFIED]
        assert len(dep_events) == 1
        assert dep_events[0][0][0].data["story_id"] == "story-x"

    @pytest.mark.asyncio
    async def test_batch_get_story_statuses(self, pipeline_db):
        """_batch_get_story_statuses returns vault statuses."""
        from core.daemon.engine import ManagementEngine

        config = MagicMock()
        config.daemon_poll_interval = 999
        config.daemon_max_concurrent_jobs = 1
        config.daemon_auto_dispatch = True
        config.daemon_auto_resolve = False
        config.daemon_validate_output = False
        config.daemon_max_daemon_retries = 0
        config.daemon_default_owner = "grim"
        config.daemon_nudge_after_days = 3
        config.daemon_auto_pr = False

        engine = ManagementEngine(config, AsyncMock(), MagicMock(),
                                   vault_path=pipeline_db.parent / "vault")
        mock_te = MagicMock()
        mock_te.get_items_batch.return_value = {
            "s1": {"status": "resolved"},
            "s2": {"status": "active"},
        }
        engine._task_engine = mock_te

        result = engine._batch_get_story_statuses(["s1", "s2"])
        assert result == {"s1": "resolved", "s2": "active"}


# ── TaskEngine depends_on ────────────────────────────────────────────────────


class TestTaskEngineDependsOn:
    """TaskEngine creates/updates/lists stories with depends_on."""

    def test_create_story_with_depends_on(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        result = engine.create_story(
            proj_id="proj-test",
            title="Story with deps for testing purposes",
            depends_on=["story-test-001"],
        )
        assert "error" not in result
        story = result["story"]
        assert story["depends_on"] == ["story-test-001"]

    def test_create_story_rejects_invalid_depends_on(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        result = engine.create_story(
            proj_id="proj-test",
            title="Story with invalid dep ID provided",
            depends_on=["not-a-story"],
        )
        assert "error" in result

    def test_update_depends_on(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        result = engine.update_item("story-test-001", {
            "depends_on": ["story-test-004"],
        })
        assert "error" not in result
        assert "depends_on" in result.get("fields_changed", [])

    def test_update_rejects_invalid_depends_on(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        result = engine.update_item("story-test-001", {
            "depends_on": "not-a-list",
        })
        assert "error" in result

    def test_list_includes_depends_on(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        items = engine.list_items(project_id="proj-test")
        by_id = {i["id"]: i for i in items}
        assert by_id["story-test-002"]["depends_on"] == ["story-test-001"]
        assert by_id["story-test-001"]["depends_on"] == []

    def test_get_items_batch_includes_depends_on(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        batch = engine.get_items_batch(["story-test-002"])
        assert batch["story-test-002"]["depends_on"] == ["story-test-001"]

    def test_create_story_without_depends_on(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        result = engine.create_story(
            proj_id="proj-test",
            title="Story without any dependencies defined",
        )
        assert "error" not in result
        assert result["story"]["depends_on"] == []

    def test_update_depends_on_rejects_bad_id_in_list(self, vault):
        from kronos_mcp.tasks import TaskEngine
        engine = TaskEngine(str(vault))
        result = engine.update_item("story-test-001", {
            "depends_on": ["story-valid", "invalid-id"],
        })
        assert "error" in result


# ── Discord deps command ─────────────────────────────────────────────────────


class TestDaemonDepsCommand:
    """Discord command parsing for deps command."""

    def test_deps_pattern_matches(self):
        from clients.daemon_commands import DEPS_PATTERN
        m = DEPS_PATTERN.search("deps story-test-001")
        assert m is not None
        assert m.group(1) == "story-test-001"

    def test_deps_pattern_case_insensitive(self):
        from clients.daemon_commands import DEPS_PATTERN
        m = DEPS_PATTERN.search("DEPS story-mewtwo-003")
        assert m is not None
        assert m.group(1) == "story-mewtwo-003"


# ── Daemon event formatting ──────────────────────────────────────────────────


class TestDaemonDependencyEventFormatting:
    """Event formatting for dependency events."""

    def test_dependency_satisfied_event(self):
        from clients.daemon_commands import format_daemon_event
        result = format_daemon_event({
            "event_type": "daemon_dependency_satisfied",
            "data": {"story_id": "story-test-002"},
        })
        assert result is not None
        assert "Dependencies Met" in result
        assert "story-test-002" in result

    def test_dependency_blocked_event(self):
        from clients.daemon_commands import format_daemon_event
        result = format_daemon_event({
            "event_type": "daemon_dependency_blocked",
            "data": {
                "story_id": "story-test-002",
                "blocking": ["story-test-001"],
            },
        })
        assert result is not None
        assert "Blocked" in result
        assert "story-test-001" in result

    def test_new_event_types_in_daemon_events(self):
        from clients.daemon_commands import is_daemon_event
        assert is_daemon_event({"event_type": "daemon_dependency_satisfied"})
        assert is_daemon_event({"event_type": "daemon_dependency_blocked"})


# ── Event types exist ────────────────────────────────────────────────────────


class TestDependencyEventTypes:
    """New event types exist in PoolEventType."""

    def test_dependency_satisfied_type(self):
        from core.pool.events import PoolEventType
        assert PoolEventType.DAEMON_DEPENDENCY_SATISFIED.value == "daemon_dependency_satisfied"

    def test_dependency_blocked_type(self):
        from core.pool.events import PoolEventType
        assert PoolEventType.DAEMON_DEPENDENCY_BLOCKED.value == "daemon_dependency_blocked"
