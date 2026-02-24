---
id: engine-ironclaw
title: "SPEC: IronClaw Engine Integration"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: developing
confidence: 0.7
related: [grim-architecture, grim-skills, coding-integration]
source_repos: [GRIM, CyberSecurityUP/ironclaw]
tags: [spec, engine, ironclaw, rust, runtime]
---

# SPEC: IronClaw Engine Integration

## Overview

IronClaw is GRIM's runtime engine — a Rust-based secure AI agent framework providing LLM orchestration, channel management, tool sandboxing, and security. It is treated as an inlined fork, not modified directly unless necessary.

## Why IronClaw

| Need | IronClaw Provides |
|------|-------------------|
| LLM access | 25 providers (Anthropic first-class) |
| Mobile access | 20 channels (Telegram, Discord, Web UI) |
| Security | 13-layer pipeline (guardian, DLP, RBAC, sandbox) |
| Tool execution | Sandboxed skill runtime with signature verification |
| Cost management | SQLite-backed daily/monthly budgets |
| Audit | Structured JSON logging, SIEM export, PII redaction |

## Requirements

- [ ] Build successfully on Windows (cargo build --release)
- [ ] Connect to Anthropic API with claude-sonnet-4-6
- [ ] Accept input from CLI channel
- [ ] Accept input from Telegram channel
- [ ] Load custom system prompt from `identity/system_prompt.md`
- [ ] Load skills from `skills/` directory
- [ ] Support MCP server connections (for Obsidian)
- [ ] Web UI accessible on localhost:3000

## Configuration

GRIM overrides IronClaw defaults via `config/grim.yaml`:

**Key overrides from default IronClaw:**
- `system_prompt`: GRIM identity instead of generic IronClaw
- `default_model`: claude-sonnet-4-6
- `allow_shell: true` (needed for Claude Code / Copilot CLI skills)
- `allow_private_network: true` (needed for Obsidian Local REST API)
- `require_signatures: false` (development mode)
- `max_daily_cost_cents: 1000` ($10/day budget)

## Build & Run

```bash
# Prerequisites: Rust 1.75+, Docker (optional for sandbox)
cd engine
cargo build --release

# Run with GRIM config
./target/release/ironclaw run --config ../config/grim.yaml

# Or with Web UI
./target/release/ironclaw run --config ../config/grim.yaml --ui
```

## MCP Integration

IronClaw needs to connect to MCP servers. The `config/grim.yaml` specifies:

```yaml
mcp:
  obsidian:
    command: "uvx"
    args: ["mcp-obsidian"]
    env:
      OBSIDIAN_API_KEY: "${OBSIDIAN_API_KEY}"
```

**Open question:** IronClaw v0.2 may not have native MCP client support yet. If not, we need either:
1. A skill that wraps MCP calls as HTTP requests to the Obsidian REST API directly
2. A sidecar process that bridges MCP ↔ IronClaw tool interface
3. Contribute MCP client support upstream

## Upstream Tracking

Current base: `CyberSecurityUP/ironclaw@dbd8b17` (v0.2.0, Feb 2026)

To merge upstream changes:
```bash
cd /tmp && git clone https://github.com/CyberSecurityUP/ironclaw.git
diff -rq /tmp/ironclaw/src engine/src
# Review and apply relevant changes
```

## Risks

| Risk | Mitigation |
|------|------------|
| IronClaw is brand new (v0.2, 1 week old) | Inlined fork means we're not blocked by upstream |
| Rust makes custom mods harder | Skills are Python/shell — engine mods should be rare |
| MCP support may not exist | Fallback to direct HTTP calls to Obsidian REST API |
| Windows build issues | IronClaw targets Linux; may need WSL or Docker |

## Connections

- Parent: [[grim-architecture]]
- Skills it runs: [[grim-skills]]
- Coding tools it sandboxes: [[coding-integration]]

## Status

- [x] Specified
- [x] Cloned into engine/
- [ ] Built successfully
- [ ] Anthropic provider verified
- [ ] CLI channel working
- [ ] MCP integration verified
- [ ] Telegram channel configured
