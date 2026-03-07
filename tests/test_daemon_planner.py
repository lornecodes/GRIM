"""Tests for Phase 5C: Goal Decomposition.

Tests PlanParser, PlanExecutor, GoalTracker, engine PLAN handling,
Discord commands, and REST endpoints for goal management.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from core.daemon.models import PipelineItem, PipelineStatus
from core.daemon.pipeline import PipelineStore
from core.daemon.planner import (
    ExecutedPlan,
    GoalTracker,
    ParsedPlan,
    PlanExecutor,
    PlanParser,
    PlannedStory,
)
from core.pool.events import PoolEvent, PoolEventBus, PoolEventType


# ── Helpers ───────────────────────────────────────────────────────────────────

VALID_PLAN_YAML = """```yaml
stories:
  - title: Research authentication methods
    assignee: research
    description: Survey OAuth2 vs JWT
    acceptance_criteria:
      - Comparison table produced
    priority: high
    estimate_days: 0.5
  - title: Implement token auth
    assignee: code
    description: Build JWT auth layer
    acceptance_criteria:
      - Tests pass
      - Token refresh works
    depends_on_index: [0]
    priority: medium
    estimate_days: 2.0
  - title: Audit security
    assignee: audit
    description: Review auth implementation
    depends_on_index: [1]
    priority: medium
    estimate_days: 0.5
```"""

RAW_PLAN_YAML = """stories:
  - title: Add caching
    assignee: code
    description: Redis caching layer
    priority: high
  - title: Validate cache
    assignee: audit
    depends_on_index: [0]
"""

INVALID_YAML = """```yaml
this is: [not valid: yaml: [
```"""

MISSING_STORIES_YAML = """```yaml
tasks:
  - name: something
```"""


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


# ══════════════════════════════════════════════════════════════════════════════
# PlanParser Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPlanParser:
    """Tests for PlanParser.parse() — YAML extraction and validation."""

    def test_parse_valid_fenced_yaml(self):
        parser = PlanParser()
        result = parser.parse(VALID_PLAN_YAML)
        assert result.valid
        assert len(result.stories) == 3
        assert result.errors == []

    def test_parse_raw_yaml(self):
        parser = PlanParser()
        result = parser.parse(RAW_PLAN_YAML)
        assert result.valid
        assert len(result.stories) == 2
        assert result.stories[0].title == "Add caching"

    def test_parse_story_fields(self):
        parser = PlanParser()
        result = parser.parse(VALID_PLAN_YAML)
        s = result.stories[0]
        assert s.title == "Research authentication methods"
        assert s.assignee == "research"
        assert s.description == "Survey OAuth2 vs JWT"
        assert s.acceptance_criteria == ["Comparison table produced"]
        assert s.priority == "high"
        assert s.estimate_days == 0.5
        assert s.depends_on_index == []

    def test_parse_depends_on_index(self):
        parser = PlanParser()
        result = parser.parse(VALID_PLAN_YAML)
        assert result.stories[1].depends_on_index == [0]
        assert result.stories[2].depends_on_index == [1]

    def test_parse_invalid_yaml(self):
        parser = PlanParser()
        result = parser.parse(INVALID_YAML)
        assert not result.valid
        assert any("Invalid YAML" in e for e in result.errors)

    def test_parse_missing_stories_key(self):
        parser = PlanParser()
        result = parser.parse(MISSING_STORIES_YAML)
        assert not result.valid
        assert any("No stories found" in e for e in result.errors)

    def test_parse_no_yaml_content(self):
        parser = PlanParser()
        result = parser.parse("Just some random text without YAML.")
        assert not result.valid
        assert any("No YAML content" in e for e in result.errors)

    def test_parse_missing_title(self):
        parser = PlanParser()
        text = """```yaml
stories:
  - assignee: code
    description: no title
```"""
        result = parser.parse(text)
        assert not result.valid
        assert any("missing or invalid title" in e for e in result.errors)

    def test_parse_invalid_assignee(self):
        parser = PlanParser()
        text = """```yaml
stories:
  - title: Do something
    assignee: invalid_agent
```"""
        result = parser.parse(text)
        assert not result.valid
        assert any("invalid assignee" in e for e in result.errors)

    def test_parse_invalid_depends_on_index_self_ref(self):
        parser = PlanParser()
        text = """```yaml
stories:
  - title: Self reference
    assignee: code
    depends_on_index: [0]
```"""
        result = parser.parse(text)
        # Self-reference is filtered out
        assert result.stories[0].depends_on_index == []
        # Error logged but story still valid
        assert any("invalid depends_on_index 0" in e for e in result.errors)

    def test_parse_invalid_depends_on_index_out_of_range(self):
        parser = PlanParser()
        text = """```yaml
stories:
  - title: Out of range
    assignee: code
    depends_on_index: [5]
```"""
        result = parser.parse(text)
        assert result.stories[0].depends_on_index == []

    def test_parse_defaults(self):
        """Missing optional fields get defaults."""
        parser = PlanParser()
        text = """```yaml
stories:
  - title: Minimal story
    assignee: code
```"""
        result = parser.parse(text)
        assert result.valid
        s = result.stories[0]
        assert s.priority == "medium"
        assert s.estimate_days == 1.0
        assert s.description == ""
        assert s.acceptance_criteria == []


# ══════════════════════════════════════════════════════════════════════════════
# PlanExecutor Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPlanExecutor:
    """Tests for PlanExecutor — creating draft stories in vault."""

    def _mock_task_engine(self, created_ids: list[str] | None = None):
        engine = MagicMock()
        ids = created_ids or []
        call_count = [0]

        def create_side_effect(**kwargs):
            if call_count[0] < len(ids):
                sid = ids[call_count[0]]
                call_count[0] += 1
                return {"created": sid}
            call_count[0] += 1
            return {"created": f"story-auto-{call_count[0]:03d}"}

        engine.create_story.side_effect = create_side_effect
        engine.update_item.return_value = {}
        return engine

    def test_execute_creates_stories(self):
        engine = self._mock_task_engine(["story-a", "story-b", "story-c"])
        executor = PlanExecutor(engine)
        parser = PlanParser()
        plan = parser.parse(VALID_PLAN_YAML)

        result = executor.execute(plan, "proj-test", "story-goal-001")
        assert len(result.created_ids) == 3
        assert result.goal_story_id == "story-goal-001"
        assert engine.create_story.call_count == 3

    def test_execute_resolves_depends_on_index(self):
        engine = self._mock_task_engine(["story-a", "story-b", "story-c"])
        executor = PlanExecutor(engine)
        parser = PlanParser()
        plan = parser.parse(VALID_PLAN_YAML)

        executor.execute(plan, "proj-test", "story-goal-001")

        # Second story depends on first (index 0 → story-a)
        call_args = engine.create_story.call_args_list
        assert call_args[1].kwargs.get("depends_on") == ["story-a"]
        # Third depends on second (index 1 → story-b)
        assert call_args[2].kwargs.get("depends_on") == ["story-b"]

    def test_execute_sets_correct_fields(self):
        engine = self._mock_task_engine(["story-x"])
        executor = PlanExecutor(engine)
        plan = ParsedPlan(stories=[
            PlannedStory(title="Test", assignee="code", priority="high"),
        ])

        executor.execute(plan, "proj-test", "story-goal-001")
        kwargs = engine.create_story.call_args_list[0].kwargs
        assert kwargs["status"] == "draft"
        assert kwargs["owner"] == "grim"
        assert kwargs["created_by"] == "agent:plan"
        assert "goal-child" in kwargs["tags"]
        assert "goal:story-goal-001" in kwargs["tags"]

    def test_execute_handles_creation_failure(self):
        engine = MagicMock()
        engine.create_story.side_effect = [
            {"created": "story-a"},
            {"error": "Something failed"},
            {"created": "story-c"},
        ]
        executor = PlanExecutor(engine)
        plan = ParsedPlan(stories=[
            PlannedStory(title="A", assignee="code"),
            PlannedStory(title="B", assignee="code"),
            PlannedStory(title="C", assignee="code"),
        ])

        result = executor.execute(plan, "proj-test", "story-goal-001")
        # Only successful ones in created_ids
        assert result.created_ids == ["story-a", "story-c"]

    def test_execute_dependency_map(self):
        engine = self._mock_task_engine(["story-a", "story-b"])
        executor = PlanExecutor(engine)
        plan = ParsedPlan(stories=[
            PlannedStory(title="A", assignee="research"),
            PlannedStory(title="B", assignee="code", depends_on_index=[0]),
        ])

        result = executor.execute(plan, "proj-test", "story-goal-001")
        assert result.dependency_map == {"story-b": ["story-a"]}

    def test_activate_plan(self):
        engine = MagicMock()
        engine.update_item.return_value = {}
        executor = PlanExecutor(engine)
        executed = ExecutedPlan(
            goal_story_id="story-goal-001",
            created_ids=["story-a", "story-b", "story-c"],
            dependency_map={},
        )

        activated = executor.activate_plan(executed)
        assert activated == 3
        # Each should be set to active
        for call in engine.update_item.call_args_list:
            assert call.args[1] == {"status": "active"}

    def test_activate_plan_partial_failure(self):
        engine = MagicMock()
        engine.update_item.side_effect = [
            {},
            {"error": "not found"},
            {},
        ]
        executor = PlanExecutor(engine)
        executed = ExecutedPlan(
            goal_story_id="story-goal-001",
            created_ids=["story-a", "story-b", "story-c"],
            dependency_map={},
        )

        activated = executor.activate_plan(executed)
        assert activated == 2

    def test_reject_plan(self):
        engine = MagicMock()
        engine.update_item.return_value = {}
        executor = PlanExecutor(engine)

        closed = executor.reject_plan(["story-a", "story-b"])
        assert closed == 2
        for call in engine.update_item.call_args_list:
            assert call.args[1] == {"status": "closed"}

    def test_reject_plan_partial_failure(self):
        engine = MagicMock()
        engine.update_item.side_effect = [{}, {"error": "nope"}]
        executor = PlanExecutor(engine)

        closed = executor.reject_plan(["story-a", "story-b"])
        assert closed == 1


# ══════════════════════════════════════════════════════════════════════════════
# GoalTracker Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestGoalTracker:
    """Tests for GoalTracker — monitors child story completion."""

    def _mock_task_engine(self, items: list[dict]):
        engine = MagicMock()
        engine.list_items.return_value = items
        engine.update_item.return_value = {}
        return engine

    def test_check_no_children(self):
        engine = self._mock_task_engine([])
        tracker = GoalTracker(engine)
        complete, stats = tracker.check_goal_complete("story-goal-001")
        assert not complete
        assert stats["total"] == 0

    def test_check_all_resolved(self):
        items = [
            {"id": "story-a", "tags": ["goal:story-goal-001", "goal-child"], "status": "resolved"},
            {"id": "story-b", "tags": ["goal:story-goal-001", "goal-child"], "status": "closed"},
        ]
        engine = self._mock_task_engine(items)
        tracker = GoalTracker(engine)
        complete, stats = tracker.check_goal_complete("story-goal-001")
        assert complete
        assert stats["total"] == 2
        assert stats["done"] == 2
        assert stats["pending"] == 0

    def test_check_some_pending(self):
        items = [
            {"id": "story-a", "tags": ["goal:story-goal-001", "goal-child"], "status": "resolved"},
            {"id": "story-b", "tags": ["goal:story-goal-001", "goal-child"], "status": "active"},
        ]
        engine = self._mock_task_engine(items)
        tracker = GoalTracker(engine)
        complete, stats = tracker.check_goal_complete("story-goal-001")
        assert not complete
        assert stats["pending"] == 1

    def test_check_only_matching_goal(self):
        """Only counts children of the specific goal."""
        items = [
            {"id": "story-a", "tags": ["goal:story-goal-001"], "status": "resolved"},
            {"id": "story-b", "tags": ["goal:story-goal-002"], "status": "active"},
        ]
        engine = self._mock_task_engine(items)
        tracker = GoalTracker(engine)
        complete, stats = tracker.check_goal_complete("story-goal-001")
        assert complete
        assert stats["total"] == 1

    def test_auto_resolve_goal(self):
        items = [
            {"id": "story-a", "tags": ["goal:story-goal-001"], "status": "resolved"},
        ]
        engine = self._mock_task_engine(items)
        tracker = GoalTracker(engine)
        result = tracker.auto_resolve_goal("story-goal-001")
        assert result is True
        engine.update_item.assert_called_once_with("story-goal-001", {"status": "resolved"})

    def test_auto_resolve_not_complete(self):
        items = [
            {"id": "story-a", "tags": ["goal:story-goal-001"], "status": "active"},
        ]
        engine = self._mock_task_engine(items)
        tracker = GoalTracker(engine)
        result = tracker.auto_resolve_goal("story-goal-001")
        assert result is False
        engine.update_item.assert_not_called()

    def test_auto_resolve_update_failure(self):
        items = [
            {"id": "story-a", "tags": ["goal:story-goal-001"], "status": "resolved"},
        ]
        engine = self._mock_task_engine(items)
        engine.update_item.return_value = {"error": "not found"}
        tracker = GoalTracker(engine)
        result = tracker.auto_resolve_goal("story-goal-001")
        assert result is False


# ══════════════════════════════════════════════════════════════════════════════
# Engine PLAN Handling Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestEnginePlanHandling:
    """Tests for ManagementEngine PLAN job handling."""

    @pytest.fixture
    def engine_deps(self, tmp_path):
        """Create engine dependencies."""
        vault_path = tmp_path / "vault"
        _make_project_fdo(vault_path, "proj-test", [
            {
                "id": "story-goal-001",
                "title": "Add auth system",
                "status": "active",
                "priority": "high",
                "assignee": "plan",
                "owner": "grim",
                "tags": ["goal"],
            },
        ])

        db_path = tmp_path / "pipeline.db"
        pool_events = PoolEventBus()
        pool_queue = AsyncMock()

        return {
            "db_path": db_path,
            "pool_events": pool_events,
            "pool_queue": pool_queue,
            "vault_path": vault_path,
        }

    @pytest.fixture
    def make_engine(self, engine_deps):
        async def _make(auto_approve_threshold=3):
            from core.daemon.engine import ManagementEngine
            config = MagicMock()
            config.daemon_poll_interval = 999
            config.daemon_auto_approve_threshold = auto_approve_threshold
            config.daemon_auto_resolve = False
            config.daemon_validate_output = False
            config.daemon_max_daemon_retries = 0
            config.daemon_resolve_model = "claude-haiku"
            config.daemon_validate_model = "claude-opus"
            config.daemon_resolve_confidence_threshold = 0.8
            config.daemon_nudge_after_days = 3
            config.daemon_default_owner = "grim"
            config.daemon_db_path = engine_deps["db_path"]
            config.vault_path = engine_deps["vault_path"]
            config.daemon_project_filter = []

            engine = ManagementEngine(
                config=config,
                pool_queue=engine_deps["pool_queue"],
                pool_events=engine_deps["pool_events"],
                vault_path=engine_deps["vault_path"],
            )
            await engine._store.initialize()
            return engine
        return _make

    @pytest.mark.asyncio
    async def test_handle_plan_complete_auto_approve(self, make_engine, engine_deps):
        """PLAN job with ≤threshold stories auto-approves."""
        engine = await make_engine(auto_approve_threshold=5)

        # Add goal to pipeline
        await engine._store.add(
            story_id="story-goal-001",
            project_id="proj-test",
            assignee="plan",
            priority=1,
            owner="grim",
        )
        item = await engine._store.get_by_story("story-goal-001")
        await engine._store.advance(item.id, PipelineStatus.READY)
        await engine._store.advance(item.id, PipelineStatus.DISPATCHED)

        # Mock pool queue to return PLAN result
        job = MagicMock()
        job.result = VALID_PLAN_YAML
        engine_deps["pool_queue"].get.return_value = job

        # Mock task engine
        mock_te = MagicMock()
        call_count = [0]
        def create_side(**kwargs):
            call_count[0] += 1
            return {"created": f"story-child-{call_count[0]:03d}"}
        mock_te.create_story.side_effect = create_side
        mock_te.update_item.return_value = {}
        mock_te.list_items.return_value = []
        engine._task_engine = mock_te

        await engine._handle_plan_complete(item, "job-001", {})

        # Should have auto-approved (3 stories ≤ threshold of 5)
        updated = await engine._store.get_by_story("story-goal-001")
        assert updated.status == PipelineStatus.MERGED

    @pytest.mark.asyncio
    async def test_handle_plan_complete_requires_approval(self, make_engine, engine_deps):
        """PLAN job with >threshold stories emits PLAN_PROPOSED."""
        engine = await make_engine(auto_approve_threshold=1)
        emitted = []
        engine_deps["pool_events"].subscribe(lambda e: emitted.append(e))

        await engine._store.add(
            story_id="story-goal-001", project_id="proj-test",
            assignee="plan", priority=1, owner="grim",
        )
        item = await engine._store.get_by_story("story-goal-001")
        await engine._store.advance(item.id, PipelineStatus.READY)
        await engine._store.advance(item.id, PipelineStatus.DISPATCHED)

        job = MagicMock()
        job.result = VALID_PLAN_YAML
        engine_deps["pool_queue"].get.return_value = job

        mock_te = MagicMock()
        cnt = [0]
        def create_s(**kw):
            cnt[0] += 1
            return {"created": f"story-child-{cnt[0]:03d}"}
        mock_te.create_story.side_effect = create_s
        mock_te.update_item.return_value = {}
        mock_te.list_items.return_value = []
        engine._task_engine = mock_te

        await engine._handle_plan_complete(item, "job-002", {})

        # Should be in REVIEW, not MERGED
        updated = await engine._store.get_by_story("story-goal-001")
        assert updated.status == PipelineStatus.REVIEW

        # Should have emitted PLAN_PROPOSED
        plan_events = [e for e in emitted if e.type == PoolEventType.DAEMON_PLAN_PROPOSED]
        assert len(plan_events) == 1
        assert plan_events[0].data["story_count"] == 3

    @pytest.mark.asyncio
    async def test_handle_plan_complete_no_output(self, make_engine, engine_deps):
        """PLAN job with no output fails the goal."""
        engine = await make_engine()

        await engine._store.add(
            story_id="story-goal-001", project_id="proj-test",
            assignee="plan", priority=1, owner="grim",
        )
        item = await engine._store.get_by_story("story-goal-001")
        await engine._store.advance(item.id, PipelineStatus.READY)
        await engine._store.advance(item.id, PipelineStatus.DISPATCHED)

        engine_deps["pool_queue"].get.return_value = MagicMock(result="")
        engine._task_engine = MagicMock()

        await engine._handle_plan_complete(item, "job-003", {})

        updated = await engine._store.get_by_story("story-goal-001")
        assert updated.status == PipelineStatus.FAILED

    @pytest.mark.asyncio
    async def test_handle_plan_complete_parse_failure(self, make_engine, engine_deps):
        """PLAN job with invalid output fails the goal."""
        engine = await make_engine()

        await engine._store.add(
            story_id="story-goal-001", project_id="proj-test",
            assignee="plan", priority=1, owner="grim",
        )
        item = await engine._store.get_by_story("story-goal-001")
        await engine._store.advance(item.id, PipelineStatus.READY)
        await engine._store.advance(item.id, PipelineStatus.DISPATCHED)

        job = MagicMock()
        job.result = "This is not valid YAML plan output"
        engine_deps["pool_queue"].get.return_value = job
        engine._task_engine = MagicMock()

        await engine._handle_plan_complete(item, "job-004", {})

        updated = await engine._store.get_by_story("story-goal-001")
        assert updated.status == PipelineStatus.FAILED

    @pytest.mark.asyncio
    async def test_approve_goal(self, make_engine, engine_deps):
        """approve_goal activates draft children and advances goal."""
        engine = await make_engine()

        await engine._store.add(
            story_id="story-goal-001", project_id="proj-test",
            assignee="plan", priority=1, owner="grim",
        )
        item = await engine._store.get_by_story("story-goal-001")
        await engine._store.advance(item.id, PipelineStatus.READY)
        await engine._store.advance(item.id, PipelineStatus.DISPATCHED)
        await engine._store.advance(item.id, PipelineStatus.REVIEW)

        mock_te = MagicMock()
        mock_te.list_items.return_value = [
            {"id": "story-child-001", "tags": ["goal:story-goal-001"], "status": "draft"},
            {"id": "story-child-002", "tags": ["goal:story-goal-001"], "status": "draft"},
        ]
        mock_te.update_item.return_value = {}
        engine._task_engine = mock_te

        result = await engine.approve_goal("story-goal-001")
        assert result["activated"] == 2
        assert result["approved"] == "story-goal-001"

        # Goal should be MERGED
        updated = await engine._store.get_by_story("story-goal-001")
        assert updated.status == PipelineStatus.MERGED

    @pytest.mark.asyncio
    async def test_reject_goal(self, make_engine, engine_deps):
        """reject_goal closes draft children and fails the goal."""
        engine = await make_engine()

        await engine._store.add(
            story_id="story-goal-001", project_id="proj-test",
            assignee="plan", priority=1, owner="grim",
        )
        item = await engine._store.get_by_story("story-goal-001")
        await engine._store.advance(item.id, PipelineStatus.READY)
        await engine._store.advance(item.id, PipelineStatus.DISPATCHED)
        await engine._store.advance(item.id, PipelineStatus.REVIEW)

        mock_te = MagicMock()
        mock_te.list_items.return_value = [
            {"id": "story-child-001", "tags": ["goal:story-goal-001"], "status": "draft"},
        ]
        mock_te.update_item.return_value = {}
        engine._task_engine = mock_te

        result = await engine.reject_goal("story-goal-001")
        assert result["closed"] == 1
        assert result["rejected"] == "story-goal-001"

        updated = await engine._store.get_by_story("story-goal-001")
        assert updated.status == PipelineStatus.FAILED


# ══════════════════════════════════════════════════════════════════════════════
# Discord Command Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestDaemonGoalCommands:
    """Tests for Discord daemon command patterns and formatting."""

    def test_goal_pattern(self):
        from clients.daemon_commands import GOAL_PATTERN
        m = GOAL_PATTERN.search('@GRIM goal proj-auth "Add OAuth2 support"')
        assert m
        assert m.group(1) == "proj-auth"
        assert m.group(2) == "Add OAuth2 support"

    def test_goal_pattern_no_match(self):
        from clients.daemon_commands import GOAL_PATTERN
        m = GOAL_PATTERN.search("@GRIM goal without quotes")
        assert m is None

    def test_show_plan_pattern(self):
        from clients.daemon_commands import SHOW_PLAN_PATTERN
        m = SHOW_PLAN_PATTERN.search("@GRIM show plan story-goal-001")
        assert m
        assert m.group(1) == "story-goal-001"

    def test_approve_plan_pattern(self):
        from clients.daemon_commands import APPROVE_PLAN_PATTERN
        m = APPROVE_PLAN_PATTERN.search("@GRIM approve plan story-goal-001")
        assert m
        assert m.group(1) == "story-goal-001"

    def test_reject_plan_pattern(self):
        from clients.daemon_commands import REJECT_PLAN_PATTERN
        m = REJECT_PLAN_PATTERN.search("@GRIM reject plan story-goal-001")
        assert m
        assert m.group(1) == "story-goal-001"

    def test_approve_before_show(self):
        """Approve should match before show (specificity order)."""
        from clients.daemon_commands import APPROVE_PLAN_PATTERN, SHOW_PLAN_PATTERN
        text = "approve plan story-x"
        assert APPROVE_PLAN_PATTERN.search(text) is not None
        # Show should not match "approve plan" (different prefix)
        assert SHOW_PLAN_PATTERN.search(text) is None


class TestDaemonGoalEventFormatting:
    """Tests for formatting goal-related daemon events."""

    def test_format_plan_proposed(self):
        from clients.daemon_commands import format_daemon_event
        event = {
            "type": "daemon_plan_proposed",
            "data": {"story_id": "story-goal-001", "story_count": 5},
        }
        result = format_daemon_event(event)
        assert result is not None
        assert "Plan Ready" in result
        assert "story-goal-001" in result
        assert "5" in result
        assert "approve plan" in result

    def test_format_goal_complete(self):
        from clients.daemon_commands import format_daemon_event
        event = {
            "type": "daemon_goal_complete",
            "data": {"story_id": "story-goal-001", "children_count": 3},
        }
        result = format_daemon_event(event)
        assert result is not None
        assert "Goal Complete" in result
        assert "3" in result

    def test_goal_events_in_daemon_event_types(self):
        from clients.daemon_commands import DAEMON_EVENT_TYPES
        assert "daemon_plan_proposed" in DAEMON_EVENT_TYPES
        assert "daemon_goal_complete" in DAEMON_EVENT_TYPES

    def test_is_daemon_event_for_goal_events(self):
        from clients.daemon_commands import is_daemon_event
        assert is_daemon_event({"type": "daemon_plan_proposed"})
        assert is_daemon_event({"type": "daemon_goal_complete"})


# ══════════════════════════════════════════════════════════════════════════════
# Event Type Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestGoalEventTypes:
    """Tests for new PoolEventType enum values."""

    def test_plan_proposed_event_type(self):
        assert PoolEventType.DAEMON_PLAN_PROPOSED.value == "daemon_plan_proposed"

    def test_goal_complete_event_type(self):
        assert PoolEventType.DAEMON_GOAL_COMPLETE.value == "daemon_goal_complete"

    def test_event_serialization(self):
        event = PoolEvent(
            type=PoolEventType.DAEMON_PLAN_PROPOSED,
            job_id="job-001",
            data={"story_id": "story-goal-001", "story_count": 3},
        )
        d = event.to_dict()
        assert d["event_type"] == "daemon_plan_proposed"
        assert d["story_id"] == "story-goal-001"


# ══════════════════════════════════════════════════════════════════════════════
# Config Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestGoalConfig:
    """Tests for daemon_auto_approve_threshold config."""

    def test_default_auto_approve_threshold(self):
        from core.config import GrimConfig
        config = GrimConfig()
        assert config.daemon_auto_approve_threshold == 3

    def test_auto_approve_threshold_from_yaml(self, tmp_path):
        from core.config import load_config
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({
            "daemon": {"auto_approve_threshold": 10},
        }))
        config = load_config(config_file, grim_root=tmp_path)
        assert config.daemon_auto_approve_threshold == 10
