"""Tests for v2 WebSocket and REST endpoints (GrimClient SDK sessions).

These tests mock the SessionManager and GrimClient to test the server
endpoints without needing a real SDK or MCP connection.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_mock_session_manager():
    """Build a mock SessionManager that returns mock GrimClients."""
    manager = MagicMock()
    manager.active_count = 0
    manager.list_sessions.return_value = []

    mock_client = AsyncMock()
    mock_client.session_info = {
        "started": True,
        "turn_count": 1,
        "total_cost_usd": 0.001,
        "total_agent_turns": 2,
        "caller_id": "peter",
    }

    mock_response = MagicMock()
    mock_response.text = "Hello from GRIM v2!"
    mock_response.tool_calls = []
    mock_response.cost_usd = 0.001
    mock_response.num_turns = 1
    mock_client.send = AsyncMock(return_value=mock_response)

    manager.get_or_create = AsyncMock(return_value=mock_client)
    manager.destroy = AsyncMock(return_value=True)
    manager.touch = MagicMock()

    return manager, mock_client


@contextmanager
def _mock_sm():
    """Context manager: set _session_manager to a mock after lifespan runs."""
    import server.app as app_module
    manager, client = _make_mock_session_manager()
    with TestClient(app_module.app, raise_server_exceptions=False) as tc:
        original = app_module._session_manager
        app_module._session_manager = manager
        try:
            yield tc, manager, client
        finally:
            app_module._session_manager = original


@contextmanager
def _no_sm():
    """Context manager: set _session_manager to None after lifespan runs."""
    import server.app as app_module
    with TestClient(app_module.app, raise_server_exceptions=False) as tc:
        original = app_module._session_manager
        app_module._session_manager = None
        try:
            yield tc
        finally:
            app_module._session_manager = original


# ── REST v2 chat tests ──────────────────────────────────────────────────

class TestV2RestChat:
    def test_chat_v2_rest_basic(self):
        with _mock_sm() as (tc, manager, client):
            resp = tc.post("/api/v2/chat", json={
                "message": "Hello GRIM",
                "session_id": "test-1",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["response"] == "Hello from GRIM v2!"
            assert data["session_id"] == "test-1"

    def test_chat_v2_rest_auto_session_id(self):
        with _mock_sm() as (tc, manager, client):
            resp = tc.post("/api/v2/chat", json={"message": "Hello"})
            assert resp.status_code == 200
            data = resp.json()
            assert "session_id" in data
            assert len(data["session_id"]) > 0

    def test_chat_v2_rest_with_caller_id(self):
        with _mock_sm() as (tc, manager, client):
            resp = tc.post("/api/v2/chat", json={
                "message": "Hello",
                "caller_id": "discord-alice",
            })
            assert resp.status_code == 200
            manager.get_or_create.assert_called()

    def test_chat_v2_rest_includes_cost(self):
        with _mock_sm() as (tc, manager, client):
            resp = tc.post("/api/v2/chat", json={"message": "Hi", "session_id": "s1"})
            data = resp.json()
            assert "cost_usd" in data
            assert "num_turns" in data

    def test_chat_v2_rest_no_session_manager(self):
        with _no_sm() as tc:
            resp = tc.post("/api/v2/chat", json={"message": "Hello"})
            assert resp.status_code == 503

    def test_chat_v2_rest_error_handling(self):
        with _mock_sm() as (tc, manager, client):
            client.send = AsyncMock(side_effect=RuntimeError("SDK boom"))
            manager.get_or_create = AsyncMock(return_value=client)
            resp = tc.post("/api/v2/chat", json={"message": "Hi", "session_id": "s1"})
            assert resp.status_code == 500
            assert "SDK boom" in resp.json()["error"]


# ── Session list/destroy tests ───────────────────────────────────────────

class TestV2SessionAPIs:
    def test_list_sessions(self):
        with _mock_sm() as (tc, manager, _):
            manager.list_sessions.return_value = [
                {"session_id": "s1", "caller_id": "peter", "idle_seconds": 5,
                 "started": True, "turn_count": 3, "total_cost_usd": 0.01},
            ]
            manager.active_count = 1

            resp = tc.get("/api/v2/sessions")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert len(data["sessions"]) == 1
            assert data["sessions"][0]["session_id"] == "s1"

    def test_list_sessions_empty(self):
        with _mock_sm() as (tc, manager, _):
            resp = tc.get("/api/v2/sessions")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 0
            assert data["sessions"] == []

    def test_list_sessions_no_manager(self):
        with _no_sm() as tc:
            resp = tc.get("/api/v2/sessions")
            assert resp.status_code == 503

    def test_destroy_session(self):
        with _mock_sm() as (tc, manager, _):
            manager.destroy = AsyncMock(return_value=True)
            resp = tc.delete("/api/v2/sessions/test-session")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True

    def test_destroy_nonexistent_session(self):
        with _mock_sm() as (tc, manager, _):
            manager.destroy = AsyncMock(return_value=False)
            resp = tc.delete("/api/v2/sessions/no-such")
            assert resp.status_code == 404

    def test_destroy_no_manager(self):
        with _no_sm() as tc:
            resp = tc.delete("/api/v2/sessions/x")
            assert resp.status_code == 503


# ── WebSocket v2 tests ───────────────────────────────────────────────────

class TestV2WebSocket:
    def test_websocket_v2_no_session_manager(self):
        """When session manager is None, WebSocket returns error and closes."""
        import server.app as app_module
        with TestClient(app_module.app) as tc:
            original = app_module._session_manager
            app_module._session_manager = None
            try:
                with tc.websocket_connect("/ws/v2/test") as ws:
                    data = ws.receive_json()
                    assert data["type"] == "error"
                    assert "not available" in data["content"]
            finally:
                app_module._session_manager = original
