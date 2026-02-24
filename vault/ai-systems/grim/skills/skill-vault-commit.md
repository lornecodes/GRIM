---
id: skill-vault-commit
title: "SPEC: Skill — vault-commit"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: seed
confidence: 0.6
related: [grim-skills, kronos-vault]
source_repos: [GRIM]
tags: [spec, skill, git, vault, maintenance, p1]
---

# SPEC: Skill — vault-commit

## Overview

Detect changes in the vault directory, generate a meaningful commit message based on what changed, and commit. Keeps vault history clean and automatic.

## Interface

**Input**: Optional manual commit message
**Output**: Commit hash and summary

## Logic

1. `git status vault/` → detect changes
2. Parse changed files to understand what domains/concepts were affected
3. Generate commit message: `vault: updated [domains] — [summary of changes]`
4. `git add vault/` → `git commit -m "message"`
5. Optionally push

## Trigger Modes

- **Manual**: User says "commit the vault"
- **Scheduled**: Every N hours (via IronClaw workflow engine)
- **On skill exit**: After kronos-create or kronos-link runs

## Priority

**P1** — Important for not losing work, but manual git works in the interim.

## Status

- [x] Specified
- [ ] Implemented
- [ ] Tested
