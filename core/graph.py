"""GRIM State Graph — wire all nodes into the LangGraph state machine.

This is the central wiring file. It builds the full graph:

    identity → memory → skill_match → router → [companion | dispatch] → integrate → evolve

The graph is stateful, checkpointed to SQLite, and supports multi-turn
conversation with session persistence.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from core.agents.coder_agent import make_coder_agent
from core.agents.memory_agent import make_memory_agent
from core.agents.operator_agent import make_operator_agent
from core.agents.research_agent import make_research_agent
from core.config import GrimConfig
from core.nodes.companion import make_companion_node
from core.nodes.compress import make_compress_node
from core.nodes.dispatch import make_dispatch_node
from core.nodes.evolve import make_evolve_node
from core.nodes.identity import make_identity_node
from core.nodes.integrate import integrate_node
from core.nodes.memory import make_memory_node
from core.nodes.router import make_router_node, route_decision
from core.nodes.skill_match import make_skill_match_node
from core.skills.loader import load_skills
from core.skills.registry import SkillRegistry
from core.state import GrimState

logger = logging.getLogger(__name__)


def build_graph(
    config: GrimConfig,
    mcp_session: Any = None,
    checkpointer: Any = None,
    reasoning_cache: Any = None,
) -> Any:
    """Build and compile the GRIM state graph.

    Args:
        config: Resolved runtime configuration.
        mcp_session: MCP client session for Kronos (None in debug mode).
        reasoning_cache: ReasoningCache instance (optional, for companion node).
        checkpointer: LangGraph checkpointer for persistence (default: MemorySaver).

    Returns:
        Compiled LangGraph graph ready for invocation.
    """
    # Load skills at boot
    skill_registry = load_skills(config.skills_path)
    logger.info("Skills loaded: %s", skill_registry)

    # Set MCP session for tools
    if mcp_session:
        from core.tools.kronos_read import set_mcp_session
        set_mcp_session(mcp_session)

    # Create node closures with config/dependencies
    identity_fn = make_identity_node(config, mcp_session)
    compress_fn = make_compress_node(config)
    memory_fn = make_memory_node(mcp_session)
    skill_match_fn = make_skill_match_node(skill_registry)
    router_fn = make_router_node(config)
    companion_fn = make_companion_node(config, reasoning_cache=reasoning_cache)
    evolve_fn = make_evolve_node(config)

    # Create all four doer agents
    memory_agent_fn = make_memory_agent(config)
    coder_agent_fn = make_coder_agent(config)
    research_agent_fn = make_research_agent(config)
    operator_agent_fn = make_operator_agent(config)

    agents = {
        "memory": memory_agent_fn,
        "code": coder_agent_fn,
        "research": research_agent_fn,
        "operate": operator_agent_fn,
    }
    dispatch_fn = make_dispatch_node(agents)

    logger.info("Agents registered: %s", list(agents.keys()))

    # Build the state graph
    graph = StateGraph(GrimState)

    # Add nodes
    graph.add_node("identity", identity_fn)
    graph.add_node("compress", compress_fn)
    graph.add_node("memory", memory_fn)
    graph.add_node("skill_match", skill_match_fn)
    graph.add_node("router", router_fn)
    graph.add_node("companion", companion_fn)
    graph.add_node("dispatch", dispatch_fn)
    graph.add_node("integrate", integrate_node)
    graph.add_node("evolve", evolve_fn)

    # Wire edges: identity → compress → memory → skill_match → router
    graph.set_entry_point("identity")
    graph.add_edge("identity", "compress")
    graph.add_edge("compress", "memory")
    graph.add_edge("memory", "skill_match")
    graph.add_edge("skill_match", "router")

    # Conditional branch at router: companion OR dispatch
    graph.add_conditional_edges(
        "router",
        route_decision,
        {"companion": "companion", "dispatch": "dispatch"},
    )

    # Both paths converge at integrate
    graph.add_edge("companion", "integrate")
    graph.add_edge("dispatch", "integrate")

    # integrate → evolve → END
    graph.add_edge("integrate", "evolve")
    graph.add_edge("evolve", END)

    # Compile with checkpointer
    if checkpointer is None:
        checkpointer = MemorySaver()

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("GRIM state graph compiled — 9 nodes, 4 agents")

    return compiled
