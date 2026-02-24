---
id: skill-kronos-link
title: "SPEC: Skill — kronos-link"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: seed
confidence: 0.5
related: [grim-skills, kronos-vault, kronos-fdo-schema]
source_repos: [GRIM]
tags: [spec, skill, kronos, link, p1]
---

# SPEC: Skill — kronos-link

## Overview

Create or update bidirectional relationships between FDO nodes. When A links to B, B should also link back to A. Keeps the `related` frontmatter field and `[[wikilinks]]` in sync.

## Interface

**Input**: Two FDO IDs, relationship description (optional)
**Output**: Confirmation with updated link counts for both FDOs

## Logic

1. Read both FDOs
2. Add each ID to the other's `related` list (if not already present)
3. Add `[[wikilink]]` in the Connections section of each
4. Update `updated` date on both
5. Write both files back

## Priority

**P1** — Important for graph quality but not blocking basic operation.

## Status

- [x] Specified
- [ ] Implemented
- [ ] Tested
