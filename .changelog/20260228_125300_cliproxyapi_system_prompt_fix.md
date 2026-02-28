# CLIProxyAPI System Prompt Fix + Journal Skill

**Date**: 2026-02-28

## Summary

Fixed CLIProxyAPI injecting "You are Claude Code..." system prompt into every request, which overrode GRIM's personality. Added kronos-journal skill for structured learning capture.

## Problem

CLIProxyAPI's `checkSystemInstructions()` unconditionally prepends the Claude Code system prompt to the API `system` field. This is hardcoded in the Go binary and not controlled by any config option. GRIM's personality layer (Shade archetype) was being completely overridden.

## Solution

Two-part fix:

1. **Proxy config** (`config/cliproxyapi.yaml`): Added `payload.filter` to strip the `system` field from all Claude requests
2. **Application code** (`core/nodes/companion.py`, `core/agents/base.py`): Changed from `SystemMessage` to `HumanMessage`/`AIMessage` pairs to bypass the stripped field

## New Files

- `skills/kronos-journal/manifest.yaml` — Journal/notes skill manifest
- `skills/kronos-journal/protocol.md` — Protocol for creating journal entries and technical notes
- `kronos-vault/journal/2026-02-28_cliproxyapi_system_prompt.md` — Journal entry documenting the discovery

## Modified Files

- `config/cliproxyapi.yaml` — Added payload filter for system field
- `core/nodes/companion.py` — SystemMessage → HumanMessage/AIMessage pair
- `core/agents/base.py` — Same pattern for agent system prompts
- `kronos-vault/decisions/adr-cliproxyapi-integration.md` — Added system prompt injection section

## Tests

- 130 core unit tests passing
- GRIM personality verified working through REST API
