---
id: grim-skills
title: "SPEC: GRIM Skills System"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: developing
confidence: 0.7
related: [grim-architecture, engine-ironclaw, skill-kronos-query, skill-kronos-create, skill-kronos-link, skill-claude-code, skill-copilot-cli, skill-vault-commit, skill-repo-ingest, skill-proton-sync]
source_repos: [GRIM]
tags: [spec, skills, grim]
---

# SPEC: GRIM Skills System

## Overview

Skills are GRIM's capability layer — self-contained modules that the IronClaw engine discovers, verifies, and executes in its sandbox. They are the bridge between the engine runtime and external tools (Kronos vault, Claude Code, Copilot CLI, git, rclone).

## Skill Architecture

Each skill is a directory under `skills/` with:

```
skills/
├── skill-name/
│   ├── manifest.yaml    # Identity, permissions, entry point
│   ├── README.md        # Documentation
│   └── src/
│       └── main.py      # Implementation (or .sh, .rs)
```

### Manifest Schema

```yaml
name: skill-name
version: "0.1.0"
description: What this skill does
author: Dawn Field Institute
entry_point: src/main.py
language: python  # python | shell | rust

permissions:
  filesystem:
    read: [./vault/**]
    write: [./vault/**]
  network:
    allow: [127.0.0.1:27124]  # Obsidian REST API
  shell: false

tags: [kronos, vault]
```

## Skill Inventory

### Kronos Skills (Vault Operations)

| Skill | Priority | Description |
|-------|----------|-------------|
| [[skill-kronos-query]] | P0 | Search and retrieve from vault |
| [[skill-kronos-create]] | P0 | Create new FDO notes |
| [[skill-kronos-link]] | P1 | Manage bidirectional relationships |

### Coding Skills

| Skill | Priority | Description |
|-------|----------|-------------|
| [[skill-claude-code]] | P0 | Delegate coding tasks to Claude Code CLI |
| [[skill-copilot-cli]] | P1 | GitHub Copilot for shell/git suggestions |

### Maintenance Skills

| Skill | Priority | Description |
|-------|----------|-------------|
| [[skill-vault-commit]] | P1 | Auto-commit vault changes |
| [[skill-repo-ingest]] | P2 | Parse repos into FDO nodes |
| [[skill-proton-sync]] | P2 | Backup to Proton Drive |

## Implementation Priority

**Phase 1 (This Weekend):**
1. `kronos-query` — Can GRIM read the vault?
2. `kronos-create` — Can GRIM write to the vault?
3. `claude-code` — Can GRIM code?

**Phase 2 (Next Week):**
4. `kronos-link` — Relationship management
5. `vault-commit` — Auto-commit
6. `copilot-cli` — Shell/git assistance

**Phase 3 (When Needed):**
7. `repo-ingest` — Batch knowledge import
8. `proton-sync` — Cloud backup

## Requirements

- [ ] At least one skill loads and executes in IronClaw
- [ ] Skills can call Obsidian REST API
- [ ] Skills can invoke CLI tools (claude, gh copilot)
- [ ] Skills return structured responses to the engine
- [ ] Failed skills degrade gracefully (don't crash GRIM)

## Connections

- Parent: [[grim-architecture]]
- Engine that runs them: [[engine-ironclaw]]
- Individual skill specs linked above

## Status

- [x] Specified
- [x] Directory structure created
- [ ] First skill (kronos-query) implemented
- [ ] Skill loading verified in IronClaw
- [ ] All P0 skills working
