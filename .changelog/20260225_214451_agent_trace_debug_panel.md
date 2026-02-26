# Agent Trace Debug Panel + Streaming UI

**Date**: 2026-02-25 21:44
**Type**: engineering

## Summary
Rebuilt the GRIM chat UI with a comprehensive agent trace/debug panel and real-time token streaming. The server WebSocket handler now emits rich trace events for the entire graph pipeline, and the UI renders them in a collapsible side panel with category filtering.

## Changes

### Changed
- `server/app.py` — WebSocket handler rewritten with full trace protocol:
  - `trace` events with categories: `node`, `llm`, `tool`, `graph`
  - Node lifecycle with timing, FDO counts, mode, skills, field_state
  - LLM lifecycle with message counts and token usage metadata
  - Tool lifecycle with input/output previews
  - Graph start/complete with total timing
  - Added `_safe_truncate()` helper for trace display
- `server/static/index.html` — Complete UI rebuild:
  - New collapsible debug side-panel (420px, toggled via "trace" button)
  - Color-coded trace entries by category (blue=node, purple=llm, green=tool, amber=graph)
  - Expandable detail sections showing JSON payloads (tool inputs/outputs, token counts, etc.)
  - Category filter buttons to show/hide specific trace types
  - Clear button for trace log
  - Inline status lines in chat area (spinners for active nodes/tools)
  - Token streaming preserved from previous implementation
  - Removed old `thinking`/`tool_call`/`status` handlers (replaced by trace protocol)
  - Meta badge on final response shows mode, FDO count, skills, total_ms

### Removed
- Old thinking dots animation
- Old tool_call badges that overwrote status
- Inline trace panel CSS (replaced by side-panel approach)

## Details
The previous UI had a lag issue where `ainvoke` waited for the full LLM response before sending anything. This was fixed in the prior session by switching to `astream_events`. This session adds the debug visibility layer on top — the full agent trace showing every node transition, LLM call, tool invocation, and timing data.

The trace protocol emits events as they happen, so the debug panel updates in real-time alongside the streamed response. Each trace entry can be expanded to show the full detail payload (e.g., tool input arguments, output previews, token usage stats).

MCP tool calls themselves are fast (31-47ms measured via `/api/test-mcp`). The perceived latency is Claude's thinking time, which is now visible in the trace panel as LLM call duration.
