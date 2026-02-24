---
id: grim-architecture
title: "SPEC: GRIM Architecture"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: developing
confidence: 0.8
related: [engine-ironclaw, kronos-vault, grim-skills, grim-identity, coding-integration]
source_repos: [GRIM]
tags: [spec, architecture, grim, core]
---

# SPEC: GRIM Architecture

## Overview

GRIM (General Recursive Intelligence Machine) is a personal AI assistant built on three independent layers: an engine (runtime), a vault (memory), and skills (capabilities). The architecture is designed so any layer can be replaced without breaking the others.

## Three-Layer Design

```
┌─────────────────────────────────────────────┐
│  YOUR LAYER (persists forever)              │
│                                             │
│  vault/     Kronos knowledge graph          │
│  skills/    GRIM capabilities               │
│  identity/  Who GRIM is                     │
│  config/    How GRIM runs                   │
└──────────────┬──────────────────────────────┘
               │  MCP / skill interface
┌──────────────▼──────────────────────────────┐
│  ENGINE (swappable runtime)                 │
│                                             │
│  engine/        IronClaw (Rust)             │
│  mcp/obsidian/  Vault MCP bridge            │
└─────────────────────────────────────────────┘
```

## Requirements

- [x] Engine is a dependency, not the project
- [x] Knowledge lives in plain files (markdown + YAML)
- [x] Skills work as plugins that can survive engine replacement
- [x] Security by default (sandbox all tool execution)
- [ ] Single interface for research, coding, and memory
- [ ] Accessible from mobile (Telegram) and desktop (CLI, Web UI)
- [ ] All state backed up to Proton Drive
- [ ] Git-versioned vault with auto-commit

## Component Map

| Component | Directory | Technology | Status |
|-----------|-----------|------------|--------|
| Engine | `engine/` | IronClaw v0.2 (Rust) | Cloned |
| MCP Bridge | `mcp/obsidian/` | mcp-obsidian (Python) | Cloned |
| Vault | `vault/` | Obsidian + Markdown | Scaffolded |
| Skills | `skills/` | Python / Shell | Planned |
| Identity | `identity/` | YAML + Markdown | Scaffolded |
| Config | `config/` | YAML | Written |

## Data Flow

```
User message (Telegram/CLI/Web)
  → IronClaw engine (auth, rate limit, sanitize)
    → Route to skill based on intent
      → Skill executes (in sandbox)
        → May query Kronos via MCP
        → May call Claude Code / Copilot CLI
        → May read/write vault files
      → Response flows back through engine
    → Output sanitized (DLP, credential redaction)
  → User sees response
```

## Upgrade Strategy

IronClaw is an inlined fork. To pull upstream improvements:

```bash
cd /tmp && git clone https://github.com/CyberSecurityUP/ironclaw.git
# diff and cherry-pick changes into engine/
```

If IronClaw is eventually replaced (e.g., with a custom Axiom runtime), only `engine/` and `config/grim.yaml` change. Vault, skills, and identity persist untouched.

## Connections

- Engine spec: [[engine-ironclaw]]
- Vault spec: [[kronos-vault]]
- Skills spec: [[grim-skills]]
- Identity spec: [[grim-identity]]
- Coding integration: [[coding-integration]]

## Status

- [x] Specified
- [x] Scaffolded
- [ ] Engine building
- [ ] Vault populated
- [ ] Skills implemented
- [ ] End-to-end tested
