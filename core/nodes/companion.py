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
from typing import Any, TYPE_CHECKING

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from core.config import GrimConfig
from core.state import GrimState
from core.tools.kronos_read import COMPANION_TOOLS

if TYPE_CHECKING:
    from core.reasoning_cache import ReasoningCache

logger = logging.getLogger(__name__)

# Max tool-call round trips per turn
MAX_TOOL_STEPS = 3


def make_companion_node(config: GrimConfig, reasoning_cache: ReasoningCache | None = None):
    """Create a companion node closure with config."""

    # Build a name→tool lookup for execution
    tool_map = {t.name: t for t in COMPANION_TOOLS}

    async def companion_node(state: GrimState, run_config: RunnableConfig = None) -> dict:
        """Generate GRIM's response in companion (thinker) mode.

        Two caching layers:
        1. Anthropic prompt caching — static system prompt prefix marked with
           cache_control: {"type": "ephemeral"} so Anthropic caches it for 5 min.
        2. Reasoning cache — tool-loop results cached in Redis so repeated
           questions skip the tool loop entirely (3-4 LLM calls → 1).
        """
        # Select model — use router's selection or fall back to config default
        selected_model = state.get("selected_model") or config.model

        llm = ChatAnthropic(
            model=selected_model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            default_headers={"X-Caller-ID": "grim"},
        )
        llm_with_tools = llm.bind_tools(COMPANION_TOOLS) if COMPANION_TOOLS else llm

        logger.info("Companion: using model %s", selected_model)

        messages = list(state.get("messages", []))
        knowledge_context = state.get("knowledge_context", [])
        matched_skills = state.get("matched_skills", [])

        # Build system prompt with static/dynamic split for prompt caching
        from core.personality.prompt_builder import build_system_prompt_parts

        parts = build_system_prompt_parts(
            prompt_path=config.identity_prompt_path,
            personality_path=config.identity_personality_path,
            field_state=state.get("field_state"),
            knowledge_context=knowledge_context,
            matched_skills=matched_skills,
            objectives=state.get("objectives"),
            personality_cache_path=config.personality_cache_path,
            caller_id=state.get("caller_id"),
            caller_context=state.get("caller_context"),
        )

        # Build system instruction with cache_control on stable prefix.
        # CLIProxyAPI hijacks the system field, so we inject via HumanMessage/
        # AIMessage pair. The static prefix (identity + personality + caller)
        # gets cache_control: {"type": "ephemeral"} — Anthropic caches it for
        # 5 min, reducing input token cost on subsequent calls.
        system_content = [
            {
                "type": "text",
                "text": f"[SYSTEM INSTRUCTIONS — follow exactly]\n{parts.static}",
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if parts.dynamic:
            system_content.append({
                "type": "text",
                "text": f"{parts.dynamic}\n[END SYSTEM INSTRUCTIONS]",
            })
        else:
            system_content[0]["text"] += "\n[END SYSTEM INSTRUCTIONS]"

        llm_messages = [
            HumanMessage(content=system_content),
            AIMessage(content="Understood. I am GRIM and will follow these instructions precisely."),
        ] + messages

        logger.info("Companion: generating response (%d messages in history)", len(messages))

        # Extract user message and FDO IDs for reasoning cache
        user_msg = ""
        if messages:
            last = messages[-1]
            user_msg = last.content if hasattr(last, "content") and isinstance(last.content, str) else str(last)

        fdo_ids = [fdo.id for fdo in knowledge_context] if knowledge_context else []

        # ── Reasoning cache check ─────────────────────────────────────────
        cached_results = None
        if reasoning_cache and user_msg:
            cached_results = await reasoning_cache.get(user_msg, fdo_ids)

        if cached_results is not None:
            # CACHE HIT: Skip tool loop, inject cached context, one LLM call
            logger.info(
                "Companion: reasoning cache hit — skipping tool loop (%d cached results)",
                len(cached_results),
            )
            context_parts = []
            for r in cached_results:
                context_parts.append(f"[{r['name']}({r.get('args', {})})]\n{r['content']}")
            cached_context = "\n\n".join(context_parts)

            llm_messages.append(
                HumanMessage(
                    content=(
                        "[CACHED KNOWLEDGE CONTEXT — gathered from previous identical query]\n"
                        f"{cached_context}\n"
                        "[END CACHED CONTEXT]\n\n"
                        "Please respond to the user's message using this context."
                    )
                )
            )
            response = await _safe_invoke(llm, llm_messages, run_config)
            new_messages: list[Any] = [response]
        else:
            # CACHE MISS: Normal tool-calling loop
            new_messages = []
            got_text_response = False
            tool_results_to_cache: list[dict] = []

            for step in range(MAX_TOOL_STEPS):
                response = await _safe_invoke(llm_with_tools, llm_messages, run_config)
                new_messages.append(response)
                llm_messages.append(response)

                if hasattr(response, "tool_calls") and response.tool_calls:
                    logger.info(
                        "Companion: tool calls at step %d: %s",
                        step, [tc["name"] for tc in response.tool_calls],
                    )
                    for tc in response.tool_calls:
                        tool_result = await _execute_tool(tool_map, tc)
                        new_messages.append(tool_result)
                        llm_messages.append(tool_result)

                        # Collect successful results for caching
                        if not tool_result.content.startswith(("Tool error:", "Unknown tool:")):
                            tool_results_to_cache.append({
                                "name": tc["name"],
                                "args": tc.get("args", {}),
                                "content": tool_result.content,
                            })
                else:
                    got_text_response = True
                    break

            # Force final response if tool loop exhausted
            if not got_text_response:
                logger.info("Companion: tool loop exhausted (%d steps), forcing final response", MAX_TOOL_STEPS)
                response = await _safe_invoke(llm, llm_messages, run_config)
                new_messages.append(response)

            # Cache the tool results for future identical queries
            if reasoning_cache and tool_results_to_cache and user_msg:
                await reasoning_cache.set(user_msg, fdo_ids, tool_results_to_cache)

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


async def _safe_invoke(llm, messages: list, run_config=None):
    """Invoke LLM with error recovery.

    Catches streaming/serialization errors (e.g. langchain-anthropic
    context_management bug) and retries once. If retry fails, returns
    a fallback AIMessage so the graph doesn't crash.
    """
    try:
        return await llm.ainvoke(messages, config=run_config)
    except AttributeError as exc:
        logger.warning("Companion: LLM streaming error (retrying): %s", exc)
        try:
            return await llm.ainvoke(messages, config=run_config)
        except Exception as retry_exc:
            logger.error("Companion: LLM retry failed: %s", retry_exc)
            return AIMessage(content="I encountered a temporary error. Could you try again?")
    except Exception as exc:
        logger.error("Companion: LLM call failed: %s", exc)
        return AIMessage(content=f"I hit an error: {exc}. Let me try a different approach if you ask again.")


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
