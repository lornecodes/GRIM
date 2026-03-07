"""Unit tests for GRIM Discord bot.

Tests security, rate limiting, message splitting, session management,
people profiles, health checks, and metrics.
No real Discord connection or API calls.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.discord_bot import (
    DISCORD_GUEST_TOOLS,
    DISCORD_OWNER_TOOLS,
    DISCORD_MAX_CHARS,
    DISCORD_SAFE_CHARS,
    GrimDiscordBot,
    PeopleProfile,
    _create_people_fdo,
    _extract_fdo_summary,
    _make_people_fdo_id,
    split_message,
)
from core.client import GrimClient, GrimResponse
from core.config import GrimConfig


@pytest.fixture
def config(tmp_path):
    """Minimal config for Discord bot tests."""
    prompt = tmp_path / "system_prompt.md"
    prompt.write_text("You are GRIM.")

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
        kronos_mcp_command="",
    )


@pytest.fixture
def bot(config):
    return GrimDiscordBot(
        config,
        allowed_guild_ids=[123],
        allowed_channel_ids=[456],
        daily_cost_cap=0.50,
    )


# ── Security ─────────────────────────────────────────────────────

class TestDiscordSecurity:
    def test_allowed_guild_and_channel(self, bot):
        assert bot.is_allowed(guild_id=123, channel_id=456) is True

    def test_wrong_guild(self, bot):
        assert bot.is_allowed(guild_id=999, channel_id=456) is False

    def test_wrong_channel(self, bot):
        assert bot.is_allowed(guild_id=123, channel_id=999) is False

    def test_no_guild_rejected(self, bot):
        """DMs (no guild) are rejected when guild allowlist is set."""
        assert bot.is_allowed(guild_id=None, channel_id=456) is False

    def test_no_restrictions(self, config):
        """Bot with no allowlists accepts everything."""
        bot = GrimDiscordBot(config)
        assert bot.is_allowed(guild_id=999, channel_id=888) is True
        assert bot.is_allowed(guild_id=None, channel_id=888) is True

    @pytest.mark.asyncio
    async def test_ignores_bots(self, bot):
        result = await bot.handle_message(
            "hello", guild_id=123, channel_id=456, user_id=1, is_bot=True
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_ignores_wrong_guild(self, bot):
        result = await bot.handle_message(
            "hello", guild_id=999, channel_id=456, user_id=1
        )
        assert result == []

    def test_guest_tools_no_write(self):
        """Guest tool list must NOT include write/create/update tools."""
        for t in DISCORD_GUEST_TOOLS:
            assert "create" not in t, f"Write tool in guest list: {t}"
            assert "update" not in t, f"Write tool in guest list: {t}"
            assert "note_append" not in t, f"Write tool in guest list: {t}"

    def test_guest_tools_no_bash(self):
        """No Bash or file-write tools for guests."""
        tool_names = {t.split("__")[-1] for t in DISCORD_GUEST_TOOLS}
        assert "Bash" not in tool_names
        assert "Write" not in tool_names
        assert "Edit" not in tool_names

    def test_guest_tools_has_read_tools(self):
        """Guests should have search/get/list (read-only Kronos)."""
        tool_names = {t.split("__")[-1] for t in DISCORD_GUEST_TOOLS}
        assert "kronos_search" in tool_names
        assert "kronos_get" in tool_names
        assert "kronos_list" in tool_names
        assert "kronos_graph" in tool_names

    def test_owner_tools_has_write_tools(self):
        """Owner tool list includes vault write + task management tools."""
        tool_names = {t.split("__")[-1] for t in DISCORD_OWNER_TOOLS}
        assert "kronos_create" in tool_names
        assert "kronos_update" in tool_names
        assert "kronos_note_append" in tool_names
        assert "kronos_task_create" in tool_names
        assert "kronos_task_move" in tool_names
        assert "kronos_task_dispatch" in tool_names


# ── Identity ────────────────────────────────────────────────────

class TestDiscordIdentity:
    def test_owner_detection(self, config):
        bot = GrimDiscordBot(config, owner_user_id=42)
        assert bot.is_owner(42) is True
        assert bot.is_owner(99) is False

    def test_no_owner_set(self, bot):
        """When no owner is configured, nobody is owner."""
        assert bot.is_owner(1) is False

    def test_register_user(self, bot):
        bot.register_user(1, "alice", "Alice W.")
        profile = bot._people_cache[1]
        assert profile.username == "alice"
        assert profile.display_name == "Alice W."

    def test_user_context_owner(self, config):
        bot = GrimDiscordBot(config, owner_user_id=42)
        bot.register_user(42, "peter", "Peter")
        ctx = bot.get_user_context(42)
        assert "Peter" in ctx
        assert "creator" in ctx

    def test_user_context_guest(self, bot):
        bot.register_user(99, "bob", "Bob")
        ctx = bot.get_user_context(99)
        assert "Bob" in ctx
        assert "friend" in ctx or "guest" in ctx

    def test_user_context_unknown(self, bot):
        ctx = bot.get_user_context(999)
        assert "user-999" in ctx

    @pytest.mark.asyncio
    async def test_message_includes_user_context(self, bot):
        """Messages sent to GrimClient include user identity context."""
        from clients.discord_bot import ChannelSession

        mock_client = AsyncMock()
        mock_client.send = AsyncMock(
            return_value=GrimResponse(text="Hi there!", cost_usd=0.01)
        )
        session = ChannelSession(client=mock_client)
        bot.sessions[456] = session

        await bot.handle_message(
            "hello", guild_id=123, channel_id=456, user_id=1,
            username="alice", display_name="Alice",
        )
        # Verify the message sent to GRIM includes user context
        sent_msg = mock_client.send.call_args[0][0]
        assert "Alice" in sent_msg
        assert "hello" in sent_msg


# ── Rate limiting ────────────────────────────────────────────────

class TestDiscordRateLimiting:
    def test_not_rate_limited_initially(self, bot):
        assert bot.is_rate_limited(user_id=1) is False

    def test_rate_limited_after_burst(self, bot):
        for _ in range(10):
            bot.is_rate_limited(user_id=1, max_messages=10)
        assert bot.is_rate_limited(user_id=1, max_messages=10) is True

    def test_different_users_independent(self, bot):
        for _ in range(10):
            bot.is_rate_limited(user_id=1, max_messages=10)
        # User 2 should not be limited
        assert bot.is_rate_limited(user_id=2, max_messages=10) is False

    @pytest.mark.asyncio
    async def test_rate_limit_response(self, bot):
        # Exhaust rate limit
        for _ in range(10):
            bot.is_rate_limited(user_id=1, max_messages=10)

        # Mock get_or_create_session to avoid real SDK calls
        with patch.object(bot, "get_or_create_session"):
            result = await bot.handle_message(
                "hello", guild_id=123, channel_id=456, user_id=1
            )
        assert len(result) == 1
        assert "Slow down" in result[0]


# ── Cost capping ─────────────────────────────────────────────────

class TestDiscordCostCap:
    def test_not_exceeded_initially(self, bot):
        assert bot.is_cost_exceeded(channel_id=456) is False

    @pytest.mark.asyncio
    async def test_cost_exceeded_blocks_messages(self, bot):
        """When daily cost exceeds cap, bot refuses to respond."""
        from clients.discord_bot import ChannelSession

        mock_client = MagicMock()
        session = ChannelSession(
            client=mock_client,
            daily_cost=0.60,  # exceeds 0.50 cap
            last_reset="2026-03-04",
        )
        bot.sessions[456] = session

        # Override last_reset to today so it doesn't auto-reset
        from datetime import datetime, timezone
        session.last_reset = datetime.now(timezone.utc).date().isoformat()

        result = await bot.handle_message(
            "hello", guild_id=123, channel_id=456, user_id=1
        )
        assert len(result) == 1
        assert "daily usage limit" in result[0]


# ── Session management ───────────────────────────────────────────

class TestDiscordSessionManagement:
    @pytest.mark.asyncio
    async def test_creates_session_per_channel(self, bot):
        """Each channel gets its own session."""
        with patch("clients.discord_bot.GrimClient") as MockClient:
            mock_instance = AsyncMock()
            MockClient.return_value = mock_instance

            session1 = await bot.get_or_create_session(456)
            session2 = await bot.get_or_create_session(789)

            assert 456 in bot.sessions
            assert 789 in bot.sessions
            assert session1 is not session2

    @pytest.mark.asyncio
    async def test_reuses_existing_session(self, bot):
        """Same channel reuses the same session."""
        with patch("clients.discord_bot.GrimClient") as MockClient:
            mock_instance = AsyncMock()
            MockClient.return_value = mock_instance

            session1 = await bot.get_or_create_session(456)
            session2 = await bot.get_or_create_session(456)

            assert session1 is session2

    @pytest.mark.asyncio
    async def test_close_all(self, bot):
        """close_all stops all sessions."""
        from clients.discord_bot import ChannelSession

        mock_client1 = AsyncMock()
        mock_client2 = AsyncMock()
        bot.sessions = {
            1: ChannelSession(client=mock_client1),
            2: ChannelSession(client=mock_client2),
        }

        await bot.close_all()
        mock_client1.stop.assert_called_once()
        mock_client2.stop.assert_called_once()
        assert len(bot.sessions) == 0


# ── Message handling ─────────────────────────────────────────────

class TestDiscordMessageHandling:
    @pytest.mark.asyncio
    async def test_successful_message(self, bot):
        """Successful message returns response chunks."""
        from clients.discord_bot import ChannelSession

        mock_client = AsyncMock()
        mock_client.send = AsyncMock(
            return_value=GrimResponse(text="Hello from GRIM!", cost_usd=0.01)
        )
        session = ChannelSession(client=mock_client)
        bot.sessions[456] = session

        result = await bot.handle_message(
            "hey", guild_id=123, channel_id=456, user_id=1
        )
        assert result == ["Hello from GRIM!"]
        assert session.daily_cost == 0.01
        assert session.message_count == 1

    @pytest.mark.asyncio
    async def test_empty_response(self, bot):
        """When GRIM returns no text, bot sends a default message."""
        from clients.discord_bot import ChannelSession

        mock_client = AsyncMock()
        mock_client.send = AsyncMock(
            return_value=GrimResponse(text=None)
        )
        session = ChannelSession(client=mock_client)
        bot.sessions[456] = session

        result = await bot.handle_message(
            "hey", guild_id=123, channel_id=456, user_id=1
        )
        assert len(result) == 1
        assert "nothing to say" in result[0]


# ── Message splitting (retest from client tests for completeness) ──

class TestMessageSplitting:
    def test_under_limit(self):
        assert split_message("short", max_chars=100) == ["short"]

    def test_long_message_splits(self):
        text = "A" * 5000
        chunks = split_message(text, max_chars=DISCORD_SAFE_CHARS)
        assert len(chunks) >= 3
        assert all(len(c) <= DISCORD_SAFE_CHARS for c in chunks)

    def test_preserves_content(self):
        text = "word " * 500  # 2500 chars
        chunks = split_message(text, max_chars=1000)
        reassembled = " ".join(c.strip() for c in chunks)
        # Should preserve all words (whitespace may change at boundaries)
        assert reassembled.count("word") == 500


# ── People profile helpers ──────────────────────────────────────

class TestPeopleHelpers:
    def test_make_fdo_id_simple(self):
        assert _make_people_fdo_id("alice") == "discord-alice"

    def test_make_fdo_id_special_chars(self):
        assert _make_people_fdo_id("Bob_123") == "discord-bob-123"

    def test_make_fdo_id_caps(self):
        assert _make_people_fdo_id("CoolUser") == "discord-cooluser"

    def test_extract_summary_with_section(self, tmp_path):
        fdo = tmp_path / "test.md"
        fdo.write_text("---\nid: test\n---\n# Test\n\n## How GRIM Knows Them\n\nMet in Discord, likes physics.\n\n## Interests\n\nStuff\n")
        assert "likes physics" in _extract_fdo_summary(fdo)

    def test_extract_summary_no_section(self, tmp_path):
        fdo = tmp_path / "test.md"
        fdo.write_text("---\nid: test\n---\n# Test\n\nJust a name.\n")
        assert _extract_fdo_summary(fdo) == ""

    def test_extract_summary_missing_file(self, tmp_path):
        fdo = tmp_path / "nonexistent.md"
        assert _extract_fdo_summary(fdo) == ""

    def test_create_people_fdo(self, tmp_path):
        fdo_path = tmp_path / "people" / "discord-alice.md"
        profile = PeopleProfile(
            fdo_id="discord-alice",
            username="alice",
            display_name="Alice W.",
            discord_user_id=12345,
        )
        _create_people_fdo(fdo_path, profile)
        assert fdo_path.exists()
        content = fdo_path.read_text()
        assert "discord-alice" in content
        assert "Alice W." in content
        assert "12345" in content
        assert "person" in content
        assert "friend" in content


# ── People profiles integration ─────────────────────────────────

class TestPeopleProfiles:
    @pytest.mark.asyncio
    async def test_ensure_fdo_creates_new(self, bot, tmp_path):
        """Creates a vault FDO when user is new."""
        bot.config.vault_path = tmp_path
        (tmp_path / "people").mkdir()
        bot.register_user(42, "alice", "Alice")

        profile = await bot.ensure_people_fdo(42)
        assert profile.exists_in_vault is True
        assert (tmp_path / "people" / "discord-alice.md").exists()

    @pytest.mark.asyncio
    async def test_ensure_fdo_loads_existing(self, bot, tmp_path):
        """Loads existing FDO instead of creating."""
        bot.config.vault_path = tmp_path
        people_dir = tmp_path / "people"
        people_dir.mkdir()
        # Pre-create the FDO
        fdo = people_dir / "discord-bob.md"
        fdo.write_text("---\nid: discord-bob\n---\n# Bob\n\n## How GRIM Knows Them\n\nOld friend from gaming.\n")

        bot.register_user(99, "bob", "Bob")
        profile = await bot.ensure_people_fdo(99)
        assert profile.exists_in_vault is True
        assert "gaming" in profile.summary

    @pytest.mark.asyncio
    async def test_ensure_fdo_links_owner_to_peter(self, config, tmp_path):
        """Owner user links to existing peter.md FDO."""
        config.vault_path = tmp_path
        bot = GrimDiscordBot(config, owner_user_id=42)
        people_dir = tmp_path / "people"
        people_dir.mkdir()
        peter_fdo = people_dir / "peter.md"
        peter_fdo.write_text("---\nid: peter\n---\n# Peter\n\n## Role\n\nFounder of DFI.\n")

        bot.register_user(42, "peterg", "Peter")
        profile = await bot.ensure_people_fdo(42)
        assert profile.fdo_id == "peter"
        assert profile.exists_in_vault is True

    @pytest.mark.asyncio
    async def test_user_context_includes_summary(self, bot, tmp_path):
        """User context includes vault summary when available."""
        bot.config.vault_path = tmp_path
        people_dir = tmp_path / "people"
        people_dir.mkdir()
        fdo = people_dir / "discord-carol.md"
        fdo.write_text("---\nid: discord-carol\n---\n# Carol\n\n## How GRIM Knows Them\n\nPhysicist friend.\n")

        bot.register_user(55, "carol", "Carol")
        await bot.ensure_people_fdo(55)
        ctx = bot.get_user_context(55)
        assert "Carol" in ctx
        assert "Physicist friend" in ctx

    @pytest.mark.asyncio
    async def test_ensure_fdo_unregistered_user(self, bot):
        """Unregistered user gets a fallback profile."""
        profile = await bot.ensure_people_fdo(999)
        assert profile.exists_in_vault is False
        assert profile.discord_user_id == 999


# ── Metrics ─────────────────────────────────────────────────────

class TestDiscordMetrics:
    def test_initial_metrics(self, bot):
        m = bot.metrics
        assert m["active_sessions"] == 0
        assert m["total_messages"] == 0
        assert m["known_users"] == 0
        assert m["total_daily_cost"] == 0.0
        assert m["uptime_seconds"] >= 0

    def test_metrics_after_activity(self, bot):
        from clients.discord_bot import ChannelSession
        mock_client = MagicMock()
        bot.sessions[1] = ChannelSession(client=mock_client, daily_cost=0.05)
        bot.sessions[2] = ChannelSession(client=mock_client, daily_cost=0.10)
        bot._total_messages = 15
        bot.register_user(1, "alice")
        bot.register_user(2, "bob")

        m = bot.metrics
        assert m["active_sessions"] == 2
        assert m["total_messages"] == 15
        assert m["known_users"] == 2
        assert m["total_daily_cost"] == 0.15


# ── Health check server ─────────────────────────────────────────

class TestHealthServer:
    @pytest.mark.asyncio
    async def test_health_endpoint(self, bot):
        from clients.discord_bot import run_health_server

        server = await run_health_server(bot, port=0)
        port = server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(4096), timeout=5)
            response = data.decode()
            assert "200 OK" in response
            assert '"status": "ok"' in response
            assert '"active_sessions"' in response
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_health_404(self, bot):
        from clients.discord_bot import run_health_server

        server = await run_health_server(bot, port=0)
        port = server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /notfound HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(4096), timeout=5)
            assert b"404 Not Found" in data
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()


# ── Env var parsing ─────────────────────────────────────────────

class TestEnvParsing:
    def test_parse_int_list(self):
        from clients.discord_bot import _parse_int_list
        import os
        os.environ["TEST_IDS"] = "123,456,789"
        try:
            result = _parse_int_list("TEST_IDS")
            assert result == [123, 456, 789]
        finally:
            del os.environ["TEST_IDS"]

    def test_parse_int_list_empty(self):
        from clients.discord_bot import _parse_int_list
        result = _parse_int_list("NONEXISTENT_VAR")
        assert result is None

    def test_parse_int_list_single(self):
        from clients.discord_bot import _parse_int_list
        import os
        os.environ["TEST_SINGLE"] = "42"
        try:
            result = _parse_int_list("TEST_SINGLE")
            assert result == [42]
        finally:
            del os.environ["TEST_SINGLE"]
