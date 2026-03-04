"""Tests for the Response Generator node — the loop heartbeat.

Tests:
  - should_auto_continue() logic (all decision paths)
  - Objective updates from subgraph output
  - Response formatting per UX mode
  - Loop/exit edge function
  - Full node behavior with state
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.nodes.response_generator import (
    DEFAULT_MAX_LOOPS,
    _apply_objective_updates,
    _format_response,
    make_response_generator_node,
    response_generator_decision,
    should_auto_continue,
)
from core.state import (
    Objective,
    ObjectiveStatus,
    SubgraphOutput,
    UXMode,
)


# ── Fixtures ────────────────────────────────────────────────────────────


def _obj(
    title: str = "Test objective",
    status: ObjectiveStatus = ObjectiveStatus.PENDING,
    target: str | None = "code",
    auto_continue: bool = False,
    obj_id: str | None = None,
) -> Objective:
    ctx = {"auto_continue": True} if auto_continue else {}
    return Objective(
        id=obj_id or f"obj-{title.lower().replace(' ', '-')[:12]}",
        title=title,
        status=status,
        target_subgraph=target,
        context=ctx,
    )


def _output(
    response: str = "Done.",
    source: str = "companion",
    continuation: dict | None = None,
    objective_updates: list[Objective] | None = None,
    artifacts: list[str] | None = None,
) -> SubgraphOutput:
    return SubgraphOutput(
        response=response,
        source_subgraph=source,
        continuation=continuation,
        objective_updates=objective_updates or [],
        artifacts=artifacts or [],
    )


def _state(**overrides) -> dict:
    base = {
        "loop_count": 0,
        "max_loops": DEFAULT_MAX_LOOPS,
        "ux_mode": UXMode.FULLSCREEN.value,
        "objectives": [],
        "subgraph_history": [],
        "context_stack": [],
        "subgraph_output": None,
        "should_continue": False,
        "continuation_intent": None,
    }
    base.update(overrides)
    return base


# ── should_auto_continue tests ──────────────────────────────────────────


class TestShouldAutoContinue:
    """Test all decision paths in should_auto_continue."""

    def test_safety_valve_stops_at_max(self):
        cont, intent, reason = should_auto_continue(
            objectives=[], loop_count=10, max_loops=10,
            subgraph_output=None,
        )
        assert cont is False
        assert "safety valve" in reason.lower()

    def test_safety_valve_stops_above_max(self):
        cont, _, _ = should_auto_continue(
            objectives=[], loop_count=15, max_loops=10,
            subgraph_output=None,
        )
        assert cont is False

    def test_explicit_continuation(self):
        output = _output(continuation={"next_intent": "code"})
        cont, intent, reason = should_auto_continue(
            objectives=[], loop_count=0, max_loops=10,
            subgraph_output=output,
        )
        assert cont is True
        assert intent == "code"
        assert "explicit" in reason.lower()

    def test_explicit_continuation_blocked_by_safety_valve(self):
        output = _output(continuation={"next_intent": "code"})
        cont, _, reason = should_auto_continue(
            objectives=[], loop_count=10, max_loops=10,
            subgraph_output=output,
        )
        assert cont is False
        assert "safety valve" in reason.lower()

    def test_auto_continue_objective(self):
        obj = _obj("Build auth", auto_continue=True, target="code")
        cont, intent, reason = should_auto_continue(
            objectives=[obj], loop_count=0, max_loops=10,
            subgraph_output=None,
        )
        assert cont is True
        assert intent == "code"
        assert "auto-continue" in reason.lower()

    def test_auto_continue_skips_active_objectives(self):
        """Only PENDING objectives with auto_continue trigger continuation."""
        obj = _obj("Build auth", status=ObjectiveStatus.ACTIVE,
                    auto_continue=True, target="code")
        cont, _, reason = should_auto_continue(
            objectives=[obj], loop_count=0, max_loops=10,
            subgraph_output=None,
        )
        # get_next_objective only returns PENDING, so active won't match
        assert cont is False

    def test_no_auto_continue_without_flag(self):
        obj = _obj("Build auth", auto_continue=False, target="code")
        cont, _, _ = should_auto_continue(
            objectives=[obj], loop_count=0, max_loops=10,
            subgraph_output=None,
        )
        assert cont is False

    def test_all_blocked_stops_loop(self):
        objs = [
            _obj("Task A", status=ObjectiveStatus.BLOCKED, obj_id="a"),
            _obj("Task B", status=ObjectiveStatus.BLOCKED, obj_id="b"),
        ]
        cont, _, reason = should_auto_continue(
            objectives=objs, loop_count=0, max_loops=10,
            subgraph_output=None,
        )
        assert cont is False
        assert "blocked" in reason.lower()

    def test_no_objectives_no_continuation(self):
        cont, intent, reason = should_auto_continue(
            objectives=[], loop_count=0, max_loops=10,
            subgraph_output=None,
        )
        assert cont is False
        assert intent is None
        assert "no continuation" in reason.lower()

    def test_discord_mode_more_autonomous(self):
        """Discord mode continues with pending objectives even without auto_continue."""
        obj = _obj("Build auth", auto_continue=False, target="code")
        cont, intent, reason = should_auto_continue(
            objectives=[obj], loop_count=0, max_loops=10,
            subgraph_output=None, ux_mode=UXMode.DISCORD.value,
        )
        assert cont is True
        assert intent == "code"
        assert "autonomous" in reason.lower()

    def test_mission_control_more_autonomous(self):
        obj = _obj("Build auth", auto_continue=False, target="code")
        cont, _, _ = should_auto_continue(
            objectives=[obj], loop_count=0, max_loops=10,
            subgraph_output=None, ux_mode=UXMode.MISSION_CONTROL.value,
        )
        assert cont is True

    def test_fullscreen_not_autonomous(self):
        """Fullscreen mode requires explicit continuation or auto_continue."""
        obj = _obj("Build auth", auto_continue=False, target="code")
        cont, _, _ = should_auto_continue(
            objectives=[obj], loop_count=0, max_loops=10,
            subgraph_output=None, ux_mode=UXMode.FULLSCREEN.value,
        )
        assert cont is False

    def test_explicit_continuation_without_next_intent(self):
        """Continuation dict without next_intent → don't continue."""
        output = _output(continuation={"context": "some context"})
        cont, _, _ = should_auto_continue(
            objectives=[], loop_count=0, max_loops=10,
            subgraph_output=output,
        )
        assert cont is False

    def test_custom_max_loops(self):
        cont, _, _ = should_auto_continue(
            objectives=[], loop_count=3, max_loops=3,
            subgraph_output=None,
        )
        assert cont is False

    def test_zero_max_loops_always_stops(self):
        output = _output(continuation={"next_intent": "code"})
        cont, _, _ = should_auto_continue(
            objectives=[], loop_count=0, max_loops=0,
            subgraph_output=output,
        )
        assert cont is False


# ── Objective update tests ──────────────────────────────────────────────


class TestApplyObjectiveUpdates:
    """Test _apply_objective_updates merging."""

    def test_no_updates(self):
        objs = [_obj("A", obj_id="a"), _obj("B", obj_id="b")]
        result = _apply_objective_updates(objs, [])
        assert len(result) == 2

    def test_update_existing(self):
        objs = [_obj("A", status=ObjectiveStatus.PENDING, obj_id="a")]
        updated = _obj("A", status=ObjectiveStatus.COMPLETE, obj_id="a")
        result = _apply_objective_updates(objs, [updated])
        assert len(result) == 1
        assert result[0].status == ObjectiveStatus.COMPLETE

    def test_add_new(self):
        objs = [_obj("A", obj_id="a")]
        new_obj = _obj("B", obj_id="b")
        result = _apply_objective_updates(objs, [new_obj])
        assert len(result) == 2

    def test_merge_preserves_order(self):
        objs = [_obj("A", obj_id="a"), _obj("B", obj_id="b")]
        updated = _obj("A", status=ObjectiveStatus.ACTIVE, obj_id="a")
        result = _apply_objective_updates(objs, [updated])
        assert result[0].id == "a"
        assert result[0].status == ObjectiveStatus.ACTIVE
        assert result[1].id == "b"

    def test_empty_objectives_with_updates(self):
        updates = [_obj("New", obj_id="new")]
        result = _apply_objective_updates([], updates)
        assert len(result) == 1
        assert result[0].id == "new"


# ── Response formatting tests ────────────────────────────────────────────


class TestFormatResponse:
    """Test UX-mode-aware response formatting."""

    def test_fullscreen_passthrough(self):
        output = _output(response="# Full Response\n\nWith **markdown**")
        result = _format_response(output, UXMode.FULLSCREEN.value)
        assert result == output.response

    def test_discord_truncation(self):
        long_response = "x" * 2000
        output = _output(response=long_response)
        result = _format_response(output, UXMode.DISCORD.value)
        assert len(result) <= 1920  # 1900 + truncation suffix
        assert "truncated" in result

    def test_discord_no_truncation_short(self):
        output = _output(response="Short message")
        result = _format_response(output, UXMode.DISCORD.value)
        assert result == "Short message"

    def test_sidepanel_passthrough_for_now(self):
        """Sidepanel formatting is TODO — should passthrough for now."""
        output = _output(response="Content")
        result = _format_response(output, UXMode.SIDEPANEL.value)
        assert result == "Content"

    def test_mission_control_passthrough_for_now(self):
        output = _output(response="Content")
        result = _format_response(output, UXMode.MISSION_CONTROL.value)
        assert result == "Content"


# ── Edge function tests ──────────────────────────────────────────────────


class TestResponseGeneratorDecision:
    """Test the loop/exit conditional edge function."""

    def test_continue_loops_back(self):
        assert response_generator_decision({"should_continue": True}) == "continue"

    def test_exit_proceeds(self):
        assert response_generator_decision({"should_continue": False}) == "exit"

    def test_default_exits(self):
        assert response_generator_decision({}) == "exit"


# ── Full node behavior tests ────────────────────────────────────────────


class TestResponseGeneratorNode:
    """Test the full response_generator_node function."""

    @pytest.mark.asyncio
    async def test_no_subgraph_output(self):
        node = make_response_generator_node()
        result = await node(_state())
        assert result["should_continue"] is False
        assert result["loop_count"] == 1
        assert result["subgraph_output"] is None

    @pytest.mark.asyncio
    async def test_basic_output_processing(self):
        output = _output(response="Hello", source="companion")
        node = make_response_generator_node()
        result = await node(_state(subgraph_output=output.model_dump()))
        assert result["loop_count"] == 1
        assert "companion" in result["subgraph_history"]
        assert len(result["context_stack"]) == 1
        assert result["context_stack"][0]["source"] == "companion"

    @pytest.mark.asyncio
    async def test_explicit_continuation(self):
        output = _output(
            response="Partially done",
            source="code",
            continuation={"next_intent": "research"},
        )
        node = make_response_generator_node()
        result = await node(_state(subgraph_output=output.model_dump()))
        assert result["should_continue"] is True
        assert result["continuation_intent"] == "research"

    @pytest.mark.asyncio
    async def test_safety_valve_at_max(self):
        output = _output(
            response="Still going",
            continuation={"next_intent": "code"},
        )
        node = make_response_generator_node()
        result = await node(_state(
            subgraph_output=output.model_dump(),
            loop_count=9,
            max_loops=10,
        ))
        assert result["should_continue"] is False
        assert result["loop_count"] == 10

    @pytest.mark.asyncio
    async def test_objective_updates_applied(self):
        original = _obj("Build auth", status=ObjectiveStatus.PENDING, obj_id="auth")
        updated = _obj("Build auth", status=ObjectiveStatus.COMPLETE, obj_id="auth")
        output = _output(
            response="Auth module complete",
            source="code",
            objective_updates=[updated],
        )
        node = make_response_generator_node()
        result = await node(_state(
            objectives=[original],
            subgraph_output=output.model_dump(),
        ))
        assert result["objectives"][0].status == ObjectiveStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_auto_continue_objective(self):
        obj = _obj("Step 1", auto_continue=True, target="code")
        node = make_response_generator_node()
        result = await node(_state(
            objectives=[obj],
            subgraph_output=_output(source="planning").model_dump(),
        ))
        assert result["should_continue"] is True
        assert result["continuation_intent"] == "code"

    @pytest.mark.asyncio
    async def test_subgraph_history_accumulates(self):
        node = make_response_generator_node()
        state = _state(
            subgraph_output=_output(source="companion").model_dump(),
            subgraph_history=["research"],
        )
        result = await node(state)
        assert result["subgraph_history"] == ["research", "companion"]

    @pytest.mark.asyncio
    async def test_context_stack_accumulates(self):
        node = make_response_generator_node()
        existing_stack = [{"loop": 0, "source": "planning"}]
        state = _state(
            subgraph_output=_output(response="Done", source="code").model_dump(),
            context_stack=existing_stack,
            loop_count=1,
        )
        result = await node(state)
        assert len(result["context_stack"]) == 2
        assert result["context_stack"][1]["loop"] == 1
        assert result["context_stack"][1]["source"] == "code"

    @pytest.mark.asyncio
    async def test_subgraph_output_cleared_after_processing(self):
        node = make_response_generator_node()
        result = await node(_state(
            subgraph_output=_output(source="companion").model_dump(),
        ))
        assert result["subgraph_output"] is None

    @pytest.mark.asyncio
    async def test_loop_count_increments(self):
        node = make_response_generator_node()
        result = await node(_state(loop_count=5))
        assert result["loop_count"] == 6

    @pytest.mark.asyncio
    async def test_discord_mode_auto_continues(self):
        obj = _obj("Pending task", target="research")
        node = make_response_generator_node()
        result = await node(_state(
            objectives=[obj],
            ux_mode=UXMode.DISCORD.value,
            subgraph_output=_output(source="companion").model_dump(),
        ))
        assert result["should_continue"] is True

    @pytest.mark.asyncio
    async def test_all_blocked_stops(self):
        objs = [
            _obj("A", status=ObjectiveStatus.BLOCKED, obj_id="a"),
            _obj("B", status=ObjectiveStatus.BLOCKED, obj_id="b"),
        ]
        node = make_response_generator_node()
        result = await node(_state(
            objectives=objs,
            subgraph_output=_output(source="code").model_dump(),
        ))
        assert result["should_continue"] is False

    @pytest.mark.asyncio
    async def test_handles_subgraph_output_as_dict(self):
        """SubgraphOutput stored as dict in state should be parsed."""
        output_dict = {
            "response": "Hello",
            "source_subgraph": "companion",
            "artifacts": [],
            "memory_updates": {},
            "objective_updates": [],
            "continuation": None,
        }
        node = make_response_generator_node()
        result = await node(_state(subgraph_output=output_dict))
        assert "companion" in result["subgraph_history"]

    @pytest.mark.asyncio
    async def test_new_objective_from_subgraph(self):
        """Subgraph can add new objectives via objective_updates."""
        new_obj = _obj("New task", obj_id="new-task")
        output = _output(
            response="Created a new task",
            objective_updates=[new_obj],
        )
        node = make_response_generator_node()
        result = await node(_state(subgraph_output=output.model_dump()))
        assert len(result["objectives"]) == 1
        assert result["objectives"][0].id == "new-task"


# ── Integration: edge function with node output ─────────────────────────


class TestNodeEdgeIntegration:
    """Verify edge function produces correct routing from node output."""

    @pytest.mark.asyncio
    async def test_continue_path(self):
        output = _output(continuation={"next_intent": "code"})
        node = make_response_generator_node()
        result = await node(_state(subgraph_output=output.model_dump()))
        assert response_generator_decision(result) == "continue"

    @pytest.mark.asyncio
    async def test_exit_path(self):
        node = make_response_generator_node()
        result = await node(_state(
            subgraph_output=_output(source="companion").model_dump(),
        ))
        assert response_generator_decision(result) == "exit"

    @pytest.mark.asyncio
    async def test_safety_valve_exit(self):
        output = _output(continuation={"next_intent": "code"})
        node = make_response_generator_node()
        result = await node(_state(
            subgraph_output=output.model_dump(),
            loop_count=9, max_loops=10,
        ))
        assert response_generator_decision(result) == "exit"
