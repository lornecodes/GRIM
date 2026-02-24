---
id: skill-repo-ingest
title: "SPEC: Skill — repo-ingest"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: seed
confidence: 0.4
related: [grim-skills, kronos-vault, kronos-fdo-schema]
source_repos: [GRIM]
tags: [spec, skill, ingestion, repos, p2]
---

# SPEC: Skill — repo-ingest

## Overview

Parse a local repository and generate FDO notes for its major concepts, architecture, and components. Used for initial seeding and onboarding new projects into Kronos.

## Interface

**Input**: Repo path, domain classification
**Output**: Count of FDOs created, list of paths

## Logic

1. Read README, docs, specs, meta.yaml
2. Extract key concepts, components, relationships
3. Generate FDO notes for each major concept
4. Link related nodes
5. Code files become reference pointers (not embedded code)

## Seed Data Available

The fracton repo already has a 115-node DFT knowledge graph at `fracton/data/dft_knowledge_graph.json`. This can be converted directly to FDOs without needing the full ingestion pipeline.

## Priority

**P2** — Manual vault building works now. Automated ingestion is a luxury.

## Status

- [x] Specified
- [ ] DFT graph conversion script written
- [ ] Full repo ingestion implemented
