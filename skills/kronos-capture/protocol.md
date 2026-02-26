# Kronos Capture — Short-Term Memory

> **Skill**: `kronos-capture`
> **Version**: 1.0
> **Purpose**: Quick capture of vault-worthy information to the inbox DMZ.
> **Deployed to**: `.github/instructions/kronos-capture.instructions.md`

---

## When This Applies

Activate this skill when:
- A person, concept, idea, or reference surfaces worth remembering
- User explicitly says "remember this", "save this", "capture this"
- A new person, book, tool, or topic appears that isn't in the vault
- An insight, connection, or observation emerges during work

## Capture Mode

**Default: Proactive** — Notice vault-worthy information and suggest capturing it.

**Suppress when user signals:**
- "don't save this", "off the record", "no capture"
- Re-enable on explicit request or new session

## How to Capture

1. Create a note in `kronos-vault/_inbox/`
2. Use minimal frontmatter (see format below)
3. Include enough context that future-you can promote it
4. One note per concept — don't bundle unrelated things

### Inbox Note Format

```yaml
---
title: "Descriptive title"
captured: YYYY-MM-DD
tags: [relevant, tags]
source: conversation | reference | user
target_domain: people | interests | notes | media | journal | physics | ai-systems | computing | modelling | projects
---

Freeform content. Keep it natural.
Include context: why this matters, where it came from, what triggered it.
```

### Filename Convention

`YYYYMMDD_HHMMSS_brief_slug.md`

Examples:
- `20260225_143000_john_doe_ml_researcher.md`
- `20260225_150000_interesting_topology_paper.md`
- `20260225_160000_pottery_hobby_idea.md`

## Target Domain Guide

| Domain | Capture When |
|--------|-------------|
| `people` | Someone is mentioned with context worth preserving |
| `interests` | A hobby, curiosity, or non-research topic comes up |
| `notes` | General knowledge, observations, useful reference material |
| `media` | Books, papers, talks, podcasts, videos worth tracking |
| `journal` | Personal reflections, session notes, daily observations |
| `physics` | DFT-related concepts, equations, physical phenomena |
| `ai-systems` | GRIM, GAIA, Kronos, AI tools, architecture topics |
| `computing` | Fracton, implementations, operators, runtime topics |
| `modelling` | ML experiments, GAIA POCs, validation work |
| `projects` | Don't capture — use project template directly |

## Method

Create files directly in `kronos-vault/_inbox/` using file operations.
Inbox notes are **NOT FDOs** and are **not indexed** by Kronos MCP search.

## Rules

1. **Always use inbox** — never create full FDOs during capture
2. **Context over completeness** — a note with "why" beats a note with every detail
3. **Tag generously** — tags power recall and promotion routing
4. **Set target_domain** — best guess where this becomes an FDO
5. **Low friction** — capture should take seconds, not minutes
6. **Don't duplicate** — check if concept already exists as FDO (`kronos_search`) or in inbox before creating

## Currency Check

After completing this skill, verify the protocol is still accurate:
- [ ] Commands in this protocol match the actual codebase
- [ ] File paths referenced still exist
- [ ] Test counts and quality gates match current reality
- [ ] If anything is stale, update this protocol before finishing
