"""Tests for ConversationStore — SQLite conversation persistence."""
import asyncio
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from server.conversation_store import ConversationStore


@pytest_asyncio.fixture
async def store(tmp_path):
    """Create a fresh ConversationStore with a temp database."""
    db_path = tmp_path / "test_conversations.db"
    s = ConversationStore(db_path)
    await s.init()
    yield s
    await s.close()


# ── Session CRUD ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_and_get_session(store):
    await store.save_session("s1", caller_id="peter", metadata={"foo": "bar"})
    session = await store.get_session("s1")
    assert session is not None
    assert session["session_id"] == "s1"
    assert session["caller_id"] == "peter"
    assert session["metadata"] == {"foo": "bar"}
    assert session["closed"] is False


@pytest.mark.asyncio
async def test_get_nonexistent_session(store):
    result = await store.get_session("nope")
    assert result is None


@pytest.mark.asyncio
async def test_save_session_upsert(store):
    await store.save_session("s1", metadata={"v": 1})
    await store.save_session("s1", metadata={"v": 2})
    session = await store.get_session("s1")
    assert session["metadata"] == {"v": 2}


@pytest.mark.asyncio
async def test_close_session(store):
    await store.save_session("s1")
    await store.close_session("s1")
    session = await store.get_session("s1")
    assert session["closed"] is True


@pytest.mark.asyncio
async def test_touch_session(store):
    await store.save_session("s1")
    s1 = await store.get_session("s1")
    original_active = s1["last_active"]
    # Small delay to ensure timestamp changes
    await asyncio.sleep(0.01)
    await store.touch_session("s1")
    s2 = await store.get_session("s1")
    assert s2["last_active"] >= original_active


@pytest.mark.asyncio
async def test_list_sessions_excludes_closed(store):
    await store.save_session("s1")
    await store.save_session("s2")
    await store.close_session("s2")
    sessions = await store.list_sessions(include_closed=False)
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "s1"


@pytest.mark.asyncio
async def test_list_sessions_includes_closed(store):
    await store.save_session("s1")
    await store.save_session("s2")
    await store.close_session("s2")
    sessions = await store.list_sessions(include_closed=True)
    assert len(sessions) == 2


@pytest.mark.asyncio
async def test_delete_session(store):
    await store.save_session("s1")
    await store.save_message("s1", 1, "hello", "hi")
    result = await store.delete_session("s1")
    assert result is True
    assert await store.get_session("s1") is None
    assert await store.get_messages("s1") == []


@pytest.mark.asyncio
async def test_delete_nonexistent_session(store):
    result = await store.delete_session("nope")
    assert result is False


# ── Message CRUD ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_and_get_messages(store):
    await store.save_session("s1")
    row_id = await store.save_message(
        "s1", turn_number=1,
        user_message="What is PAC?",
        assistant_message="PAC is...",
        cost_usd=0.05,
        tools_used=["kronos_search", "kronos_get"],
    )
    assert isinstance(row_id, int)

    msgs = await store.get_messages("s1")
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg["user_message"] == "What is PAC?"
    assert msg["assistant_message"] == "PAC is..."
    assert msg["cost_usd"] == 0.05
    assert msg["tools_used"] == ["kronos_search", "kronos_get"]
    assert msg["turn_number"] == 1


@pytest.mark.asyncio
async def test_messages_ordered_by_turn(store):
    await store.save_session("s1")
    await store.save_message("s1", 1, "first")
    await store.save_message("s1", 2, "second")
    await store.save_message("s1", 3, "third")

    msgs = await store.get_messages("s1")
    assert [m["turn_number"] for m in msgs] == [1, 2, 3]


@pytest.mark.asyncio
async def test_messages_pagination(store):
    await store.save_session("s1")
    for i in range(5):
        await store.save_message("s1", i + 1, f"msg {i + 1}")

    page1 = await store.get_messages("s1", limit=2, offset=0)
    assert len(page1) == 2
    assert page1[0]["turn_number"] == 1

    page2 = await store.get_messages("s1", limit=2, offset=2)
    assert len(page2) == 2
    assert page2[0]["turn_number"] == 3


@pytest.mark.asyncio
async def test_message_count(store):
    await store.save_session("s1")
    assert await store.get_message_count("s1") == 0
    await store.save_message("s1", 1, "hello")
    await store.save_message("s1", 2, "world")
    assert await store.get_message_count("s1") == 2


@pytest.mark.asyncio
async def test_message_without_tools(store):
    await store.save_session("s1")
    await store.save_message("s1", 1, "hello", "hi")
    msgs = await store.get_messages("s1")
    assert msgs[0]["tools_used"] is None


@pytest.mark.asyncio
async def test_messages_isolated_by_session(store):
    await store.save_session("s1")
    await store.save_session("s2")
    await store.save_message("s1", 1, "s1 msg")
    await store.save_message("s2", 1, "s2 msg")

    s1_msgs = await store.get_messages("s1")
    s2_msgs = await store.get_messages("s2")
    assert len(s1_msgs) == 1
    assert s1_msgs[0]["user_message"] == "s1 msg"
    assert len(s2_msgs) == 1
    assert s2_msgs[0]["user_message"] == "s2 msg"


# ── Init / Lifecycle ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_creates_tables(tmp_path):
    """Tables are created on init, even on fresh DB."""
    db_path = tmp_path / "fresh.db"
    s = ConversationStore(db_path)
    await s.init()
    # Should work immediately
    await s.save_session("test")
    session = await s.get_session("test")
    assert session is not None
    await s.close()


@pytest.mark.asyncio
async def test_reopen_preserves_data(tmp_path):
    """Data survives close + reopen."""
    db_path = tmp_path / "persist.db"

    s1 = ConversationStore(db_path)
    await s1.init()
    await s1.save_session("s1", caller_id="peter")
    await s1.save_message("s1", 1, "hello", "world")
    await s1.close()

    s2 = ConversationStore(db_path)
    await s2.init()
    session = await s2.get_session("s1")
    assert session is not None
    assert session["caller_id"] == "peter"
    msgs = await s2.get_messages("s1")
    assert len(msgs) == 1
    assert msgs[0]["user_message"] == "hello"
    await s2.close()
