# IronClaw Integration Spec

## Overview

IronClaw is GRIM's sandboxed execution engine. It runs as a REST gateway sidecar
and handles tool execution (file I/O, shell commands, HTTP requests) with a 13-layer
zero-trust security pipeline. GRIM's LangGraph controls all reasoning; IronClaw
only executes tools.

**Principle**: "Engine is the limbs, not the brain."

## Architecture

```
LangGraph → dispatch → ironclaw_agent → IronClawBridge → REST → IronClaw Gateway
                                                                   ↓
                                                            Sandbox (Docker/Native)
                                                                   ↓
                                                            Tool execution
```

## Bridge API

### Base URL
- Default: `http://localhost:3100`
- Docker: `http://ironclaw:3100`
- Env: `IRONCLAW_URL`

### Endpoints Used

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/health` | GET | Health check (no auth) |
| `/v1/tools` | GET | List available tools |
| `/v1/tools/{name}/execute` | POST | Execute a tool |
| `/v1/metrics` | GET | Prometheus metrics |

### Tool Execution Request
```json
POST /v1/tools/file_read/execute
{
  "arguments": {
    "path": "src/main.rs",
    "start_line": 1
  }
}
```

### Tool Execution Response
```json
{
  "success": true,
  "output": "file contents...",
  "execution_id": "uuid",
  "duration_ms": 42,
  "exit_code": 0,
  "stderr": "",
  "timed_out": false,
  "resource_usage": {
    "cpu_time_ms": 5,
    "memory_peak_kb": 1024,
    "wall_time_ms": 42
  }
}
```

## Tool Mapping

| GRIM Tool | IronClaw Tool | Risk Level |
|-----------|---------------|------------|
| `claw_read_file` | `file_read` | Low |
| `claw_write_file` | `file_write` | High |
| `claw_shell` | `shell` | Critical |
| `claw_list_dir` | `directory_list` | Low |
| `claw_http_request` | `http_request` | High |

## Config: `engine/config/grim.yaml`

Key overrides from default IronClaw config:
- `agent.max_turns: 0` — No autonomous LLM calls
- `gateway.bind: 0.0.0.0:3100` — Internal network access
- `gateway.loopback_no_auth: true` — GRIM connects via docker network
- `sandbox.backend: native` — Docker/Bubblewrap optional
- `permissions.system.allow_shell: true` — GRIM delegates shell here
- `permissions.tools.*.require_approval: false` — GRIM handles approval at router level

## Graph Integration

### New Agent: `ironclaw`
- Registered in dispatch: `agents["ironclaw"] = ironclaw_agent_fn`
- Follows BaseAgent pattern (tool-calling loop, max 10 calls)
- Tools: `IRONCLAW_TOOLS + COMPANION_TOOLS` (sandboxed ops + read-only Kronos)

### Routing
- Skill-driven: `consumer: ironclaw` on skill manifests
- Keyword fallback: "run sandboxed", "execute safely", "isolated shell", etc.
- Skill mapping: `sandboxed-execution`, `secure-shell`, `ironclaw-execute`

### State
- `ironclaw_available: bool` — Set by identity node on health check
- `delegation_type` literal includes `"ironclaw"`

## Server Lifecycle

1. **Startup**: Create `IronClawBridge`, health check, pass to `build_graph()`
2. **Runtime**: Bridge used by ironclaw tools via module-level reference
3. **Shutdown**: Close bridge HTTP client

### Env Vars
- `IRONCLAW_URL` — Gateway URL (default: `http://localhost:3100`)

## WebSocket Protocol

New trace category: `claw`
- Emitted for `claw_*` tool start/end events
- Includes `sandboxed: true` flag
- UI renders with orange accent color

## Error Handling

- Bridge returns `ToolResult(success=False, ...)` on HTTP errors
- Gateway unreachable → graceful fallback, log warning, don't crash graph
- `ironclaw_available: false` in state → router won't route to ironclaw agent
- Timeout: 30s per tool call (matches IronClaw's `tool_timeout_secs`)

## Docker

### Service: `ironclaw`
- Build: `engine/Dockerfile` (multi-stage Rust build)
- Port: 3100 (internal network)
- Config: `engine/config/grim.yaml` mounted read-only
- Health: `GET /v1/health`
- Depends: nothing (standalone)
- GRIM depends on: ironclaw (healthy)

## Security

- IronClaw's own LLM is disabled (`max_turns: 0`)
- All reasoning happens in GRIM's LangGraph
- IronClaw applies: RBAC, command guardian, anti-stealer, SSRF guard, DLP, audit logging
- Dangerous patterns blocked: `rm -rf /`, fork bombs, `mkfs`, `dd if=/dev`
- File deny list: `.env`, `*.pem`, `*.key`, `/proc/**`, `/sys/**`
