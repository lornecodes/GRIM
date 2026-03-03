"""IronClaw Bridge — HTTP client for the IronClaw REST gateway.

GRIM communicates with IronClaw via its REST API (default port 3100).
IronClaw runs as a sidecar service and handles sandboxed tool execution
with security policies, DLP, audit logging, and cost tracking.

The bridge is intentionally simple: it calls tool execution endpoints
and returns structured results. IronClaw never makes LLM calls —
GRIM controls all reasoning via LangGraph.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Default timeout for tool execution (matches IronClaw's tool_timeout_secs)
TOOL_TIMEOUT = 30.0

# Health check timeout (fast — just a ping)
HEALTH_TIMEOUT = 5.0


@dataclass
class ResourceUsage:
    """Resource consumption from a sandboxed execution."""

    cpu_time_ms: int = 0
    memory_peak_kb: int = 0
    wall_time_ms: int = 0


@dataclass
class ToolResult:
    """Result from an IronClaw tool execution."""

    success: bool
    output: str
    execution_id: str = ""
    duration_ms: int = 0
    exit_code: int | None = None
    stderr: str = ""
    timed_out: bool = False
    resource_usage: ResourceUsage = field(default_factory=ResourceUsage)


@dataclass
class ToolSchema:
    """Tool description from IronClaw."""

    name: str
    description: str
    risk_level: str  # Low, Medium, High, Critical


@dataclass
class AuditEntry:
    """Single audit log entry."""

    event: str
    request_id: str
    method: str = ""
    uri: str = ""
    peer: str = ""
    timestamp: str = ""


@dataclass
class EngineMetrics:
    """Prometheus-style metrics from IronClaw."""

    requests_total: int = 0
    requests_failed: int = 0
    auth_failures: int = 0
    rate_limited: int = 0
    active_sessions: int = 0
    active_websockets: int = 0
    uptime_seconds: float = 0.0


@dataclass
class HealthStatus:
    """IronClaw gateway health."""

    healthy: bool
    version: str = ""
    uptime_secs: float = 0.0


class IronClawBridge:
    """HTTP client for the IronClaw REST gateway.

    Usage:
        bridge = IronClawBridge("http://localhost:3100")
        if await bridge.health():
            result = await bridge.execute_tool("file_read", {"path": "src/main.rs"})
            print(result.output)
        await bridge.close()
    """

    def __init__(
        self,
        base_url: str = "http://localhost:3100",
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        headers = {}
        if api_key:
            headers["X-Api-Key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(TOOL_TIMEOUT, connect=5.0),
        )
        logger.info("IronClaw bridge: targeting %s", self.base_url)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    # ── Health ──

    async def health(self) -> HealthStatus:
        """Check if the IronClaw gateway is healthy."""
        try:
            resp = await self._client.get(
                "/v1/health",
                timeout=HEALTH_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            return HealthStatus(
                healthy=data.get("status") == "healthy",
                version=data.get("version", ""),
                uptime_secs=data.get("uptime_secs", 0.0),
            )
        except Exception as exc:
            logger.warning("IronClaw health check failed: %s", exc)
            return HealthStatus(healthy=False)

    async def is_available(self) -> bool:
        """Quick boolean availability check."""
        status = await self.health()
        return status.healthy

    # ── Tool execution ──

    async def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Execute a tool on IronClaw via the gateway.

        This is the core integration point. GRIM's LangGraph agents
        call this to run sandboxed file/shell/network operations.

        Args:
            name: Tool name (file_read, file_write, shell, directory_list, etc.)
            arguments: Tool arguments dict.

        Returns:
            ToolResult with output, execution metadata, and resource usage.
        """
        try:
            resp = await self._client.post(
                f"/v1/tools/{name}/execute",
                json={"arguments": arguments or {}},
            )
            resp.raise_for_status()
            data = resp.json()

            # Parse resource usage if present
            usage_data = data.get("resource_usage", {})
            resource_usage = ResourceUsage(
                cpu_time_ms=usage_data.get("cpu_time_ms", 0),
                memory_peak_kb=usage_data.get("memory_peak_kb", 0),
                wall_time_ms=usage_data.get("wall_time_ms", 0),
            )

            output = data.get("output", "")
            # If the tool failed with an empty output, surface the error field
            # from the Rust ToolResult (e.g. "Write path not in allow list: ...")
            if not data.get("success", False) and not output:
                output = data.get("error", "")

            return ToolResult(
                success=data.get("success", False),
                output=output,
                execution_id=data.get("execution_id", resp.headers.get("x-request-id", "")),
                duration_ms=data.get("duration_ms", 0),
                exit_code=data.get("exit_code"),
                stderr=data.get("stderr", ""),
                timed_out=data.get("timed_out", False),
                resource_usage=resource_usage,
            )
        except httpx.HTTPStatusError as exc:
            error_body = exc.response.json() if exc.response.content else {}
            return ToolResult(
                success=False,
                output=f"IronClaw error ({exc.response.status_code}): "
                       f"{error_body.get('message', str(exc))}",
                execution_id=exc.response.headers.get("x-request-id", ""),
            )
        except Exception as exc:
            logger.error("IronClaw tool execution failed: %s", exc)
            return ToolResult(
                success=False,
                output=f"IronClaw bridge error: {exc}",
            )

    # ── Tool listing ──

    async def list_tools(self) -> list[ToolSchema]:
        """List available tools on the IronClaw gateway."""
        try:
            resp = await self._client.get("/v1/tools")
            resp.raise_for_status()
            return [
                ToolSchema(
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    risk_level=t.get("risk_level", "Unknown"),
                )
                for t in resp.json()
            ]
        except Exception as exc:
            logger.warning("IronClaw list_tools failed: %s", exc)
            return []

    # ── Agents ──

    async def list_agents(self) -> dict:
        """List IronClaw's built-in agent roles."""
        try:
            resp = await self._client.get("/v1/agents")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("IronClaw list_agents failed: %s", exc)
            return {"enabled": False, "roles": [], "active_sessions": 0, "max_concurrent_sessions": 0}

    async def run_workflow(self, task: str, pattern: dict) -> dict:
        """Run a simple agent workflow via IronClaw orchestrator."""
        try:
            resp = await self._client.post(
                "/v1/agents/workflow",
                json={"task": task, "pattern": pattern},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            error_body = exc.response.json() if exc.response.content else {}
            return {
                "session_id": "",
                "status": "failed",
                "agents_executed": [],
                "results": {},
                "error": error_body.get("message", str(exc)),
            }
        except Exception as exc:
            logger.error("IronClaw workflow execution failed: %s", exc)
            return {
                "session_id": "",
                "status": "failed",
                "agents_executed": [],
                "results": {},
                "error": str(exc),
            }

    # ── Security scanning ──

    async def scan_skill(self, code: str, file_name: str = "code.py") -> dict:
        """Scan code for security vulnerabilities via IronClaw."""
        try:
            resp = await self._client.post(
                "/v1/skills/scan",
                json={"source": code, "file_name": file_name},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("IronClaw skill scan failed: %s", exc)
            return {"error": str(exc), "findings": [], "risk_score": -1}

    # ── Metrics ──

    async def get_metrics(self) -> EngineMetrics:
        """Get Prometheus-style metrics from IronClaw."""
        try:
            resp = await self._client.get("/v1/metrics")
            resp.raise_for_status()
            text = resp.text
            return _parse_prometheus_metrics(text)
        except Exception as exc:
            logger.warning("IronClaw metrics fetch failed: %s", exc)
            return EngineMetrics()


def _parse_prometheus_metrics(text: str) -> EngineMetrics:
    """Parse Prometheus text format into EngineMetrics."""
    metrics = EngineMetrics()
    for line in text.strip().split("\n"):
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        key, value = parts[0], parts[1]
        try:
            if key == "ironclaw_requests_total":
                metrics.requests_total = int(float(value))
            elif key == "ironclaw_requests_failed":
                metrics.requests_failed = int(float(value))
            elif key == "ironclaw_auth_failures":
                metrics.auth_failures = int(float(value))
            elif key == "ironclaw_rate_limited":
                metrics.rate_limited = int(float(value))
            elif key == "ironclaw_active_sessions":
                metrics.active_sessions = int(float(value))
            elif key == "ironclaw_active_websockets":
                metrics.active_websockets = int(float(value))
            elif key == "ironclaw_uptime_seconds":
                metrics.uptime_seconds = float(value)
        except ValueError:
            continue
    return metrics
