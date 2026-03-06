"""Tests for subgraph wrappers — SubgraphOutput packaging.

Tests:
  - Base wrapper: response extraction, artifact detection, SubgraphOutput creation
  - Conversation subgraph: companion/personal routing
  - Research subgraph: dispatch wrapper
  - Planning subgraph: terminal by default, execution intent detection
  - Code subgraph: dispatch wrapper, continuation detection
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.state import (
    AgentResult,
    Objective,
    ObjectiveStatus,
    SubgraphOutput,
)
from core.subgraphs.base import (
    extract_artifacts,
    extract_response_text,
    make_subgraph_wrapper,
)
from core.subgraphs.code import _detect_code_continuation, make_code_subgraph
from core.subgraphs.conversation import make_conversation_subgraph
from core.subgraphs.planning import (
    _detect_execution_intent,
    make_planning_subgraph,
)
from core.subgraphs.research import make_research_subgraph


# ── Fixtures ────────────────────────────────────────────────────────────


def _msg(content: str):
    m = MagicMock()
    m.content = content
    return m


def _state(message: str = "hello", **overrides) -> dict:
    base = {
        "messages": [_msg(message)],
        "objectives": [],
        "graph_target": "research",
        "agent_result": None,
    }
    base.update(overrides)
    return base


# ── Base: extract_response_text tests ────────────────────────────────────


class TestExtractResponseText:
    """Test AI response extraction from message lists."""

    def test_single_ai_message(self):
        messages = [AIMessage(content="Hello there")]
        assert extract_response_text(messages) == "Hello there"

    def test_ai_after_tool_calls(self):
        messages = [
            AIMessage(content="", additional_kwargs={"tool_calls": [{}]}),
            ToolMessage(content="result", tool_call_id="1"),
            AIMessage(content="Based on the results, here's my answer."),
        ]
        assert extract_response_text(messages) == "Based on the results, here's my answer."

    def test_last_ai_message_wins(self):
        messages = [
            AIMessage(content="First response"),
            AIMessage(content="Final response"),
        ]
        assert extract_response_text(messages) == "Final response"

    def test_skips_empty_ai_messages(self):
        messages = [
            AIMessage(content="Good answer"),
            AIMessage(content=""),
        ]
        assert extract_response_text(messages) == "Good answer"

    def test_no_ai_messages(self):
        messages = [HumanMessage(content="hello")]
        assert extract_response_text(messages) == ""

    def test_empty_list(self):
        assert extract_response_text([]) == ""

    def test_multipart_content(self):
        messages = [
            AIMessage(content=[
                {"type": "text", "text": "Part one."},
                {"type": "text", "text": "Part two."},
            ]),
        ]
        assert "Part one" in extract_response_text(messages)
        assert "Part two" in extract_response_text(messages)

    def test_strips_whitespace(self):
        messages = [AIMessage(content="  trimmed  ")]
        assert extract_response_text(messages) == "trimmed"


class TestExtractArtifacts:
    """Test artifact extraction from tool messages."""

    def test_no_tool_messages(self):
        messages = [AIMessage(content="hello")]
        assert extract_artifacts(messages) == []

    def test_file_path_in_tool_result(self):
        messages = [
            ToolMessage(content="Created file at src/auth.py", tool_call_id="1"),
        ]
        artifacts = extract_artifacts(messages)
        assert "src/auth.py" in artifacts

    def test_multiple_artifacts(self):
        messages = [
            ToolMessage(content="Modified src/config.yaml and lib/utils.py", tool_call_id="1"),
        ]
        artifacts = extract_artifacts(messages)
        assert len(artifacts) >= 1

    def test_cap_at_20(self):
        content = " ".join(f"file{i}.py" for i in range(30))
        messages = [ToolMessage(content=content, tool_call_id="1")]
        assert len(extract_artifacts(messages)) <= 20

    def test_no_false_positives(self):
        messages = [
            ToolMessage(content="The function returned 42", tool_call_id="1"),
        ]
        assert extract_artifacts(messages) == []


# ── Base: make_subgraph_wrapper tests ────────────────────────────────────


class TestMakeSubgraphWrapper:
    """Test the base subgraph wrapper factory."""

    @pytest.mark.asyncio
    async def test_wraps_node_output(self):
        async def fake_node(state):
            return {"messages": [AIMessage(content="Test response")]}

        wrapper = make_subgraph_wrapper(
            name="Test",
            node_fn=fake_node,
            source_subgraph="test",
        )
        result = await wrapper(_state())
        assert "subgraph_output" in result
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.response == "Test response"
        assert output.source_subgraph == "test"

    @pytest.mark.asyncio
    async def test_preserves_original_output(self):
        async def fake_node(state):
            return {
                "messages": [AIMessage(content="Hello")],
                "session_topics": ["topic1"],
            }

        wrapper = make_subgraph_wrapper(
            name="Test", node_fn=fake_node, source_subgraph="test",
        )
        result = await wrapper(_state())
        assert "session_topics" in result
        assert result["session_topics"] == ["topic1"]

    @pytest.mark.asyncio
    async def test_continuation_extraction(self):
        async def fake_node(state):
            return {"messages": [AIMessage(content="Done")]}

        def extract_cont(result, state):
            return {"next_intent": "code"}

        wrapper = make_subgraph_wrapper(
            name="Test", node_fn=fake_node, source_subgraph="test",
            extract_continuation=extract_cont,
        )
        result = await wrapper(_state())
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.continuation == {"next_intent": "code"}

    @pytest.mark.asyncio
    async def test_objective_extraction(self):
        async def fake_node(state):
            return {"messages": [AIMessage(content="Created tasks")]}

        obj = Objective(title="New task", status=ObjectiveStatus.PENDING)

        def extract_objs(result, state):
            return [obj]

        wrapper = make_subgraph_wrapper(
            name="Test", node_fn=fake_node, source_subgraph="test",
            extract_objectives=extract_objs,
        )
        result = await wrapper(_state())
        output = SubgraphOutput(**result["subgraph_output"])
        assert len(output.objective_updates) == 1

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        async def fake_node(state):
            return {"messages": []}

        wrapper = make_subgraph_wrapper(
            name="Test", node_fn=fake_node, source_subgraph="test",
        )
        result = await wrapper(_state())
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.response == ""

    @pytest.mark.asyncio
    async def test_no_messages_key(self):
        async def fake_node(state):
            return {}

        wrapper = make_subgraph_wrapper(
            name="Test", node_fn=fake_node, source_subgraph="test",
        )
        result = await wrapper(_state())
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.response == ""


# ── Conversation subgraph tests ──────────────────────────────────────────


class TestConversationSubgraph:
    """Test conversation subgraph routing between companion and personal."""

    @pytest.mark.asyncio
    async def test_personal_routing(self):
        companion_fn = AsyncMock(return_value={"messages": [AIMessage(content="companion")]})
        personal_fn = AsyncMock(return_value={"messages": [AIMessage(content="personal")]})

        subgraph = make_conversation_subgraph(companion_fn, personal_fn)
        result = await subgraph(_state(graph_target="personal"))

        personal_fn.assert_called_once()
        companion_fn.assert_not_called()
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.response == "personal"
        assert output.source_subgraph == "conversation"

    @pytest.mark.asyncio
    async def test_companion_routing(self):
        companion_fn = AsyncMock(return_value={"messages": [AIMessage(content="companion")]})
        personal_fn = AsyncMock(return_value={"messages": [AIMessage(content="personal")]})

        subgraph = make_conversation_subgraph(companion_fn, personal_fn)
        result = await subgraph(_state(graph_target="research"))

        companion_fn.assert_called_once()
        personal_fn.assert_not_called()
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.response == "companion"

    @pytest.mark.asyncio
    async def test_default_routing(self):
        companion_fn = AsyncMock(return_value={"messages": [AIMessage(content="default")]})
        personal_fn = AsyncMock(return_value={"messages": []})

        subgraph = make_conversation_subgraph(companion_fn, personal_fn)
        result = await subgraph({"messages": [_msg("hi")]})

        companion_fn.assert_called_once()


# ── Research subgraph tests ──────────────────────────────────────────────


class TestResearchSubgraph:
    """Test research subgraph dispatch wrapper."""

    @pytest.mark.asyncio
    async def test_wraps_dispatch(self):
        dispatch_fn = AsyncMock(return_value={
            "messages": [AIMessage(content="Research results")],
            "agent_result": AgentResult(agent="research", success=True, summary="Found 5 FDOs"),
        })

        subgraph = make_research_subgraph(dispatch_fn)
        result = await subgraph(_state())

        dispatch_fn.assert_called_once()
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.response == "Research results"
        assert output.source_subgraph == "research"

    @pytest.mark.asyncio
    async def test_no_auto_continuation(self):
        dispatch_fn = AsyncMock(return_value={
            "messages": [AIMessage(content="Done")],
        })

        subgraph = make_research_subgraph(dispatch_fn)
        result = await subgraph(_state())
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.continuation is None


# ── Planning subgraph tests ──────────────────────────────────────────────


class TestPlanningSubgraph:
    """Test planning subgraph wrapper and execution intent detection."""

    @pytest.mark.asyncio
    async def test_wraps_planning_companion(self):
        planning_fn = AsyncMock(return_value={
            "messages": [AIMessage(content="Here's the sprint plan")],
        })

        subgraph = make_planning_subgraph(planning_fn)
        result = await subgraph(_state())

        planning_fn.assert_called_once()
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.response == "Here's the sprint plan"
        assert output.source_subgraph == "planning"

    @pytest.mark.asyncio
    async def test_no_continuation_by_default(self):
        planning_fn = AsyncMock(return_value={
            "messages": [AIMessage(content="Plan created")],
        })

        subgraph = make_planning_subgraph(planning_fn)
        result = await subgraph(_state("create stories for auth"))
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.continuation is None

    @pytest.mark.asyncio
    async def test_execution_intent_detected(self):
        planning_fn = AsyncMock(return_value={
            "messages": [AIMessage(content="Plan ready")],
        })

        subgraph = make_planning_subgraph(planning_fn)
        result = await subgraph(_state("build it"))
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.continuation is not None
        assert output.continuation["next_intent"] == "code"


class TestDetectExecutionIntent:
    """Test _detect_execution_intent function directly."""

    def test_build_it(self):
        result = _detect_execution_intent({}, _state("build it"))
        assert result is not None
        assert result["next_intent"] == "code"

    def test_implement_it(self):
        result = _detect_execution_intent({}, _state("implement it"))
        assert result is not None

    def test_start_coding(self):
        result = _detect_execution_intent({}, _state("start coding"))
        assert result is not None

    def test_no_execution_signal(self):
        result = _detect_execution_intent({}, _state("looks good"))
        assert result is None

    def test_plan_request_no_execution(self):
        result = _detect_execution_intent({}, _state("plan the next feature"))
        assert result is None

    def test_empty_messages(self):
        result = _detect_execution_intent({}, {"messages": []})
        assert result is None


# ── Code subgraph tests ──────────────────────────────────────────────────


class TestCodeSubgraph:
    """Test code subgraph dispatch wrapper and continuation."""

    @pytest.mark.asyncio
    async def test_wraps_dispatch(self):
        dispatch_fn = AsyncMock(return_value={
            "messages": [AIMessage(content="Code executed")],
            "agent_result": AgentResult(agent="code", success=True, summary="Done"),
        })

        subgraph = make_code_subgraph(dispatch_fn)
        result = await subgraph(_state())

        dispatch_fn.assert_called_once()
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.response == "Code executed"
        assert output.source_subgraph == "code"

    @pytest.mark.asyncio
    async def test_no_continuation_on_failure(self):
        dispatch_fn = AsyncMock(return_value={
            "messages": [AIMessage(content="Error occurred")],
            "agent_result": AgentResult(agent="code", success=False, summary="Failed"),
        })

        subgraph = make_code_subgraph(dispatch_fn)
        result = await subgraph(_state())
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.continuation is None


class TestDetectCodeContinuation:
    """Test _detect_code_continuation function directly."""

    def test_no_agent_result(self):
        result = _detect_code_continuation({}, _state())
        assert result is None

    def test_failed_agent_no_continuation(self):
        agent_result = AgentResult(agent="code", success=False, summary="Failed")
        result = _detect_code_continuation(
            {"agent_result": agent_result}, _state(),
        )
        assert result is None

    def test_successful_with_auto_continue_objective(self):
        obj = Objective(
            title="Next step",
            status=ObjectiveStatus.PENDING,
            target_subgraph="code",
            context={"auto_continue": True},
        )
        agent_result = AgentResult(agent="code", success=True, summary="Done")
        result = _detect_code_continuation(
            {"agent_result": agent_result},
            _state(objectives=[obj]),
        )
        assert result is not None
        assert result["next_intent"] == "code"

    def test_successful_no_objectives(self):
        agent_result = AgentResult(agent="code", success=True, summary="Done")
        result = _detect_code_continuation(
            {"agent_result": agent_result}, _state(),
        )
        assert result is None
