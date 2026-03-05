"""Tests for server/sessions.py — SessionManager for GrimClient SDK sessions."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.sessions import SessionInfo, SessionManager


# ── Fixtures ─────────────────────────────────────────────────────────────

def _make_mock_config():
    """Build a minimal GrimConfig mock for SessionManager."""
    config = MagicMock()
    config.vault_path = "/vault"
    config.skills_path = "/skills"
    config.workspace_root = "/workspace"
    config.identity_prompt_path = MagicMock()
    config.identity_prompt_path.exists.return_value = False
    config.identity_personality_path = MagicMock()
    config.identity_personality_path.exists.return_value = False
    config.personality_cache_path = MagicMock()
    config.personality_cache_path.exists.return_value = False
    config.kronos_mcp_command = None
    config.kronos_mcp_args = []
    config.pool_enabled = False
    return config


def _mock_grim_client():
    """Create a mock GrimClient that responds to start/stop/send/send_streaming."""
    client = AsyncMock()
    client._started = True
    client.session_info = {
        "started": True,
        "turn_count": 0,
        "total_cost_usd": 0.0,
        "total_agent_turns": 0,
        "caller_id": "peter",
    }
    client.start = AsyncMock()
    client.stop = AsyncMock()
    return client


# ── SessionInfo tests ────────────────────────────────────────────────────

class TestSessionInfo:
    def test_to_dict(self):
        client = _mock_grim_client()
        info = SessionInfo(session_id="test-1", client=client, caller_id="alice")
        d = info.to_dict()
        assert d["session_id"] == "test-1"
        assert d["caller_id"] == "alice"
        assert "started" in d
        assert "turn_count" in d

    def test_touch_updates_last_active(self):
        client = _mock_grim_client()
        info = SessionInfo(session_id="test-1", client=client)
        old_active = info.last_active
        time.sleep(0.05)  # Windows monotonic clock needs more time
        info.touch()
        assert info.last_active > old_active

    def test_idle_seconds(self):
        client = _mock_grim_client()
        info = SessionInfo(session_id="test-1", client=client)
        time.sleep(0.05)
        assert info.idle_seconds > 0


# ── SessionManager tests ────────────────────────────────────────────────

class TestSessionManager:
    @pytest.fixture
    def config(self):
        return _make_mock_config()

    @pytest.fixture
    def manager(self, config):
        return SessionManager(config, max_sessions=3, max_idle_seconds=60)

    @patch("server.sessions.GrimClient")
    async def test_get_or_create_new(self, MockClient, manager):
        mock_instance = _mock_grim_client()
        MockClient.return_value = mock_instance

        client = await manager.get_or_create("session-1")
        assert client is mock_instance
        mock_instance.start.assert_called_once()
        assert manager.active_count == 1

    @patch("server.sessions.GrimClient")
    async def test_get_or_create_reuses_existing(self, MockClient, manager):
        mock_instance = _mock_grim_client()
        MockClient.return_value = mock_instance

        client1 = await manager.get_or_create("session-1")
        client2 = await manager.get_or_create("session-1")
        assert client1 is client2
        # Only one start() call — session reused
        assert mock_instance.start.call_count == 1
        assert manager.active_count == 1

    @patch("server.sessions.GrimClient")
    async def test_get_or_create_evicts_at_capacity(self, MockClient, manager):
        clients = []
        for i in range(3):
            mock = _mock_grim_client()
            clients.append(mock)

        MockClient.side_effect = clients
        await manager.get_or_create("s1", caller_id="a")
        await manager.get_or_create("s2", caller_id="b")
        await manager.get_or_create("s3", caller_id="c")
        assert manager.active_count == 3

        # 4th session should evict the oldest (s1)
        mock4 = _mock_grim_client()
        MockClient.side_effect = [mock4]
        await manager.get_or_create("s4", caller_id="d")
        assert manager.active_count == 3
        # s1 was evicted — its client.stop() was called
        clients[0].stop.assert_called_once()

    @patch("server.sessions.GrimClient")
    async def test_destroy(self, MockClient, manager):
        mock_instance = _mock_grim_client()
        MockClient.return_value = mock_instance

        await manager.get_or_create("session-1")
        assert manager.active_count == 1

        existed = await manager.destroy("session-1")
        assert existed is True
        mock_instance.stop.assert_called_once()
        assert manager.active_count == 0

    @patch("server.sessions.GrimClient")
    async def test_destroy_nonexistent(self, MockClient, manager):
        existed = await manager.destroy("no-such-session")
        assert existed is False

    @patch("server.sessions.GrimClient")
    async def test_list_sessions(self, MockClient, manager):
        mock1 = _mock_grim_client()
        mock2 = _mock_grim_client()
        MockClient.side_effect = [mock1, mock2]

        await manager.get_or_create("s1", caller_id="alice")
        await manager.get_or_create("s2", caller_id="bob")

        sessions = manager.list_sessions()
        assert len(sessions) == 2
        ids = {s["session_id"] for s in sessions}
        assert ids == {"s1", "s2"}

    @patch("server.sessions.GrimClient")
    async def test_touch_updates_activity(self, MockClient, manager):
        mock_instance = _mock_grim_client()
        MockClient.return_value = mock_instance

        await manager.get_or_create("session-1")
        sessions = manager.list_sessions()
        old_idle = sessions[0]["idle_seconds"]
        time.sleep(0.01)
        manager.touch("session-1")
        sessions = manager.list_sessions()
        assert sessions[0]["idle_seconds"] <= old_idle

    @patch("server.sessions.GrimClient")
    async def test_stop_destroys_all(self, MockClient, manager):
        mocks = [_mock_grim_client(), _mock_grim_client()]
        MockClient.side_effect = mocks

        await manager.get_or_create("s1")
        await manager.get_or_create("s2")

        await manager.stop()
        for m in mocks:
            m.stop.assert_called_once()
        assert manager.active_count == 0

    @patch("server.sessions.GrimClient")
    async def test_caller_id_passed_through(self, MockClient, manager):
        mock_instance = _mock_grim_client()
        MockClient.return_value = mock_instance

        await manager.get_or_create("session-1", caller_id="discord-alice")
        MockClient.assert_called_once()
        _, kwargs = MockClient.call_args
        assert kwargs["caller_id"] == "discord-alice"


# ── Reaper tests ─────────────────────────────────────────────────────────

class TestSessionReaper:
    @patch("server.sessions.GrimClient")
    async def test_reaper_removes_idle_sessions(self, MockClient):
        config = _make_mock_config()
        # Use very short idle timeout; reaper checks every 60s so we patch the loop
        manager = SessionManager(config, max_sessions=5, max_idle_seconds=0.01)

        mock_instance = _mock_grim_client()
        MockClient.return_value = mock_instance

        await manager.get_or_create("old-session")
        assert manager.active_count == 1

        # Manually run the reap logic instead of relying on the background loop
        await asyncio.sleep(0.05)  # ensure idle time exceeds threshold
        async with manager._lock:
            expired = [
                sid for sid, info in manager._sessions.items()
                if info.idle_seconds > manager.max_idle_seconds
            ]
            for sid in expired:
                info = manager._sessions.pop(sid)
                await manager._destroy_session(info)

        assert manager.active_count == 0
        mock_instance.stop.assert_called_once()
