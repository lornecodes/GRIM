"""Base agent — common infrastructure for all doer agents."""

from __future__ import annotations

import logging
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from core.config import GrimConfig
from core.state import AgentResult

logger = logging.getLogger(__name__)


class BaseAgent:
    """Base class for GRIM doer agents.

    Each agent receives:
    1. A skill protocol as its system prompt (instructions for HOW to do the task)
    2. A set of tools it's allowed to use
    3. The user's request (extracted by Router/Companion)

    Agents execute and return AgentResult.
    """

    agent_name: str = "base"

    def __init__(self, config: GrimConfig, tools: list[BaseTool]) -> None:
        self.config = config
        self.tools = tools
        self.llm = ChatAnthropic(
            model=config.model,
            temperature=0.3,  # lower temp for agents — precision matters
            max_tokens=config.max_tokens,
            default_headers={"X-Caller-ID": "grim"},
        )
        if tools:
            self.llm_with_tools = self.llm.bind_tools(tools)
        else:
            self.llm_with_tools = self.llm

    async def execute(
        self,
        task: str,
        skill_protocol: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Execute a task following the skill protocol.

        Args:
            task: What needs to be done (from Router/Companion).
            skill_protocol: Full protocol.md content as instructions.
            context: Additional context (knowledge, matched skills, etc.).

        Returns:
            AgentResult with success/failure and summary.
        """
        # Build system prompt from skill protocol
        system_parts = [f"You are the {self.agent_name} agent for GRIM."]

        if skill_protocol:
            system_parts.append(
                f"\n## Skill Protocol (follow these instructions)\n\n{skill_protocol}"
            )

        if context:
            ctx_str = "\n".join(f"- {k}: {v}" for k, v in context.items() if v)
            if ctx_str:
                system_parts.append(f"\n## Context\n\n{ctx_str}")

        system_prompt = "\n".join(system_parts)

        # Pass system prompt via messages (not the API system field).
        # CLIProxyAPI injects "You are Claude Code..." into the system field;
        # the proxy config filters it out, so we use messages instead.
        messages = [
            HumanMessage(content=f"[SYSTEM INSTRUCTIONS — follow exactly]\n{system_prompt}\n[END SYSTEM INSTRUCTIONS]"),
            AIMessage(content=f"Understood. I am the {self.agent_name} agent and will follow these instructions."),
            HumanMessage(content=task),
        ]

        logger.info(
            "%s agent: executing task '%s' (protocol: %s)",
            self.agent_name,
            task[:80],
            "yes" if skill_protocol else "no",
        )

        try:
            # Agent tool-calling loop (max 10 tool calls per task)
            for step in range(10):
                response = await self.llm_with_tools.ainvoke(messages)
                messages.append(response)

                # Check for tool calls
                if hasattr(response, "tool_calls") and response.tool_calls:
                    for tool_call in response.tool_calls:
                        tool_result = await self._execute_tool(tool_call)
                        messages.append(tool_result)
                else:
                    # No more tool calls — agent is done
                    break

            # Extract final response
            final_content = response.content if hasattr(response, "content") else str(response)

            return AgentResult(
                agent=self.agent_name,
                success=True,
                summary=final_content[:500],
            )

        except Exception as exc:
            logger.exception("%s agent: task failed", self.agent_name)
            return AgentResult(
                agent=self.agent_name,
                success=False,
                summary=f"Failed: {exc}",
            )

    async def _execute_tool(self, tool_call: Any) -> Any:
        """Execute a single tool call and return the result message."""
        from langchain_core.messages import ToolMessage

        tool_name = tool_call["name"]
        tool_args = tool_call["args"]

        logger.debug("%s agent: calling tool %s", self.agent_name, tool_name)

        # Find the matching tool
        for t in self.tools:
            if t.name == tool_name:
                try:
                    result = await t.ainvoke(tool_args)
                    return ToolMessage(
                        content=str(result),
                        tool_call_id=tool_call["id"],
                    )
                except Exception as exc:
                    return ToolMessage(
                        content=f"Tool error: {exc}",
                        tool_call_id=tool_call["id"],
                    )

        return ToolMessage(
            content=f"Unknown tool: {tool_name}",
            tool_call_id=tool_call["id"],
        )
