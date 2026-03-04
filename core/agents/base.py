"""Base agent — common infrastructure for all doer agents."""

from __future__ import annotations

import asyncio
import logging
import time as _time
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from core.config import GrimConfig
from core.state import AgentResult, FDOSummary

logger = logging.getLogger(__name__)


def _merge_knowledge_sources(state: dict) -> list[FDOSummary]:
    """Merge per-turn knowledge_context with accumulated session_knowledge.

    Returns a deduplicated list with per-turn entries first (fresh),
    then session entries not already in per-turn set.
    """
    knowledge_context = state.get("knowledge_context", [])
    session_knowledge = state.get("session_knowledge", [])

    if not session_knowledge:
        return list(knowledge_context)

    per_turn_ids = {fdo.id for fdo in knowledge_context}
    merged = list(knowledge_context)

    for entry in session_knowledge:
        if entry.fdo.id not in per_turn_ids:
            merged.append(entry.fdo)
            per_turn_ids.add(entry.fdo.id)

    return merged


class BaseAgent:
    """Base class for GRIM doer agents.

    Each agent receives:
    1. A skill protocol as its system prompt (instructions for HOW to do the task)
    2. A set of tools it's allowed to use
    3. The user's request (extracted by Router/Companion)

    Agents execute and return AgentResult.
    """

    agent_name: str = "base"
    protocol_priority: list[str] = []
    default_protocol: str = ""

    # UI display metadata — set on subclasses
    agent_display_name: str = ""
    agent_role: str = ""
    agent_description: str = ""
    agent_color: str = "#6b7280"
    agent_tier: str = "grim"       # "grim" or "ironclaw"
    agent_toggleable: bool = False  # can be enabled/disabled from UI

    def metadata(self) -> dict:
        """Return UI-ready metadata dict for the agent roster API."""
        return {
            "id": self.agent_name,
            "name": self.agent_display_name or self.agent_name.title(),
            "role": self.agent_role,
            "description": self.agent_description or self.default_protocol.split("\n")[0],
            "tools": [t.name for t in self.tools],
            "tools_detail": [
                {"name": t.name, "description": (t.description or "").split("\n")[0]}
                for t in self.tools
            ],
            "color": self.agent_color,
            "tier": self.agent_tier,
            "toggleable": self.agent_toggleable,
            "protocol_priority": list(self.protocol_priority),
            "default_protocol": self.default_protocol,
            "temperature": 0.3,
            "max_tool_steps": 10,
            "model": self.config.model if hasattr(self, "config") else None,
        }

    def __init__(
        self,
        config: GrimConfig,
        tools: list[BaseTool],
        model_override: str | None = None,
    ) -> None:
        self.config = config
        self.tools = tools
        model = model_override or config.model
        self.llm = ChatAnthropic(
            model=model,
            temperature=0.3,  # lower temp for agents — precision matters
            max_tokens=config.max_tokens,
            default_headers={"X-Caller-ID": "grim"},
        )
        logger.info("%s agent: using model %s", self.agent_name, model)
        if tools:
            self.llm_with_tools = self.llm.bind_tools(tools)
        else:
            self.llm_with_tools = self.llm

    def _emit(self, queue: asyncio.Queue | None, event: dict) -> None:
        """Push a trace event onto the live-monitoring queue (non-blocking)."""
        if queue is not None:
            try:
                queue.put_nowait(event)
            except Exception:
                pass  # never block agent execution for monitoring

    async def execute(
        self,
        task: str,
        skill_protocol: str | None = None,
        context: dict[str, Any] | None = None,
        event_queue: asyncio.Queue | None = None,
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

        # Explicitly list available tools so the agent knows its capabilities
        if self.tools:
            tool_lines = []
            for t in self.tools:
                desc = t.description.split("\n")[0] if t.description else ""
                tool_lines.append(f"  - **{t.name}**: {desc}")
            system_parts.append(
                "\n## Your Tools (USE THEM)\n\n"
                "You have the following tools available. Use them to accomplish your task.\n"
                + "\n".join(tool_lines)
            )

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
        # cache_control on the system prompt block enables Anthropic prompt
        # caching — the static instructions are cached for 5 min.
        messages = [
            HumanMessage(content=[
                {
                    "type": "text",
                    "text": f"[SYSTEM INSTRUCTIONS — follow exactly]\n{system_prompt}\n[END SYSTEM INSTRUCTIONS]",
                    "cache_control": {"type": "ephemeral"},
                },
            ]),
            AIMessage(content=f"Understood. I am the {self.agent_name} agent and will follow these instructions."),
            HumanMessage(content=task),
        ]

        logger.info(
            "%s agent: executing task '%s' (protocol: %s)",
            self.agent_name,
            task[:80],
            "yes" if skill_protocol else "no",
        )

        _t0 = _time.monotonic()
        self._emit(event_queue, {
            "cat": "node", "node": self.agent_name, "action": "start",
            "text": f"→ {self.agent_name}",
        })

        try:
            # Agent tool-calling loop (max 10 tool calls per task)
            for step in range(10):
                self._emit(event_queue, {
                    "cat": "llm", "node": self.agent_name, "action": "start",
                    "text": f"LLM call (step {step + 1})",
                })

                # Trim context to prevent bloat while preserving tool_call_id
                # chains (Anthropic API rejects orphaned ToolMessages).
                # Strategy: keep head (3 system msgs) + last complete
                # AI→Tool exchange pair + the most recent AI response.
                if len(messages) > 9:
                    head = messages[:3]  # system + ack + task
                    rest = messages[3:]

                    # Walk backward to find complete tool exchange pairs.
                    # Keep only the last AI+Tool pair to maintain valid chain.
                    tail: list = []
                    i = len(rest) - 1
                    kept_tokens = 0
                    while i >= 0 and kept_tokens < 4:
                        tail.insert(0, rest[i])
                        kept_tokens += 1
                        i -= 1

                    # Validate: strip leading ToolMessages with no matching
                    # AIMessage (their tool_call_id would be orphaned).
                    from langchain_core.messages import ToolMessage
                    valid_tool_ids: set = set()
                    for m in head + tail:
                        if hasattr(m, "tool_calls") and m.tool_calls:
                            for tc in m.tool_calls:
                                valid_tool_ids.add(tc.get("id", ""))

                    tail = [
                        m for m in tail
                        if not isinstance(m, ToolMessage)
                        or getattr(m, "tool_call_id", "") in valid_tool_ids
                    ]

                    dropped = len(messages) - len(head) - len(tail)
                    if dropped > 0:
                        summary_msg = HumanMessage(
                            content=f"[{dropped} earlier messages trimmed. "
                            f"Continue with the task based on recent results.]"
                        )
                        messages = head + [summary_msg] + tail
                    else:
                        messages = head + tail

                try:
                    response = await self.llm_with_tools.ainvoke(messages)
                except ValueError as ve:
                    # "No generations found in stream" — transient API error.
                    # Retry once; if it fails again, let it propagate.
                    if "No generations found" in str(ve) and step < 9:
                        logger.warning("%s agent: empty stream on step %d, retrying", self.agent_name, step + 1)
                        import asyncio as _aio
                        await _aio.sleep(1)
                        response = await self.llm_with_tools.ainvoke(messages)
                    else:
                        raise
                messages.append(response)

                # Check for tool calls
                if hasattr(response, "tool_calls") and response.tool_calls:
                    for tool_call in response.tool_calls:
                        tool_name = tool_call["name"]
                        tool_input = tool_call.get("args", {})
                        self._emit(event_queue, {
                            "cat": "tool", "node": self.agent_name, "action": "start",
                            "text": f"tool: {tool_name}",
                            "tool": tool_name,
                            "input": str(tool_input)[:200],
                        })

                        tool_result = await self._execute_tool(tool_call)
                        messages.append(tool_result)

                        output_preview = str(tool_result.content)[:200] if hasattr(tool_result, "content") else ""
                        self._emit(event_queue, {
                            "cat": "tool", "node": self.agent_name, "action": "end",
                            "text": f"✓ {tool_name}",
                            "tool": tool_name,
                            "output_preview": output_preview,
                        })
                else:
                    # No more tool calls — agent is done
                    break

            # Extract final response — content may be a list of blocks (text + tool_use)
            raw_content = response.content if hasattr(response, "content") else str(response)
            if isinstance(raw_content, list):
                # Extract text from content blocks
                final_content = "\n".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in raw_content
                    if not (isinstance(block, dict) and block.get("type") == "tool_use")
                )
            else:
                final_content = raw_content

            elapsed_ms = round((_time.monotonic() - _t0) * 1000)
            self._emit(event_queue, {
                "cat": "node", "node": self.agent_name, "action": "end",
                "text": f"✓ {self.agent_name} ({elapsed_ms}ms)",
                "duration_ms": elapsed_ms,
                "step_content": final_content[:300],
            })

            return AgentResult(
                agent=self.agent_name,
                success=True,
                summary=final_content[:500],
            )

        except Exception as exc:
            logger.exception("%s agent: task failed", self.agent_name)
            elapsed_ms = round((_time.monotonic() - _t0) * 1000)
            self._emit(event_queue, {
                "cat": "node", "node": self.agent_name, "action": "end",
                "text": f"✗ {self.agent_name} failed ({elapsed_ms}ms)",
                "duration_ms": elapsed_ms,
            })
            return AgentResult(
                agent=self.agent_name,
                success=False,
                summary=f"Failed: {exc}",
            )

    # Max characters per tool result to prevent context bloat.
    # Dispatch workflow results can be very large; truncating keeps
    # the agent loop fast across many steps.
    TOOL_RESULT_MAX_CHARS = 4000

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
                    content = str(result)
                    if len(content) > self.TOOL_RESULT_MAX_CHARS:
                        content = content[:self.TOOL_RESULT_MAX_CHARS] + f"\n\n[truncated — {len(content)} chars total]"
                    return ToolMessage(
                        content=content,
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

    @staticmethod
    def _extract_task(state: dict) -> str:
        """Extract the user's request from state messages, with conversation context.

        Includes recent conversation history so agents can resolve references
        like "do that", "the thing above", "can you have ironclaw do that?" etc.
        Without context, agents only see the latest message and lose track of
        what "that" refers to.
        """
        messages = state.get("messages", [])
        if not messages:
            return ""

        last_msg = messages[-1]
        task = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

        # Include recent conversation context (last 6 messages = ~3 exchanges)
        # so the agent can resolve anaphoric references ("that", "it", etc.)
        if len(messages) > 1:
            # Grab up to 6 recent messages before the current one
            context_msgs = messages[max(0, len(messages) - 7):-1]
            if context_msgs:
                context_lines = []
                for m in context_msgs:
                    role = getattr(m, "type", "unknown")
                    content = m.content if hasattr(m, "content") else str(m)
                    # Truncate long messages (tool outputs, etc.)
                    if isinstance(content, str) and len(content) > 300:
                        content = content[:300] + "..."
                    elif isinstance(content, list):
                        # Multi-block messages — extract text blocks
                        texts = [b.get("text", "")[:200] for b in content if isinstance(b, dict) and b.get("type") == "text"]
                        content = " ".join(texts)[:300]
                    if content:
                        context_lines.append(f"[{role}]: {content}")

                if context_lines:
                    context_block = "\n".join(context_lines)
                    task = (
                        f"[CONVERSATION CONTEXT — recent messages for reference]\n"
                        f"{context_block}\n"
                        f"[END CONTEXT]\n\n"
                        f"[CURRENT REQUEST]\n{task}"
                    )

        return task

    @staticmethod
    def _find_protocol(state: dict, priority: list[str], default: str) -> str:
        """Find the most relevant skill protocol from state.

        Args:
            state: GrimState dict.
            priority: Ordered list of skill names to check.
            default: Fallback protocol text if no skill matches.
        """
        skill_protocols = state.get("skill_protocols", {})

        for skill_name in priority:
            if skill_name in skill_protocols:
                return skill_protocols[skill_name]

        # Use first available protocol as fallback
        if skill_protocols:
            first_key = next(iter(skill_protocols))
            return skill_protocols[first_key]

        return default

    def build_context(self, state: dict) -> dict:
        """Build context dict from state. Override in subclasses for richer context.

        Default: merges per-turn knowledge_context with accumulated
        session_knowledge, deduplicating by FDO ID.
        """
        context = {}
        all_fdos = _merge_knowledge_sources(state)
        if all_fdos:
            context["relevant_fdos"] = ", ".join(
                f"{fdo.id} ({fdo.domain})" for fdo in all_fdos[:10]
            )
        return context

    @classmethod
    def make_callable(cls, config):
        """Create an agent callable for the dispatch node.

        This is the generic factory that replaces per-agent make_*_agent()
        functions. Creates the agent instance and returns an async function
        matching the dispatch signature.

        Returns:
            Async function: (GrimState, *, event_queue=None) -> AgentResult
        """
        agent = cls(config)

        async def agent_fn(state, *, event_queue=None):
            task = cls._extract_task(state)
            protocol = cls._find_protocol(state, agent.protocol_priority, agent.default_protocol)
            context = agent.build_context(state)
            return await agent.execute(
                task=task,
                skill_protocol=protocol,
                context=context,
                event_queue=event_queue,
            )

        return agent_fn
