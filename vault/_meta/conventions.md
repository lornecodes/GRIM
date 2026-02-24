---
id: meta-conventions
title: Kronos Vault Conventions
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: stable
confidence: 1.0
related: []
source_repos: [GRIM]
tags: [kronos, conventions, meta]
---

# Kronos Vault Conventions

## FDO (Field Data Object) Notes

Every knowledge node in this vault is an FDO — a structured markdown note with YAML frontmatter.

### Required Frontmatter

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier (slug or UUID) |
| `title` | string | Human-readable concept name |
| `domain` | enum | physics, ai-systems, tools, personal |
| `created` | date | When first created |
| `updated` | date | Last modification |
| `status` | enum | seed → developing → stable → archived |
| `confidence` | float | 0.0 (speculative) to 1.0 (established) |
| `related` | list | Links to other FDO IDs |
| `source_repos` | list | Which repos this knowledge comes from |
| `tags` | list | Free-form tags for search |

### Status Lifecycle

- **seed**: Initial capture — rough, may be incomplete
- **developing**: Being actively refined, connections forming
- **stable**: Well-understood, cross-referenced, reliable
- **archived**: Superseded or no longer relevant

### Linking

Use Obsidian `[[wikilinks]]` in the body for human navigation.
Use the `related` frontmatter field for machine-readable links.
Both should stay in sync.

### Naming

Files: `kebab-case.md` matching the `id` field.
Folders: domain-level organization (physics/, ai-systems/, etc.)
