# GRIM

**General Recursive Intelligence Machine** — Personal AI assistant built on secure infrastructure, persistent knowledge, and unified coding tools.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    YOUR LAYER (persists)                  │
│                                                          │
│  ../kronos-vault/ ─── Obsidian Kronos knowledge graph    │
│  skills/          ─── GRIM personality, coding, memory   │
│  config/          ─── GRIM-specific configuration        │
│  identity/        ─── Personality, field state, prompts  │
└──────────────────────┬──────────────────────────────────┘
                       │  MCP / skill interface
┌──────────────────────▼──────────────────────────────────┐
│              ENGINE (swappable runtime)                   │
│                                                          │
│  engine/          ─── IronClaw fork (Rust agent core)    │
│  mcp/obsidian/    ─── Obsidian vault MCP bridge          │
└─────────────────────────────────────────────────────────┘
```

## Components

| Directory | What | Why |
|-----------|------|-----|
| `engine/` | IronClaw (Rust agent framework) | 25 LLM providers, 20 channels, 13 security layers, sandboxed tool execution |
| `mcp/obsidian/` | MCP server for Obsidian | Read/write/search vault via Model Context Protocol |
| `../kronos-vault/` | Obsidian knowledge graph (Kronos) | Master vault — external, shared across all repos, git-backed |
| `skills/` | GRIM-specific skills | Kronos ops, Claude Code integration, repo ingestion, vault commit |
| `identity/` | Personality layer | Field state, tone, epistemic stance — who GRIM is |
| `config/` | GRIM configuration | Overrides for IronClaw, MCP endpoints, vault paths |

## Key Design Decisions

1. **Engine is a dependency, not the project.** IronClaw can be upgraded or replaced without touching vault, skills, or identity.
2. **Knowledge lives in plain files.** Obsidian vault = markdown + YAML frontmatter. Git-versioned. Human-browsable. Survives any tooling change.
3. **Skills are the integration layer.** Claude Code, Copilot CLI, Kronos operations, and GRIM personality all expressed as skills that plug into the engine.
4. **Security by default.** IronClaw's 13-layer security pipeline wraps everything — even Claude Code runs inside the sandbox.

## Skills

| Skill | Purpose |
|-------|---------|
| `kronos-query` | Search and retrieve from Obsidian vault |
| `kronos-create` | Create FDO notes with proper schema |
| `kronos-link` | Bidirectional relationship management |
| `claude-code` | Delegate coding tasks to Claude Code CLI |
| `copilot-cli` | GitHub Copilot CLI for shell/git operations |
| `vault-commit` | Auto-commit vault changes with meaningful messages |
| `repo-ingest` | Parse repos into FDO knowledge nodes |
| `proton-sync` | Backup vault to Proton Drive via rclone |

## Quick Start

```bash
# 1. Build the engine
cd engine && cargo build --release

# 2. Configure
cp config/grim.yaml.example config/grim.yaml
# Edit with your Anthropic API key and vault path

# 3. Run
./engine/target/release/ironclaw run --config config/grim.yaml
```

## Vault (Kronos)

Every knowledge node follows the FDO (Field Data Object) schema:

```yaml
---
id: <uuid>
title: <concept name>
domain: <physics|ai-systems|tools|personal|work>
created: <date>
updated: <date>
status: <seed|developing|stable|archived>
confidence: <0.0-1.0>
related: []
source_repos: []
tags: []
---
```

The Kronos vault lives at `../kronos-vault/` (external to this repo). Open it in Obsidian for visual graph navigation.

## Lineage

GRIM descends from:
- **Grimm v0.2** (Python, Axiom framework, Soul/Lobe architecture)
- **Kronos v1** (Neo4j + Qdrant graph memory)  
- **Kronos v2** (fracton conceptual genealogy engine, 115-node DFT graph)
- **IronClaw v0.2** (Rust secure agent framework)

The old Grimm codebase is preserved in git history at `lornecodes/grimm@e5cc792`.

---

*Runtime: IronClaw | Memory: Kronos/Obsidian | Identity: GRIM*
