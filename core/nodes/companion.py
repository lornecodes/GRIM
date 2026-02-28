"""Companion node — GRIM's main conversation personality.

This is the THINKER. It has:
- System prompt (from Identity node)
- Knowledge context (from Memory node)
- Skill awareness (from Skill Match node)
- READ-ONLY Kronos tools

It NEVER writes to Kronos or executes code. When action is needed,
the Router sends to Dispatch instead.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from core.config import GrimConfig
from core.state import GrimState
from core.tools.kronos_read import COMPANION_TOOLS

logger = logging.getLogger(__name__)

# Max tool-call round trips per turn
MAX_TOOL_STEPS = 3


def make_companion_node(config: GrimConfig):
    """Create a companion node closure with config."""

    # Initialize LLM with read-only tools
    llm = ChatAnthropic(
        model=config.model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        default_headers={"X-Caller-ID": "grim"},
    )
    llm_with_tools = llm.bind_tools(COMPANION_TOOLS) if COMPANION_TOOLS else llm

    # Build a name→tool lookup for execution
    tool_map = {t.name: t for t in COMPANION_TOOLS}

    async def companion_node(state: GrimState, run_config: RunnableConfig = None) -> dict:
        """Generate GRIM's response in companion (thinker) mode.

        Includes a tool-calling loop so the companion can query Kronos
        mid-turn (e.g. kronos_search, kronos_get) and fold results
        into its final answer.

        Accepts RunnableConfig (as run_config to avoid shadowing the
        outer GrimConfig) so LangGraph's astream_events callback chain
        propagates through our LLM calls, enabling token streaming.
        """
        system_prompt = state.get("system_prompt", "You are GRIM.")
        messages = list(state.get("messages", []))
        knowledge_context = state.get("knowledge_context", [])
        matched_skills = state.get("matched_skills", [])

        # Enrich system prompt with per-turn context
        from core.personality.prompt_builder import build_system_prompt

        enriched_prompt = build_system_prompt(
            prompt_path=config.identity_prompt_path,
            personality_path=config.identity_personality_path,
            field_state=state.get("field_state"),
            knowledge_context=knowledge_context,
            matched_skills=matched_skills,
            personality_cache_path=config.personality_cache_path,
            caller_id=state.get("caller_id"),
            caller_context=state.get("caller_context"),
        )

        # Inject system prompt via messages rather than the API system field.
        # CLIProxyAPI prepends "You are Claude Code..." to the system field,
        # overriding GRIM's identity. The proxy config filters out the system
        # field entirely, so we pass GRIM's prompt through a HumanMessage/
        # AIMessage pair which survives the filter and establishes identity.
        llm_messages = [
            HumanMessage(content=f"[SYSTEM INSTRUCTIONS — follow exactly]\n{enriched_prompt}\n[END SYSTEM INSTRUCTIONS]"),
            AIMessage(content="Understood. I am GRIM and will follow these instructions precisely."),
        ] + messages

        logger.info("Companion: generating response (%d messages in history)", len(messages))

        # Tool-calling loop — pass config so astream_events can
        # intercept and emit on_chat_model_stream / on_chat_model_end events.
        new_messages: list[Any] = []
        for step in range(MAX_TOOL_STEPS):
            response = await llm_with_tools.ainvoke(llm_messages, config=run_config)
            new_messages.append(response)
            llm_messages.append(response)

            # If the LLM made tool calls, execute them and loop
            if hasattr(response, "tool_calls") and response.tool_calls:
                logger.info("Companion: tool calls at step %d: %s",
                            step, [tc["name"] for tc in response.tool_calls])
                for tc in response.tool_calls:
                    tool_result = await _execute_tool(tool_map, tc)
                    new_messages.append(tool_result)
                    llm_messages.append(tool_result)
            else:
                # No tool calls — we have the final answer
                break

        # Track topics for evolution
        topics = list(state.get("session_topics", []))
        if knowledge_context:
            new_topics = [fdo.id for fdo in knowledge_context if fdo.id not in topics]
            topics = topics + new_topics[:5]

        return {
            "messages": new_messages,
            "session_topics": topics,
        }

    return companion_node


async def _execute_tool(tool_map: dict, tool_call: dict) -> ToolMessage:
    """Execute a single tool call and return a ToolMessage."""
    name = tool_call["name"]
    args = tool_call.get("args", {})
    call_id = tool_call.get("id", "unknown")

    tool = tool_map.get(name)
    if tool is None:
        return ToolMessage(content=f"Unknown tool: {name}", tool_call_id=call_id)

    try:
        result = await tool.ainvoke(args)
        return ToolMessage(content=str(result), tool_call_id=call_id)
    except Exception as exc:
        logger.warning("Companion tool %s failed: %s", name, exc)
        return ToolMessage(content=f"Tool error: {exc}", tool_call_id=call_id)
