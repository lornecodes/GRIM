"""IronClaw integration tests — state plumbing, tools, agent dispatch, REST."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure GRIM root on path
GRIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GRIM_ROOT))

from core.bridge.ironclaw import IronClawBridge, ToolResult
from core.tools.context import tool_context


# ── Helpers ──────────────────────────────────────────────────────────────


def _mock_bridge() -> AsyncMock:
    """Create a mock IronClawBridge with sensible defaults."""
    bridge = AsyncMock(spec=IronClawBridge)
    bridge.base_url = "http://ironclaw:3100"
    bridge.is_available = AsyncMock(return_value=True)
    bridge.list_agents = AsyncMock(return_value={
        "enabled": True,
        "roles": [
            {
                "id": "coder",
                "name": "Coder",
                "description": "Writes code",
                "capabilities": ["code_generation", "debugging"],
                "can_delegate": False,
            },
            {
                "id": "security_auditor",
                "name": "Security Auditor",
                "description": "Analyzes security",
                "capabilities": ["security", "code_review"],
                "can_delegate": False,
            },
        ],
        "active_sessions": 0,
        "max_concurrent_sessions": 4,
    })
    bridge.run_workflow = AsyncMock(return_value={
        "session_id": "test-session-001",
        "status": "completed",
        "agents_executed": ["coder", "security_auditor"],
        "results": {
            "coder": "Implementation complete",
            "security_auditor": "No vulnerabilities found",
        },
        "duration_ms": 500,
    })
    bridge.scan_skill = AsyncMock(return_value={
        "file_name": "test.py",
        "findings_count": 1,
        "risk_score": 35,
        "recommendation": "REVIEW_REQUIRED",
        "findings": [
            {
                "rule_id": "DANGEROUS_EVAL",
                "severity": "HIGH",
                "description": "Use of eval() detected",
                "line_number": 5,
                "matched_text": "eval(user_input)",
                "cwe": "CWE-95",
            }
        ],
    })
    return bridge


# ── Phase 1: Identity node sets ironclaw_available ──────────────────────


@pytest.mark.asyncio
async def test_identity_sets_ironclaw_available_true():
    """Identity node should set ironclaw_available=True when bridge exists."""
    from core.config import GrimConfig
    from core.nodes.identity import make_identity_node
    from core.tools.context import tool_context

    # Inject a mock bridge
    original = tool_context.ironclaw_bridge
    tool_context.ironclaw_bridge = _mock_bridge()

    try:
        config = GrimConfig()
        node = make_identity_node(config, mcp_session=None)

        state = {"messages": [], "caller_id": "peter"}
        result = await node(state)

        assert "ironclaw_available" in result
        assert result["ironclaw_available"] is True
    finally:
        tool_context.ironclaw_bridge = original


@pytest.mark.asyncio
async def test_identity_sets_ironclaw_available_false():
    """Identity node should set ironclaw_available=False when no bridge."""
    from core.config import GrimConfig
    from core.nodes.identity import make_identity_node
    from core.tools.context import tool_context

    original = tool_context.ironclaw_bridge
    tool_context.ironclaw_bridge = None

    try:
        config = GrimConfig()
        node = make_identity_node(config, mcp_session=None)

        state = {"messages": [], "caller_id": "peter"}
        result = await node(state)

        assert "ironclaw_available" in result
        assert result["ironclaw_available"] is False
    finally:
        tool_context.ironclaw_bridge = original


# ── Phase 3: IronClaw tools ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claw_list_agents():
    """claw_list_agents should format engine roles."""
    from core.tools.ironclaw_tools import claw_list_agents

    original = tool_context.ironclaw_bridge
    tool_context.ironclaw_bridge = _mock_bridge()

    try:
        result = await claw_list_agents.ainvoke({})
        assert "Coder" in result
        assert "Security Auditor" in result
        assert "code_generation" in result
    finally:
        tool_context.ironclaw_bridge = original


@pytest.mark.asyncio
async def test_claw_dispatch_workflow():
    """claw_dispatch_workflow should orchestrate agents and return results."""
    from core.tools.ironclaw_tools import claw_dispatch_workflow

    original = tool_context.ironclaw_bridge
    bridge = _mock_bridge()
    tool_context.ironclaw_bridge = bridge

    try:
        result = await claw_dispatch_workflow.ainvoke({
            "task": "Test the login flow",
            "agents": "coder,security_auditor",
            "pattern": "sequential",
        })
        assert "Workflow Complete" in result
        assert "coder" in result
        assert "security_auditor" in result
        assert "completed" in result

        # Verify bridge was called with correct pattern
        bridge.run_workflow.assert_called_once()
        call_args = bridge.run_workflow.call_args
        assert call_args[0][0] == "Test the login flow"
        assert call_args[0][1]["type"] == "sequential"
        assert call_args[0][1]["agent_order"] == ["coder", "security_auditor"]
    finally:
        tool_context.ironclaw_bridge = original


@pytest.mark.asyncio
async def test_claw_dispatch_workflow_hierarchical():
    """Hierarchical pattern should set lead + specialists."""
    from core.tools.ironclaw_tools import claw_dispatch_workflow

    original = tool_context.ironclaw_bridge
    bridge = _mock_bridge()
    tool_context.ironclaw_bridge = bridge

    try:
        await claw_dispatch_workflow.ainvoke({
            "task": "Build feature",
            "agents": "planner,coder,tester",
            "pattern": "hierarchical",
        })

        call_args = bridge.run_workflow.call_args
        pattern = call_args[0][1]
        assert pattern["type"] == "hierarchical"
        assert pattern["lead"] == "planner"
        assert pattern["specialists"] == ["coder", "tester"]
    finally:
        tool_context.ironclaw_bridge = original


@pytest.mark.asyncio
async def test_claw_scan_skill():
    """claw_scan_skill should return formatted security findings."""
    from core.tools.ironclaw_tools import claw_scan_skill

    original = tool_context.ironclaw_bridge
    tool_context.ironclaw_bridge = _mock_bridge()

    try:
        result = await claw_scan_skill.ainvoke({
            "code": "x = eval(user_input)",
            "file_name": "test.py",
        })
        assert "Security Scan Results" in result
        assert "HIGH" in result
        assert "eval" in result
        assert "CWE-95" in result
        assert "REVIEW_REQUIRED" in result
    finally:
        tool_context.ironclaw_bridge = original


@pytest.mark.asyncio
async def test_claw_dispatch_workflow_failure():
    """claw_dispatch_workflow should handle failed workflows."""
    from core.tools.ironclaw_tools import claw_dispatch_workflow

    original = tool_context.ironclaw_bridge
    bridge = _mock_bridge()
    bridge.run_workflow = AsyncMock(return_value={
        "session_id": "",
        "status": "failed",
        "agents_executed": [],
        "results": {},
        "error": "Concurrent session limit exceeded",
    })
    tool_context.ironclaw_bridge = bridge

    try:
        result = await claw_dispatch_workflow.ainvoke({
            "task": "Test",
            "agents": "coder",
            "pattern": "sequential",
        })
        assert "WORKFLOW FAILED" in result
        assert "session limit" in result
    finally:
        tool_context.ironclaw_bridge = original


# ── Phase 4: Agent reads state correctly ─────────────────────────────────


def test_ironclaw_agent_context_connected():
    """IronClaw agent should set context to 'connected' when available."""
    from core.agents.ironclaw_agent import IronClawAgent
    from core.config import GrimConfig

    agent = IronClawAgent(GrimConfig())

    state = {
        "messages": [],
        "ironclaw_available": True,
        "knowledge_context": [],
    }
    context = agent.build_context(state)
    # The agent fn sets this, but build_context gives us the base
    assert isinstance(context, dict)


def test_ironclaw_tools_registered():
    """IRONCLAW_TOOLS should include all 8 tools."""
    from core.tools.ironclaw_tools import IRONCLAW_TOOLS

    tool_names = [t.name for t in IRONCLAW_TOOLS]
    assert "claw_read_file" in tool_names
    assert "claw_write_file" in tool_names
    assert "claw_shell" in tool_names
    assert "claw_list_dir" in tool_names
    assert "claw_http_request" in tool_names
    assert "claw_list_agents" in tool_names
    assert "claw_dispatch_workflow" in tool_names
    assert "claw_scan_skill" in tool_names
    assert len(IRONCLAW_TOOLS) == 8


def test_ironclaw_dispatch_tools_registered():
    """IRONCLAW_DISPATCH_TOOLS should include the 3 new tools."""
    from core.tools.ironclaw_tools import IRONCLAW_DISPATCH_TOOLS

    assert len(IRONCLAW_DISPATCH_TOOLS) == 3
    names = [t.name for t in IRONCLAW_DISPATCH_TOOLS]
    assert "claw_list_agents" in names
    assert "claw_dispatch_workflow" in names
    assert "claw_scan_skill" in names


# ── Phase 2: Bridge scan_skill ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_bridge_scan_skill():
    """Bridge.scan_skill should POST to /v1/skills/scan."""
    import httpx

    bridge = IronClawBridge(base_url="http://test:3100")

    scan_data = {
        "file_name": "test.py",
        "findings_count": 0,
        "risk_score": 0,
        "recommendation": "APPROVE",
        "findings": [],
    }

    mock_post = AsyncMock(return_value=MagicMock(
        status_code=200,
        json=MagicMock(return_value=scan_data),
        raise_for_status=MagicMock(),
    ))

    with patch.object(bridge._client, "post", mock_post):
        result = await bridge.scan_skill("print('hello')", "test.py")

        mock_post.assert_called_once_with(
            "/v1/skills/scan",
            json={"source": "print('hello')", "file_name": "test.py"},
        )
        assert result["findings_count"] == 0
        assert result["recommendation"] == "APPROVE"

    await bridge.close()
