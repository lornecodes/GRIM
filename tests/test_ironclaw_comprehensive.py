"""Comprehensive IronClaw tests — bridge, tools, staging, audit workflow,
security boundaries, error handling, and configuration.

All tests are synchronous/mocked — no real API or LLM calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

GRIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GRIM_ROOT))

from core.bridge.ironclaw import (
    AuditEntry,
    EngineMetrics,
    HealthStatus,
    IronClawBridge,
    ResourceUsage,
    ToolResult,
    ToolSchema,
    _parse_prometheus_metrics,
)
from core.config import GrimConfig


# ─── Fixtures ───────────────────────────────────────────────────────────────


def make_httpx_response(
    status_code=200,
    json_data=None,
    text="",
    headers=None,
    method="GET",
    url="http://ironclaw:3100/mock",
):
    return httpx.Response(
        status_code=status_code,
        headers=headers or {"x-request-id": "test-uuid"},
        content=json.dumps(json_data).encode() if json_data is not None else text.encode(),
        request=httpx.Request(method, url),
    )


@pytest.fixture
def config():
    cfg = GrimConfig()
    cfg.model = "claude-sonnet-4-6"
    return cfg


@pytest.fixture
def bridge():
    return IronClawBridge(base_url="http://ironclaw:3100", api_key="test-key")


# ═══════════════════════════════════════════════════════════════════════════
# Bridge — Data Types
# ═══════════════════════════════════════════════════════════════════════════


class TestDataTypes:
    """ToolResult, HealthStatus, ResourceUsage, etc."""

    def test_tool_result_defaults(self):
        r = ToolResult(success=True, output="ok")
        assert r.execution_id == ""
        assert r.duration_ms == 0
        assert r.exit_code is None
        assert r.stderr == ""
        assert r.timed_out is False
        assert r.resource_usage.cpu_time_ms == 0

    def test_tool_result_all_fields(self):
        usage = ResourceUsage(cpu_time_ms=50, memory_peak_kb=2048, wall_time_ms=100)
        r = ToolResult(
            success=True, output="hello", execution_id="abc-123",
            duration_ms=42, exit_code=0, stderr="", timed_out=False,
            resource_usage=usage,
        )
        assert r.duration_ms == 42
        assert r.resource_usage.memory_peak_kb == 2048

    def test_health_status_healthy(self):
        h = HealthStatus(healthy=True, version="0.2.0", uptime_secs=1234.5)
        assert h.healthy
        assert h.version == "0.2.0"

    def test_health_status_unhealthy(self):
        h = HealthStatus(healthy=False)
        assert not h.healthy
        assert h.version == ""

    def test_tool_schema(self):
        t = ToolSchema(name="shell", description="Execute commands", risk_level="High")
        assert t.name == "shell"
        assert t.risk_level == "High"

    def test_audit_entry(self):
        a = AuditEntry(event="tool_execute", request_id="abc")
        assert a.event == "tool_execute"
        assert a.method == ""

    def test_engine_metrics_defaults(self):
        m = EngineMetrics()
        assert m.requests_total == 0
        assert m.uptime_seconds == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Bridge — Health Checks
# ═══════════════════════════════════════════════════════════════════════════


class TestBridgeHealth:
    """Health check tests."""

    @pytest.mark.asyncio
    async def test_health_success(self, bridge):
        resp = make_httpx_response(json_data={"status": "healthy", "version": "0.2.0", "uptime_secs": 100})
        with patch.object(bridge._client, "get", new_callable=AsyncMock, return_value=resp):
            h = await bridge.health()
            assert h.healthy
            assert h.version == "0.2.0"
            assert h.uptime_secs == 100

    @pytest.mark.asyncio
    async def test_health_failure(self, bridge):
        with patch.object(bridge._client, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused")):
            h = await bridge.health()
            assert not h.healthy

    @pytest.mark.asyncio
    async def test_health_non_healthy_status(self, bridge):
        resp = make_httpx_response(json_data={"status": "starting", "version": "0.2.0"})
        with patch.object(bridge._client, "get", new_callable=AsyncMock, return_value=resp):
            h = await bridge.health()
            assert not h.healthy

    @pytest.mark.asyncio
    async def test_is_available_delegates_to_health(self, bridge):
        resp = make_httpx_response(json_data={"status": "healthy"})
        with patch.object(bridge._client, "get", new_callable=AsyncMock, return_value=resp):
            assert await bridge.is_available()


# ═══════════════════════════════════════════════════════════════════════════
# Bridge — Tool Execution
# ═══════════════════════════════════════════════════════════════════════════


class TestBridgeToolExecution:
    """Execute tools via the bridge."""

    @pytest.mark.asyncio
    async def test_execute_tool_success(self, bridge):
        resp = make_httpx_response(json_data={
            "success": True,
            "output": "hello world",
            "execution_id": "exec-1",
            "duration_ms": 42,
        })
        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=resp):
            result = await bridge.execute_tool("shell", {"command": "echo hello"})
            assert result.success
            assert result.output == "hello world"
            assert result.duration_ms == 42

    @pytest.mark.asyncio
    async def test_execute_tool_with_resource_usage(self, bridge):
        resp = make_httpx_response(json_data={
            "success": True, "output": "ok", "execution_id": "e2",
            "duration_ms": 10,
            "resource_usage": {
                "cpu_time_ms": 5, "memory_peak_kb": 1024, "wall_time_ms": 10,
            },
        })
        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=resp):
            result = await bridge.execute_tool("file_read", {"path": "main.py"})
            assert result.resource_usage.cpu_time_ms == 5
            assert result.resource_usage.memory_peak_kb == 1024

    @pytest.mark.asyncio
    async def test_execute_tool_failure_response(self, bridge):
        resp = make_httpx_response(json_data={
            "success": False, "output": "permission denied",
            "execution_id": "e3", "duration_ms": 1,
        })
        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=resp):
            result = await bridge.execute_tool("file_write", {"path": "/etc/shadow", "content": "x"})
            assert not result.success
            assert "permission denied" in result.output

    @pytest.mark.asyncio
    async def test_execute_tool_http_error(self, bridge):
        resp = make_httpx_response(status_code=403, json_data={"message": "Forbidden"})
        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=resp):
            result = await bridge.execute_tool("shell", {"command": "rm -rf /"})
            assert not result.success
            assert "403" in result.output

    @pytest.mark.asyncio
    async def test_execute_tool_connection_error(self, bridge):
        with patch.object(bridge._client, "post", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            result = await bridge.execute_tool("shell", {"command": "echo"})
            assert not result.success
            assert "bridge error" in result.output.lower()

    @pytest.mark.asyncio
    async def test_execute_tool_timeout(self, bridge):
        with patch.object(bridge._client, "post", new_callable=AsyncMock, side_effect=httpx.ReadTimeout("timeout")):
            result = await bridge.execute_tool("shell", {"command": "sleep 999"})
            assert not result.success

    @pytest.mark.asyncio
    async def test_execute_tool_url_construction(self, bridge):
        """Verify the URL pattern: /v1/tools/{name}/execute"""
        mock_post = AsyncMock(return_value=make_httpx_response(json_data={
            "success": True, "output": "", "execution_id": "x", "duration_ms": 0,
        }))
        with patch.object(bridge._client, "post", mock_post):
            await bridge.execute_tool("shell", {"command": "echo"})
            call_url = mock_post.call_args[0][0]
            assert call_url == "/v1/tools/shell/execute"

    @pytest.mark.asyncio
    async def test_execute_tool_sends_arguments(self, bridge):
        mock_post = AsyncMock(return_value=make_httpx_response(json_data={
            "success": True, "output": "", "execution_id": "x", "duration_ms": 0,
        }))
        with patch.object(bridge._client, "post", mock_post):
            await bridge.execute_tool("file_read", {"path": "test.py", "start_line": 10})
            call_json = mock_post.call_args[1]["json"]
            assert call_json == {"arguments": {"path": "test.py", "start_line": 10}}

    @pytest.mark.asyncio
    async def test_execute_tool_empty_arguments(self, bridge):
        mock_post = AsyncMock(return_value=make_httpx_response(json_data={
            "success": True, "output": "", "execution_id": "x", "duration_ms": 0,
        }))
        with patch.object(bridge._client, "post", mock_post):
            await bridge.execute_tool("directory_list", None)
            call_json = mock_post.call_args[1]["json"]
            assert call_json == {"arguments": {}}

    @pytest.mark.asyncio
    async def test_execute_tool_exit_code(self, bridge):
        resp = make_httpx_response(json_data={
            "success": True, "output": "", "execution_id": "e",
            "duration_ms": 0, "exit_code": 1, "stderr": "not found",
        })
        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=resp):
            result = await bridge.execute_tool("shell", {"command": "false"})
            assert result.exit_code == 1
            assert result.stderr == "not found"

    @pytest.mark.asyncio
    async def test_execute_tool_timed_out(self, bridge):
        resp = make_httpx_response(json_data={
            "success": False, "output": "killed", "execution_id": "e",
            "duration_ms": 30000, "timed_out": True,
        })
        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=resp):
            result = await bridge.execute_tool("shell", {"command": "sleep 999"})
            assert result.timed_out


# ═══════════════════════════════════════════════════════════════════════════
# Bridge — Tool Listing
# ═══════════════════════════════════════════════════════════════════════════


class TestBridgeToolListing:

    @pytest.mark.asyncio
    async def test_list_tools_success(self, bridge):
        resp = make_httpx_response(json_data=[
            {"name": "file_read", "description": "Read file", "risk_level": "Low"},
            {"name": "shell", "description": "Run command", "risk_level": "High"},
        ])
        with patch.object(bridge._client, "get", new_callable=AsyncMock, return_value=resp):
            tools = await bridge.list_tools()
            assert len(tools) == 2
            assert tools[0].name == "file_read"
            assert tools[1].risk_level == "High"

    @pytest.mark.asyncio
    async def test_list_tools_failure(self, bridge):
        with patch.object(bridge._client, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            tools = await bridge.list_tools()
            assert tools == []


# ═══════════════════════════════════════════════════════════════════════════
# Bridge — Agent Listing + Workflow
# ═══════════════════════════════════════════════════════════════════════════


class TestBridgeAgents:

    @pytest.mark.asyncio
    async def test_list_agents_success(self, bridge):
        resp = make_httpx_response(json_data={
            "enabled": True,
            "roles": [{"id": "coder", "name": "Coder", "capabilities": ["code"]}],
            "active_sessions": 0,
            "max_concurrent_sessions": 4,
        })
        with patch.object(bridge._client, "get", new_callable=AsyncMock, return_value=resp):
            data = await bridge.list_agents()
            assert data["enabled"]
            assert len(data["roles"]) == 1

    @pytest.mark.asyncio
    async def test_list_agents_failure(self, bridge):
        with patch.object(bridge._client, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            data = await bridge.list_agents()
            assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_run_workflow_success(self, bridge):
        resp = make_httpx_response(json_data={
            "session_id": "sess-1",
            "status": "completed",
            "agents_executed": ["coder", "tester"],
            "results": {"coder": "done", "tester": "all pass"},
            "duration_ms": 5000,
        })
        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=resp):
            data = await bridge.run_workflow("write tests", {"type": "sequential", "agent_order": ["coder", "tester"]})
            assert data["status"] == "completed"
            assert len(data["agents_executed"]) == 2

    @pytest.mark.asyncio
    async def test_run_workflow_failure(self, bridge):
        resp = make_httpx_response(status_code=422, json_data={"message": "Invalid pattern"})
        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=resp):
            data = await bridge.run_workflow("test", {"type": "invalid"})
            assert data["status"] == "failed"

    @pytest.mark.asyncio
    async def test_run_workflow_connection_error(self, bridge):
        with patch.object(bridge._client, "post", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            data = await bridge.run_workflow("test", {"type": "sequential"})
            assert data["status"] == "failed"


# ═══════════════════════════════════════════════════════════════════════════
# Bridge — Security Scanning
# ═══════════════════════════════════════════════════════════════════════════


class TestBridgeSecurityScan:

    @pytest.mark.asyncio
    async def test_scan_clean_code(self, bridge):
        resp = make_httpx_response(json_data={
            "file_name": "test.py",
            "findings_count": 0,
            "risk_score": 0,
            "recommendation": "ALLOW",
            "findings": [],
        })
        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=resp):
            data = await bridge.scan_skill("print('hello')", "test.py")
            assert data["risk_score"] == 0
            assert data["findings_count"] == 0

    @pytest.mark.asyncio
    async def test_scan_with_findings(self, bridge):
        resp = make_httpx_response(json_data={
            "file_name": "bad.py",
            "findings_count": 1,
            "risk_score": 75,
            "recommendation": "BLOCK",
            "findings": [{"severity": "HIGH", "description": "Hardcoded API key"}],
        })
        with patch.object(bridge._client, "post", new_callable=AsyncMock, return_value=resp):
            data = await bridge.scan_skill("API_KEY='sk-secret'", "bad.py")
            assert data["risk_score"] == 75
            assert len(data["findings"]) == 1

    @pytest.mark.asyncio
    async def test_scan_failure(self, bridge):
        with patch.object(bridge._client, "post", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            data = await bridge.scan_skill("code", "test.py")
            assert "error" in data


# ═══════════════════════════════════════════════════════════════════════════
# Bridge — Metrics
# ═══════════════════════════════════════════════════════════════════════════


class TestBridgeMetrics:

    @pytest.mark.asyncio
    async def test_get_metrics_success(self, bridge):
        prom_text = (
            "# HELP ironclaw_requests_total Total requests\n"
            "ironclaw_requests_total 1234\n"
            "ironclaw_requests_failed 5\n"
            "ironclaw_active_sessions 2\n"
            "ironclaw_uptime_seconds 3600.5\n"
        )
        resp = make_httpx_response(text=prom_text)
        with patch.object(bridge._client, "get", new_callable=AsyncMock, return_value=resp):
            m = await bridge.get_metrics()
            assert m.requests_total == 1234
            assert m.requests_failed == 5
            assert m.active_sessions == 2
            assert m.uptime_seconds == 3600.5

    @pytest.mark.asyncio
    async def test_get_metrics_failure(self, bridge):
        with patch.object(bridge._client, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("down")):
            m = await bridge.get_metrics()
            assert m.requests_total == 0

    def test_parse_prometheus_all_fields(self):
        text = (
            "ironclaw_requests_total 100\n"
            "ironclaw_requests_failed 3\n"
            "ironclaw_auth_failures 1\n"
            "ironclaw_rate_limited 2\n"
            "ironclaw_active_sessions 5\n"
            "ironclaw_active_websockets 1\n"
            "ironclaw_uptime_seconds 7200.0\n"
        )
        m = _parse_prometheus_metrics(text)
        assert m.requests_total == 100
        assert m.requests_failed == 3
        assert m.auth_failures == 1
        assert m.rate_limited == 2
        assert m.active_sessions == 5
        assert m.active_websockets == 1
        assert m.uptime_seconds == 7200.0

    def test_parse_prometheus_empty(self):
        m = _parse_prometheus_metrics("")
        assert m.requests_total == 0

    def test_parse_prometheus_comments_only(self):
        m = _parse_prometheus_metrics("# HELP\n# TYPE\n")
        assert m.requests_total == 0

    def test_parse_prometheus_unknown_metric(self):
        m = _parse_prometheus_metrics("unknown_metric 42\n")
        assert m.requests_total == 0  # unrecognized metrics ignored


# ═══════════════════════════════════════════════════════════════════════════
# Bridge — Configuration
# ═══════════════════════════════════════════════════════════════════════════


class TestBridgeConfiguration:

    def test_default_base_url(self):
        b = IronClawBridge()
        assert b.base_url == "http://localhost:3100"

    def test_custom_base_url(self):
        b = IronClawBridge(base_url="http://ironclaw:3100")
        assert b.base_url == "http://ironclaw:3100"

    def test_trailing_slash_stripped(self):
        b = IronClawBridge(base_url="http://ironclaw:3100/")
        assert b.base_url == "http://ironclaw:3100"

    def test_api_key_in_headers(self):
        b = IronClawBridge(api_key="my-key")
        assert b._client.headers.get("X-Api-Key") == "my-key"

    def test_no_api_key(self):
        b = IronClawBridge()
        assert "X-Api-Key" not in b._client.headers

    @pytest.mark.asyncio
    async def test_close(self):
        b = IronClawBridge()
        await b.close()
        # Should not raise


# ═══════════════════════════════════════════════════════════════════════════
# LangChain Tool Wrappers
# ═══════════════════════════════════════════════════════════════════════════


class TestLangChainToolWrappers:
    """Test the claw_* LangChain tools that wrap bridge calls."""

    def _mock_bridge(self, result=None):
        """Create a mock bridge that returns a ToolResult."""
        if result is None:
            result = ToolResult(
                success=True, output="mock output",
                execution_id="test-exec", duration_ms=10,
            )
        mock_bridge = MagicMock(spec=IronClawBridge)
        mock_bridge.execute_tool = AsyncMock(return_value=result)
        mock_bridge.list_agents = AsyncMock(return_value={
            "roles": [{"id": "coder", "name": "Coder", "capabilities": ["code"], "can_delegate": False, "description": "A coder"}],
            "active_sessions": 0, "max_concurrent_sessions": 4,
        })
        mock_bridge.run_workflow = AsyncMock(return_value={
            "session_id": "s1", "status": "completed",
            "agents_executed": ["coder"], "results": {"coder": "done"}, "duration_ms": 100,
        })
        mock_bridge.scan_skill = AsyncMock(return_value={
            "file_name": "test.py", "findings_count": 0, "risk_score": 0,
            "recommendation": "ALLOW", "findings": [],
        })
        return mock_bridge

    @pytest.mark.asyncio
    async def test_claw_read_file(self):
        from core.tools.ironclaw_tools import claw_read_file
        from core.tools.context import tool_context
        mock_bridge = self._mock_bridge()
        tool_context.ironclaw_bridge = mock_bridge
        try:
            result = await claw_read_file.ainvoke({"path": "main.py"})
            assert "mock output" in result
            mock_bridge.execute_tool.assert_called_once()
            call_args = mock_bridge.execute_tool.call_args
            assert call_args[0][0] == "file_read"
        finally:
            tool_context.ironclaw_bridge = None

    @pytest.mark.asyncio
    async def test_claw_write_file(self):
        from core.tools.ironclaw_tools import claw_write_file
        from core.tools.context import tool_context
        mock_bridge = self._mock_bridge()
        tool_context.ironclaw_bridge = mock_bridge
        try:
            result = await claw_write_file.ainvoke({"path": "test.py", "content": "print(1)"})
            assert "mock output" in result
            call_args = mock_bridge.execute_tool.call_args
            assert call_args[0][0] == "file_write"
            assert call_args[0][1]["content"] == "print(1)"
        finally:
            tool_context.ironclaw_bridge = None

    @pytest.mark.asyncio
    async def test_claw_shell(self):
        from core.tools.ironclaw_tools import claw_shell
        from core.tools.context import tool_context
        mock_bridge = self._mock_bridge()
        tool_context.ironclaw_bridge = mock_bridge
        try:
            result = await claw_shell.ainvoke({"command": "echo hello"})
            assert "mock output" in result
            call_args = mock_bridge.execute_tool.call_args
            assert call_args[0][0] == "shell"
            assert call_args[0][1]["command"] == "echo hello"
        finally:
            tool_context.ironclaw_bridge = None

    @pytest.mark.asyncio
    async def test_claw_list_dir(self):
        from core.tools.ironclaw_tools import claw_list_dir
        from core.tools.context import tool_context
        mock_bridge = self._mock_bridge()
        tool_context.ironclaw_bridge = mock_bridge
        try:
            result = await claw_list_dir.ainvoke({"path": "src/"})
            assert "mock output" in result
            call_args = mock_bridge.execute_tool.call_args
            assert call_args[0][0] == "directory_list"
        finally:
            tool_context.ironclaw_bridge = None

    @pytest.mark.asyncio
    async def test_claw_http_request(self):
        from core.tools.ironclaw_tools import claw_http_request
        from core.tools.context import tool_context
        mock_bridge = self._mock_bridge()
        tool_context.ironclaw_bridge = mock_bridge
        try:
            result = await claw_http_request.ainvoke({"url": "https://example.com", "method": "GET"})
            assert "mock output" in result
            call_args = mock_bridge.execute_tool.call_args
            assert call_args[0][0] == "http_request"
        finally:
            tool_context.ironclaw_bridge = None

    @pytest.mark.asyncio
    async def test_claw_http_request_with_headers(self):
        from core.tools.ironclaw_tools import claw_http_request
        from core.tools.context import tool_context
        mock_bridge = self._mock_bridge()
        tool_context.ironclaw_bridge = mock_bridge
        try:
            result = await claw_http_request.ainvoke({
                "url": "https://api.example.com",
                "method": "POST",
                "body": '{"key": "value"}',
                "headers": '{"Authorization": "Bearer token"}',
            })
            assert "mock output" in result
            call_args = mock_bridge.execute_tool.call_args
            assert call_args[0][1]["headers"] == {"Authorization": "Bearer token"}
        finally:
            tool_context.ironclaw_bridge = None

    @pytest.mark.asyncio
    async def test_claw_http_request_invalid_headers(self):
        from core.tools.ironclaw_tools import claw_http_request
        from core.tools.context import tool_context
        mock_bridge = self._mock_bridge()
        tool_context.ironclaw_bridge = mock_bridge
        try:
            result = await claw_http_request.ainvoke({
                "url": "https://example.com",
                "headers": "not valid json",
            })
            assert "Invalid JSON" in result
        finally:
            tool_context.ironclaw_bridge = None

    @pytest.mark.asyncio
    async def test_claw_list_agents(self):
        from core.tools.ironclaw_tools import claw_list_agents
        from core.tools.context import tool_context
        mock_bridge = self._mock_bridge()
        tool_context.ironclaw_bridge = mock_bridge
        try:
            result = await claw_list_agents.ainvoke({})
            assert "Coder" in result
            assert "IronClaw Engine Agents" in result
        finally:
            tool_context.ironclaw_bridge = None

    @pytest.mark.asyncio
    async def test_claw_dispatch_workflow(self):
        from core.tools.ironclaw_tools import claw_dispatch_workflow
        from core.tools.context import tool_context
        mock_bridge = self._mock_bridge()
        tool_context.ironclaw_bridge = mock_bridge
        try:
            result = await claw_dispatch_workflow.ainvoke({
                "task": "write tests", "agents": "coder,tester", "pattern": "sequential",
            })
            assert "Workflow Complete" in result
        finally:
            tool_context.ironclaw_bridge = None

    @pytest.mark.asyncio
    async def test_claw_scan_skill(self):
        from core.tools.ironclaw_tools import claw_scan_skill
        from core.tools.context import tool_context
        mock_bridge = self._mock_bridge()
        tool_context.ironclaw_bridge = mock_bridge
        try:
            result = await claw_scan_skill.ainvoke({"code": "print(1)"})
            assert "Security Scan Results" in result
            assert "No security issues" in result
        finally:
            tool_context.ironclaw_bridge = None

    @pytest.mark.asyncio
    async def test_tool_no_bridge_raises(self):
        from core.tools.ironclaw_tools import claw_shell
        from core.tools.context import tool_context
        tool_context.ironclaw_bridge = None
        with pytest.raises(RuntimeError, match="bridge not initialized"):
            await claw_shell.ainvoke({"command": "echo"})


# ═══════════════════════════════════════════════════════════════════════════
# Tool Result Formatting
# ═══════════════════════════════════════════════════════════════════════════


class TestToolResultFormatting:

    def test_success_format(self):
        from core.tools.ironclaw_tools import _format_result
        r = ToolResult(success=True, output="hello world")
        assert _format_result(r) == "hello world"

    def test_failure_format(self):
        from core.tools.ironclaw_tools import _format_result
        r = ToolResult(success=False, output="permission denied")
        result = _format_result(r)
        assert "[FAILED]" in result
        assert "permission denied" in result

    def test_stderr_included(self):
        from core.tools.ironclaw_tools import _format_result
        r = ToolResult(success=True, output="ok", stderr="warning: unused var")
        result = _format_result(r)
        assert "[stderr]" in result
        assert "unused var" in result

    def test_timed_out_included(self):
        from core.tools.ironclaw_tools import _format_result
        r = ToolResult(success=False, output="killed", timed_out=True)
        result = _format_result(r)
        assert "[TIMED OUT]" in result

    def test_duration_included(self):
        from core.tools.ironclaw_tools import _format_result
        r = ToolResult(success=True, output="ok", duration_ms=42, execution_id="abc12345")
        result = _format_result(r)
        assert "42ms" in result
        assert "abc12345" in result


# ═══════════════════════════════════════════════════════════════════════════
# Tool Registration
# ═══════════════════════════════════════════════════════════════════════════


class TestToolRegistration:

    def test_ironclaw_tools_registered(self):
        from core.tools.ironclaw_tools import IRONCLAW_TOOLS
        names = {t.name for t in IRONCLAW_TOOLS}
        expected = {"claw_read_file", "claw_write_file", "claw_shell",
                    "claw_list_dir", "claw_http_request", "claw_list_agents",
                    "claw_dispatch_workflow", "claw_scan_skill"}
        assert names == expected

    def test_read_tools_subset(self):
        from core.tools.ironclaw_tools import IRONCLAW_READ_TOOLS
        names = {t.name for t in IRONCLAW_READ_TOOLS}
        assert names == {"claw_read_file", "claw_list_dir"}

    def test_dispatch_tools_subset(self):
        from core.tools.ironclaw_tools import IRONCLAW_DISPATCH_TOOLS
        names = {t.name for t in IRONCLAW_DISPATCH_TOOLS}
        assert names == {"claw_list_agents", "claw_dispatch_workflow", "claw_scan_skill"}

    def test_tool_registry_has_ironclaw_group(self):
        from core.tools.registry import tool_registry
        assert "ironclaw" in tool_registry._groups

    def test_tool_registry_has_ironclaw_read_group(self):
        from core.tools.registry import tool_registry
        assert "ironclaw_read" in tool_registry._groups


# ═══════════════════════════════════════════════════════════════════════════
# Staging Tools
# ═══════════════════════════════════════════════════════════════════════════


class TestStagingTools:
    """Staging tools for audit/integrate pipeline."""

    @pytest.fixture
    def staging_dir(self, tmp_path):
        """Create a staging directory with a test job."""
        job_dir = tmp_path / "test-job-123"
        output_dir = job_dir / "output"
        output_dir.mkdir(parents=True)
        (output_dir / "main.py").write_text("print('hello')", encoding="utf-8")
        (output_dir / "test.py").write_text("assert True", encoding="utf-8")
        return tmp_path

    @pytest.mark.asyncio
    async def test_staging_list(self, staging_dir):
        from core.tools.staging_tools import staging_list
        with patch("core.tools.staging_tools.STAGING_BASE", staging_dir):
            result = await staging_list.ainvoke({"job_id": "test-job-123"})
            assert "2 file(s)" in result
            assert "main.py" in result
            assert "test.py" in result

    @pytest.mark.asyncio
    async def test_staging_list_empty_output(self, staging_dir):
        from core.tools.staging_tools import staging_list
        empty_job = staging_dir / "empty-job"
        (empty_job / "output").mkdir(parents=True)
        with patch("core.tools.staging_tools.STAGING_BASE", staging_dir):
            result = await staging_list.ainvoke({"job_id": "empty-job"})
            assert "empty" in result.lower()

    @pytest.mark.asyncio
    async def test_staging_list_no_output_dir(self, staging_dir):
        from core.tools.staging_tools import staging_list
        (staging_dir / "no-output-job").mkdir()
        with patch("core.tools.staging_tools.STAGING_BASE", staging_dir):
            result = await staging_list.ainvoke({"job_id": "no-output-job"})
            assert "No output" in result

    @pytest.mark.asyncio
    async def test_staging_list_nonexistent_job(self, staging_dir):
        from core.tools.staging_tools import staging_list
        with patch("core.tools.staging_tools.STAGING_BASE", staging_dir):
            result = await staging_list.ainvoke({"job_id": "nonexistent"})
            assert "ERROR" in result

    @pytest.mark.asyncio
    async def test_staging_list_path_traversal(self, staging_dir):
        from core.tools.staging_tools import staging_list
        with patch("core.tools.staging_tools.STAGING_BASE", staging_dir):
            result = await staging_list.ainvoke({"job_id": "../../../etc"})
            assert "ERROR" in result or "Invalid" in result

    @pytest.mark.asyncio
    async def test_staging_read(self, staging_dir):
        from core.tools.staging_tools import staging_read
        with patch("core.tools.staging_tools.STAGING_BASE", staging_dir):
            result = await staging_read.ainvoke({"job_id": "test-job-123", "path": "main.py"})
            assert "print('hello')" in result

    @pytest.mark.asyncio
    async def test_staging_read_nonexistent_file(self, staging_dir):
        from core.tools.staging_tools import staging_read
        with patch("core.tools.staging_tools.STAGING_BASE", staging_dir):
            result = await staging_read.ainvoke({"job_id": "test-job-123", "path": "missing.py"})
            assert "ERROR" in result

    @pytest.mark.asyncio
    async def test_staging_read_path_traversal(self, staging_dir):
        from core.tools.staging_tools import staging_read
        with patch("core.tools.staging_tools.STAGING_BASE", staging_dir):
            result = await staging_read.ainvoke({"job_id": "test-job-123", "path": "../../etc/passwd"})
            assert "ERROR" in result

    @pytest.mark.asyncio
    async def test_staging_accept(self, staging_dir, tmp_path):
        from core.tools.staging_tools import staging_accept
        dest = tmp_path / "accepted" / "main.py"
        with patch("core.tools.staging_tools.STAGING_BASE", staging_dir):
            result = await staging_accept.ainvoke({
                "job_id": "test-job-123", "path": "main.py", "destination": str(dest),
            })
            assert "ACCEPTED" in result
            assert dest.exists()

    @pytest.mark.asyncio
    async def test_staging_reject(self, staging_dir):
        from core.tools.staging_tools import staging_reject
        with patch("core.tools.staging_tools.STAGING_BASE", staging_dir):
            result = await staging_reject.ainvoke({
                "job_id": "test-job-123", "path": "test.py", "reason": "Security issue",
            })
            assert "REJECTED" in result
            # File should be removed
            assert not (staging_dir / "test-job-123" / "output" / "test.py").exists()
            # Rejection should be logged
            log = staging_dir / "test-job-123" / "audit" / "rejections.jsonl"
            assert log.exists()
            data = json.loads(log.read_text(encoding="utf-8").strip())
            assert data["reason"] == "Security issue"

    @pytest.mark.asyncio
    async def test_staging_large_file_rejected(self, staging_dir):
        """Files over 100KB should be rejected by staging_read."""
        from core.tools.staging_tools import staging_read
        large_file = staging_dir / "test-job-123" / "output" / "big.txt"
        large_file.write_text("x" * 200_000, encoding="utf-8")
        with patch("core.tools.staging_tools.STAGING_BASE", staging_dir):
            result = await staging_read.ainvoke({"job_id": "test-job-123", "path": "big.txt"})
            assert "too large" in result.lower() or "ERROR" in result


# ═══════════════════════════════════════════════════════════════════════════
# IronClaw Agent Factory — Custom Logic
# ═══════════════════════════════════════════════════════════════════════════


class TestIronClawFactory:
    """Test the custom make_ironclaw_agent factory with staging/audit context."""

    def test_factory_returns_coroutine(self, config):
        from core.agents.ironclaw_agent import make_ironclaw_agent
        fn = make_ironclaw_agent(config)
        assert asyncio.iscoroutinefunction(fn)

    @pytest.mark.asyncio
    async def test_factory_emits_claw_events(self, config):
        """Factory should emit claw start/end events."""
        from core.agents.ironclaw_agent import make_ironclaw_agent
        fn = make_ironclaw_agent(config)

        queue = asyncio.Queue()
        state = {
            "messages": [SimpleNamespace(content="test task")],
            "skill_protocols": {},
            "knowledge_context": [],
        }

        # Mock the LLM to avoid real API calls
        with patch("core.agents.base.BaseAgent.execute", new_callable=AsyncMock) as mock_exec:
            from core.state import AgentResult
            mock_exec.return_value = AgentResult(agent="ironclaw", success=True, summary="done")
            await fn(state, event_queue=queue)

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        claw_events = [e for e in events if e.get("cat") == "claw"]
        assert len(claw_events) >= 2  # start + end
        assert claw_events[0]["action"] == "start"
        assert claw_events[-1]["action"] == "end"

    @pytest.mark.asyncio
    async def test_factory_staging_context(self, config):
        """When staging_job_id is set, context should include staging_path."""
        from core.agents.ironclaw_agent import make_ironclaw_agent
        fn = make_ironclaw_agent(config)

        state = {
            "messages": [SimpleNamespace(content="write code")],
            "skill_protocols": {},
            "knowledge_context": [],
            "staging_job_id": "job-456",
        }

        with patch("core.agents.base.BaseAgent.execute", new_callable=AsyncMock) as mock_exec:
            from core.state import AgentResult
            mock_exec.return_value = AgentResult(agent="ironclaw", success=True, summary="done")
            await fn(state, event_queue=None)

        # Check the context passed to execute
        call_kwargs = mock_exec.call_args[1]
        assert "staging" in str(call_kwargs.get("context", {})).lower()

    @pytest.mark.asyncio
    async def test_factory_audit_feedback(self, config):
        """When audit_feedback is set, it should be appended to task."""
        from core.agents.ironclaw_agent import make_ironclaw_agent
        fn = make_ironclaw_agent(config)

        state = {
            "messages": [SimpleNamespace(content="original task")],
            "skill_protocols": {},
            "knowledge_context": [],
            "audit_feedback": "Fix the security issue from previous attempt",
        }

        with patch("core.agents.base.BaseAgent.execute", new_callable=AsyncMock) as mock_exec:
            from core.state import AgentResult
            mock_exec.return_value = AgentResult(agent="ironclaw", success=True, summary="done")
            await fn(state, event_queue=None)

        call_kwargs = mock_exec.call_args[1]
        assert "security issue" in call_kwargs["task"].lower()


# ═══════════════════════════════════════════════════════════════════════════
# Engine View API
# ═══════════════════════════════════════════════════════════════════════════


class TestEngineStatusAPI:
    """Test the server-side IronClaw status endpoint logic."""

    @pytest.mark.asyncio
    async def test_bridge_status_aggregation(self, bridge):
        """Bridge should aggregate health + tools + metrics."""
        health_resp = make_httpx_response(json_data={"status": "healthy", "version": "0.2.0", "uptime_secs": 100})
        tools_resp = make_httpx_response(json_data=[
            {"name": "shell", "description": "Run commands", "risk_level": "High"},
        ])
        metrics_text = "ironclaw_requests_total 50\nironclaw_uptime_seconds 100\n"
        metrics_resp = make_httpx_response(text=metrics_text)

        with patch.object(bridge._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [health_resp, tools_resp, metrics_resp]
            h = await bridge.health()
            tools = await bridge.list_tools()
            m = await bridge.get_metrics()

            assert h.healthy
            assert len(tools) == 1
            assert m.requests_total == 50


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-x"])
