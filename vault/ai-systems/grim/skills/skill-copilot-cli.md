---
id: skill-copilot-cli
title: "SPEC: Skill — copilot-cli"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: seed
confidence: 0.5
related: [grim-skills, coding-integration]
source_repos: [GRIM]
tags: [spec, skill, copilot, github, p1]
---

# SPEC: Skill — copilot-cli

## Overview

Use GitHub Copilot CLI for quick shell command suggestions, git operations, and code explanations. Lighter-weight than Claude Code — good for one-off commands rather than multi-file tasks.

## Interface

**Input**: Natural language description of what the user wants to do
**Output**: Suggested command and/or explanation

## Commands

```bash
# Suggest a shell command
gh copilot suggest "find all Python files modified in the last week"

# Explain a command
gh copilot explain "git rebase -i HEAD~5"
```

## Use Cases

- "What's the git history for the PAC experiments?"
- "How do I squash the last 3 commits?"
- "Find all files importing KronosGraph"
- "Explain this docker-compose config"

## Prerequisites

```bash
gh extension install github/gh-copilot
# Verify
gh copilot --version
```

## Priority

**P1** — Nice to have alongside Claude Code, not blocking.

## Status

- [x] Specified
- [ ] GH Copilot CLI installed
- [ ] Basic invocation working
