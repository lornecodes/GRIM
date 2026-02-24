---
id: skill-proton-sync
title: "SPEC: Skill — proton-sync"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: seed
confidence: 0.5
related: [grim-skills, kronos-vault]
source_repos: [GRIM]
tags: [spec, skill, backup, proton-drive, p2]
---

# SPEC: Skill — proton-sync

## Overview

Backup the Kronos vault (and optionally GRIM state) to Proton Drive via rclone. If hardware dies, restore from Proton Drive and GRIM picks up where it left off.

## Interface

**Input**: Optional scope (vault-only, full state)
**Output**: Sync summary (files transferred, bytes, duration)

## Implementation

```bash
rclone sync ./vault proton:grim-vault --progress
rclone sync ~/.grim proton:grim-state --progress
```

## Prerequisites

```bash
# Install rclone
# Configure Proton Drive remote
rclone config
# Select: Proton Drive
# Follow auth flow
```

## Trigger Modes

- **Manual**: "sync to proton"
- **Scheduled**: Every 6 hours via IronClaw workflow
- **On commit**: After vault-commit skill runs

## Priority

**P2** — Git push is a sufficient backup for now. Proton Drive adds hardware resilience.

## Status

- [x] Specified
- [ ] rclone configured
- [ ] Proton Drive remote set up
- [ ] Sync tested
