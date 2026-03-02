"""GRIM State Graph — wire all nodes into the LangGraph state machine.

The graph is built from composable sections. Each section adds a group
of related nodes and edges. This makes it possible to build different
graph configurations for different interaction modes.

v0.0.6 graph (multi-graph architecture):
    identity → compress → memory → skill_match → graph_router →
        [personal: personal_companion → integrate → evolve → END]
        [research: router → [companion | dispatch] → audit_gate →
            [audit | integrate] → evolve → END]
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from core.agents.registry import AgentRegistry
from core.bridge.ironclaw import IronClawBridge
from core.config import GrimConfig
from core.nodes.audit_gate import audit_gate_decision, audit_gate_node
from core.nodes.companion import make_companion_node
from core.nodes.compress import make_compress_node
from core.nodes.dispatch import make_dispatch_node
from core.nodes.evolve import make_evolve_node
from core.nodes.graph_router import graph_route_decision, graph_router_node
from core.nodes.identity import make_identity_node
from core.nodes.integrate import integrate_node
from core.nodes.memory import make_memory_node
from core.nodes.personal_companion import make_personal_companion_node
from core.nodes.planning_companion import make_planning_companion_node
from core.nodes.re_dispatch import audit_decision, re_dispatch_node
from core.nodes.router import make_router_node, route_decision
from core.nodes.skill_match import make_skill_match_node
from core.skills.loader import load_skills
from core.state import GrimState

logger = logging.getLogger(__name__)


# ── Composable graph sections ───────────────────────────────────────────

def add_preprocessing(
    graph: StateGraph,
    config: GrimConfig,
    mcp_session: Any,
    skill_registry: Any,
    reasoning_cache: Any = None,
) -> dict[str, Any]:
    """Add preprocessing nodes: identity → compress → memory → skill_match.

    These nodes prepare the state before routing decisions are made.
    Returns a dict of created node functions for reference.
    """
    identity_fn = make_identity_node(config, mcp_session)
    compress_fn = make_compress_node(config)
    memory_fn = make_memory_node(mcp_session)
    skill_match_fn = make_skill_match_node(skill_registry, config=config)

    graph.add_node("identity", identity_fn)
    graph.add_node("compress", compress_fn)
    graph.add_node("memory", memory_fn)
    graph.add_node("skill_match", skill_match_fn)

    graph.set_entry_point("identity")
    graph.add_edge("identity", "compress")
    graph.add_edge("compress", "memory")
    graph.add_edge("memory", "skill_match")

    return {
        "identity": identity_fn,
        "compress": compress_fn,
        "memory": memory_fn,
        "skill_match": skill_match_fn,
    }


def add_graph_routing(graph: StateGraph) -> dict[str, Any]:
    """Add graph-level routing: graph_router → [research | personal].

    Sits after preprocessing (skill_match), before graph-specific nodes.
    Routes to specialized graph pipelines based on user intent.
    """
    graph.add_node("graph_router", graph_router_node)
    graph.add_edge("skill_match", "graph_router")

    graph.add_conditional_edges(
        "graph_router",
        graph_route_decision,
        {
            "research": "router",
            "personal": "personal_companion",
            "planning": "planning_companion",
        },
    )

    return {"graph_router": graph_router_node}


def add_routing(
    graph: StateGraph,
    config: GrimConfig,
    reasoning_cache: Any = None,
) -> dict[str, Any]:
    """Add research graph routing: router → [companion | dispatch].

    Companion handles thinking; dispatch delegates to agents.
    The graph_router feeds into this section via the "research" edge.
    """
    router_fn = make_router_node(config)
    companion_fn = make_companion_node(config, reasoning_cache=reasoning_cache)

    graph.add_node("router", router_fn)
    graph.add_node("companion", companion_fn)

    # Router branches: companion (think) or dispatch (delegate)
    graph.add_conditional_edges(
        "router",
        route_decision,
        {"companion": "companion", "dispatch": "dispatch"},
    )

    return {"router": router_fn, "companion": companion_fn}


def add_personal_graph(
    graph: StateGraph,
    config: GrimConfig,
    reasoning_cache: Any = None,
) -> dict[str, Any]:
    """Add personal companion graph branch.

    Personal mode: personality-forward, no delegation, straight to integrate.
    """
    personal_fn = make_personal_companion_node(config, reasoning_cache=reasoning_cache)
    graph.add_node("personal_companion", personal_fn)
    graph.add_edge("personal_companion", "integrate")

    return {"personal_companion": personal_fn}


def add_planning_graph(
    graph: StateGraph,
    config: GrimConfig,
    reasoning_cache: Any = None,
) -> dict[str, Any]:
    """Add planning companion graph branch.

    Planning mode: work breakdown, scoping, draft creation, board population.
    Has full task tools + vault read. Goes straight to integrate after responding.
    """
    planning_fn = make_planning_companion_node(config, reasoning_cache=reasoning_cache)
    graph.add_node("planning_companion", planning_fn)
    graph.add_edge("planning_companion", "integrate")

    return {"planning_companion": planning_fn}


def add_agents(
    graph: StateGraph,
    agents: dict[str, Any],
) -> dict[str, Any]:
    """Add agent execution nodes: dispatch → audit_gate → [audit | integrate].

    Includes the zero-trust audit pipeline for IronClaw dispatches.
    """
    dispatch_fn = make_dispatch_node(agents)
    audit_agent_fn = agents.get("audit")

    graph.add_node("dispatch", dispatch_fn)
    graph.add_node("audit_gate", audit_gate_node)
    graph.add_node("audit", audit_agent_fn)
    graph.add_node("re_dispatch", re_dispatch_node)

    # Dispatch → audit gate
    graph.add_edge("dispatch", "audit_gate")

    # Audit gate: IronClaw + artifacts → audit, else → integrate
    graph.add_conditional_edges(
        "audit_gate",
        audit_gate_decision,
        {"audit": "audit", "skip": "integrate"},
    )

    # Audit decision: pass → integrate, fail → re_dispatch, escalate → integrate
    graph.add_conditional_edges(
        "audit",
        audit_decision,
        {"pass": "integrate", "fail": "re_dispatch", "escalate": "integrate"},
    )

    # Re-dispatch loops back
    graph.add_edge("re_dispatch", "dispatch")

    return {"dispatch": dispatch_fn, "audit": audit_agent_fn}


def add_postprocessing(
    graph: StateGraph,
    config: GrimConfig,
    mcp_session: Any = None,
) -> dict[str, Any]:
    """Add postprocessing nodes: integrate → evolve → END.

    Companion and agent results are integrated and the session evolves.
    """
    evolve_fn = make_evolve_node(config, mcp_session=mcp_session)

    graph.add_node("integrate", integrate_node)
    graph.add_node("evolve", evolve_fn)

    # Companion → integrate (no audit needed)
    graph.add_edge("companion", "integrate")

    # integrate → evolve → END
    graph.add_edge("integrate", "evolve")
    graph.add_edge("evolve", END)

    return {"integrate": integrate_node, "evolve": evolve_fn}


# ── Main graph builder ──────────────────────────────────────────────────

def build_graph(
    config: GrimConfig,
    mcp_session: Any = None,
    checkpointer: Any = None,
    reasoning_cache: Any = None,
    ironclaw_bridge: IronClawBridge | None = None,
) -> Any:
    """Build and compile the GRIM state graph.

    Composes preprocessing, routing, agent, and postprocessing sections
    into the full graph. Each section can be used independently for
    different graph configurations.

    Args:
        config: Resolved runtime configuration.
        mcp_session: MCP client session for Kronos (None in debug mode).
        reasoning_cache: ReasoningCache instance (optional, for companion node).
        checkpointer: LangGraph checkpointer for persistence (default: MemorySaver).
        ironclaw_bridge: IronClaw bridge for sandboxed execution.

    Returns:
        Compiled LangGraph graph ready for invocation.
    """
    # Load skills at boot
    skill_registry = load_skills(config.skills_path)
    logger.info("Skills loaded: %s", skill_registry)

    # Configure tool dependencies (single injection point)
    from core.tools.context import tool_context
    tool_context.configure(
        mcp_session=mcp_session,
        ironclaw_bridge=ironclaw_bridge,
    )

    # Auto-discover agents (skip disabled ones)
    disabled = list(config.agents_disabled)
    if not ironclaw_bridge:
        disabled.append("ironclaw")

    agent_registry = AgentRegistry.discover(config, disabled=disabled)
    agents = {
        name: factory(config)
        for name, factory in agent_registry.all().items()
    }

    if ironclaw_bridge and "ironclaw" in agents:
        logger.info("IronClaw agent registered (bridge: %s)", ironclaw_bridge.base_url)

    logger.info("Agents registered: %s", list(agents.keys()))

    # Build the state graph from composable sections
    graph = StateGraph(GrimState)

    add_preprocessing(graph, config, mcp_session, skill_registry, reasoning_cache)
    add_graph_routing(graph)
    add_routing(graph, config, reasoning_cache)
    add_personal_graph(graph, config, reasoning_cache)
    add_planning_graph(graph, config, reasoning_cache)
    add_agents(graph, agents)
    add_postprocessing(graph, config, mcp_session)

    # Compile with checkpointer
    if checkpointer is None:
        checkpointer = MemorySaver()

    compiled = graph.compile(checkpointer=checkpointer)
    agent_count = len(agents)
    logger.info("GRIM state graph compiled — 15 nodes, %d agents", agent_count)

    return compiled
