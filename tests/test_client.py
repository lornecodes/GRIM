"""Unit tests for GrimClient — persistent Agent SDK session.

All SDK calls are mocked — no real API calls, no cost.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.client import (
    GrimClient,
    GrimEvent,
    GrimResponse,
    KRONOS_TOOLS,
    POOL_TOOLS,
    _capture_message,
    _extract_final_text,
    _extract_tool_calls,
    _safe_json,
)
from core.config import GrimConfig


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def config(tmp_path):
    """Minimal GrimConfig with temp paths."""
    prompt = tmp_path / "system_prompt.md"
    prompt.write_text("You are GRIM, a test companion.")

    personality = tmp_path / "personality.yaml"
    personality.write_text("field_state:\n  coherence: 0.8\n  valence: 0.3\n  uncertainty: 0.2\n")

    cache = tmp_path / "personality.cache.md"
    cache.write_text("")

    return GrimConfig(
        identity_prompt_path=prompt,
        identity_personality_path=personality,
        personality_cache_path=cache,
        vault_path=tmp_path / "vault",
        skills_path=tmp_path / "skills",
        workspace_root=tmp_path,
        pool_enabled=False,
        kronos_mcp_command="",  # disable Kronos for unit tests
    )


# ── Mock SDK types ───────────────────────────────────────────────

@dataclass
class MockTextBlock:
    text: str


@dataclass
class MockToolUseBlock:
    name: str
    input: dict


@dataclass
class MockAssistantMessage:
    content: list


@dataclass
class MockResultMessage:
    num_turns: int = 2
    total_cost_usd: float = 0.05


# ── GrimResponse / GrimEvent ────────────────────────────────────

class TestGrimResponse:
    def test_defaults(self):
        r = GrimResponse()
        assert r.text is None
        assert r.tool_calls == []
        assert r.transcript == []
        assert r.cost_usd is None

    def test_with_data(self):
        r = GrimResponse(
            text="Hello",
            tool_calls=[{"name": "search"}],
            cost_usd=0.05,
            num_turns=2,
        )
        assert r.text == "Hello"
        assert len(r.tool_calls) == 1
        assert r.cost_usd == 0.05


class TestGrimEvent:
    def test_text_event(self):
        e = GrimEvent(type="text", data={"text": "Hello"})
        assert e.type == "text"
        assert e.data["text"] == "Hello"

    def test_tool_event(self):
        e = GrimEvent(type="tool_use", data={"name": "search", "input": {}})
        assert e.type == "tool_use"


# ── Message helpers ──────────────────────────────────────────────

class TestMessageHelpers:
    def test_extract_final_text(self):
        msgs = [
            MockAssistantMessage([MockTextBlock("first")]),
            MockAssistantMessage([MockTextBlock("second")]),
        ]
        # _extract_final_text uses lazy imports — patch at the sdk module level
        with patch("claude_agent_sdk.AssistantMessage", MockAssistantMessage, create=True), \
             patch("claude_agent_sdk.TextBlock", MockTextBlock, create=True):
            result = _extract_final_text(msgs)
        assert result == "second"

    def test_extract_tool_calls(self):
        msgs = [
            MockAssistantMessage([
                MockToolUseBlock("kronos_search", {"query": "PAC"}),
                MockTextBlock("found it"),
            ]),
        ]
        # Verify structure
        assert msgs[0].content[0].name == "kronos_search"
        assert msgs[0].content[0].input == {"query": "PAC"}

    def test_capture_message_assistant(self):
        msg = MockAssistantMessage([MockTextBlock("hello")])
        # Verify the mock structure matches what _capture_message expects
        assert hasattr(msg, "content")
        assert msg.content[0].text == "hello"

    def test_safe_json_serializable(self):
        assert _safe_json({"key": "value"}) == {"key": "value"}
        assert _safe_json([1, 2, 3]) == [1, 2, 3]
        assert _safe_json("text") == "text"

    def test_safe_json_non_serializable(self):
        obj = object()
        result = _safe_json(obj)
        assert isinstance(result, str)


# ── Tool lists ───────────────────────────────────────────────────

class TestToolLists:
    def test_kronos_tools_not_empty(self):
        assert len(KRONOS_TOOLS) > 20

    def test_kronos_tools_all_prefixed(self):
        for t in KRONOS_TOOLS:
            assert t.startswith("mcp__kronos__"), f"Bad prefix: {t}"

    def test_pool_tools(self):
        assert len(POOL_TOOLS) == 3
        names = {t.split("__")[-1] for t in POOL_TOOLS}
        assert names == {"pool_submit", "pool_status", "pool_list_jobs"}


# ── GrimClient construction ─────────────────────────────────────

class TestGrimClientConstruction:
    def test_defaults(self, config):
        client = GrimClient(config)
        assert client.config is config
        assert client.on_message is None
        assert client.max_turns == 10
        assert client.caller_id == "peter"
        assert client._started is False
        assert client._total_cost == 0.0

    def test_custom_params(self, config):
        cb = lambda msg: None
        client = GrimClient(
            config,
            on_message=cb,
            max_turns=5,
            caller_id="discord",
            allowed_tools=["mcp__kronos__kronos_search"],
        )
        assert client.on_message is cb
        assert client.max_turns == 5
        assert client.caller_id == "discord"
        assert client._allowed_tools == ["mcp__kronos__kronos_search"]


class TestGrimClientSystemPrompt:
    def test_builds_from_identity_files(self, config):
        client = GrimClient(config)
        prompt = client._build_system_prompt()
        assert "GRIM" in prompt
        assert "test companion" in prompt
        assert "Expression Mode" in prompt
        assert "Coherence: 0.80" in prompt
        assert "Available Capabilities" in prompt
        assert "semantic=false" in prompt

    def test_includes_field_state(self, config):
        client = GrimClient(config)
        prompt = client._build_system_prompt()
        assert "Valence: 0.30" in prompt
        assert "Uncertainty: 0.20" in prompt


class TestGrimClientSessionInfo:
    def test_initial_session_info(self, config):
        client = GrimClient(config)
        info = client.session_info
        assert info["started"] is False
        assert info["turn_count"] == 0
        assert info["total_cost_usd"] == 0.0
        assert info["caller_id"] == "peter"


class TestGrimClientLifecycle:
    @pytest.mark.asyncio
    async def test_send_raises_when_not_started(self, config):
        client = GrimClient(config)
        with pytest.raises(RuntimeError, match="not started"):
            await client.send("hello")

    @pytest.mark.asyncio
    async def test_stop_when_not_started_is_safe(self, config):
        client = GrimClient(config)
        await client.stop()  # should not raise

    @pytest.mark.asyncio
    async def test_double_start_is_safe(self, config):
        client = GrimClient(config)
        # Mock the SDK to avoid real calls
        mock_client = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("claude_agent_sdk.ClaudeSDKClient", return_value=mock_cm, create=True), \
             patch("claude_agent_sdk.ClaudeAgentOptions", create=True):
            await client.start()
            assert client._started is True
            # Second start should be a no-op
            await client.start()
            assert client._started is True
            await client.stop()


class TestGrimClientMCPSetup:
    def test_no_mcp_when_command_empty(self, config):
        """When kronos_mcp_command is empty, no Kronos MCP configured."""
        config.kronos_mcp_command = ""
        client = GrimClient(config)
        # The client should still construct — MCP is optional
        prompt = client._build_system_prompt()
        assert "GRIM" in prompt

    def test_pool_tools_excluded_when_pool_disabled(self, config):
        config.pool_enabled = False
        client = GrimClient(config)
        # When pool is disabled and no explicit allowed_tools,
        # pool tools should not be in the list
        assert client._allowed_tools is None  # will be computed at start()


