"""Research Agent — deep analysis, document ingestion, and synthesis."""
from __future__ import annotations

import logging

from core.agents.base import BaseAgent
from core.config import GrimConfig
from core.tools.kronos_read import COMPANION_TOOLS
from core.tools.workspace import FILE_READ_TOOLS

logger = logging.getLogger(__name__)


class ResearchAgent(BaseAgent):
    """Agent for research analysis and document ingestion (read-only)."""

    agent_name = "research"
    protocol_priority = ["deep-ingest", "kronos-recall"]
    default_protocol = (
        "You are a research agent with Kronos vault read access and file reading tools.\n"
        "Use kronos tools to search, retrieve, and analyze FDOs from the knowledge graph.\n"
        "Use file tools to read source material and documents.\n"
        "Analyze, synthesize, and report. Vault writes go through the Memory agent.\n"
        "Always execute the task — do not say you can't do something "
        "if you have a tool that can do it."
    )

    def __init__(self, config: GrimConfig) -> None:
        tools = list(FILE_READ_TOOLS) + list(COMPANION_TOOLS)
        super().__init__(config=config, tools=tools)

    def build_context(self, state: dict) -> dict:
        """Research agent gets more detailed FDO context."""
        context = {}
        knowledge_context = state.get("knowledge_context", [])
        if knowledge_context:
            fdo_details = []
            for fdo in knowledge_context[:8]:
                detail = f"{fdo.id} ({fdo.domain}, {fdo.status}): {fdo.summary[:150]}"
                if fdo.related:
                    detail += f" → related: {', '.join(fdo.related[:3])}"
                fdo_details.append(detail)
            context["relevant_knowledge"] = "\n".join(fdo_details)
        return context


def make_research_agent(config: GrimConfig):
    """Create a Research Agent callable for the dispatch node."""
    return ResearchAgent.make_callable(config)


# Discovery attributes for AgentRegistry
__agent_name__ = "research"
__make_agent__ = make_research_agent
