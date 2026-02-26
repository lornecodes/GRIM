# MCP Async Fix + Skills Ecosystem Audit

**Date**: 2026-02-26

## Changes

### Bug Fix: Kronos MCP Async Hang
- **File**: `mcp/kronos/src/kronos_mcp/server.py`
- **Root cause**: `call_tool` async handler was calling synchronous file I/O handlers directly, blocking the event loop during large FDO body writes (`vault.write_fdo()` + `search_engine.invalidate()`).
- **Fix**: Added `import asyncio` and changed `result = handler(arguments)` → `result = await asyncio.to_thread(handler, arguments)` so file I/O runs in a thread pool without blocking the event loop.
- **Impact**: Fixes intermittent hangs on `kronos_update`, `kronos_create`, and other tools with large payloads.

### Skill Manifest Deployment Path Fixes
- Fixed `deployment.claude_skill` path in 5 kronos skill manifests: `.github/instructions/` → `.claude/instructions/`
  - `skills/kronos-capture/manifest.yaml`
  - `skills/kronos-promote/manifest.yaml`
  - `skills/kronos-recall/manifest.yaml`
  - `skills/kronos-reflect/manifest.yaml`
  - `skills/kronos-relate/manifest.yaml`

### Vault FDO Updates (via Kronos MCP)
- `grim-langgraph`: seed/0.6 → stable/0.85. Full body rewrite reflecting 8-node LangGraph, 4 agents, actual file paths.
- `skill-kronos-query`: → stable/0.9
- `skill-kronos-create`: → stable/0.9
- `skill-kronos-link`: → stable/0.85
- `skill-claude-code`: → developing/0.8
- `skill-vault-commit`: → developing/0.6

## Pending After MCP Restart
- `grim-architecture`: needs status → stable/0.85, directory structure update, Phase 1+2 checked off
- `grim-server-ui`: needs `confidence_basis` field + bidirectional link fixes
- 9 non-bidirectional links to fix across grim-langgraph, grim-server-ui, proj-kronos

## Context Setup (DFI Workspace)
- Created `MEMORY.md` at `~/.claude/projects/.../memory/MEMORY.md` — lean session orientation card
- Slimmed `dawn-field-theory.instructions.md`: 198 → 18 lines, scoped to `dawn-field-theory/**` only
- Scoped `experiment-schema.instructions.md` and `zenodo-workflow.instructions.md` to physics repos only
- Added `vault-sync` and `deep-ingest` as on-demand skill entries in `main.instructions.md` dispatch table
- Fixed `settings.local.json`: removed API key, added auto-approve for all tools including `mcp__kronos`

## Notes
- `tools/actualization/` discovered — full LangGraph vault ingestion pipeline with `scan` and `status` commands. Needs vault FDO (currently undocumented).
- `skills/code-execution/`, `skills/file-operations/`, `skills/git-operations/` are untracked in GRIM git.
