"""Unit tests for the IronClaw integration.

Tests the Python bridge, LangChain tools, agent routing, and delegation
without needing a live IronClaw gateway — all HTTP calls are mocked.

Run: cd GRIM && python tests/test_ironclaw.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

# Ensure GRIM root is on path
GRIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GRIM_ROOT))

from core.bridge.ironclaw import (
    EngineMetrics,
    HealthStatus,
    IronClawBridge,
    ResourceUsage,
    ToolResult,
    ToolSchema,
    _parse_prometheus_metrics,
)
from core.state import AgentResult, GrimState, SkillContext


# ═══════════════════════════════════════════════════════════════════════════
# Test infrastructure
# ═══════════════════════════════════════════════════════════════════════════

def run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


def make_httpx_response(
    status_code: int = 200,
    json_data: dict | list | None = None,
    text: str = "",
    headers: dict | None = None,
) -> httpx.Response:
    """Create a mock httpx.Response with request set (needed for raise_for_status)."""
    resp = httpx.Response(
        status_code=status_code,
        headers=headers or {"x-request-id": "test-uuid-123"},
        content=json.dumps(json_data).encode() if json_data is not None else text.encode(),
        request=httpx.Request("GET", "http://localhost:3100/mock"),
    )
    return resp


# ═══════════════════════════════════════════════════════════════════════════
# Bridge — data types
# ═══════════════════════════════════════════════════════════════════════════


class TestToolResult(unittest.TestCase):
    """Test ToolResult dataclass."""

    def test_successful_result(self):
        r = ToolResult(success=True, output="hello world", execution_id="abc", duration_ms=42)
        self.assertTrue(r.success)
        self.assertEqual(r.output, "hello world")
        self.assertEqual(r.duration_ms, 42)
        self.assertFalse(r.timed_out)

    def test_failed_result(self):
        r = ToolResult(success=False, output="permission denied")
        self.assertFalse(r.success)
        self.assertIsNone(r.exit_code)

    def test_resource_usage_defaults(self):
        r = ToolResult(success=True, output="ok")
        self.assertEqual(r.resource_usage.cpu_time_ms, 0)
        self.assertEqual(r.resource_usage.memory_peak_kb, 0)

    def test_resource_usage_populated(self):
        usage = ResourceUsage(cpu_time_ms=50, memory_peak_kb=2048, wall_time_ms=100)
        r = ToolResult(success=True, output="ok", resource_usage=usage)
        self.assertEqual(r.resource_usage.cpu_time_ms, 50)
        self.assertEqual(r.resource_usage.wall_time_ms, 100)


class TestHealthStatus(unittest.TestCase):
    """Test HealthStatus dataclass."""

    def test_healthy(self):
        h = HealthStatus(healthy=True, version="0.2.0", uptime_secs=3600)
        self.assertTrue(h.healthy)
        self.assertEqual(h.version, "0.2.0")

    def test_unhealthy_default(self):
        h = HealthStatus(healthy=False)
        self.assertFalse(h.healthy)
        self.assertEqual(h.version, "")
        self.assertEqual(h.uptime_secs, 0.0)


class TestToolSchema(unittest.TestCase):
    """Test ToolSchema dataclass."""

    def test_fields(self):
        s = ToolSchema(name="file_read", description="Read a file", risk_level="Low")
        self.assertEqual(s.name, "file_read")
        self.assertEqual(s.risk_level, "Low")


# ═══════════════════════════════════════════════════════════════════════════
# Bridge — health check
# ═══════════════════════════════════════════════════════════════════════════


class TestBridgeHealth(unittest.TestCase):
    """Test IronClawBridge health check."""

    def test_healthy_gateway(self):
        bridge = IronClawBridge("http://localhost:3100")
        mock_resp = make_httpx_response(json_data={
            "status": "healthy",
            "version": "0.2.0",
            "uptime_secs": 120.5,
        })

        with patch.object(bridge._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            result = run_async(bridge.health())

        self.assertTrue(result.healthy)
        self.assertEqual(result.version, "0.2.0")
        self.assertAlmostEqual(result.uptime_secs, 120.5)

    def test_unhealthy_gateway(self):
        bridge = IronClawBridge("http://localhost:3100")
        mock_resp = make_httpx_response(json_data={"status": "degraded"})

        with patch.object(bridge._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            result = run_async(bridge.health())

        self.assertFalse(result.healthy)

    def test_connection_refused(self):
        bridge = IronClawBridge("http://localhost:3100")

        with patch.object(bridge._client, "get", new_callable=AsyncMock,
                         side_effect=httpx.ConnectError("Connection refused")):
            result = run_async(bridge.health())

        self.assertFalse(result.healthy)

    def test_is_available_true(self):
        bridge = IronClawBridge("http://localhost:3100")
        mock_resp = make_httpx_response(json_data={"status": "healthy", "version": "0.2.0"})

        with patch.object(bridge._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            result = run_async(bridge.is_available())

        self.assertTrue(result)

    def test_is_available_false(self):
        bridge = IronClawBridge("http://localhost:3100")

        with patch.object(bridge._client, "get", new_callable=AsyncMock,
                         side_effect=httpx.ConnectError("refused")):
            result = run_async(bridge.is_available())

        self.assertFalse(result)


# ═══════════════════════════════════════════════════════════════════════════
# Bridge — tool execution
# ═══════════════════════════════════════════════════════════════════════════


class TestBridgeToolExecution(unittest.TestCase):
    """Test IronClawBridge.execute_tool()."""

    def test_successful_execution(self):
        bridge = IronClawBridge("http://localhost:3100")
        mock_resp = make_httpx_response(json_data={
            "success": True,
            "output": "fn main() {\n    println!(\"hello\");\n}",
            "execution_id": "exec-001",
            "duration_ms": 15,
        })

        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = run_async(bridge.execute_tool("file_read", {"path": "src/main.rs"}))

        self.assertTrue(result.success)
        self.assertIn("fn main()", result.output)
        self.assertEqual(result.duration_ms, 15)
        self.assertEqual(result.execution_id, "exec-001")

    def test_failed_execution(self):
        bridge = IronClawBridge("http://localhost:3100")
        mock_resp = make_httpx_response(json_data={
            "success": False,
            "output": "Permission denied: /etc/shadow",
            "execution_id": "exec-002",
            "duration_ms": 1,
        })

        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = run_async(bridge.execute_tool("file_read", {"path": "/etc/shadow"}))

        self.assertFalse(result.success)
        self.assertIn("Permission denied", result.output)

    def test_timeout_execution(self):
        bridge = IronClawBridge("http://localhost:3100")
        mock_resp = make_httpx_response(json_data={
            "success": False,
            "output": "",
            "timed_out": True,
            "duration_ms": 30000,
        })

        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = run_async(bridge.execute_tool("shell", {"command": "sleep 60"}))

        self.assertFalse(result.success)
        self.assertTrue(result.timed_out)

    def test_resource_usage_parsed(self):
        bridge = IronClawBridge("http://localhost:3100")
        mock_resp = make_httpx_response(json_data={
            "success": True,
            "output": "ok",
            "resource_usage": {
                "cpu_time_ms": 25,
                "memory_peak_kb": 4096,
                "wall_time_ms": 30,
            },
        })

        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = run_async(bridge.execute_tool("shell", {"command": "echo hi"}))

        self.assertEqual(result.resource_usage.cpu_time_ms, 25)
        self.assertEqual(result.resource_usage.memory_peak_kb, 4096)

    def test_http_error_handled(self):
        bridge = IronClawBridge("http://localhost:3100")
        error_resp = httpx.Response(
            status_code=401,
            headers={"x-request-id": "err-001"},
            content=json.dumps({"error": "Unauthorized", "message": "Invalid API key"}).encode(),
        )
        mock_request = httpx.Request("POST", "http://localhost:3100/v1/tools/shell/execute")
        error_resp._request = mock_request

        async def raise_error(*args, **kwargs):
            resp = error_resp
            resp.raise_for_status()

        with patch.object(bridge._client, "post", new_callable=AsyncMock, side_effect=httpx.HTTPStatusError(
            "401", request=mock_request, response=error_resp,
        )):
            result = run_async(bridge.execute_tool("shell", {"command": "ls"}))

        self.assertFalse(result.success)
        self.assertIn("401", result.output)

    def test_connection_error_handled(self):
        bridge = IronClawBridge("http://localhost:3100")

        with patch.object(bridge._client, "post", new_callable=AsyncMock,
                         side_effect=httpx.ConnectError("Connection refused")):
            result = run_async(bridge.execute_tool("file_read", {"path": "test.txt"}))

        self.assertFalse(result.success)
        self.assertIn("bridge error", result.output)

    def test_empty_arguments(self):
        bridge = IronClawBridge("http://localhost:3100")
        mock_resp = make_httpx_response(json_data={"success": True, "output": ".", "execution_id": "e3"})

        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            run_async(bridge.execute_tool("directory_list"))

        # Check that empty args are passed correctly
        call_args = mock_post.call_args
        self.assertEqual(call_args.kwargs.get("json", call_args[1].get("json", {})), {"arguments": {}})


# ═══════════════════════════════════════════════════════════════════════════
# Bridge — tool listing
# ═══════════════════════════════════════════════════════════════════════════


class TestBridgeListTools(unittest.TestCase):
    """Test IronClawBridge.list_tools()."""

    def test_list_tools(self):
        bridge = IronClawBridge("http://localhost:3100")
        mock_resp = make_httpx_response(json_data=[
            {"name": "file_read", "description": "Read a file", "risk_level": "Low"},
            {"name": "shell", "description": "Execute shell command", "risk_level": "Critical"},
        ])

        with patch.object(bridge._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            tools = run_async(bridge.list_tools())

        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0].name, "file_read")
        self.assertEqual(tools[1].risk_level, "Critical")

    def test_list_tools_error(self):
        bridge = IronClawBridge("http://localhost:3100")

        with patch.object(bridge._client, "get", new_callable=AsyncMock,
                         side_effect=httpx.ConnectError("refused")):
            tools = run_async(bridge.list_tools())

        self.assertEqual(tools, [])


# ═══════════════════════════════════════════════════════════════════════════
# Bridge — metrics
# ═══════════════════════════════════════════════════════════════════════════


class TestBridgeMetrics(unittest.TestCase):
    """Test IronClawBridge.get_metrics() and Prometheus parsing."""

    def test_parse_prometheus(self):
        text = """# HELP ironclaw_requests_total Total requests
# TYPE ironclaw_requests_total counter
ironclaw_requests_total 42
# HELP ironclaw_requests_failed Total failed
# TYPE ironclaw_requests_failed counter
ironclaw_requests_failed 3
ironclaw_active_sessions 1
ironclaw_uptime_seconds 3600.5
"""
        metrics = _parse_prometheus_metrics(text)
        self.assertEqual(metrics.requests_total, 42)
        self.assertEqual(metrics.requests_failed, 3)
        self.assertEqual(metrics.active_sessions, 1)
        self.assertAlmostEqual(metrics.uptime_seconds, 3600.5)

    def test_parse_empty(self):
        metrics = _parse_prometheus_metrics("")
        self.assertEqual(metrics.requests_total, 0)

    def test_get_metrics_success(self):
        bridge = IronClawBridge("http://localhost:3100")
        mock_resp = make_httpx_response(
            text="ironclaw_requests_total 100\nironclaw_uptime_seconds 500\n"
        )

        with patch.object(bridge._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            metrics = run_async(bridge.get_metrics())

        self.assertEqual(metrics.requests_total, 100)
        self.assertAlmostEqual(metrics.uptime_seconds, 500.0)

    def test_get_metrics_error(self):
        bridge = IronClawBridge("http://localhost:3100")

        with patch.object(bridge._client, "get", new_callable=AsyncMock,
                         side_effect=httpx.ConnectError("refused")):
            metrics = run_async(bridge.get_metrics())

        self.assertEqual(metrics.requests_total, 0)


# ═══════════════════════════════════════════════════════════════════════════
# Bridge — API key auth
# ═══════════════════════════════════════════════════════════════════════════


class TestBridgeAuth(unittest.TestCase):
    """Test IronClawBridge authentication."""

    def test_api_key_header(self):
        bridge = IronClawBridge("http://localhost:3100", api_key="secret-key-123")
        self.assertEqual(bridge._client.headers.get("X-Api-Key"), "secret-key-123")

    def test_no_api_key(self):
        bridge = IronClawBridge("http://localhost:3100")
        self.assertNotIn("X-Api-Key", bridge._client.headers)

    def test_base_url_strip(self):
        bridge = IronClawBridge("http://localhost:3100/")
        self.assertEqual(bridge.base_url, "http://localhost:3100")


# ═══════════════════════════════════════════════════════════════════════════
# Tools — format_result
# ═══════════════════════════════════════════════════════════════════════════


class TestToolFormatResult(unittest.TestCase):
    """Test _format_result helper."""

    def test_success(self):
        from core.tools.ironclaw_tools import _format_result
        r = ToolResult(success=True, output="file contents here", duration_ms=10, execution_id="abc123")
        text = _format_result(r)
        self.assertIn("file contents here", text)
        self.assertIn("10ms", text)

    def test_failure(self):
        from core.tools.ironclaw_tools import _format_result
        r = ToolResult(success=False, output="permission denied")
        text = _format_result(r)
        self.assertIn("[FAILED]", text)
        self.assertIn("permission denied", text)

    def test_timeout(self):
        from core.tools.ironclaw_tools import _format_result
        r = ToolResult(success=False, output="", timed_out=True)
        text = _format_result(r)
        self.assertIn("[TIMED OUT]", text)

    def test_stderr(self):
        from core.tools.ironclaw_tools import _format_result
        r = ToolResult(success=True, output="ok", stderr="warning: unused var")
        text = _format_result(r)
        self.assertIn("[stderr]", text)
        self.assertIn("unused var", text)


# ═══════════════════════════════════════════════════════════════════════════
# Tools — bridge injection
# ═══════════════════════════════════════════════════════════════════════════


class TestToolBridgeInjection(unittest.TestCase):
    """Test tool bridge set/get."""

    def test_set_and_get_bridge(self):
        import core.tools.ironclaw_tools as tools_mod
        bridge = IronClawBridge("http://localhost:3100")
        tools_mod.set_bridge(bridge)
        self.assertIs(tools_mod._get_bridge(), bridge)
        # Cleanup
        tools_mod._bridge = None

    def test_get_bridge_unset_raises(self):
        import core.tools.ironclaw_tools as tools_mod
        tools_mod._bridge = None
        with self.assertRaises(RuntimeError):
            tools_mod._get_bridge()


# ═══════════════════════════════════════════════════════════════════════════
# Tools — LangChain tool calls
# ═══════════════════════════════════════════════════════════════════════════


class TestLangChainTools(unittest.TestCase):
    """Test the LangChain tool wrappers call the bridge correctly."""

    def setUp(self):
        import core.tools.ironclaw_tools as tools_mod
        self.bridge = IronClawBridge("http://localhost:3100")
        tools_mod.set_bridge(self.bridge)
        self.tools_mod = tools_mod

    def tearDown(self):
        self.tools_mod._bridge = None

    def test_claw_read_file(self):
        mock_resp = make_httpx_response(json_data={
            "success": True, "output": "file data", "execution_id": "r1", "duration_ms": 5,
        })
        with patch.object(self.bridge._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            result = run_async(self.tools_mod.claw_read_file.ainvoke({"path": "test.txt"}))

        self.assertIn("file data", result)
        # Check the correct endpoint was called
        call_url = mock_post.call_args[0][0]
        self.assertIn("/v1/tools/file_read/execute", call_url)

    def test_claw_write_file(self):
        mock_resp = make_httpx_response(json_data={
            "success": True, "output": "Written 42 bytes", "execution_id": "w1",
        })
        with patch.object(self.bridge._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = run_async(self.tools_mod.claw_write_file.ainvoke({
                "path": "output.txt", "content": "hello",
            }))

        self.assertIn("Written", result)

    def test_claw_shell(self):
        mock_resp = make_httpx_response(json_data={
            "success": True, "output": "total 42\ndrwxr-xr-x", "execution_id": "s1", "duration_ms": 20,
        })
        with patch.object(self.bridge._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = run_async(self.tools_mod.claw_shell.ainvoke({
                "command": "ls -la", "cwd": "/tmp",
            }))

        self.assertIn("total 42", result)

    def test_claw_list_dir(self):
        mock_resp = make_httpx_response(json_data={
            "success": True, "output": "[\"src\", \"Cargo.toml\"]", "execution_id": "d1",
        })
        with patch.object(self.bridge._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = run_async(self.tools_mod.claw_list_dir.ainvoke({"path": "."}))

        self.assertIn("src", result)

    def test_claw_http_request(self):
        mock_resp = make_httpx_response(json_data={
            "success": True, "output": "{\"data\": 42}", "execution_id": "h1",
        })
        with patch.object(self.bridge._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            result = run_async(self.tools_mod.claw_http_request.ainvoke({
                "url": "https://api.example.com/data", "method": "GET",
            }))

        self.assertIn("42", result)

    def test_tool_list_contents(self):
        """Verify IRONCLAW_TOOLS has the expected tools."""
        tool_names = [t.name for t in self.tools_mod.IRONCLAW_TOOLS]
        self.assertIn("claw_read_file", tool_names)
        self.assertIn("claw_write_file", tool_names)
        self.assertIn("claw_shell", tool_names)
        self.assertIn("claw_list_dir", tool_names)
        self.assertIn("claw_http_request", tool_names)
        self.assertEqual(len(tool_names), 5)

    def test_read_only_list(self):
        tool_names = [t.name for t in self.tools_mod.IRONCLAW_READ_TOOLS]
        self.assertIn("claw_read_file", tool_names)
        self.assertIn("claw_list_dir", tool_names)
        self.assertEqual(len(tool_names), 2)


# ═══════════════════════════════════════════════════════════════════════════
# Router — IronClaw routing
# ═══════════════════════════════════════════════════════════════════════════


class TestRouterIronClaw(unittest.TestCase):
    """Test that the router correctly routes to ironclaw agent."""

    def test_ironclaw_keywords(self):
        from core.nodes.router import DELEGATION_KEYWORDS
        self.assertIn("ironclaw", DELEGATION_KEYWORDS)
        keywords = DELEGATION_KEYWORDS["ironclaw"]
        self.assertIn("run sandboxed", keywords)
        self.assertIn("execute safely", keywords)
        self.assertIn("sandboxed execution", keywords)

    def test_skill_to_delegation_ironclaw(self):
        from core.nodes.router import _skill_ctx_to_delegation
        skill = SkillContext(
            name="sandboxed-execution",
            version="1.0",
            description="Execute in sandbox",
        )
        result = _skill_ctx_to_delegation(skill)
        self.assertEqual(result, "ironclaw")

    def test_skill_to_delegation_secure_shell(self):
        from core.nodes.router import _skill_ctx_to_delegation
        skill = SkillContext(
            name="secure-shell",
            version="1.0",
            description="Secure shell",
        )
        result = _skill_ctx_to_delegation(skill)
        self.assertEqual(result, "ironclaw")

    def test_skill_to_delegation_ironclaw_execute(self):
        from core.nodes.router import _skill_ctx_to_delegation
        skill = SkillContext(
            name="ironclaw-execute",
            version="1.0",
            description="IronClaw exec",
        )
        result = _skill_ctx_to_delegation(skill)
        self.assertEqual(result, "ironclaw")


# ═══════════════════════════════════════════════════════════════════════════
# State — IronClaw fields
# ═══════════════════════════════════════════════════════════════════════════


class TestStateIronClaw(unittest.TestCase):
    """Test that GrimState accepts ironclaw fields."""

    def test_delegation_type_includes_ironclaw(self):
        state: GrimState = {
            "messages": [],
            "delegation_type": "ironclaw",
            "ironclaw_available": True,
        }
        self.assertEqual(state["delegation_type"], "ironclaw")
        self.assertTrue(state["ironclaw_available"])

    def test_ironclaw_available_false(self):
        state: GrimState = {
            "messages": [],
            "ironclaw_available": False,
        }
        self.assertFalse(state["ironclaw_available"])


# ═══════════════════════════════════════════════════════════════════════════
# Agent — IronClaw agent creation
# ═══════════════════════════════════════════════════════════════════════════


class TestIronClawAgent(unittest.TestCase):
    """Test IronClaw agent creation and tool binding."""

    def test_agent_creation(self):
        from core.config import GrimConfig
        from core.agents.ironclaw_agent import IronClawAgent

        config = GrimConfig()
        agent = IronClawAgent(config)
        self.assertEqual(agent.agent_name, "ironclaw")
        # Should have ironclaw tools + companion tools
        tool_names = [t.name for t in agent.tools]
        self.assertIn("claw_shell", tool_names)
        self.assertIn("claw_read_file", tool_names)

    def test_make_ironclaw_agent_returns_callable(self):
        from core.config import GrimConfig
        from core.agents.ironclaw_agent import make_ironclaw_agent

        config = GrimConfig()
        fn = make_ironclaw_agent(config)
        self.assertTrue(callable(fn))


# ═══════════════════════════════════════════════════════════════════════════
# Graph — IronClaw wiring
# ═══════════════════════════════════════════════════════════════════════════


class TestGraphIronClawWiring(unittest.TestCase):
    """Test that graph.py wires ironclaw correctly."""

    def test_build_graph_without_bridge(self):
        """Graph should build fine without IronClaw bridge."""
        from core.config import GrimConfig
        from core.graph import build_graph

        config = GrimConfig()
        graph = build_graph(config, ironclaw_bridge=None)
        self.assertIsNotNone(graph)

    def test_build_graph_with_bridge(self):
        """Graph should register ironclaw agent when bridge provided."""
        from core.config import GrimConfig
        from core.graph import build_graph

        config = GrimConfig()
        bridge = IronClawBridge("http://localhost:3100")
        graph = build_graph(config, ironclaw_bridge=bridge)
        self.assertIsNotNone(graph)


# ═══════════════════════════════════════════════════════════════════════════
# Bridge: Agent listing + workflow tests
# ═══════════════════════════════════════════════════════════════════════════

class TestIronClawAgents(unittest.TestCase):
    """Tests for the agent listing and workflow bridge methods."""

    def test_list_agents_success(self):
        """list_agents() returns parsed response from /v1/agents."""
        mock_data = {
            "enabled": True,
            "roles": [
                {"id": "researcher", "name": "Researcher", "capabilities": ["research"]},
                {"id": "coder", "name": "Coder", "capabilities": ["code_generation"]},
            ],
            "active_sessions": 0,
            "max_concurrent_sessions": 4,
        }
        bridge = IronClawBridge("http://localhost:3100")
        bridge._client = AsyncMock()
        resp = make_httpx_response(200, mock_data)
        bridge._client.get = AsyncMock(return_value=resp)

        result = run_async(bridge.list_agents())
        self.assertTrue(result["enabled"])
        self.assertEqual(len(result["roles"]), 2)
        self.assertEqual(result["roles"][0]["id"], "researcher")
        bridge._client.get.assert_called_once_with("/v1/agents")

    def test_list_agents_failure_returns_default(self):
        """list_agents() returns disabled response on error."""
        bridge = IronClawBridge("http://localhost:3100")
        bridge._client = AsyncMock()
        bridge._client.get = AsyncMock(side_effect=Exception("connection refused"))

        result = run_async(bridge.list_agents())
        self.assertFalse(result["enabled"])
        self.assertEqual(result["roles"], [])

    def test_run_workflow_sequential(self):
        """run_workflow() sends POST to /v1/agents/workflow."""
        mock_response = {
            "session_id": "abc-123",
            "status": "completed",
            "agents_executed": ["planner", "coder", "reviewer"],
            "results": {
                "planner": "[Planner analysis]",
                "coder": "[Coder analysis]",
                "reviewer": "[Reviewer analysis]",
            },
            "duration_ms": 42,
        }
        bridge = IronClawBridge("http://localhost:3100")
        bridge._client = AsyncMock()
        resp = make_httpx_response(200, mock_response)
        bridge._client.post = AsyncMock(return_value=resp)

        pattern = {"type": "sequential", "agent_order": ["planner", "coder", "reviewer"]}
        result = run_async(bridge.run_workflow("Write hello world", pattern))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["agents_executed"]), 3)
        self.assertIn("planner", result["results"])
        bridge._client.post.assert_called_once_with(
            "/v1/agents/workflow",
            json={"task": "Write hello world", "pattern": pattern},
        )

    def test_run_workflow_parallel(self):
        """run_workflow() with parallel pattern."""
        mock_response = {
            "session_id": "def-456",
            "status": "completed",
            "agents_executed": ["researcher", "coder"],
            "results": {
                "researcher": "[Researcher analysis]",
                "coder": "[Coder analysis]",
            },
            "duration_ms": 15,
        }
        bridge = IronClawBridge("http://localhost:3100")
        bridge._client = AsyncMock()
        resp = make_httpx_response(200, mock_response)
        bridge._client.post = AsyncMock(return_value=resp)

        pattern = {"type": "parallel", "agents": ["researcher", "coder"]}
        result = run_async(bridge.run_workflow("Research and code", pattern))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["agents_executed"]), 2)

    def test_run_workflow_invalid_agent_returns_error(self):
        """run_workflow() handles HTTP 400 for invalid agents."""
        error_body = {
            "error": "Bad Request",
            "message": "Agent role 'ghost' not found",
            "request_id": "req-789",
        }
        bridge = IronClawBridge("http://localhost:3100")
        bridge._client = AsyncMock()

        error_resp = make_httpx_response(400, error_body)
        error_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "400 Bad Request",
                request=httpx.Request("POST", "http://localhost:3100/v1/agents/workflow"),
                response=error_resp,
            )
        )
        bridge._client.post = AsyncMock(return_value=error_resp)

        pattern = {"type": "sequential", "agent_order": ["ghost"]}
        result = run_async(bridge.run_workflow("Test", pattern))

        self.assertEqual(result["status"], "failed")
        self.assertIn("ghost", result.get("error", ""))

    def test_run_workflow_connection_error(self):
        """run_workflow() handles connection failures gracefully."""
        bridge = IronClawBridge("http://localhost:3100")
        bridge._client = AsyncMock()
        bridge._client.post = AsyncMock(side_effect=Exception("connection refused"))

        result = run_async(bridge.run_workflow("Test", {"type": "sequential", "agent_order": ["coder"]}))
        self.assertEqual(result["status"], "failed")
        self.assertIn("connection refused", result.get("error", ""))


# ═══════════════════════════════════════════════════════════════════════════
# Run
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
