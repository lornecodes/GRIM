"""Tests for BaseAgent.make_callable and helpers."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import HumanMessage

from core.agents.base import BaseAgent
from core.state import FieldState, FDOSummary


class TestExtractTask:
    """Test BaseAgent._extract_task."""

    def test_extracts_from_human_message(self):
        state = {"messages": [HumanMessage(content="do something")]}
        assert BaseAgent._extract_task(state) == "do something"

    def test_empty_messages(self):
        assert BaseAgent._extract_task({"messages": []}) == ""

    def test_missing_messages_key(self):
        assert BaseAgent._extract_task({}) == ""

    def test_uses_last_message(self):
        state = {"messages": [
            HumanMessage(content="first"),
            HumanMessage(content="second"),
        ]}
        assert BaseAgent._extract_task(state) == "second"

    def test_non_message_object(self):
        """Should handle objects without .content by using str()."""
        state = {"messages": ["plain string"]}
        result = BaseAgent._extract_task(state)
        assert result == "plain string"

    def test_single_message(self):
        state = {"messages": [HumanMessage(content="only one")]}
        assert BaseAgent._extract_task(state) == "only one"


class TestFindProtocol:
    """Test BaseAgent._find_protocol."""

    def test_finds_by_priority(self):
        state = {
            "skill_protocols": {
                "kronos-recall": "recall protocol",
                "kronos-capture": "capture protocol",
            }
        }
        result = BaseAgent._find_protocol(
            state, ["kronos-capture", "kronos-recall"], "default"
        )
        assert result == "capture protocol"

    def test_priority_order_matters(self):
        """First matching priority entry wins."""
        state = {
            "skill_protocols": {
                "skill-a": "protocol A",
                "skill-b": "protocol B",
            }
        }
        # skill-b is first in priority list
        result = BaseAgent._find_protocol(state, ["skill-b", "skill-a"], "default")
        assert result == "protocol B"

    def test_fallback_to_first_available(self):
        """When no priority matches, uses first available protocol."""
        state = {"skill_protocols": {"some-skill": "some protocol"}}
        result = BaseAgent._find_protocol(state, ["not-here"], "default")
        assert result == "some protocol"

    def test_fallback_to_default(self):
        """When no protocols available at all, returns default."""
        state = {"skill_protocols": {}}
        result = BaseAgent._find_protocol(state, ["not-here"], "my default")
        assert result == "my default"

    def test_empty_priority_list(self):
        """Empty priority list falls back to first available."""
        state = {"skill_protocols": {"a": "protocol a"}}
        result = BaseAgent._find_protocol(state, [], "default")
        assert result == "protocol a"  # falls back to first available

    def test_missing_skill_protocols_key(self):
        """Missing skill_protocols key returns default."""
        state = {}
        result = BaseAgent._find_protocol(state, ["anything"], "fallback")
        assert result == "fallback"


class TestBuildContext:
    """Test BaseAgent.build_context."""

    def test_empty_knowledge(self):
        agent = BaseAgent.__new__(BaseAgent)  # skip __init__
        state = {"knowledge_context": []}
        assert agent.build_context(state) == {}

    def test_missing_knowledge_key(self):
        agent = BaseAgent.__new__(BaseAgent)
        state = {}
        assert agent.build_context(state) == {}

    def test_with_fdos(self):
        agent = BaseAgent.__new__(BaseAgent)
        fdos = [
            FDOSummary(id="pac", title="PAC", domain="physics",
                       status="stable", confidence=0.9, summary="PAC framework"),
            FDOSummary(id="sec", title="SEC", domain="physics",
                       status="stable", confidence=0.8, summary="SEC framework"),
        ]
        state = {"knowledge_context": fdos}
        ctx = agent.build_context(state)
        assert "relevant_fdos" in ctx
        assert "pac (physics)" in ctx["relevant_fdos"]
        assert "sec (physics)" in ctx["relevant_fdos"]

    def test_limits_to_5_fdos(self):
        agent = BaseAgent.__new__(BaseAgent)
        fdos = [
            FDOSummary(id=f"fdo-{i}", title=f"FDO {i}", domain="test",
                       status="stable", confidence=0.5, summary=f"FDO {i}")
            for i in range(10)
        ]
        state = {"knowledge_context": fdos}
        ctx = agent.build_context(state)
        # Should only include first 5
        assert "fdo-4 (test)" in ctx["relevant_fdos"]
        assert "fdo-5" not in ctx["relevant_fdos"]

    def test_single_fdo(self):
        agent = BaseAgent.__new__(BaseAgent)
        fdos = [
            FDOSummary(id="solo", title="Solo", domain="ai-systems",
                       status="developing", confidence=0.7, summary="Alone"),
        ]
        state = {"knowledge_context": fdos}
        ctx = agent.build_context(state)
        assert "solo (ai-systems)" in ctx["relevant_fdos"]


class TestProtocolPriorityOnSubclass:
    """Test that subclass protocol_priority and default_protocol work."""

    def test_memory_agent_priority(self):
        from core.agents.memory_agent import MemoryAgent
        assert "kronos-capture" in MemoryAgent.protocol_priority

    def test_coder_agent_priority(self):
        from core.agents.coder_agent import CoderAgent
        assert "code-execution" in CoderAgent.protocol_priority

    def test_research_agent_priority(self):
        from core.agents.research_agent import ResearchAgent
        assert "deep-ingest" in ResearchAgent.protocol_priority

    def test_operator_agent_priority(self):
        from core.agents.operator_agent import OperatorAgent
        assert len(OperatorAgent.protocol_priority) > 0

    def test_default_protocol_non_empty(self):
        from core.agents.memory_agent import MemoryAgent
        from core.agents.coder_agent import CoderAgent
        from core.agents.research_agent import ResearchAgent
        from core.agents.operator_agent import OperatorAgent
        for cls in [MemoryAgent, CoderAgent, ResearchAgent, OperatorAgent]:
            assert len(cls.default_protocol) > 0, f"{cls.__name__} has empty default_protocol"

    def test_base_agent_has_empty_defaults(self):
        """BaseAgent itself has empty protocol_priority and default_protocol."""
        assert BaseAgent.protocol_priority == []
        assert BaseAgent.default_protocol == ""


class TestMakeCallable:
    """Test BaseAgent.make_callable returns a usable async function."""

    def test_make_callable_returns_callable(self):
        """make_callable should return an async-compatible callable."""
        from core.agents.memory_agent import MemoryAgent

        with patch.object(MemoryAgent, "__init__", return_value=None):
            fn = MemoryAgent.make_callable(MagicMock())
            assert callable(fn)
            assert asyncio.iscoroutinefunction(fn)
