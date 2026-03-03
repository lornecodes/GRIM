# IronClaw 404 Fix + Comprehensive Agent & IronClaw Tests

**Date**: 2026-03-02
**Scope**: engine, tests

## Bug Fix

- **IronClaw tool execution 404**: Fixed route pattern in Rust gateway (`engine/src/gateway/mod.rs`).
  The route `/v1/tools/{name}/execute` used axum 0.8+ `{name}` capture syntax, but the engine
  runs axum 0.7.9 which uses `:name` syntax. Changed to `/v1/tools/:name/execute`.
  All tool execution endpoints (shell, file_read, file_write, http_request, directory_list) now
  return 200 instead of 404.

## Tests Added (333 new tests)

### `test_agent_comprehensive.py` — Agent subclass coverage
- **TestMemoryAgent** (14 tests): vault write tools, task tools, memory tools, protocol priority
- **TestResearchAgent** (14 tests): read-only boundary, build_context with FDOs
- **TestCodebaseAgent** (15 tests): source tools, repos manifest loading, read-only boundary
- **TestOperatorAgent** (10 tests): git read tools, no write tools
- **TestCoderAgent** (10 tests): file/shell/companion tools
- **TestIronClawAgentComprehensive** (15 tests): claw_* tools, tier, toggleable, custom factory
- **TestAuditAgentComprehensive** (10 tests): staging read-only, no accept/reject
- **TestAuditVerdictParsing** (7 tests): JSON fence, raw JSON, invalid, missing fields
- **TestCrossAgentInvariants** (18 tests × 7 agents = 126): parametrized across all agents
- **TestToolBoundaries** (4 tests): trust boundary enforcement
- **TestAgentUniqueness** (3 tests): unique names, display names, colors
- **TestModelConfiguration** (3 tests): config model binding, temperature, caller ID
- **TestDiscoveryAttributes** (5 × 7 + 1 = 36): module exports, planning deprecated

### `test_ironclaw_comprehensive.py` — Bridge, tools, staging, factory
- **TestDataTypes** (7 tests): ToolResult, HealthStatus, ResourceUsage, etc.
- **TestBridgeHealth** (4 tests): success, failure, non-healthy, is_available
- **TestBridgeToolExecution** (12 tests): success, resource usage, failure, HTTP errors, URL construction, arguments, exit codes, timeouts
- **TestBridgeToolListing** (2 tests): success and failure
- **TestBridgeAgents** (5 tests): list agents, run workflow, failure cases
- **TestBridgeSecurityScan** (3 tests): clean code, findings, failure
- **TestBridgeMetrics** (6 tests): Prometheus parsing, all fields, edge cases
- **TestBridgeConfiguration** (6 tests): URLs, API keys, close
- **TestLangChainToolWrappers** (11 tests): all 8 claw_* tools, headers, invalid headers, no bridge
- **TestToolResultFormatting** (5 tests): success, failure, stderr, timeout, duration
- **TestToolRegistration** (5 tests): tool groups, registry
- **TestStagingTools** (11 tests): list, read, accept, reject, path traversal, large files
- **TestIronClawFactory** (4 tests): coroutine, events, staging context, audit feedback
- **TestEngineStatusAPI** (1 test): bridge aggregation

## Metrics
- Total tests: 1637 (was 1304, +333)
