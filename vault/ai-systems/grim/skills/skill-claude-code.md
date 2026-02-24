---
id: skill-claude-code
title: "SPEC: Skill — claude-code"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: seed
confidence: 0.6
related: [grim-skills, coding-integration, engine-ironclaw]
source_repos: [GRIM]
tags: [spec, skill, claude-code, coding, p0]
---

# SPEC: Skill — claude-code

## Overview

Delegate complex coding tasks to Claude Code CLI. GRIM provides context from Kronos and the user's request; Claude Code does the actual file reading, editing, test running, and git operations.

## Interface

**Input**: Task description, target repo path, optional Kronos context
**Output**: Summary of changes made, files modified, test results

## Implementation

```bash
# Basic invocation
claude -p "task description" --cwd /path/to/target/repo

# With context injection
claude -p "Context from Kronos:\n$CONTEXT\n\nTask: $TASK" --cwd $REPO_PATH
```

## Context Injection Flow

1. GRIM receives coding request from user
2. Skill optionally queries Kronos for relevant specs/architecture
3. Builds prompt: Kronos context + user task + any constraints
4. Invokes `claude` CLI pointed at target repo
5. Captures stdout/stderr
6. Returns structured summary to GRIM

## Security

- Runs inside IronClaw sandbox
- Filesystem access limited by IronClaw permissions
- DLP scans output for credential exposure
- Audit logged

## Supported Repos

Any repo the user points to. Primary targets:
- `reality-engine/`
- `dawn-field-theory/`
- `dawn-models/`
- `fracton/`
- `GRIM/` itself (self-modification!)

## Prerequisites

```bash
npm install -g @anthropic-ai/claude-code
# Verify
claude --version
```

## Priority

**P0** — Core value prop: one interface for thinking AND coding.

## Status

- [x] Specified
- [ ] Claude Code CLI installed
- [ ] Basic invocation working
- [ ] Context injection working
- [ ] Sandbox permissions configured
