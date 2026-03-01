"""Research Agent — deep analysis, document ingestion, and synthesis.

The Research Agent handles:
- Deep document ingestion and analysis
- Cross-referencing knowledge across domains
- Summarizing papers, experiments, and results
- Building SYNTHESIS.md files

It follows skill protocols from deep-ingest and related skills.
"""

from __future__ import annotations

import logging

from core.agents.base import BaseAgent
from core.config import GrimConfig
from core.state import AgentResult, GrimState
from core.tools.kronos_read import COMPANION_TOOLS
from core.tools.kronos_write import kronos_create, kronos_update
from core.tools.workspace import FILE_TOOLS

logger = logging.getLogger(__name__)


class ResearchAgent(BaseAgent):
    """Agent for research analysis and document ingestion."""

    agent_name = "research"

    def __init__(self, config: GrimConfig) -> None:
        # Research gets: file tools (for reading docs) + Kronos read/write (for FDO updates)
        tools = FILE_TOOLS + list(COMPANION_TOOLS) + [kronos_create, kronos_update]
        super().__init__(config=config, tools=tools)


def make_research_agent(config: GrimConfig):
    """Create a Research Agent callable for the dispatch node.

    Returns an async function that takes GrimState and returns AgentResult.
    """
    agent = ResearchAgent(config)

    async def research_agent_fn(state: GrimState, *, event_queue=None) -> AgentResult:
        """Execute a research task following skill protocols."""
        messages = state.get("messages", [])
        skill_protocols = state.get("skill_protocols", {})

        # Extract the user's request
        task = ""
        if messages:
            last_msg = messages[-1]
            task = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

        # Find the most relevant skill protocol
        protocol = None
        protocol_priority = ["deep-ingest", "kronos-recall"]

        for skill_name in protocol_priority:
            if skill_name in skill_protocols:
                protocol = skill_protocols[skill_name]
                logger.info("Research agent: using protocol '%s'", skill_name)
                break

        if protocol is None and skill_protocols:
            first_key = next(iter(skill_protocols))
            protocol = skill_protocols[first_key]

        if protocol is None:
            protocol = (
                "You are a research agent with Kronos vault access and file reading tools.\n"
                "Use kronos tools to search, retrieve, and analyze FDOs from the knowledge graph.\n"
                "Use file tools to read source material and documents.\n"
                "Always execute the task — do not say you can't do something "
                "if you have a tool that can do it."
            )

        # Build rich context for research tasks
        context = {}
        knowledge_context = state.get("knowledge_context", [])
        if knowledge_context:
            # Give research agent more detailed knowledge context
            fdo_details = []
            for fdo in knowledge_context[:8]:
                detail = f"{fdo.id} ({fdo.domain}, {fdo.status}): {fdo.summary[:150]}"
                if fdo.related:
                    detail += f" → related: {', '.join(fdo.related[:3])}"
                fdo_details.append(detail)
            context["relevant_knowledge"] = "\n".join(fdo_details)

        return await agent.execute(
            task=task,
            skill_protocol=protocol,
            context=context,
            event_queue=event_queue,
        )

    return research_agent_fn
