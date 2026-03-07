"""Tests for Discord bot pool client integration."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.discord_bot import (
    DISCORD_GUEST_TOOLS,
    DISCORD_OWNER_TOOLS,
    DISCORD_FORMAT_ADDENDUM,
    DISCORD_VOICE_PREAMBLE,
    ChannelSession,
    GrimDiscordBot,
    format_pool_event,
    parse_clarification,
    split_message,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.vault_path = MagicMock()
    cfg.vault_path.__truediv__ = MagicMock(return_value=MagicMock(exists=MagicMock(return_value=False)))
    return cfg


@pytest.fixture
def bot(config):
    return GrimDiscordBot(
        config,
        owner_user_id=12345,
        allowed_guild_ids=[100],
        allowed_channel_ids=[200],
    )


# ── Owner-only tool enforcement ──────────────────────────────────


def test_owner_has_write_tools():
    """Owner has vault write + task tools that guests don't."""
    assert "mcp__kronos__kronos_create" in DISCORD_OWNER_TOOLS
    assert "mcp__kronos__kronos_task_dispatch" in DISCORD_OWNER_TOOLS
    assert "mcp__kronos__kronos_create" not in DISCORD_GUEST_TOOLS
    assert "mcp__kronos__kronos_task_dispatch" not in DISCORD_GUEST_TOOLS


def test_pool_proxy_tools_owner_only():
    """Pool proxy tools are in owner list but not guest list."""
    assert "mcp__pool_proxy__pool_submit" in DISCORD_OWNER_TOOLS
    assert "mcp__pool_proxy__pool_cancel" in DISCORD_OWNER_TOOLS
    assert "mcp__pool_proxy__pool_submit" not in DISCORD_GUEST_TOOLS
    # No direct pool MCP tools (mcp__pool__*) — only proxied ones
    direct_pool = [t for t in DISCORD_OWNER_TOOLS if t.startswith("mcp__pool__")]
    assert direct_pool == [], f"Direct pool tools found: {direct_pool}"


def test_get_allowed_tools_owner(bot):
    tools = bot.get_allowed_tools(12345)
    assert "mcp__kronos__kronos_create" in tools
    assert "mcp__kronos__kronos_task_dispatch" in tools
    assert "mcp__pool_proxy__pool_submit" in tools


def test_get_allowed_tools_non_owner(bot):
    tools = bot.get_allowed_tools(99999)
    assert "mcp__kronos__kronos_create" not in tools
    assert "mcp__kronos__kronos_update" not in tools
    assert "mcp__pool_proxy__pool_submit" not in tools
    assert "mcp__kronos__kronos_search" in tools


def test_get_allowed_tools_guest_has_read_tools(bot):
    tools = bot.get_allowed_tools(99999)
    assert "mcp__kronos__kronos_search" in tools
    assert "mcp__kronos__kronos_get" in tools
    assert "mcp__kronos__kronos_list" in tools


# ── Discord formatting & caller_id ──────────────────────────────


def test_discord_voice_preamble():
    """Voice preamble contains critical personality rules."""
    assert "NO EMOJI" in DISCORD_VOICE_PREAMBLE
    assert "Shade" in DISCORD_VOICE_PREAMBLE
    assert "He Who Fights With Monsters" in DISCORD_VOICE_PREAMBLE
    assert "exclamation" in DISCORD_VOICE_PREAMBLE.lower()
    assert "Coherence" in DISCORD_VOICE_PREAMBLE


def test_discord_format_addendum_exists():
    """DISCORD_FORMAT_ADDENDUM contains reminder rules."""
    assert "Discord" in DISCORD_FORMAT_ADDENDUM
    assert "emoji" in DISCORD_FORMAT_ADDENDUM.lower()


def test_user_context_owner(bot):
    """Owner gets creator context, no privacy restriction."""
    bot.register_user(12345, "peter", "Peter")
    ctx = bot.get_user_context(12345)
    assert "Peter" in ctx
    assert "creator" in ctx
    assert "PRIVACY" not in ctx


def test_user_context_non_owner(bot):
    """Non-owner gets guest context WITH privacy restriction."""
    bot.register_user(99999, "groom.yourself", "Groom")
    ctx = bot.get_user_context(99999)
    assert "Groom" in ctx
    assert "guest" in ctx
    assert "PRIVACY" in ctx
    assert "Do NOT share" in ctx


def test_user_context_unknown(bot):
    """Unknown user (not registered) gets privacy restriction."""
    ctx = bot.get_user_context(77777)
    assert "unknown" in ctx.lower()
    assert "PRIVACY" in ctx


# ── Pool event formatting ────────────────────────────────────────


def test_format_job_complete():
    event = {
        "type": "job_complete",
        "job_id": "job-abc",
        "result_preview": "All tests pass",
        "cost_usd": 0.0512,
        "num_turns": 5,
        "diff_stat": "2 files changed",
    }
    result = format_pool_event(event)
    assert "Job Complete" in result
    assert "job-abc" in result
    assert "$0.0512" in result
    assert "2 files changed" in result


def test_format_job_failed():
    event = {
        "type": "job_failed",
        "job_id": "job-fail",
        "error": "Syntax error",
    }
    result = format_pool_event(event)
    assert "Job Failed" in result
    assert "Syntax error" in result


def test_format_job_blocked():
    event = {
        "type": "job_blocked",
        "job_id": "job-block",
        "question": "Which database?",
    }
    result = format_pool_event(event)
    assert "Job Needs Input" in result
    assert "Which database?" in result
    assert "clarify job-block" in result


def test_format_job_review():
    event = {
        "type": "job_review",
        "job_id": "job-rev",
        "workspace_id": "ws-123",
        "changed_files": ["a.py", "b.py"],
        "diff_stat": "2 files changed",
    }
    result = format_pool_event(event)
    assert "Review" in result
    assert "ws-123" in result


def test_format_job_cancelled():
    event = {"type": "job_cancelled", "job_id": "job-x"}
    result = format_pool_event(event)
    assert "Cancelled" in result


def test_format_unknown_event():
    event = {"type": "some_other_event", "job_id": "j1"}
    result = format_pool_event(event)
    assert result is None


def test_format_missing_fields():
    event = {"type": "job_complete", "job_id": "j1"}
    result = format_pool_event(event)
    assert "Job Complete" in result
    assert "$0.0000" in result


# ── Clarification parsing ────────────────────────────────────────


def test_parse_clarification_valid():
    result = parse_clarification("clarify job-abc123 Use PostgreSQL")
    assert result == ("job-abc123", "Use PostgreSQL")


def test_parse_clarification_multiword_answer():
    result = parse_clarification("clarify job-abc I want to use Redis for caching")
    assert result == ("job-abc", "I want to use Redis for caching")


def test_parse_clarification_case_insensitive():
    result = parse_clarification("CLARIFY job-xyz yes")
    assert result == ("job-xyz", "yes")


def test_parse_clarification_not_a_clarify():
    result = parse_clarification("what is the pool status?")
    assert result is None


def test_parse_clarification_empty_answer():
    # Regex requires at least one char after job_id
    result = parse_clarification("clarify job-abc ")
    assert result is None


# ── Session tracking ─────────────────────────────────────────────


def test_channel_session_submitted_jobs():
    session = ChannelSession(
        client=MagicMock(),
        submitted_jobs=set(),
    )
    session.submitted_jobs.add("job-1")
    session.submitted_jobs.add("job-2")
    assert "job-1" in session.submitted_jobs
    assert "job-3" not in session.submitted_jobs


def test_find_channel_for_job(bot):
    session = ChannelSession(
        client=MagicMock(),
        submitted_jobs={"job-abc", "job-def"},
    )
    bot.sessions[200] = session
    assert bot.find_channel_for_job("job-abc") == 200
    assert bot.find_channel_for_job("job-xyz") is None


# ── Handle pool event ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_pool_event_routes_to_channel(bot):
    session = ChannelSession(
        client=MagicMock(),
        submitted_jobs={"job-routed"},
    )
    bot.sessions[200] = session
    event = {"type": "job_complete", "job_id": "job-routed", "cost_usd": 0.01, "num_turns": 2}
    result = await bot.handle_pool_event(event)
    assert result is not None
    assert 200 in result
    assert "Job Complete" in result[200]


@pytest.mark.asyncio
async def test_handle_pool_event_no_match(bot):
    event = {"type": "job_complete", "job_id": "job-unknown"}
    result = await bot.handle_pool_event(event)
    assert result is None


@pytest.mark.asyncio
async def test_handle_pool_event_empty_event(bot):
    result = await bot.handle_pool_event({})
    assert result is None


# ── Conversation persistence ────────────────────────────────────


@pytest.fixture
async def bot_with_store(config, tmp_path):
    """Bot with a real ConversationStore backed by temp SQLite."""
    b = GrimDiscordBot(
        config,
        owner_user_id=12345,
        allowed_guild_ids=[100],
        allowed_channel_ids=[200],
    )
    await b.init_store(str(tmp_path / "test_discord.db"))
    yield b
    await b.close_all()


@pytest.mark.asyncio
async def test_init_store_creates_db(bot_with_store):
    assert bot_with_store.store is not None


@pytest.mark.asyncio
async def test_store_save_and_retrieve(bot_with_store):
    store = bot_with_store.store
    await store.save_session("discord-200", caller_id="discord")
    await store.save_message(
        session_id="discord-200",
        turn_number=1,
        user_message="what is SEC?",
        assistant_message="SEC is Symbolic Entropy Collapse.",
        cost_usd=0.01,
    )
    messages = await store.get_messages("discord-200")
    assert len(messages) == 1
    assert messages[0]["user_message"] == "what is SEC?"
    assert messages[0]["assistant_message"] == "SEC is Symbolic Entropy Collapse."


@pytest.mark.asyncio
async def test_build_context_preamble_empty(bot_with_store):
    """No history → empty preamble."""
    preamble = await bot_with_store._build_context_preamble(999)
    assert preamble == ""


@pytest.mark.asyncio
async def test_build_context_preamble_recent_only(bot_with_store):
    """3 messages → all in 'Recent messages' section."""
    store = bot_with_store.store
    await store.save_session("discord-200", caller_id="discord")
    for i in range(3):
        await store.save_message(
            session_id="discord-200",
            turn_number=i + 1,
            user_message=f"question {i}",
            assistant_message=f"answer {i}",
        )
    preamble = await bot_with_store._build_context_preamble(200)
    assert "Recent messages:" in preamble
    assert "question 0" in preamble
    assert "answer 2" in preamble
    # No summary section with only 3 messages
    assert "Summary of earlier" not in preamble


@pytest.mark.asyncio
async def test_build_context_preamble_with_compaction(bot_with_store):
    """10 messages → first 5 compacted, last 5 verbatim."""
    store = bot_with_store.store
    await store.save_session("discord-200", caller_id="discord")
    for i in range(10):
        await store.save_message(
            session_id="discord-200",
            turn_number=i + 1,
            user_message=f"question {i}",
            assistant_message=f"answer {i}",
        )
    preamble = await bot_with_store._build_context_preamble(200)
    assert "Summary of earlier" in preamble
    assert "Recent messages:" in preamble
    # Recent should have the last 5
    assert "question 5" in preamble
    assert "question 9" in preamble


@pytest.mark.asyncio
async def test_build_context_preamble_truncation(bot_with_store):
    """Preamble respects max_chars limit."""
    store = bot_with_store.store
    await store.save_session("discord-200", caller_id="discord")
    for i in range(50):
        await store.save_message(
            session_id="discord-200",
            turn_number=i + 1,
            user_message=f"long question about topic number {i} with extra detail",
            assistant_message=f"detailed answer about topic {i} with lots of context",
        )
    preamble = await bot_with_store._build_context_preamble(200, max_chars=500)
    assert len(preamble) <= 520  # small fudge for truncation marker
    assert "[...truncated]" in preamble


@pytest.mark.asyncio
async def test_context_injected_flag():
    """context_injected starts False, set after first message."""
    session = ChannelSession(client=MagicMock())
    assert session.context_injected is False
    session.context_injected = True
    assert session.context_injected is True
