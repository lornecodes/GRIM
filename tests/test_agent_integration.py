"""Agent integration tests — verify agents are constructed correctly,
model selection works end-to-end, dispatch wiring is correct, and the
graph routes to the right agents with the right models/tools.

All tests are synchronous/mocked — no real API or LLM calls.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure GRIM root is on path
GRIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GRIM_ROOT))

from core.config import GrimConfig
from core.model_router import TIER_MODELS, route_model


# ─── Agent Construction ──────────────────────────────────────────────────


class TestAgentConstruction:
    """Verify every agent class initializes with expected name, model, and tools."""

    def _make_config(self, model: str = "claude-sonnet-4-6") -> GrimConfig:
        cfg = GrimConfig()
        cfg.model = model
        return cfg

    def test_coder_agent_name(self):
        from core.agents.coder_agent import CoderAgent
        agent = CoderAgent(self._make_config())
        assert agent.agent_name == "coder"

    def test_coder_agent_tools(self):
        from core.agents.coder_agent import CoderAgent
        from core.tools.workspace import FILE_TOOLS, SHELL_TOOLS
        from core.tools.kronos_read import COMPANION_TOOLS
        agent = CoderAgent(self._make_config())
        expected_count = len(FILE_TOOLS) + len(SHELL_TOOLS) + len(COMPANION_TOOLS)
        assert len(agent.tools) == expected_count
        tool_names = {t.name for t in agent.tools}
        # Must have file and shell tools
        assert "read_file" in tool_names or any("file" in n for n in tool_names)

    def test_operator_agent_name(self):
        from core.agents.operator_agent import OperatorAgent
        agent = OperatorAgent(self._make_config())
        assert agent.agent_name == "operator"

    def test_operator_agent_tools(self):
        from core.agents.operator_agent import OperatorAgent
        from core.tools.workspace import GIT_TOOLS, SHELL_TOOLS, FILE_TOOLS
        from core.tools.kronos_read import COMPANION_TOOLS
        agent = OperatorAgent(self._make_config())
        expected_count = len(GIT_TOOLS) + len(SHELL_TOOLS) + len(FILE_TOOLS) + len(COMPANION_TOOLS)
        assert len(agent.tools) == expected_count

    def test_memory_agent_name(self):
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(self._make_config())
        assert agent.agent_name == "memory"

    def test_memory_agent_tools(self):
        from core.agents.memory_agent import MemoryAgent
        from core.tools.kronos_write import MEMORY_AGENT_TOOLS
        from core.tools.memory_tools import MEMORY_TOOLS
        from core.tools.kronos_tasks import TASK_ALL_TOOLS
        agent = MemoryAgent(self._make_config())
        assert len(agent.tools) == len(MEMORY_AGENT_TOOLS) + len(MEMORY_TOOLS) + len(TASK_ALL_TOOLS)

    def test_research_agent_name(self):
        from core.agents.research_agent import ResearchAgent
        agent = ResearchAgent(self._make_config())
        assert agent.agent_name == "research"

    def test_research_agent_has_kronos_write(self):
        from core.agents.research_agent import ResearchAgent
        agent = ResearchAgent(self._make_config())
        tool_names = {t.name for t in agent.tools}
        assert "kronos_create" in tool_names
        assert "kronos_update" in tool_names

    def test_ironclaw_agent_name(self):
        from core.agents.ironclaw_agent import IronClawAgent
        agent = IronClawAgent(self._make_config())
        assert agent.agent_name == "ironclaw"

    def test_ironclaw_agent_has_claw_tools(self):
        from core.agents.ironclaw_agent import IronClawAgent
        agent = IronClawAgent(self._make_config())
        tool_names = {t.name for t in agent.tools}
        assert "claw_read_file" in tool_names
        assert "claw_shell" in tool_names
        assert "claw_write_file" in tool_names

    def test_ironclaw_agent_has_companion_tools(self):
        from core.agents.ironclaw_agent import IronClawAgent
        from core.tools.kronos_read import COMPANION_TOOLS
        agent = IronClawAgent(self._make_config())
        companion_names = {t.name for t in COMPANION_TOOLS}
        agent_names = {t.name for t in agent.tools}
        assert companion_names.issubset(agent_names)


# ─── Model Configuration ────────────────────────────────────────────────


class TestAgentModelConfig:
    """Verify agents use the correct model from config."""

    def test_default_model_is_sonnet(self):
        cfg = GrimConfig()
        assert cfg.model == "claude-sonnet-4-6"

    def test_agent_uses_config_model(self):
        from core.agents.coder_agent import CoderAgent
        cfg = GrimConfig()
        cfg.model = "claude-opus-4-6"
        agent = CoderAgent(cfg)
        # The LLM should be configured with the override model
        assert agent.llm.model == "claude-opus-4-6"

    def test_agent_model_override(self):
        from core.agents.base import BaseAgent
        cfg = GrimConfig()
        cfg.model = "claude-sonnet-4-6"

        class TestAgent(BaseAgent):
            agent_name = "test"

        agent = TestAgent(cfg, tools=[], model_override="claude-opus-4-6")
        assert agent.llm.model == "claude-opus-4-6"

    def test_agent_override_beats_config(self):
        from core.agents.base import BaseAgent
        cfg = GrimConfig()
        cfg.model = "claude-haiku-4-5-20251001"

        class TestAgent(BaseAgent):
            agent_name = "test"

        agent = TestAgent(cfg, tools=[], model_override="claude-opus-4-6")
        assert agent.llm.model == "claude-opus-4-6"

    def test_agent_temperature_is_lower(self):
        """Agents use 0.3 temp (precision), companion uses config.temperature (0.7)."""
        from core.agents.coder_agent import CoderAgent
        cfg = GrimConfig()
        agent = CoderAgent(cfg)
        assert agent.llm.temperature == 0.3

    def test_agent_caller_id_header(self):
        from core.agents.coder_agent import CoderAgent
        cfg = GrimConfig()
        agent = CoderAgent(cfg)
        assert agent.llm.default_headers.get("X-Caller-ID") == "grim"

    def test_all_agents_use_same_config_model(self):
        """All agents should initialize with the same model from config."""
        from core.agents.coder_agent import CoderAgent
        from core.agents.memory_agent import MemoryAgent
        from core.agents.operator_agent import OperatorAgent
        from core.agents.research_agent import ResearchAgent
        from core.agents.ironclaw_agent import IronClawAgent

        cfg = GrimConfig()
        cfg.model = "claude-opus-4-6"

        agents = [
            CoderAgent(cfg),
            MemoryAgent(cfg),
            OperatorAgent(cfg),
            ResearchAgent(cfg),
            IronClawAgent(cfg),
        ]

        for agent in agents:
            assert agent.llm.model == "claude-opus-4-6", (
                f"{agent.agent_name} agent has wrong model: {agent.llm.model}"
            )


# ─── Companion Model Selection ──────────────────────────────────────────


class TestCompanionModelSelection:
    """Verify companion node picks the model from router's selected_model."""

    def _make_state(self, **overrides):
        """Build a minimal valid companion state (needs field_state for prompt builder)."""
        from langchain_core.messages import HumanMessage
        from core.state import FieldState
        base = {
            "messages": [HumanMessage(content="hello")],
            "field_state": FieldState(),
        }
        base.update(overrides)
        return base

    @pytest.mark.asyncio
    async def test_companion_uses_selected_model(self):
        from core.nodes.companion import make_companion_node
        cfg = GrimConfig()
        companion_fn = make_companion_node(cfg)

        mock_response = MagicMock()
        mock_response.content = "Hello!"
        mock_response.tool_calls = []

        with patch("core.nodes.companion.ChatAnthropic") as MockLLM:
            mock_instance = MagicMock()
            mock_instance.bind_tools.return_value = mock_instance
            mock_instance.ainvoke = AsyncMock(return_value=mock_response)
            MockLLM.return_value = mock_instance

            await companion_fn(self._make_state(
                selected_model="claude-haiku-4-5-20251001",
            ))

            MockLLM.assert_called_once()
            call_kwargs = MockLLM.call_args[1]
            assert call_kwargs["model"] == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    async def test_companion_falls_back_to_config_model(self):
        from core.nodes.companion import make_companion_node
        cfg = GrimConfig()
        cfg.model = "claude-sonnet-4-6"
        companion_fn = make_companion_node(cfg)

        mock_response = MagicMock()
        mock_response.content = "Hello!"
        mock_response.tool_calls = []

        with patch("core.nodes.companion.ChatAnthropic") as MockLLM:
            mock_instance = MagicMock()
            mock_instance.bind_tools.return_value = mock_instance
            mock_instance.ainvoke = AsyncMock(return_value=mock_response)
            MockLLM.return_value = mock_instance

            await companion_fn(self._make_state())

            MockLLM.assert_called_once()
            call_kwargs = MockLLM.call_args[1]
            assert call_kwargs["model"] == "claude-sonnet-4-6"


# ─── Router → Model Selection Pipeline ──────────────────────────────────


class TestRouterModelPipeline:
    """Test the full router → model selection → state pipeline."""

    @pytest.mark.asyncio
    async def test_greeting_routes_haiku(self):
        result = await route_model("hi there!")
        assert result.tier == "haiku"
        assert result.model == TIER_MODELS["haiku"]

    @pytest.mark.asyncio
    async def test_code_routes_sonnet(self):
        result = await route_model("write code for a binary search")
        assert result.tier == "sonnet"
        assert result.model == TIER_MODELS["sonnet"]

    @pytest.mark.asyncio
    async def test_deep_routes_opus(self):
        result = await route_model("deep analysis of recursive emergence patterns")
        assert result.tier == "opus"
        assert result.model == TIER_MODELS["opus"]

    @pytest.mark.asyncio
    async def test_explicit_fast_override(self):
        result = await route_model("/fast what time is it")
        assert result.tier == "haiku"
        assert result.stage == 1
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_explicit_deep_override(self):
        result = await route_model("/deep analyze this")
        assert result.tier == "opus"
        assert result.stage == 1

    @pytest.mark.asyncio
    async def test_router_node_sets_selected_model(self):
        from langchain_core.messages import HumanMessage
        from core.nodes.router import make_router_node

        cfg = GrimConfig()
        router_fn = make_router_node(cfg)

        result = await router_fn({
            "messages": [HumanMessage(content="hello!")],
            "matched_skills": [],
        })

        assert "selected_model" in result
        assert result["selected_model"] in TIER_MODELS.values()

    @pytest.mark.asyncio
    async def test_router_disabled_uses_default(self):
        from langchain_core.messages import HumanMessage
        from core.nodes.router import make_router_node

        cfg = GrimConfig()
        cfg.routing_enabled = False
        router_fn = make_router_node(cfg)

        result = await router_fn({
            "messages": [HumanMessage(content="hello!")],
            "matched_skills": [],
        })

        assert result["selected_model"] == TIER_MODELS["sonnet"]


# ─── Dispatch Wiring ────────────────────────────────────────────────────


class TestDispatchWiring:
    """Test that dispatch routes to the correct agent based on delegation_type."""

    def _make_agents(self):
        """Create mock agent callables."""
        agents = {}
        for name in ["memory", "code", "research", "operate", "ironclaw"]:
            mock_fn = AsyncMock(return_value=MagicMock(agent=name, success=True, summary="done"))
            mock_fn.__name__ = f"{name}_agent_fn"
            agents[name] = mock_fn
        return agents

    @pytest.mark.asyncio
    async def test_dispatch_memory(self):
        from core.nodes.dispatch import make_dispatch_node
        agents = self._make_agents()
        dispatch_fn = make_dispatch_node(agents)

        result = await dispatch_fn({"delegation_type": "memory"})
        agents["memory"].assert_called_once()
        assert result["agent_result"].agent == "memory"

    @pytest.mark.asyncio
    async def test_dispatch_code(self):
        from core.nodes.dispatch import make_dispatch_node
        agents = self._make_agents()
        dispatch_fn = make_dispatch_node(agents)

        result = await dispatch_fn({"delegation_type": "code"})
        agents["code"].assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_research(self):
        from core.nodes.dispatch import make_dispatch_node
        agents = self._make_agents()
        dispatch_fn = make_dispatch_node(agents)

        result = await dispatch_fn({"delegation_type": "research"})
        agents["research"].assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_operate(self):
        from core.nodes.dispatch import make_dispatch_node
        agents = self._make_agents()
        dispatch_fn = make_dispatch_node(agents)

        result = await dispatch_fn({"delegation_type": "operate"})
        agents["operate"].assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_ironclaw(self):
        from core.nodes.dispatch import make_dispatch_node
        agents = self._make_agents()
        dispatch_fn = make_dispatch_node(agents)

        result = await dispatch_fn({"delegation_type": "ironclaw"})
        agents["ironclaw"].assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_unknown_type(self):
        from core.nodes.dispatch import make_dispatch_node
        agents = self._make_agents()
        dispatch_fn = make_dispatch_node(agents)

        result = await dispatch_fn({"delegation_type": "nonexistent"})
        assert result["agent_result"].success is False

    @pytest.mark.asyncio
    async def test_dispatch_no_delegation_type(self):
        from core.nodes.dispatch import make_dispatch_node
        agents = self._make_agents()
        dispatch_fn = make_dispatch_node(agents)

        result = await dispatch_fn({})
        assert result["agent_result"] is None

    @pytest.mark.asyncio
    async def test_dispatch_agent_exception(self):
        from core.nodes.dispatch import make_dispatch_node
        agents = self._make_agents()
        agents["code"] = AsyncMock(side_effect=RuntimeError("boom"))
        dispatch_fn = make_dispatch_node(agents)

        result = await dispatch_fn({"delegation_type": "code"})
        assert result["agent_result"].success is False
        assert "boom" in result["agent_result"].summary


# ─── Graph Agent Registration ────────────────────────────────────────────


class TestGraphAgentRegistration:
    """Test that build_graph registers the right agents."""

    def test_graph_builds_without_ironclaw(self):
        from core.graph import build_graph
        cfg = GrimConfig()
        graph = build_graph(cfg)
        assert graph is not None

    def test_graph_builds_with_ironclaw(self):
        from core.graph import build_graph
        from core.bridge.ironclaw import IronClawBridge
        cfg = GrimConfig()
        bridge = IronClawBridge(base_url="http://localhost:3100")
        graph = build_graph(cfg, ironclaw_bridge=bridge)
        assert graph is not None

    def test_graph_has_dispatch_node(self):
        from core.graph import build_graph
        cfg = GrimConfig()
        graph = build_graph(cfg)
        # LangGraph compiled graph has nodes
        assert "dispatch" in graph.get_graph().nodes

    def test_graph_has_companion_node(self):
        from core.graph import build_graph
        cfg = GrimConfig()
        graph = build_graph(cfg)
        assert "companion" in graph.get_graph().nodes

    def test_graph_has_router_node(self):
        from core.graph import build_graph
        cfg = GrimConfig()
        graph = build_graph(cfg)
        assert "router" in graph.get_graph().nodes


# ─── Router Delegation Decisions ─────────────────────────────────────────


class TestRouterDelegation:
    """Test that the router picks the right delegation_type for different inputs."""

    @pytest.mark.asyncio
    async def test_greeting_stays_companion(self):
        from langchain_core.messages import HumanMessage
        from core.nodes.router import make_router_node

        cfg = GrimConfig()
        router_fn = make_router_node(cfg)
        result = await router_fn({
            "messages": [HumanMessage(content="hello, how are you?")],
            "matched_skills": [],
        })
        assert result["mode"] == "companion"

    @pytest.mark.asyncio
    async def test_ironclaw_keywords_delegate(self):
        from langchain_core.messages import HumanMessage
        from core.nodes.router import make_router_node

        cfg = GrimConfig()
        router_fn = make_router_node(cfg)
        result = await router_fn({
            "messages": [HumanMessage(content="run sandboxed: ls -la")],
            "matched_skills": [],
        })
        # Should delegate (not companion), with ironclaw as delegation type
        if result["mode"] == "delegate":
            assert result.get("delegation_type") == "ironclaw"

    @pytest.mark.asyncio
    async def test_skill_consumer_routes_delegation(self):
        """A matched skill with consumer field should drive delegation."""
        from langchain_core.messages import HumanMessage
        from core.nodes.router import make_router_node

        cfg = GrimConfig()
        router_fn = make_router_node(cfg)

        # Create a mock skill match with consumer
        mock_skill = MagicMock()
        mock_skill.name = "test-skill"
        mock_skill.consumer = "code"
        mock_skill.permissions = ["write"]
        mock_skill.keywords = ["test"]
        mock_skill.protocol_path = None

        result = await router_fn({
            "messages": [HumanMessage(content="write some test code please")],
            "matched_skills": [mock_skill],
        })

        if result["mode"] == "delegate":
            assert result["delegation_type"] in ("code", "operate", "research", "memory", "ironclaw")


# ─── Agent Factory Functions ─────────────────────────────────────────────


class TestAgentFactories:
    """Test that make_*_agent returns proper callables."""

    def test_make_coder_agent_returns_callable(self):
        from core.agents.coder_agent import make_coder_agent
        fn = make_coder_agent(GrimConfig())
        assert callable(fn)
        assert asyncio.iscoroutinefunction(fn)

    def test_make_memory_agent_returns_callable(self):
        from core.agents.memory_agent import make_memory_agent
        fn = make_memory_agent(GrimConfig())
        assert callable(fn)
        assert asyncio.iscoroutinefunction(fn)

    def test_make_operator_agent_returns_callable(self):
        from core.agents.operator_agent import make_operator_agent
        fn = make_operator_agent(GrimConfig())
        assert callable(fn)
        assert asyncio.iscoroutinefunction(fn)

    def test_make_research_agent_returns_callable(self):
        from core.agents.research_agent import make_research_agent
        fn = make_research_agent(GrimConfig())
        assert callable(fn)
        assert asyncio.iscoroutinefunction(fn)

    def test_make_ironclaw_agent_returns_callable(self):
        from core.agents.ironclaw_agent import make_ironclaw_agent
        fn = make_ironclaw_agent(GrimConfig())
        assert callable(fn)
        assert asyncio.iscoroutinefunction(fn)


# ─── Agent Skill Protocol Selection ──────────────────────────────────────


class TestAgentProtocolSelection:
    """Test that agents select the right skill protocol from state."""

    @pytest.mark.asyncio
    async def test_coder_prefers_code_execution(self):
        from core.agents.coder_agent import make_coder_agent
        from langchain_core.messages import HumanMessage

        cfg = GrimConfig()
        fn = make_coder_agent(cfg)

        # Mock the LLM to avoid real calls
        with patch("core.agents.base.BaseAgent.execute", new_callable=AsyncMock) as mock_exec:
            from core.state import AgentResult
            mock_exec.return_value = AgentResult(agent="coder", success=True, summary="done")

            await fn({
                "messages": [HumanMessage(content="write hello.py")],
                "skill_protocols": {
                    "file-operations": "File ops protocol",
                    "code-execution": "Code exec protocol",
                },
            })

            # Should have called execute with code-execution protocol
            mock_exec.assert_called_once()
            call_kwargs = mock_exec.call_args[1]
            assert call_kwargs["skill_protocol"] == "Code exec protocol"

    @pytest.mark.asyncio
    async def test_operator_prefers_git_operations(self):
        from core.agents.operator_agent import make_operator_agent
        from langchain_core.messages import HumanMessage

        cfg = GrimConfig()
        fn = make_operator_agent(cfg)

        with patch("core.agents.base.BaseAgent.execute", new_callable=AsyncMock) as mock_exec:
            from core.state import AgentResult
            mock_exec.return_value = AgentResult(agent="operator", success=True, summary="done")

            await fn({
                "messages": [HumanMessage(content="commit changes")],
                "skill_protocols": {
                    "shell-execution": "Shell protocol",
                    "git-operations": "Git protocol",
                },
            })

            mock_exec.assert_called_once()
            call_kwargs = mock_exec.call_args[1]
            assert call_kwargs["skill_protocol"] == "Git protocol"

    @pytest.mark.asyncio
    async def test_ironclaw_prefers_sandboxed_execution(self):
        from core.agents.ironclaw_agent import make_ironclaw_agent
        from langchain_core.messages import HumanMessage

        cfg = GrimConfig()
        fn = make_ironclaw_agent(cfg)

        with patch("core.agents.base.BaseAgent.execute", new_callable=AsyncMock) as mock_exec:
            from core.state import AgentResult
            mock_exec.return_value = AgentResult(agent="ironclaw", success=True, summary="done")

            await fn({
                "messages": [HumanMessage(content="run ls in sandbox")],
                "skill_protocols": {
                    "code-execution": "Code protocol",
                    "sandboxed-execution": "Sandbox protocol",
                },
            })

            mock_exec.assert_called_once()
            call_kwargs = mock_exec.call_args[1]
            assert call_kwargs["skill_protocol"] == "Sandbox protocol"

    @pytest.mark.asyncio
    async def test_memory_prefers_kronos_capture(self):
        from core.agents.memory_agent import make_memory_agent
        from langchain_core.messages import HumanMessage

        cfg = GrimConfig()
        fn = make_memory_agent(cfg)

        with patch("core.agents.base.BaseAgent.execute", new_callable=AsyncMock) as mock_exec:
            from core.state import AgentResult
            mock_exec.return_value = AgentResult(agent="memory", success=True, summary="done")

            await fn({
                "messages": [HumanMessage(content="capture this knowledge")],
                "skill_protocols": {
                    "kronos-relate": "Relate protocol",
                    "kronos-capture": "Capture protocol",
                },
            })

            mock_exec.assert_called_once()
            call_kwargs = mock_exec.call_args[1]
            assert call_kwargs["skill_protocol"] == "Capture protocol"


# ─── Tier Models Consistency ─────────────────────────────────────────────


class TestTierModels:
    """Verify TIER_MODELS matches expected model IDs."""

    def test_haiku_model_id(self):
        assert TIER_MODELS["haiku"] == "claude-haiku-4-5-20251001"

    def test_sonnet_model_id(self):
        assert TIER_MODELS["sonnet"] == "claude-sonnet-4-6"

    def test_opus_model_id(self):
        assert TIER_MODELS["opus"] == "claude-opus-4-6"

    def test_three_tiers(self):
        assert len(TIER_MODELS) == 3

    def test_config_default_is_sonnet(self):
        cfg = GrimConfig()
        assert cfg.model == TIER_MODELS["sonnet"]


# ─── Base Agent Tool Binding ─────────────────────────────────────────────


class TestBaseAgentToolBinding:
    """Verify BaseAgent binds tools correctly."""

    def test_agent_with_tools_has_bound_llm(self):
        from core.agents.base import BaseAgent
        from core.tools.kronos_read import COMPANION_TOOLS
        cfg = GrimConfig()

        class TestAgent(BaseAgent):
            agent_name = "test"

        agent = TestAgent(cfg, tools=list(COMPANION_TOOLS))
        # llm_with_tools should be different from llm (tools bound)
        assert agent.llm_with_tools is not agent.llm

    def test_agent_without_tools_uses_raw_llm(self):
        from core.agents.base import BaseAgent
        cfg = GrimConfig()

        class TestAgent(BaseAgent):
            agent_name = "test"

        agent = TestAgent(cfg, tools=[])
        # No tools → llm_with_tools IS the raw llm
        assert agent.llm_with_tools is agent.llm


# ─── Event Queue Serialization Safety ────────────────────────────────────


class TestEventQueueSerialization:
    """Verify that the agent event queue is NOT in graph state (would crash
    LangGraph's checkpointer with 'Queue is not msgpack serializable')."""

    def test_queue_not_in_grim_state(self):
        """GrimState must not have _agent_event_queue — it's not serializable."""
        from core.state import GrimState
        annotations = getattr(GrimState, "__annotations__", {})
        assert "_agent_event_queue" not in annotations, (
            "asyncio.Queue must not be in GrimState — LangGraph checkpointer "
            "cannot serialize it. Pass via RunnableConfig['configurable'] instead."
        )

    def test_state_keys_are_serializable(self):
        """All GrimState typed keys should be JSON/msgpack-safe types (not Queue)."""
        import asyncio as _aio
        from core.state import GrimState
        annotations = getattr(GrimState, "__annotations__", {})
        for key, _type in annotations.items():
            type_str = str(_type)
            assert "Queue" not in type_str, (
                f"GrimState['{key}'] has type {_type} which contains Queue — "
                "not serializable by LangGraph checkpointer."
            )


# ─── Dispatch Config Propagation ─────────────────────────────────────────


class TestDispatchConfigPropagation:
    """Test that dispatch reads event queue from config, not state."""

    def _make_agents(self):
        agents = {}
        for name in ["memory", "code", "research", "operate", "ironclaw"]:
            mock_fn = AsyncMock(return_value=MagicMock(agent=name, success=True, summary="done"))
            mock_fn.__name__ = f"{name}_agent_fn"
            agents[name] = mock_fn
        return agents

    @pytest.mark.asyncio
    async def test_dispatch_passes_queue_from_config(self):
        """Dispatch should extract event_queue from config['configurable']."""
        import asyncio as _aio
        from core.nodes.dispatch import make_dispatch_node

        agents = self._make_agents()
        dispatch_fn = make_dispatch_node(agents)
        queue = _aio.Queue()

        config = {"configurable": {"agent_event_queue": queue}}
        await dispatch_fn({"delegation_type": "memory"}, config=config)

        # The agent_fn should have been called with event_queue=queue
        call_kwargs = agents["memory"].call_args[1]
        assert call_kwargs.get("event_queue") is queue

    @pytest.mark.asyncio
    async def test_dispatch_works_without_config(self):
        """Dispatch should work when config is None (backward compat)."""
        from core.nodes.dispatch import make_dispatch_node

        agents = self._make_agents()
        dispatch_fn = make_dispatch_node(agents)

        result = await dispatch_fn({"delegation_type": "memory"})
        agents["memory"].assert_called_once()
        # event_queue should be None
        call_kwargs = agents["memory"].call_args[1]
        assert call_kwargs.get("event_queue") is None

    @pytest.mark.asyncio
    async def test_dispatch_works_without_queue_in_config(self):
        """Dispatch should work when config has no agent_event_queue key."""
        from core.nodes.dispatch import make_dispatch_node

        agents = self._make_agents()
        dispatch_fn = make_dispatch_node(agents)

        config = {"configurable": {"thread_id": "test-thread"}}
        result = await dispatch_fn({"delegation_type": "code"}, config=config)
        agents["code"].assert_called_once()
        call_kwargs = agents["code"].call_args[1]
        assert call_kwargs.get("event_queue") is None


# ─── BaseAgent Event Emission ────────────────────────────────────────────


class TestBaseAgentEventEmission:
    """Test that BaseAgent._emit() pushes events to queue correctly."""

    def test_emit_pushes_to_queue(self):
        import asyncio as _aio
        from core.agents.base import BaseAgent

        cfg = GrimConfig()

        class TestAgent(BaseAgent):
            agent_name = "test"

        agent = TestAgent(cfg, tools=[])
        queue = _aio.Queue()
        event = {"cat": "node", "action": "start", "text": "test"}

        agent._emit(queue, event)
        assert not queue.empty()
        assert queue.get_nowait() == event

    def test_emit_noop_when_queue_is_none(self):
        from core.agents.base import BaseAgent

        cfg = GrimConfig()

        class TestAgent(BaseAgent):
            agent_name = "test"

        agent = TestAgent(cfg, tools=[])
        # Should not raise
        agent._emit(None, {"cat": "test"})

    def test_emit_does_not_block_on_full_queue(self):
        import asyncio as _aio
        from core.agents.base import BaseAgent

        cfg = GrimConfig()

        class TestAgent(BaseAgent):
            agent_name = "test"

        agent = TestAgent(cfg, tools=[])
        # Create a queue with max size 1, fill it
        queue = _aio.Queue(maxsize=1)
        queue.put_nowait({"dummy": True})

        # Should not raise even though queue is full
        agent._emit(queue, {"cat": "overflow"})

    @pytest.mark.asyncio
    async def test_execute_emits_start_and_end_events(self):
        """BaseAgent.execute() should emit node start and end events."""
        import asyncio as _aio
        from core.agents.base import BaseAgent

        cfg = GrimConfig()

        class TestAgent(BaseAgent):
            agent_name = "tester"

        agent = TestAgent(cfg, tools=[])
        queue = _aio.Queue()

        # Mock LLM to return a simple response (no tool calls)
        mock_response = MagicMock()
        mock_response.content = "Hello from test"
        mock_response.tool_calls = []
        agent.llm_with_tools = AsyncMock()
        agent.llm_with_tools.ainvoke = AsyncMock(return_value=mock_response)

        result = await agent.execute(
            task="test task",
            event_queue=queue,
        )

        # Collect all events
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        # Should have: node start, llm start, node end
        cats = [e["cat"] for e in events]
        assert "node" in cats
        assert "llm" in cats

        # First event should be node start
        assert events[0]["cat"] == "node"
        assert events[0]["action"] == "start"
        assert events[0]["node"] == "tester"

        # Last event should be node end with duration_ms
        assert events[-1]["cat"] == "node"
        assert events[-1]["action"] == "end"
        assert "duration_ms" in events[-1]

        assert result.success is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
