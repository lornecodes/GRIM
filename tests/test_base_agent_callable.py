"""Tests for BaseAgent.make_callable and helpers."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import AIMessage, HumanMessage

from core.agents.base import BaseAgent
from core.state import FieldState, FDOSummary


class TestExtractTask:
    """Test BaseAgent._extract_task with conversation context."""

    def test_extracts_from_single_message(self):
        """Single message — no context preamble, just the task."""
        state = {"messages": [HumanMessage(content="do something")]}
        result = BaseAgent._extract_task(state)
        assert result == "do something"

    def test_empty_messages(self):
        assert BaseAgent._extract_task({"messages": []}) == ""

    def test_missing_messages_key(self):
        assert BaseAgent._extract_task({}) == ""

    def test_single_message_no_context(self):
        """Only one message — should NOT include context block."""
        state = {"messages": [HumanMessage(content="only one")]}
        result = BaseAgent._extract_task(state)
        assert result == "only one"
        assert "CONVERSATION CONTEXT" not in result

    def test_multi_message_includes_context(self):
        """Multiple messages — should include context block."""
        state = {"messages": [
            HumanMessage(content="code me a simple webserver"),
            AIMessage(content="Sure, here's a simple Python webserver..."),
            HumanMessage(content="can you have ironclaw do that?"),
        ]}
        result = BaseAgent._extract_task(state)
        assert "CONVERSATION CONTEXT" in result
        assert "CURRENT REQUEST" in result
        assert "can you have ironclaw do that?" in result
        assert "simple webserver" in result

    def test_context_includes_prior_messages(self):
        """Context should include messages before the last one."""
        state = {"messages": [
            HumanMessage(content="first request"),
            AIMessage(content="first response"),
            HumanMessage(content="do that again"),
        ]}
        result = BaseAgent._extract_task(state)
        assert "[human]: first request" in result
        assert "[ai]: first response" in result
        assert "[CURRENT REQUEST]\ndo that again" in result

    def test_context_limited_to_6_messages(self):
        """Context window is capped at 6 prior messages (3 exchanges)."""
        messages = []
        for i in range(10):
            messages.append(HumanMessage(content=f"user msg {i}"))
            messages.append(AIMessage(content=f"ai msg {i}"))
        messages.append(HumanMessage(content="final request"))

        state = {"messages": messages}
        result = BaseAgent._extract_task(state)

        # Should NOT include very old messages
        assert "user msg 0" not in result
        # Should include recent messages (within last 6 before final)
        assert "CONVERSATION CONTEXT" in result
        assert "final request" in result

    def test_long_messages_truncated_in_context(self):
        """Long messages in context should be truncated at 300 chars."""
        long_content = "x" * 500
        state = {"messages": [
            HumanMessage(content=long_content),
            HumanMessage(content="short task"),
        ]}
        result = BaseAgent._extract_task(state)
        # The context should truncate at 300 + "..."
        assert "x" * 300 + "..." in result
        assert "x" * 400 not in result

    def test_non_message_object(self):
        """Should handle objects without .content by using str()."""
        state = {"messages": ["plain string"]}
        result = BaseAgent._extract_task(state)
        assert "plain string" in result

    def test_anaphoric_reference_preserved(self):
        """The core use case: 'do that' should carry context about what 'that' is."""
        state = {"messages": [
            HumanMessage(content="write a fibonacci function in Python"),
            AIMessage(content="Here's a fibonacci implementation..."),
            HumanMessage(content="now test that"),
        ]}
        result = BaseAgent._extract_task(state)
        assert "fibonacci" in result
        assert "now test that" in result

    def test_two_messages_includes_context(self):
        """Even just 2 messages should include context from the first."""
        state = {"messages": [
            HumanMessage(content="build a REST API"),
            HumanMessage(content="use FastAPI"),
        ]}
        result = BaseAgent._extract_task(state)
        assert "REST API" in result
        assert "use FastAPI" in result

    def test_multiblock_content_handled(self):
        """Messages with list content (cache_control blocks) are handled."""
        msg = HumanMessage(content=[
            {"type": "text", "text": "system instructions here"},
            {"type": "text", "text": "more instructions"},
        ])
        state = {"messages": [
            msg,
            HumanMessage(content="do the thing"),
        ]}
        result = BaseAgent._extract_task(state)
        assert "system instructions" in result
        assert "do the thing" in result


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


class TestMetadata:
    """Test BaseAgent.metadata() and display attributes."""

    def test_base_agent_defaults(self):
        """BaseAgent has sensible metadata defaults."""
        assert BaseAgent.agent_display_name == ""
        assert BaseAgent.agent_role == ""
        assert BaseAgent.agent_color == "#6b7280"
        assert BaseAgent.agent_tier == "grim"
        assert BaseAgent.agent_toggleable is False

    def test_metadata_returns_dict(self):
        """metadata() returns a dict with all expected keys."""
        agent = BaseAgent.__new__(BaseAgent)
        agent.agent_name = "test"
        agent.agent_display_name = "Test Agent"
        agent.agent_role = "testing"
        agent.agent_description = "A test agent"
        agent.agent_color = "#ff0000"
        agent.agent_tier = "grim"
        agent.agent_toggleable = False
        agent.tools = []

        meta = agent.metadata()
        assert meta["id"] == "test"
        assert meta["name"] == "Test Agent"
        assert meta["role"] == "testing"
        assert meta["description"] == "A test agent"
        assert meta["tools"] == []
        assert meta["color"] == "#ff0000"
        assert meta["tier"] == "grim"
        assert meta["toggleable"] is False

    def test_metadata_uses_display_name_fallback(self):
        """If agent_display_name is empty, title-case agent_name."""
        agent = BaseAgent.__new__(BaseAgent)
        agent.agent_name = "memory"
        agent.agent_display_name = ""
        agent.agent_role = ""
        agent.agent_description = ""
        agent.default_protocol = "You are a memory agent.\nMore stuff."
        agent.agent_color = "#6b7280"
        agent.agent_tier = "grim"
        agent.agent_toggleable = False
        agent.tools = []

        meta = agent.metadata()
        assert meta["name"] == "Memory"
        assert meta["description"] == "You are a memory agent."

    def test_metadata_includes_tool_names(self):
        """metadata() includes actual tool names from the tool list."""
        agent = BaseAgent.__new__(BaseAgent)
        agent.agent_name = "test"
        agent.agent_display_name = ""
        agent.agent_role = ""
        agent.agent_description = "desc"
        agent.default_protocol = ""
        agent.agent_color = "#000"
        agent.agent_tier = "grim"
        agent.agent_toggleable = False

        mock_tool = MagicMock()
        mock_tool.name = "kronos_search"
        agent.tools = [mock_tool]

        meta = agent.metadata()
        assert meta["tools"] == ["kronos_search"]

    @pytest.mark.parametrize("agent_mod,cls_name,expected_name", [
        ("core.agents.memory_agent", "MemoryAgent", "Memory"),
        ("core.agents.research_agent", "ResearchAgent", "Researcher"),
        ("core.agents.codebase_agent", "CodebaseAgent", "Codebase"),
        ("core.agents.operator_agent", "OperatorAgent", "Operator"),
        ("core.agents.coder_agent", "CoderAgent", "Coder"),
        ("core.agents.ironclaw_agent", "IronClawAgent", "IronClaw"),
        ("core.agents.audit_agent", "AuditAgent", "Audit"),
    ])
    def test_all_agents_have_display_name(self, agent_mod, cls_name, expected_name):
        """Every agent subclass declares a display name."""
        import importlib
        mod = importlib.import_module(agent_mod)
        cls = getattr(mod, cls_name)
        assert cls.agent_display_name == expected_name

    def test_ironclaw_tier_agents_are_toggleable(self):
        """IronClaw and Audit agents are toggleable."""
        from core.agents.ironclaw_agent import IronClawAgent
        from core.agents.audit_agent import AuditAgent
        assert IronClawAgent.agent_toggleable is True
        assert IronClawAgent.agent_tier == "ironclaw"
        assert AuditAgent.agent_toggleable is True
        assert AuditAgent.agent_tier == "ironclaw"

    def test_grim_tier_agents_not_toggleable(self):
        """GRIM-tier agents are not toggleable."""
        from core.agents.memory_agent import MemoryAgent
        from core.agents.research_agent import ResearchAgent
        from core.agents.codebase_agent import CodebaseAgent
        for cls in [MemoryAgent, ResearchAgent, CodebaseAgent]:
            assert cls.agent_toggleable is False
            assert cls.agent_tier == "grim"


class TestMakeCallable:
    """Test BaseAgent.make_callable returns a usable async function."""

    def test_make_callable_returns_callable(self):
        """make_callable should return an async-compatible callable."""
        from core.agents.memory_agent import MemoryAgent

        with patch.object(MemoryAgent, "__init__", return_value=None):
            fn = MemoryAgent.make_callable(MagicMock())
            assert callable(fn)
            assert asyncio.iscoroutinefunction(fn)
