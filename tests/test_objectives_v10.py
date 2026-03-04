"""Tests for v0.10 Objective system — state models, lifecycle, reducers.

Covers:
  - Objective model creation + serialization
  - ObjectiveStatus enum
  - create_objective() factory
  - create_objective_tree() hierarchical decomposition
  - update_objective() status transitions
  - get_pending_objectives() / get_active_objectives() / get_next_objective()
  - _merge_objectives() LangGraph reducer
  - RoutingDecision structured output model
  - SubgraphOutput interface model
  - UXMode enum
  - Legacy Objective conversion
  - Edge cases and validation
"""

import json
from datetime import datetime, timezone

import pytest

from core.state import (
    STATE_SCHEMA_VERSION,
    Objective,
    ObjectiveStatus,
    RoutingDecision,
    SubgraphOutput,
    UXMode,
    _merge_objectives,
    build_resume_message,
    create_objective,
    create_objective_tree,
    get_active_objectives,
    get_next_objective,
    get_pending_objectives,
    update_objective,
)


# -----------------------------------------------------------------------
# Schema version
# -----------------------------------------------------------------------

class TestSchemaVersion:
    def test_schema_version_format(self):
        assert STATE_SCHEMA_VERSION == "0.10.0"

    def test_schema_version_is_string(self):
        assert isinstance(STATE_SCHEMA_VERSION, str)


# -----------------------------------------------------------------------
# ObjectiveStatus enum
# -----------------------------------------------------------------------

class TestObjectiveStatus:
    def test_all_statuses_exist(self):
        assert ObjectiveStatus.PENDING == "pending"
        assert ObjectiveStatus.ACTIVE == "active"
        assert ObjectiveStatus.BLOCKED == "blocked"
        assert ObjectiveStatus.COMPLETE == "complete"
        assert ObjectiveStatus.FAILED == "failed"

    def test_string_coercion(self):
        assert str(ObjectiveStatus.PENDING) == "ObjectiveStatus.PENDING"
        assert ObjectiveStatus.PENDING.value == "pending"

    def test_from_string(self):
        assert ObjectiveStatus("pending") == ObjectiveStatus.PENDING
        assert ObjectiveStatus("blocked") == ObjectiveStatus.BLOCKED


# -----------------------------------------------------------------------
# Objective model
# -----------------------------------------------------------------------

class TestObjectiveModel:
    def test_defaults(self):
        o = Objective(title="Test")
        assert o.title == "Test"
        assert o.status == ObjectiveStatus.PENDING
        assert o.priority == "medium"
        assert o.parent_id is None
        assert o.children == []
        assert o.target_subgraph is None
        assert o.origin_subgraph is None
        assert o.context == {}
        assert o.artifacts == []
        assert o.blocked_reason is None
        assert o.completed_at is None
        assert o.id.startswith("obj-")

    def test_auto_id_unique(self):
        ids = {Objective(title="A").id for _ in range(50)}
        assert len(ids) == 50  # all unique

    def test_custom_fields(self):
        o = Objective(
            id="custom-id",
            title="Build API",
            status=ObjectiveStatus.ACTIVE,
            priority="high",
            parent_id="parent-1",
            children=["child-1"],
            target_subgraph="code",
            origin_subgraph="planning",
            context={"key": "value"},
            artifacts=["file.py"],
            blocked_reason=None,
        )
        assert o.id == "custom-id"
        assert o.priority == "high"
        assert o.target_subgraph == "code"

    def test_serialization_roundtrip(self):
        o = Objective(title="Roundtrip test", target_subgraph="research")
        data = o.model_dump()
        restored = Objective(**data)
        assert restored.title == o.title
        assert restored.id == o.id
        assert restored.target_subgraph == o.target_subgraph

    def test_json_serialization(self):
        o = Objective(title="JSON test")
        json_str = o.model_dump_json()
        data = json.loads(json_str)
        assert data["title"] == "JSON test"
        assert data["status"] == "pending"

    def test_model_copy_update(self):
        o = Objective(title="Original")
        updated = o.model_copy(update={"status": ObjectiveStatus.COMPLETE})
        assert updated.status == ObjectiveStatus.COMPLETE
        assert o.status == ObjectiveStatus.PENDING  # original unchanged

    def test_timestamps_set(self):
        before = datetime.now(timezone.utc).isoformat()
        o = Objective(title="Timestamp test")
        assert o.created_at >= before
        assert o.updated_at >= before


# -----------------------------------------------------------------------
# create_objective()
# -----------------------------------------------------------------------

class TestCreateObjective:
    def test_basic(self):
        o = create_objective("Build API", "code")
        assert o.title == "Build API"
        assert o.target_subgraph == "code"
        assert o.status == ObjectiveStatus.PENDING
        assert o.priority == "medium"

    def test_with_priority(self):
        o = create_objective("Urgent fix", "code", priority="high")
        assert o.priority == "high"

    def test_with_parent(self):
        o = create_objective("Sub-task", "code", parent_id="parent-123")
        assert o.parent_id == "parent-123"

    def test_auto_continue_flag(self):
        o = create_objective("Auto task", "code", auto_continue=True)
        assert o.context["auto_continue"] is True

    def test_no_auto_continue_by_default(self):
        o = create_objective("Manual task", "code")
        assert "auto_continue" not in o.context

    def test_with_context(self):
        ctx = {"repo": "GRIM", "branch": "main"}
        o = create_objective("Task", "code", context=ctx)
        assert o.context["repo"] == "GRIM"

    def test_context_plus_auto_continue(self):
        o = create_objective("Task", "code", context={"x": 1}, auto_continue=True)
        assert o.context["x"] == 1
        assert o.context["auto_continue"] is True

    def test_origin_subgraph(self):
        o = create_objective("Task", "code", origin_subgraph="planning")
        assert o.origin_subgraph == "planning"


# -----------------------------------------------------------------------
# create_objective_tree()
# -----------------------------------------------------------------------

class TestCreateObjectiveTree:
    @pytest.fixture
    def sample_plan(self):
        return {
            "title": "Publication Tracker API",
            "stories": [
                {
                    "title": "Project Setup",
                    "target": "code",
                    "priority": "high",
                    "tasks": [
                        {"title": "Init FastAPI", "target": "code"},
                        {"title": "Docker config", "target": "operations"},
                    ],
                },
                {
                    "title": "Data Models",
                    "target": "code",
                    "tasks": [
                        {"title": "SQLAlchemy models", "target": "code"},
                    ],
                },
            ],
        }

    def test_tree_count(self, sample_plan):
        tree = create_objective_tree(sample_plan)
        # 1 feature + 2 stories + 3 tasks = 6
        assert len(tree) == 6

    def test_feature_is_first(self, sample_plan):
        tree = create_objective_tree(sample_plan)
        assert tree[0].title == "Publication Tracker API"
        assert tree[0].target_subgraph == "planning"
        assert tree[0].priority == "high"

    def test_parent_child_links(self, sample_plan):
        tree = create_objective_tree(sample_plan)
        feature = tree[0]
        story1 = tree[1]
        story2 = tree[4]  # second story after story1's 2 tasks

        assert story1.parent_id == feature.id
        assert story1.id in feature.children
        assert story2.parent_id == feature.id

    def test_task_parent_links(self, sample_plan):
        tree = create_objective_tree(sample_plan)
        story1 = tree[1]
        task1 = tree[2]
        task2 = tree[3]

        assert task1.parent_id == story1.id
        assert task2.parent_id == story1.id
        assert task1.id in story1.children
        assert task2.id in story1.children

    def test_tasks_auto_continue(self, sample_plan):
        tree = create_objective_tree(sample_plan)
        tasks = [o for o in tree if o.parent_id and o.parent_id != tree[0].id]
        for task in tasks:
            assert task.context.get("auto_continue") is True

    def test_stories_no_auto_continue(self, sample_plan):
        tree = create_objective_tree(sample_plan)
        stories = [o for o in tree if o.parent_id == tree[0].id]
        for story in stories:
            assert "auto_continue" not in story.context

    def test_target_subgraph_from_plan(self, sample_plan):
        tree = create_objective_tree(sample_plan)
        # task2 is Docker config → operations
        task2 = tree[3]
        assert task2.target_subgraph == "operations"

    def test_all_origin_planning(self, sample_plan):
        tree = create_objective_tree(sample_plan)
        for obj in tree:
            assert obj.origin_subgraph == "planning"

    def test_empty_stories(self):
        tree = create_objective_tree({"title": "Empty plan", "stories": []})
        assert len(tree) == 1
        assert tree[0].title == "Empty plan"

    def test_stories_without_tasks(self):
        plan = {
            "title": "Simple plan",
            "stories": [{"title": "Story 1", "target": "research"}],
        }
        tree = create_objective_tree(plan)
        assert len(tree) == 2


# -----------------------------------------------------------------------
# update_objective()
# -----------------------------------------------------------------------

class TestUpdateObjective:
    @pytest.fixture
    def objectives(self):
        return [
            create_objective("Task A", "code"),
            create_objective("Task B", "research"),
            create_objective("Task C", "planning"),
        ]

    def test_update_status(self, objectives):
        result = update_objective(objectives, objectives[0].id, status=ObjectiveStatus.ACTIVE)
        assert result[0].status == ObjectiveStatus.ACTIVE
        assert result[1].status == ObjectiveStatus.PENDING  # unchanged
        assert result[2].status == ObjectiveStatus.PENDING  # unchanged

    def test_complete_sets_timestamp(self, objectives):
        result = update_objective(objectives, objectives[1].id, status=ObjectiveStatus.COMPLETE)
        assert result[1].completed_at is not None

    def test_block_with_reason(self, objectives):
        result = update_objective(objectives, objectives[0].id, blocked_reason="Need API key")
        assert result[0].status == ObjectiveStatus.BLOCKED
        assert result[0].blocked_reason == "Need API key"

    def test_add_artifacts(self, objectives):
        result = update_objective(objectives, objectives[0].id, artifacts=["file1.py"])
        assert "file1.py" in result[0].artifacts
        # Add more
        result2 = update_objective(result, objectives[0].id, artifacts=["file2.py"])
        assert "file1.py" in result2[0].artifacts
        assert "file2.py" in result2[0].artifacts

    def test_nonexistent_id_no_change(self, objectives):
        result = update_objective(objectives, "nonexistent", status=ObjectiveStatus.COMPLETE)
        for orig, updated in zip(objectives, result):
            assert orig.status == updated.status

    def test_updates_timestamp(self, objectives):
        old_ts = objectives[0].updated_at
        result = update_objective(objectives, objectives[0].id, status=ObjectiveStatus.ACTIVE)
        assert result[0].updated_at >= old_ts

    def test_original_unchanged(self, objectives):
        """update_objective is non-destructive."""
        original_status = objectives[0].status
        update_objective(objectives, objectives[0].id, status=ObjectiveStatus.COMPLETE)
        assert objectives[0].status == original_status


# -----------------------------------------------------------------------
# get_pending / get_active / get_next
# -----------------------------------------------------------------------

class TestObjectiveQueries:
    @pytest.fixture
    def mixed_objectives(self):
        objs = [
            create_objective("High priority", "code", priority="high"),
            create_objective("Low priority", "research", priority="low"),
            create_objective("Medium priority", "planning", priority="medium"),
        ]
        # Mark one as complete
        objs[1] = objs[1].model_copy(update={"status": ObjectiveStatus.COMPLETE})
        return objs

    def test_get_pending(self, mixed_objectives):
        pending = get_pending_objectives(mixed_objectives)
        assert len(pending) == 2  # low is complete
        assert pending[0].priority == "high"  # sorted by priority
        assert pending[1].priority == "medium"

    def test_get_active(self, mixed_objectives):
        active = get_active_objectives(mixed_objectives)
        assert len(active) == 2  # excludes complete

    def test_get_active_includes_blocked(self):
        objs = [create_objective("Blocked", "code")]
        objs[0] = objs[0].model_copy(update={"status": ObjectiveStatus.BLOCKED})
        assert len(get_active_objectives(objs)) == 1

    def test_get_next(self, mixed_objectives):
        nxt = get_next_objective(mixed_objectives)
        assert nxt is not None
        assert nxt.priority == "high"

    def test_get_next_skips_no_target(self):
        objs = [Objective(title="No target")]  # target_subgraph is None
        assert get_next_objective(objs) is None

    def test_get_next_empty(self):
        assert get_next_objective([]) is None

    def test_get_pending_priority_order(self):
        objs = [
            create_objective("Low", "code", priority="low"),
            create_objective("High", "code", priority="high"),
            create_objective("Med", "code", priority="medium"),
        ]
        pending = get_pending_objectives(objs)
        assert [o.priority for o in pending] == ["high", "medium", "low"]


# -----------------------------------------------------------------------
# _merge_objectives() reducer
# -----------------------------------------------------------------------

class TestMergeObjectivesReducer:
    def test_both_none(self):
        assert _merge_objectives(None, None) == []

    def test_existing_none(self):
        new = [create_objective("A", "code")]
        result = _merge_objectives(None, new)
        assert len(result) == 1

    def test_new_none(self):
        existing = [create_objective("A", "code")]
        result = _merge_objectives(existing, None)
        assert len(result) == 1

    def test_merge_no_overlap(self):
        existing = [create_objective("A", "code")]
        new = [create_objective("B", "research")]
        result = _merge_objectives(existing, new)
        assert len(result) == 2

    def test_merge_dedup_by_id(self):
        o = create_objective("Original", "code")
        updated = o.model_copy(update={"status": ObjectiveStatus.COMPLETE, "title": "Updated"})
        result = _merge_objectives([o], [updated])
        assert len(result) == 1
        assert result[0].status == ObjectiveStatus.COMPLETE
        assert result[0].title == "Updated"

    def test_cap_at_100(self):
        existing = [create_objective(f"Obj {i}", "code") for i in range(80)]
        new = [create_objective(f"New {i}", "code") for i in range(30)]
        result = _merge_objectives(existing, new)
        assert len(result) == 100  # capped

    def test_both_empty(self):
        assert _merge_objectives([], []) == []


# -----------------------------------------------------------------------
# RoutingDecision
# -----------------------------------------------------------------------

class TestRoutingDecision:
    def test_valid_targets(self):
        for target in ("conversation", "research", "code", "operations", "planning"):
            rd = RoutingDecision(target_subgraph=target, confidence=0.8, reasoning="test")
            assert rd.target_subgraph == target

    def test_confidence_bounds(self):
        rd = RoutingDecision(target_subgraph="code", confidence=0.0, reasoning="low")
        assert rd.confidence == 0.0
        rd = RoutingDecision(target_subgraph="code", confidence=1.0, reasoning="high")
        assert rd.confidence == 1.0

    def test_confidence_out_of_bounds(self):
        with pytest.raises(Exception):
            RoutingDecision(target_subgraph="code", confidence=1.5, reasoning="too high")
        with pytest.raises(Exception):
            RoutingDecision(target_subgraph="code", confidence=-0.1, reasoning="too low")

    def test_invalid_target(self):
        with pytest.raises(Exception):
            RoutingDecision(target_subgraph="invalid", confidence=0.5, reasoning="bad")

    def test_continuation_fields(self):
        rd = RoutingDecision(
            target_subgraph="code",
            confidence=1.0,
            reasoning="Continuing from planning",
            is_continuation=True,
            continuation_context={"plan_id": "abc"},
        )
        assert rd.is_continuation is True
        assert rd.continuation_context["plan_id"] == "abc"

    def test_serialization(self):
        rd = RoutingDecision(target_subgraph="research", confidence=0.9, reasoning="physics query")
        data = rd.model_dump()
        restored = RoutingDecision(**data)
        assert restored.target_subgraph == "research"


# -----------------------------------------------------------------------
# SubgraphOutput
# -----------------------------------------------------------------------

class TestSubgraphOutput:
    def test_minimal(self):
        so = SubgraphOutput(response="Hello")
        assert so.response == "Hello"
        assert so.artifacts == []
        assert so.continuation is None
        assert so.source_subgraph == ""

    def test_full(self):
        obj = create_objective("Task", "code")
        so = SubgraphOutput(
            response="Done",
            artifacts=["file.py"],
            memory_updates={"key": "val"},
            objective_updates=[obj],
            continuation={"next_intent": "code", "context": {}},
            source_subgraph="planning",
        )
        assert len(so.objective_updates) == 1
        assert so.continuation["next_intent"] == "code"

    def test_serialization(self):
        so = SubgraphOutput(response="Test", source_subgraph="research")
        data = so.model_dump()
        assert data["source_subgraph"] == "research"


# -----------------------------------------------------------------------
# UXMode
# -----------------------------------------------------------------------

class TestUXMode:
    def test_all_modes(self):
        assert UXMode.FULLSCREEN == "fullscreen"
        assert UXMode.SIDEPANEL == "sidepanel"
        assert UXMode.MISSION_CONTROL == "mission_control"
        assert UXMode.DISCORD == "discord"

    def test_from_string(self):
        assert UXMode("fullscreen") == UXMode.FULLSCREEN


# -----------------------------------------------------------------------
# Legacy conversion
# -----------------------------------------------------------------------

class TestLegacyConversion:
    def test_active_conversion(self):
        from core.objectives import Objective as LegacyObj
        legacy = LegacyObj(id="test-1", description="Build API", status="active")
        converted = legacy.to_state_objective()
        assert converted.id == "test-1"
        assert converted.title == "Build API"
        assert converted.status == ObjectiveStatus.ACTIVE

    def test_completed_conversion(self):
        from core.objectives import Objective as LegacyObj
        legacy = LegacyObj(id="done-1", description="Done task", status="completed")
        converted = legacy.to_state_objective()
        assert converted.status == ObjectiveStatus.COMPLETE

    def test_stalled_conversion(self):
        from core.objectives import Objective as LegacyObj
        legacy = LegacyObj(id="stuck-1", description="Stuck", status="stalled")
        converted = legacy.to_state_objective()
        assert converted.status == ObjectiveStatus.BLOCKED

    def test_legacy_context_preserved(self):
        from core.objectives import Objective as LegacyObj
        legacy = LegacyObj(id="x", description="X", notes=["note1", "note2"])
        converted = legacy.to_state_objective()
        assert converted.context["legacy"] is True
        assert converted.context["notes"] == ["note1", "note2"]


# -----------------------------------------------------------------------
# build_resume_message()
# -----------------------------------------------------------------------

class TestBuildResumeMessage:
    def test_no_objectives(self):
        assert build_resume_message([]) is None

    def test_all_complete(self):
        objs = [create_objective("Done", "code")]
        objs[0] = objs[0].model_copy(update={"status": ObjectiveStatus.COMPLETE})
        assert build_resume_message(objs) is None

    def test_single_active(self):
        objs = [create_objective("Build API", "code")]
        msg = build_resume_message(objs)
        assert msg is not None
        assert "Build API" in msg
        assert "pick that back up" in msg

    def test_multiple_active(self):
        objs = [
            create_objective("Build API", "code"),
            create_objective("Write tests", "code"),
        ]
        msg = build_resume_message(objs)
        assert msg is not None
        assert "2 things in progress" in msg
        assert "Build API" in msg
        assert "Write tests" in msg

    def test_many_active_truncates(self):
        objs = [
            create_objective(f"Task {i}", "code")
            for i in range(5)
        ]
        msg = build_resume_message(objs)
        assert "5 things in progress" in msg
        assert "and 3 more" in msg

    def test_mixed_statuses(self):
        objs = [
            create_objective("Active", "code"),
            create_objective("Done", "code"),
            create_objective("Blocked", "code"),
        ]
        objs[1] = objs[1].model_copy(update={"status": ObjectiveStatus.COMPLETE})
        objs[2] = objs[2].model_copy(update={"status": ObjectiveStatus.BLOCKED})
        msg = build_resume_message(objs)
        # 2 active (pending + blocked), 1 complete excluded
        assert "2 things in progress" in msg


# -----------------------------------------------------------------------
# handle_blocked_objective() — can only test the state update part,
# not the actual interrupt (requires LangGraph runtime)
# -----------------------------------------------------------------------

class TestHandleBlockedObjective:
    def test_interrupt_import(self):
        """Verify LangGraph interrupt is importable."""
        from langgraph.types import interrupt
        assert callable(interrupt)
