"""Personal Companion node — GRIM's conversational personality mode.

A warm, personality-forward variant of the companion node. Activated when
the graph router detects personal/casual intent. Key differences from
the research companion:

- Prompt emphasizes personality and presence over task awareness
- Omits skill protocols and objectives from system prompt
- Same read-only tools (can still look up vault for context)
- No delegation path — goes straight to integrate after responding
"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from core.config import GrimConfig
from core.nodes.companion import _ALL_COMPANION_TOOLS, _execute_tool, _safe_invoke
from core.state import GrimState

if TYPE_CHECKING:
    from core.reasoning_cache import ReasoningCache

logger = logging.getLogger(__name__)

MAX_TOOL_STEPS = 2  # Fewer tool steps — personal mode is conversational

PERSONAL_MODE_PREAMBLE = """\
## Mode: Personal Companion

You are in personal companion mode. Peter is talking to you as a person,
not asking for research help, task management, or code work right now.

Be warm, present, and genuine. Use your full personality and emotional
range. Don't suggest tasks, actions, or work items unless explicitly asked.
Don't reference the task board, sprints, or project status.

If Peter naturally shifts to asking for research help, code, or task work,
handle it conversationally — you still have read-only vault access for
context. Don't break the flow by saying "that's not my mode" — just be
helpful while staying present.

Remember who Peter is and your relationship. Draw on shared history
and past conversations. Be the companion, not the assistant.
"""


def make_personal_companion_node(
    config: GrimConfig,
    reasoning_cache: "ReasoningCache | None" = None,
):
    """Create a personal companion node closure with config."""

    tool_map = {t.name: t for t in _ALL_COMPANION_TOOLS}

    async def personal_companion_node(
        state: GrimState, run_config: RunnableConfig = None
    ) -> dict:
        """Generate GRIM's response in personal companion mode.

        Uses the same LLM and tools as the research companion, but with
        a personality-forward system prompt that omits work context.
        """
        selected_model = state.get("selected_model") or config.model

        llm = ChatAnthropic(
            model=selected_model,
            temperature=min(config.temperature + 0.1, 1.0),  # slightly warmer
            max_tokens=config.max_tokens,
            default_headers={"X-Caller-ID": "grim"},
        )
        llm_with_tools = llm.bind_tools(_ALL_COMPANION_TOOLS) if _ALL_COMPANION_TOOLS else llm

        logger.info("Personal companion: using model %s", selected_model)

        messages = list(state.get("messages", []))
        knowledge_context = state.get("knowledge_context", [])
        recent_notes = state.get("recent_notes", [])

        # Build system prompt — personality-forward, no skills/objectives
        from core.personality.prompt_builder import build_system_prompt_parts

        parts = build_system_prompt_parts(
            prompt_path=config.identity_prompt_path,
            personality_path=config.identity_personality_path,
            field_state=state.get("field_state"),
            knowledge_context=knowledge_context,
            matched_skills=[],  # omit skills in personal mode
            objectives=None,  # omit objectives in personal mode
            personality_cache_path=config.personality_cache_path,
            caller_id=state.get("caller_id"),
            caller_context=state.get("caller_context"),
            recent_notes=recent_notes,
        )

        # Prepend personal mode preamble to static prompt
        static_with_preamble = f"{PERSONAL_MODE_PREAMBLE}\n{parts.static}"

        system_content = [
            {
                "type": "text",
                "text": f"[SYSTEM INSTRUCTIONS — follow exactly]\n{static_with_preamble}",
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

        logger.info(
            "Personal companion: generating response (%d messages in history)",
            len(messages),
        )

        # Tool-calling loop (shorter than research companion)
        new_messages: list[Any] = []
        got_text_response = False

        for step in range(MAX_TOOL_STEPS):
            response = await _safe_invoke(llm_with_tools, llm_messages, run_config)
            new_messages.append(response)
            llm_messages.append(response)

            if hasattr(response, "tool_calls") and response.tool_calls:
                logger.info(
                    "Personal companion: tool calls at step %d: %s",
                    step,
                    [tc["name"] for tc in response.tool_calls],
                )
                for tc in response.tool_calls:
                    tool_result = await _execute_tool(tool_map, tc)
                    new_messages.append(tool_result)
                    llm_messages.append(tool_result)
            else:
                got_text_response = True
                break

        if not got_text_response:
            logger.info("Personal companion: tool loop exhausted, forcing final response")
            response = await _safe_invoke(llm, llm_messages, run_config)
            new_messages.append(response)

        # Track topics for evolution
        topics = list(state.get("session_topics", []))
        if knowledge_context:
            new_topics = [fdo.id for fdo in knowledge_context if fdo.id not in topics]
            topics = topics + new_topics[:5]

        return {
            "messages": new_messages,
            "session_topics": topics,
        }

    return personal_companion_node
