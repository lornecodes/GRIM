---
id: kronos-vault
title: "SPEC: Kronos Vault (Obsidian)"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: developing
confidence: 0.8
related: [grim-architecture, kronos-fdo-schema, mcp-bridge, kronos-history]
source_repos: [GRIM]
tags: [spec, kronos, vault, obsidian, knowledge-graph, memory]
---

# SPEC: Kronos Vault

## Overview

Kronos is GRIM's persistent knowledge graph, implemented as an Obsidian vault of interconnected markdown notes. Each note is a Field Data Object (FDO) with structured YAML frontmatter and free-form content. The vault is the single source of truth for everything GRIM knows.

## Why Obsidian

| Need | Obsidian Provides |
|------|-------------------|
| Human browsable | Visual graph view, click-through links |
| Machine accessible | Local REST API plugin → MCP bridge |
| Git friendly | Plain markdown files, YAML frontmatter |
| Portable | Just a folder of .md files |
| Zero infrastructure | No database, no server, no Docker |
| Plugin ecosystem | Templates, Dataview, Graph Analysis |

## Lineage

Kronos descends from three previous implementations:

1. **Kronos v1** (grimm/) — Neo4j + Qdrant, GPU-accelerated fractal signatures. Powerful but heavy infrastructure.
2. **Kronos v2** (fracton/) — Conceptual genealogy engine with PAC conservation, geometric confidence. Elegant but only Phase 1 complete.
3. **Kronos CIP** (cip-core/) — SQLite + ChromaDB for repo understanding. Functional but narrow scope.

This version keeps the best ideas (FDO schema, confidence tracking, relationship typing, epistemic awareness) and drops the infrastructure burden.

## Vault Structure

```
vault/
├── _meta/                    # Vault conventions, map
│   └── conventions.md
├── ai-systems/               # AI/ML concepts and specs
│   ├── grim/                 # GRIM project specs (this is here!)
│   │   └── skills/           # Individual skill specs
│   ├── kronos/               # Kronos-specific concepts
│   ├── gaia/                 # GAIA ML architecture
│   └── axiom/                # Axiom framework concepts
├── physics/                  # Dawn Field Theory
│   ├── dawn-field-theory/    # Core theory concepts
│   └── pac-framework/        # PAC/SEC/RBF/MED
├── tools/                    # Infrastructure and tools
│   ├── fracton/              # Knowledge graph tools
│   └── cip/                  # Cognition Index Protocol
├── personal/                 # DFI, governance, personal
│   └── dawn-field-institute/
└── templates/                # FDO and spec templates
```

## FDO Schema

See [[kronos-fdo-schema]] for full specification.

Core frontmatter fields:
```yaml
id: unique-slug
title: Human Name
domain: physics|ai-systems|tools|personal
created: YYYY-MM-DD
updated: YYYY-MM-DD
status: seed|developing|stable|archived
confidence: 0.0-1.0
related: [other-fdo-ids]
source_repos: [repo-names]
tags: [searchable, tags]
```

## Access Patterns

### Human (Obsidian UI)
- Browse graph visually
- Click through wikilinks
- Use Dataview for queries
- Edit notes directly

### GRIM (MCP Bridge)
- `list_files_in_dir` → discover notes by domain
- `get_file_contents` → read a specific FDO
- `search` → find notes matching a query
- `patch_content` → update a section
- `append_content` → add to or create notes
- `delete_file` → remove obsolete notes

### Programmatic (Git)
- `git log vault/` → change history
- `git diff` → what changed since last commit
- Full-text search with grep/ripgrep

## Requirements

- [x] Vault directory structure created
- [x] FDO template defined
- [x] Spec template defined
- [x] Conventions documented
- [ ] Obsidian opens vault successfully
- [ ] Local REST API plugin installed and configured
- [ ] MCP bridge connects and can read notes
- [ ] GRIM can search and retrieve from vault
- [ ] GRIM can create new FDO notes
- [ ] Auto-commit on vault changes

## Recommended Obsidian Plugins

| Plugin | Purpose |
|--------|---------|
| **Local REST API** | Required — MCP bridge endpoint |
| **Templater** | FDO/spec note creation from templates |
| **Dataview** | Query frontmatter (status, confidence, domain) |
| **Graph Analysis** | Explore connection patterns |
| **Git** | Auto-commit from Obsidian UI |

## Connections

- Parent: [[grim-architecture]]
- Schema: [[kronos-fdo-schema]]
- MCP access: [[mcp-bridge]]
- Vault history: [[kronos-history]]
- Seeding data: fracton/data/dft_knowledge_graph.json (115 DFT concepts)

## Status

- [x] Specified
- [x] Structure created
- [x] Templates written
- [ ] Obsidian configured
- [ ] REST API plugin active
- [ ] MCP bridge verified
- [ ] Seed data imported
