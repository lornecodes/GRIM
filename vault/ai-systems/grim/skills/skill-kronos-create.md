---
id: skill-kronos-create
title: "SPEC: Skill — kronos-create"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: seed
confidence: 0.6
related: [grim-skills, kronos-vault, kronos-fdo-schema, mcp-bridge]
source_repos: [GRIM]
tags: [spec, skill, kronos, create, p0]
---

# SPEC: Skill — kronos-create

## Overview

Create a new FDO note in the Kronos vault from a concept description. Handles ID generation, frontmatter schema, domain classification, auto-dating, and initial linking to related nodes.

## Interface

**Input**: Concept name, description, domain (optional), related concepts (optional)
**Output**: Path to created FDO file

## Logic

1. Generate kebab-case ID from concept name
2. Classify domain if not provided (physics/ai-systems/tools/personal)
3. Build frontmatter with all required fields
4. Search vault for related concepts → populate `related` list
5. Write file via Obsidian REST API: `PUT /vault/<domain>/<id>.md`
6. Return confirmation with path and initial connections

## Template

Uses FDO schema from [[kronos-fdo-schema]], setting:
- `status: seed`
- `confidence: 0.5` (default for new concepts)
- `created` and `updated` to current date

## Priority

**P0** — GRIM needs to be able to learn from conversations.

## Status

- [x] Specified
- [ ] Implemented
- [ ] Tested
