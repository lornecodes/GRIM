"""Shared pytest fixtures for GRIM test suite."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure GRIM root is on path
GRIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GRIM_ROOT))

# Ensure Kronos MCP engines are initialized for tests that call handlers directly.
# In production, initialization is deferred to first tool call to avoid blocking
# the MCP init handshake. Tests bypass call_tool() so need explicit init.
_kronos_src = GRIM_ROOT / "mcp" / "kronos" / "src"
if _kronos_src.is_dir():
    sys.path.insert(0, str(_kronos_src))
    _vault = GRIM_ROOT.parent / "kronos-vault"
    _skills = GRIM_ROOT / "skills"
    if _vault.is_dir():
        os.environ.setdefault("KRONOS_VAULT_PATH", str(_vault))
    if _skills.is_dir():
        os.environ.setdefault("KRONOS_SKILLS_PATH", str(_skills))


@pytest.fixture(autouse=True, scope="session")
def _init_kronos_engines():
    """Auto-initialize Kronos MCP engines for tests that call handlers directly.

    In production, engines are lazy-initialized on first tool call to avoid
    blocking the MCP stdio init handshake. Tests bypass call_tool(), so
    we initialize here once per session.
    """
    try:
        from kronos_mcp.server import _ensure_initialized, _engines_initialized
        if not _engines_initialized:
            _ensure_initialized()
    except (ImportError, ValueError):
        pass  # Not all test runs need kronos

from core.config import GrimConfig
from core.state import AgentResult, FDOSummary, FieldState, GrimState, SkillContext


# ── Mock infrastructure ─────────────────────────────────────────────────

class MockMCPResult:
    """Simulate an MCP call_tool result."""
    def __init__(self, data: dict | list | str):
        text = data if isinstance(data, str) else json.dumps(data)
        self.content = [SimpleNamespace(text=text)]


class MockMCPSession:
    """Mock MCP client session that returns canned responses."""
    def __init__(self, responses: dict[str, Any] | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, method: str, args: dict | None = None) -> MockMCPResult:
        self.calls.append((method, args or {}))
        if method in self._responses:
            return MockMCPResult(self._responses[method])
        return MockMCPResult({"results": []})


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def grim_root():
    """GRIM project root path."""
    return GRIM_ROOT


@pytest.fixture
def grim_config():
    """Default GrimConfig for testing (debug mode, test vault)."""
    return GrimConfig(
        env="debug",
        vault_path=GRIM_ROOT / "tests" / "vault",
        skills_path=GRIM_ROOT / "skills",
        identity_prompt_path=GRIM_ROOT / "identity" / "system_prompt.md",
        identity_personality_path=GRIM_ROOT / "identity" / "personality.yaml",
        local_dir=GRIM_ROOT / "local",
        model="claude-sonnet-4-6",
    )


@pytest.fixture
def mock_mcp_session():
    """Mock MCP session with empty responses."""
    return MockMCPSession()


@pytest.fixture
def mock_mcp_session_factory():
    """Factory for MockMCPSession with custom responses."""
    def _factory(responses: dict[str, Any] | None = None):
        return MockMCPSession(responses)
    return _factory


@pytest.fixture
def mock_state():
    """Minimal GrimState dict for testing."""
    from langchain_core.messages import HumanMessage
    return {
        "messages": [HumanMessage(content="test message")],
        "system_prompt": "You are GRIM.",
        "field_state": FieldState(),
        "knowledge_context": [],
        "matched_skills": [],
        "skill_protocols": {},
        "mode": "companion",
        "delegation_type": None,
        "selected_model": None,
        "agent_result": None,
        "caller_id": "peter",
    }


@pytest.fixture
def sample_fdo():
    """Sample FDOSummary for testing."""
    return FDOSummary(
        id="test-fdo",
        title="Test FDO",
        domain="ai-systems",
        status="stable",
        confidence=0.8,
        summary="A test FDO for unit tests",
        tags=["test"],
        related=["other-fdo"],
    )


@pytest.fixture
def sample_skill_context():
    """Sample SkillContext for testing."""
    return SkillContext(
        name="test-skill",
        version="1.0.0",
        description="A test skill",
        permissions=["vault:read"],
        triggers={"keywords": ["test"]},
    )


@pytest.fixture
def tmp_vault(tmp_path):
    """Temporary vault directory with minimal structure."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "projects").mkdir()
    (vault / "ai-systems").mkdir()
    (vault / "physics").mkdir()
    (vault / "calendar").mkdir()

    # Minimal board.yaml
    (vault / "projects" / "board.yaml").write_text(
        "columns:\n  new: []\n  active: []\n  in_progress: []\n  resolved: []\n  closed: []\n",
        encoding="utf-8",
    )
    # Minimal calendar files
    (vault / "calendar" / "schedule.yaml").write_text("entries: []\n", encoding="utf-8")
    (vault / "calendar" / "personal.yaml").write_text("entries: []\n", encoding="utf-8")

    return vault
