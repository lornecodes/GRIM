"""Context window management — token estimation and compression utilities.

Provides character-based token estimation (no external tokenizer dependency)
and a compression prompt for summarizing older messages when the context
window approaches its limit.

Design:
  - Claude averages ~3.5-4 chars per token for English text
  - We use len/4 (conservative — overestimates slightly, which is the safe direction)
  - No tiktoken dependency (Claude doesn't use that tokenizer)
"""

from __future__ import annotations

import logging
from typing import Sequence

from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)

# Conservative estimate: ~4 chars per token for Claude
CHARS_PER_TOKEN = 4


def estimate_tokens(messages: Sequence[BaseMessage]) -> int:
    """Estimate total token count for a message sequence.

    Uses character count / 4 as conservative estimate for Claude.
    Handles str content and list-of-blocks content.
    """
    total = 0
    for msg in messages:
        content = msg.content
        if isinstance(content, str):
            total += len(content) // CHARS_PER_TOKEN
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(block.get("text", "")) // CHARS_PER_TOKEN
                elif isinstance(block, str):
                    total += len(block) // CHARS_PER_TOKEN
    return total


def should_compress(token_estimate: int, max_tokens: int) -> bool:
    """Return True if messages exceed the compression threshold."""
    return token_estimate > max_tokens


def format_messages_for_summary(messages: Sequence[BaseMessage]) -> str:
    """Render messages as readable text for the compression prompt."""
    lines = []
    for msg in messages:
        role = type(msg).__name__.replace("Message", "").upper()
        content = msg.content
        if isinstance(content, list):
            # Extract text from content blocks
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            content = "\n".join(parts)
        if content:
            lines.append(f"[{role}]: {content}")
    return "\n\n".join(lines)


COMPRESSION_PROMPT = """Summarize the following conversation history into a concise, structured summary.

You MUST preserve:
1. Key decisions and conclusions reached
2. Active goals or objectives being worked on
3. Important facts, numbers, file paths, and code references
4. Unresolved questions or open threads
5. The general tone and relationship context

Format as a structured summary with clear sections. Be concise but complete — do NOT lose any actionable information.

{existing_summary}CONVERSATION TO COMPRESS:
{conversation}

STRUCTURED SUMMARY:"""
