"""Compress node — manage context window by summarizing older messages.

Inserted between identity and memory in the graph. Monitors token usage
and compresses older messages into a structured summary when approaching
the context window limit. Recent messages (last N) are always kept intact.

Uses LangGraph's RemoveMessage to cleanly remove old messages from state.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage

from core.config import GrimConfig
from core.context import (
    COMPRESSION_PROMPT,
    estimate_tokens,
    format_messages_for_summary,
    should_compress,
)
from core.state import GrimState

logger = logging.getLogger(__name__)


def make_compress_node(config: GrimConfig):
    """Create a compress node closure with config."""

    llm = ChatAnthropic(
        model=config.model,
        temperature=0.0,  # deterministic for summaries
        max_tokens=2048,
        default_headers={"X-Caller-ID": "grim"},
    )

    async def compress_node(state: GrimState) -> dict:
        """Check context window usage and compress if needed."""
        messages = list(state.get("messages", []))

        token_est = estimate_tokens(messages)
        logger.info(
            "Compress: %d messages, ~%d tokens (threshold: %d)",
            len(messages),
            token_est,
            config.context_max_tokens,
        )

        if not should_compress(token_est, config.context_max_tokens):
            return {"token_estimate": token_est}

        # Not enough messages to split
        keep_count = config.context_keep_recent
        if len(messages) <= keep_count:
            logger.info("Compress: not enough messages to split, skipping")
            return {"token_estimate": token_est}

        old_messages = messages[:-keep_count]
        recent_messages = messages[-keep_count:]

        logger.info(
            "Compress: compressing %d old messages, keeping %d recent",
            len(old_messages),
            len(recent_messages),
        )

        # Build conversation text from old messages
        conversation_text = format_messages_for_summary(old_messages)

        # Include existing summary if present (chained compression)
        existing_summary = state.get("context_summary", "")
        existing_block = ""
        if existing_summary:
            existing_block = (
                f"PREVIOUS SUMMARY (incorporate and update):\n{existing_summary}\n\n"
            )

        # Include accumulated FDO references so compression preserves them
        session_knowledge = state.get("session_knowledge", [])
        knowledge_block = ""
        if session_knowledge:
            # Sort by hit_count (most referenced first), cap at 15
            sorted_entries = sorted(
                session_knowledge,
                key=lambda e: e.hit_count,
                reverse=True,
            )[:15]
            fdo_lines = "\n".join(
                f"- {e.fdo.id}: {e.fdo.title} ({e.fdo.domain}, "
                f"referenced {e.hit_count}x)"
                for e in sorted_entries
            )
            knowledge_block = (
                f"ACCUMULATED KNOWLEDGE REFERENCES (preserve these IDs):\n"
                f"{fdo_lines}\n\n"
            )

        # LLM call to summarize
        try:
            summary_response = await llm.ainvoke([
                HumanMessage(
                    content=COMPRESSION_PROMPT.format(
                        existing_summary=existing_block,
                        knowledge_references=knowledge_block,
                        conversation=conversation_text,
                    )
                )
            ])
            new_summary = summary_response.content
        except Exception:
            logger.exception("Compress: LLM summarization failed, skipping compression")
            return {"token_estimate": token_est}

        # Remove old messages using LangGraph's RemoveMessage pattern
        removals = []
        for m in old_messages:
            if hasattr(m, "id") and m.id:
                removals.append(RemoveMessage(id=m.id))

        # Add summary as a SystemMessage at the front
        summary_msg = SystemMessage(
            content=f"[CONVERSATION SUMMARY — earlier messages compressed]\n{new_summary}"
        )

        new_token_est = estimate_tokens(recent_messages) + len(new_summary) // 4

        logger.info(
            "Compress: compressed %d→%d tokens (saved ~%d tokens)",
            token_est,
            new_token_est,
            token_est - new_token_est,
        )

        return {
            "messages": removals + [summary_msg],
            "context_summary": new_summary,
            "token_estimate": new_token_est,
        }

    return compress_node
