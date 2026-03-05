# Charizard Spikes

Experimental code for Project Charizard — GRIM Execution Pool Architecture.

Each spike is self-contained and proves a specific capability before we build it into the main codebase.

## Spikes

| # | Name | Goal | Status | Cost |
|---|------|------|--------|------|
| 01 | agent-sdk-runtime | Prove Agent SDK `query()` + `ClaudeSDKClient` work with custom MCP tools | PROVEN | ~$0.08 |
| 02 | coding-agent | Full coding agent: write code + tests, iterate on failures | PROVEN | ~$0.18 |
| 03 | research-agent | Research agent with real Kronos MCP (search, graph, source) | PROVEN | ~$0.70 |
| 04 | audit-agent | Audit agent reviewing session transcripts + diffs, structured verdicts | PROVEN | ~$0.15 |
| 05 | persistent-session | GRIM as persistent Agent SDK session — multi-turn, Kronos + Pool MCP, personality | PROVEN | ~$0.26 |

## Key Findings

### Spike 01 — Agent SDK Runtime
- `os.environ.pop("CLAUDECODE")` needed to spawn child agents from parent Claude Code
- MCP tools auto-prefixed as `mcp__<server>__<tool>` — use prefixed names in `allowed_tools`
- `ClaudeSDKClient` more reliable than `query()` for MCP tool sessions
- `can_use_tool` callback doesn't fire when `allowed_tools` covers the tool
- `permission_mode="bypassPermissions"` for non-interactive use

### Spike 02 — Coding Agent
- Agent checks project context before coding (when instructed via system prompt)
- Writes comprehensive tests autonomously (22 tests for 4 functions)
- Uses `Edit` (not full rewrite) to fix bugs — minimal diffs
- Parallelizes independent operations (read + test in same turn)
- Cost: ~$0.10 for write-from-scratch, ~$0.08 for bug-fix iteration

### Spike 03 — Research Agent
- Real Kronos MCP server works via stdio transport
- Agent uses search, get, list, tags, graph, deep_dive, navigate, read_source
- Handles search timeouts gracefully (falls back to tags + list)
- Synthesizes multi-FDO knowledge with citations
- Cost: $0.14-0.31 per research query depending on depth

### Spike 04 — Audit Agent
- Structured verdict via custom MCP tool (approve/request-changes/reject)
- Correctly approves well-written code with standards compliance
- Correctly rejects insecure code — finds SQL injection, hardcoded secrets, missing tests
- Issues categorized by severity (critical/major/minor)
- Cost: ~$0.07-0.08 per audit review

### Spike 05 — Persistent Session (GRIM-as-Client)
- Multi-turn context persists across `query()` calls on same `ClaudeSDKClient`
- External MCP (Kronos stdio) + in-process MCP (Pool SDK tools) coexist in same session
- GRIM identity/personality comes through from system prompt
- Pool tools (submit, status, list) work as in-process MCP via `create_sdk_mcp_server()`
- Real SQLite-backed JobQueue for pool state persistence
- `kronos_search` with `semantic=true` is slow on cold start (~60s model load) — use `semantic=false` for fast queries
- Cost: ~$0.26 for 10 turns across 4 test cases
- **Key insight**: GRIM = persistent SDK session + Kronos MCP + Pool MCP. No LangGraph needed.

## Running a Spike

```bash
cd GRIM/spikes/01_agent_sdk_runtime
python spike.py
```

**Prerequisites:** `pip install claude-agent-sdk` (v0.1.45+)

## Architecture Reference

See `charizard-architecture` FDO in kronos-vault for full spec.
