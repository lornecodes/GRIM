---
id: coding-integration
title: "SPEC: Coding Integration (Claude Code + Copilot CLI)"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: developing
confidence: 0.7
related: [grim-architecture, grim-skills, skill-claude-code, skill-copilot-cli, engine-ironclaw]
source_repos: [GRIM]
tags: [spec, coding, claude-code, copilot, integration]
---

# SPEC: Coding Integration

## Overview

GRIM can delegate coding tasks to specialized CLI tools, making it a unified interface for research, knowledge management, AND software development. You never leave the conversation to write code.

## Two Coding Tools

### Claude Code CLI

**What**: Anthropic's agentic coding tool — full file access, multi-file edits, test running, git operations.

**When to use**: Complex coding tasks — refactoring, implementing features, debugging, code review.

**How GRIM uses it**:
```
User: "refactor the PAC validation in reality-engine to match the new spec"

GRIM:
1. Queries Kronos for current PAC spec
2. Identifies target repo (reality-engine/)
3. Invokes Claude Code with context + task
4. Claude Code reads files, makes edits, runs tests
5. GRIM summarizes changes back to user
```

**Command**: `claude --dangerously-skip-permissions -p "task description"`

### GitHub Copilot CLI

**What**: GitHub's CLI assistant — shell command suggestions, git operations, code explanations.

**When to use**: Quick operations — "how do I rebase this?", "find all files with X", "explain this error".

**How GRIM uses it**:
```
User: "what's the git history for the PAC experiments?"

GRIM:
1. Routes to copilot-cli skill
2. Invokes: gh copilot suggest "show git log for PAC experiment files"
3. Returns the suggested command + output
```

**Commands**:
- `gh copilot suggest "description"` — Get a shell command
- `gh copilot explain "command"` — Explain what a command does

## Routing Logic

```
User message → GRIM intent detection
  │
  ├─ Knowledge query? ──→ Kronos (MCP)
  ├─ Complex code task? ─→ Claude Code CLI
  ├─ Quick shell/git? ──→ Copilot CLI
  ├─ Hybrid? ───────────→ Kronos context + Claude Code
  └─ Conversation? ─────→ Direct LLM response
```

## Context Flow (The Killer Feature)

The power is in **context injection**:

1. GRIM retrieves relevant knowledge from Kronos
2. This context is passed to Claude Code as part of the task
3. Claude Code makes changes that are *architecturally aware*
4. Results flow back through GRIM, optionally updating Kronos

Example:
```
User: "add SEC phase-shifting to the reality engine field solver"

GRIM:
1. kronos-query: "SEC phase-shifting" → retrieves SEC spec, equations, constraints
2. kronos-query: "reality engine field solver" → retrieves current architecture
3. claude-code: passes both as context + the task
4. Claude Code implements with full theoretical understanding
5. GRIM optionally creates FDO linking SEC to reality-engine implementation
```

## Security Considerations

- Claude Code runs inside IronClaw's sandbox
- Filesystem permissions restrict which repos it can access
- DLP layer catches any credential exposure in output
- `--dangerously-skip-permissions` is scoped by IronClaw's own permission model
- All tool executions are audit-logged

## Requirements

- [ ] Claude Code CLI installed and accessible (`claude` command)
- [ ] GitHub CLI installed with Copilot extension (`gh copilot`)
- [ ] skill-claude-code can invoke Claude Code with a task
- [ ] skill-copilot-cli can invoke Copilot CLI
- [ ] Context from Kronos can be injected into coding tasks
- [ ] Coding output is captured and returned to user
- [ ] Sandbox permissions allow file access to target repos

## Prerequisites

```bash
# Claude Code
npm install -g @anthropic-ai/claude-code
# or already installed if you're reading this

# GitHub Copilot CLI
gh extension install github/gh-copilot
```

## Connections

- Parent: [[grim-architecture]]
- Skills system: [[grim-skills]]
- Claude Code skill: [[skill-claude-code]]
- Copilot CLI skill: [[skill-copilot-cli]]
- Knowledge context: [[kronos-vault]]

## Status

- [x] Specified
- [ ] Claude Code CLI available on system
- [ ] Copilot CLI available on system
- [ ] skill-claude-code implemented
- [ ] skill-copilot-cli implemented
- [ ] Context injection working end-to-end
