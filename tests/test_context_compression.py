"""Tests for context window management — token estimation and compression.

All tests use mocked LLM — no real API calls needed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure GRIM root is on path
GRIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GRIM_ROOT))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from core.context import (
    CHARS_PER_TOKEN,
    COMPRESSION_PROMPT,
    estimate_tokens,
    format_messages_for_summary,
    should_compress,
)


# ─── Token Estimation ──────────────────────────────────────────────────────


class TestEstimateTokens:

    def test_basic_string_content(self):
        msgs = [HumanMessage(content="Hello world")]  # 11 chars -> 2 tokens
        est = estimate_tokens(msgs)
        assert est == len("Hello world") // CHARS_PER_TOKEN

    def test_empty_messages(self):
        assert estimate_tokens([]) == 0

    def test_multiple_messages(self):
        msgs = [
            HumanMessage(content="a" * 100),
            AIMessage(content="b" * 200),
        ]
        assert estimate_tokens(msgs) == (100 + 200) // CHARS_PER_TOKEN

    def test_content_blocks(self):
        msgs = [
            HumanMessage(content=[
                {"type": "text", "text": "a" * 80},
                {"type": "text", "text": "b" * 120},
            ]),
        ]
        assert estimate_tokens(msgs) == (80 + 120) // CHARS_PER_TOKEN

    def test_mixed_content_types(self):
        msgs = [
            HumanMessage(content="plain text"),  # 10 chars
            AIMessage(content=[{"type": "text", "text": "block text"}]),  # 10 chars
        ]
        # "plain text" = 10 chars -> 10//4 = 2 tokens, "block text" = 10 chars -> 2 tokens
        assert estimate_tokens(msgs) == 2 + 2

    def test_empty_content(self):
        msgs = [HumanMessage(content="")]
        assert estimate_tokens(msgs) == 0

    def test_tool_messages(self):
        msgs = [ToolMessage(content="result data here", tool_call_id="tc1")]
        assert estimate_tokens(msgs) == len("result data here") // CHARS_PER_TOKEN

    def test_string_blocks_in_list(self):
        """Content can be a list of strings (not just dicts)."""
        msgs = [HumanMessage(content=["hello", "world"])]
        assert estimate_tokens(msgs) == (5 + 5) // CHARS_PER_TOKEN


# ─── Should Compress ────────────────────────────────────────────────────────


class TestShouldCompress:

    def test_below_threshold(self):
        assert should_compress(100_000, 160_000) is False

    def test_above_threshold(self):
        assert should_compress(170_000, 160_000) is True

    def test_at_threshold(self):
        assert should_compress(160_000, 160_000) is False

    def test_just_above(self):
        assert should_compress(160_001, 160_000) is True


# ─── Format Messages ────────────────────────────────────────────────────────


class TestFormatMessages:

    def test_basic_formatting(self):
        msgs = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there"),
        ]
        result = format_messages_for_summary(msgs)
        assert "[HUMAN]: Hello" in result
        assert "[AI]: Hi there" in result

    def test_content_blocks_formatting(self):
        msgs = [
            HumanMessage(content=[{"type": "text", "text": "block content"}]),
        ]
        result = format_messages_for_summary(msgs)
        assert "block content" in result

    def test_empty_messages(self):
        result = format_messages_for_summary([])
        assert result == ""

    def test_system_message(self):
        msgs = [SystemMessage(content="System text")]
        result = format_messages_for_summary(msgs)
        assert "[SYSTEM]: System text" in result


# ─── Compress Node ──────────────────────────────────────────────────────────


class TestCompressNode:

    def _make_config(self, max_tokens=100, keep_recent=4):
        """Create a minimal config for testing."""
        from core.config import GrimConfig
        cfg = GrimConfig()
        cfg.context_max_tokens = max_tokens
        cfg.context_keep_recent = keep_recent
        return cfg

    def _make_messages(self, count, chars_each=100):
        """Create N alternating Human/AI messages."""
        msgs = []
        for i in range(count):
            content = f"Message {i}: " + "x" * chars_each
            if i % 2 == 0:
                msg = HumanMessage(content=content, id=f"msg-{i}")
            else:
                msg = AIMessage(content=content, id=f"msg-{i}")
            msgs.append(msg)
        return msgs

    @pytest.mark.asyncio
    async def test_no_compression_below_threshold(self):
        """Should pass through when below threshold."""
        from core.nodes.compress import make_compress_node

        config = self._make_config(max_tokens=999_999)
        node = make_compress_node(config)

        state = {"messages": self._make_messages(4)}
        result = await node(state)

        assert "token_estimate" in result
        # No messages key means no compression happened
        assert "messages" not in result or result.get("context_summary") is None

    @pytest.mark.asyncio
    async def test_no_compression_few_messages(self):
        """Should skip when not enough messages to split."""
        from core.nodes.compress import make_compress_node

        config = self._make_config(max_tokens=1, keep_recent=10)
        node = make_compress_node(config)

        state = {"messages": self._make_messages(4)}  # 4 < keep_recent=10
        result = await node(state)

        assert "context_summary" not in result

    @pytest.mark.asyncio
    async def test_compression_triggers(self):
        """Should compress when above threshold with enough messages."""
        from core.nodes.compress import make_compress_node

        config = self._make_config(max_tokens=50, keep_recent=2)

        mock_response = MagicMock()
        mock_response.content = "Summary of earlier conversation"

        with patch("core.nodes.compress.ChatAnthropic") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            MockLLM.return_value = mock_llm

            node = make_compress_node(config)
            msgs = self._make_messages(6, chars_each=200)
            state = {"messages": msgs}
            result = await node(state)

        assert "context_summary" in result
        assert result["context_summary"] == "Summary of earlier conversation"
        assert "messages" in result
        # Should have RemoveMessage entries + 1 SystemMessage
        assert len(result["messages"]) > 0

    @pytest.mark.asyncio
    async def test_preserves_recent_messages(self):
        """Should not remove recent messages."""
        from core.nodes.compress import make_compress_node
        from langchain_core.messages import RemoveMessage

        config = self._make_config(max_tokens=50, keep_recent=2)

        mock_response = MagicMock()
        mock_response.content = "Summary"

        with patch("core.nodes.compress.ChatAnthropic") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            MockLLM.return_value = mock_llm

            node = make_compress_node(config)
            msgs = self._make_messages(6, chars_each=200)
            state = {"messages": msgs}
            result = await node(state)

        # Check that RemoveMessage IDs are only from old messages (first 4)
        remove_ids = {m.id for m in result["messages"] if isinstance(m, RemoveMessage)}
        recent_ids = {m.id for m in msgs[-2:]}
        assert remove_ids.isdisjoint(recent_ids), "Recent messages should not be removed"

    @pytest.mark.asyncio
    async def test_chains_existing_summary(self):
        """Should include existing summary in compression prompt."""
        from core.nodes.compress import make_compress_node

        config = self._make_config(max_tokens=50, keep_recent=2)

        mock_response = MagicMock()
        mock_response.content = "Updated summary"

        with patch("core.nodes.compress.ChatAnthropic") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            MockLLM.return_value = mock_llm

            node = make_compress_node(config)
            msgs = self._make_messages(6, chars_each=200)
            state = {
                "messages": msgs,
                "context_summary": "Previous summary content",
            }
            result = await node(state)

        # Verify the prompt included the existing summary
        call_args = mock_llm.ainvoke.call_args[0][0]
        prompt_text = call_args[0].content
        assert "Previous summary content" in prompt_text

    @pytest.mark.asyncio
    async def test_llm_failure_skips_compression(self):
        """Should gracefully skip if LLM call fails."""
        from core.nodes.compress import make_compress_node

        config = self._make_config(max_tokens=50, keep_recent=2)

        with patch("core.nodes.compress.ChatAnthropic") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.side_effect = RuntimeError("LLM down")
            MockLLM.return_value = mock_llm

            node = make_compress_node(config)
            msgs = self._make_messages(6, chars_each=200)
            state = {"messages": msgs}
            result = await node(state)

        # Should return token estimate but no compression
        assert "token_estimate" in result
        assert "context_summary" not in result
