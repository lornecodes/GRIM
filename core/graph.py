"""GRIM State Graph — wire all nodes into the LangGraph state machine.

The graph is built from composable sections. Each section adds a group
of related nodes and edges. This makes it possible to build different
graph configurations for different interaction modes.

v0.0.6 graph (keyword routing):
    identity → compress → memory → skill_match → graph_router →
        [personal: personal_companion → integrate → evolve → END]
        [research: router → [companion | dispatch] → audit_gate →
            [audit | integrate] → evolve → END]

v0.10 graph (companion router — use_companion_router=True):
    identity → compress → memory → skill_match → companion_router →
        [conversation | planning | research | code] →
        response_generator → [continue: companion_router | exit: integrate] →
        integrate → evolve → END
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
    output_node: str = "integrate",
) -> dict[str, Any]:
    """Add personal companion graph branch.

    Personal mode: personality-forward, no delegation, straight to output_node.
    output_node is "integrate" (v0.0.6) or "response_generator" (v0.10).
    """
    personal_fn = make_personal_companion_node(config, reasoning_cache=reasoning_cache)
    graph.add_node("personal_companion", personal_fn)
    graph.add_edge("personal_companion", output_node)

    return {"personal_companion": personal_fn}


def add_planning_graph(
    graph: StateGraph,
    config: GrimConfig,
    reasoning_cache: Any = None,
    output_node: str = "integrate",
) -> dict[str, Any]:
    """Add planning companion graph branch.

    Planning mode: work breakdown, scoping, draft creation, board population.
    Has full task tools + vault read. Goes straight to output_node after responding.
    output_node is "integrate" (v0.0.6) or "response_generator" (v0.10).
    """
    planning_fn = make_planning_companion_node(config, reasoning_cache=reasoning_cache)
    graph.add_node("planning_companion", planning_fn)
    graph.add_edge("planning_companion", output_node)

    return {"planning_companion": planning_fn}


def add_companion_routing(
    graph: StateGraph,
    config: GrimConfig,
) -> dict[str, Any]:
    """Add v0.10 companion router: single LLM-backed routing node.

    Replaces both add_graph_routing() and add_routing() with a single
    companion_router node that uses structured output intent classification.
    Routes to subgraph wrapper nodes: conversation | planning | research | code.

    Requires config.use_companion_router = True.
    """
    from core.nodes.companion_router import (
        companion_route_decision,
        make_companion_router_node,
    )

    router_fn = make_companion_router_node(config)
    graph.add_node("companion_router", router_fn)
    graph.add_edge("skill_match", "companion_router")

    graph.add_conditional_edges(
        "companion_router",
        companion_route_decision,
        {
            "conversation": "conversation",
            "planning": "planning",
            "research": "research",
            "code": "code",
        },
    )

    return {"companion_router": router_fn}


def add_v10_subgraphs(
    graph: StateGraph,
    config: GrimConfig,
    reasoning_cache: Any,
    agents: dict[str, Any],
) -> dict[str, Any]:
    """Add v0.10 subgraph wrapper nodes.

    Creates all 4 subgraph wrappers and wires them to response_generator:
      - conversation: wraps companion + personal_companion
      - planning: wraps planning_companion
      - research: wraps dispatch (research/codebase/memory agents)
      - code: wraps dispatch (ironclaw/code agents)

    Each subgraph produces SubgraphOutput for the Response Generator loop.
    No graph-level audit in v0.10 — audit will move inside code subgraph later.
    """
    from core.subgraphs.code import make_code_subgraph
    from core.subgraphs.conversation import make_conversation_subgraph
    from core.subgraphs.planning import make_planning_subgraph
    from core.subgraphs.research import make_research_subgraph

    # Create underlying node functions
    companion_fn = make_companion_node(config, reasoning_cache=reasoning_cache)
    personal_fn = make_personal_companion_node(config, reasoning_cache=reasoning_cache)
    planning_fn = make_planning_companion_node(config, reasoning_cache=reasoning_cache)
    dispatch_fn = make_dispatch_node(agents)

    # Create subgraph wrappers
    conv_sg = make_conversation_subgraph(companion_fn, personal_fn)
    plan_sg = make_planning_subgraph(planning_fn)
    res_sg = make_research_subgraph(dispatch_fn)
    code_sg = make_code_subgraph(dispatch_fn)

    # Add as graph nodes
    graph.add_node("conversation", conv_sg)
    graph.add_node("planning", plan_sg)
    graph.add_node("research", res_sg)
    graph.add_node("code", code_sg)

    # All subgraphs → response_generator
    graph.add_edge("conversation", "response_generator")
    graph.add_edge("planning", "response_generator")
    graph.add_edge("research", "response_generator")
    graph.add_edge("code", "response_generator")

    return {
        "conversation": conv_sg,
        "planning": plan_sg,
        "research": res_sg,
        "code": code_sg,
    }


def add_response_generator(
    graph: StateGraph,
    router_node_name: str = "companion_router",
) -> dict[str, Any]:
    """Add v0.10 Response Generator with loop/exit control.

    The Response Generator is the loop heartbeat:
      - All subgraph outputs flow through it
      - Updates objectives, context_stack, subgraph_history
      - Decides: loop back to router OR exit to integrate

    Edges:
      companion → response_generator
      personal_companion → response_generator
      planning_companion → response_generator
      audit_gate skip → response_generator (instead of integrate)
      audit pass/escalate → response_generator (instead of integrate)
      response_generator → [continue: router | exit: integrate]
    """
    from core.nodes.response_generator import (
        make_response_generator_node,
        response_generator_decision,
    )

    rg_fn = make_response_generator_node()
    graph.add_node("response_generator", rg_fn)

    graph.add_conditional_edges(
        "response_generator",
        response_generator_decision,
        {
            "continue": router_node_name,
            "exit": "integrate",
        },
    )

    return {"response_generator": rg_fn}


def add_agents(
    graph: StateGraph,
    agents: dict[str, Any],
    output_node: str = "integrate",
) -> dict[str, Any]:
    """Add agent execution nodes: dispatch → audit_gate → [audit | output_node].

    Includes the zero-trust audit pipeline for IronClaw dispatches.
    output_node is "integrate" (v0.0.6) or "response_generator" (v0.10).
    """
    dispatch_fn = make_dispatch_node(agents)
    audit_agent_fn = agents.get("audit")

    graph.add_node("dispatch", dispatch_fn)
    graph.add_node("audit_gate", audit_gate_node)
    graph.add_node("audit", audit_agent_fn)
    graph.add_node("re_dispatch", re_dispatch_node)

    # Dispatch → audit gate
    graph.add_edge("dispatch", "audit_gate")

    # Audit gate: IronClaw + artifacts → audit, else → output_node
    graph.add_conditional_edges(
        "audit_gate",
        audit_gate_decision,
        {"audit": "audit", "skip": output_node},
    )

    # Audit decision: pass → output_node, fail → re_dispatch, escalate → output_node
    graph.add_conditional_edges(
        "audit",
        audit_decision,
        {"pass": output_node, "fail": "re_dispatch", "escalate": output_node},
    )

    # Re-dispatch loops back
    graph.add_edge("re_dispatch", "dispatch")

    return {"dispatch": dispatch_fn, "audit": audit_agent_fn}


def add_postprocessing(
    graph: StateGraph,
    config: GrimConfig,
    mcp_session: Any = None,
    companion_output_node: str = "integrate",
) -> dict[str, Any]:
    """Add postprocessing nodes: integrate → evolve → END.

    Companion and agent results are integrated and the session evolves.
    companion_output_node is "integrate" (v0.0.6) or "response_generator" (v0.10).
    """
    evolve_fn = make_evolve_node(config, mcp_session=mcp_session)

    graph.add_node("integrate", integrate_node)
    graph.add_node("evolve", evolve_fn)

    # Companion → output_node (integrate in v0.0.6, response_generator in v0.10)
    graph.add_edge("companion", companion_output_node)

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

    if config.use_companion_router:
        # v0.10: LLM-backed router → subgraph wrappers → response generator loop
        #
        # Topology:
        #   identity → compress → memory → skill_match → companion_router →
        #     [conversation | planning | research | code] →
        #     response_generator → [continue: companion_router | exit: integrate] →
        #     integrate → evolve → END
        #
        # No graph-level audit pipeline in v0.10 — audit will be added
        # inside the code subgraph in a later iteration.
        add_companion_routing(graph, config)
        add_response_generator(graph, router_node_name="companion_router")
        add_v10_subgraphs(graph, config, reasoning_cache, agents)

        # Simplified postprocessing — no standalone companion edge
        evolve_fn = make_evolve_node(config, mcp_session=mcp_session)
        graph.add_node("integrate", integrate_node)
        graph.add_node("evolve", evolve_fn)
        graph.add_edge("integrate", "evolve")
        graph.add_edge("evolve", END)

        node_count = 11  # identity compress memory skill_match companion_router
                         # conversation planning research code response_generator
                         # integrate evolve
        logger.info("Using v0.10 companion router (LLM-backed intent classification)")
    else:
        # v0.0.6: keyword-based two-stage routing (no response generator)
        add_graph_routing(graph)
        add_routing(graph, config, reasoning_cache)
        add_personal_graph(graph, config, reasoning_cache)
        add_planning_graph(graph, config, reasoning_cache)
        add_agents(graph, agents)
        add_postprocessing(graph, config, mcp_session)
        node_count = 15

    # Compile with checkpointer
    if checkpointer is None:
        checkpointer = MemorySaver()

    compiled = graph.compile(checkpointer=checkpointer)
    agent_count = len(agents)
    logger.info("GRIM state graph compiled — %d nodes, %d agents", node_count, agent_count)

    return compiled
