---
id: skill-kronos-query
title: "SPEC: Skill — kronos-query"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: seed
confidence: 0.6
related: [grim-skills, kronos-vault, mcp-bridge]
source_repos: [GRIM]
tags: [spec, skill, kronos, query, p0]
---

# SPEC: Skill — kronos-query

## Overview

Search and retrieve knowledge from the Kronos vault. This is the most fundamental skill — if GRIM can't read its own memory, nothing else works.

## Interface

**Input**: Natural language query or specific FDO ID
**Output**: Matching FDO content with frontmatter metadata

## Operations

1. **Search by text**: Full-text search across vault → return matching FDOs
2. **Get by ID**: Direct retrieval of a specific FDO by slug
3. **Browse by domain**: List all FDOs in a domain folder
4. **Browse by tag**: Find FDOs matching specific tags (requires parsing frontmatter)

## Implementation

Uses Obsidian REST API (via MCP or direct HTTP):
- Search: `GET /search/simple/?query=<text>`
- Read: `GET /vault/<path>`
- List: `GET /vault/<dir>/`

## Priority

**P0** — Required for GRIM to function.

## Status

- [x] Specified
- [ ] Implemented
- [ ] Tested
