---
id: kronos-fdo-schema
title: "SPEC: FDO (Field Data Object) Schema"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: stable
confidence: 0.9
related: [kronos-vault, grim-architecture]
source_repos: [GRIM]
tags: [spec, kronos, fdo, schema]
---

# SPEC: FDO (Field Data Object) Schema

## Overview

The FDO is the atomic unit of knowledge in Kronos. Every note in the vault is an FDO — a markdown file with structured YAML frontmatter and free-form body content.

## Schema v1.0

### Frontmatter (Required)

```yaml
---
id: string          # Unique slug (kebab-case, matches filename)
title: string       # Human-readable name
domain: enum        # physics | ai-systems | tools | personal
created: date       # YYYY-MM-DD, set once
updated: date       # YYYY-MM-DD, updated on every edit
status: enum        # seed | developing | stable | archived
confidence: float   # 0.0 (speculative) to 1.0 (established)
related: list       # IDs of related FDOs
source_repos: list  # Repository names this comes from
tags: list          # Free-form searchable tags
---
```

### Frontmatter (Optional Extensions)

```yaml
pac_parent: string          # Parent concept this actualizes from
pac_children: list          # Child concepts this gives rise to
confluence_pattern: dict    # Weighted parent blend (from Kronos v2)
equations: list             # Key equations (LaTeX)
falsifiable: boolean        # Whether this is testable
confidence_basis: string    # Why confidence is at this level
superseded_by: string       # FDO ID if this is archived/replaced
```

### Body Structure

```markdown
# Title

## Summary
One paragraph overview.

## Details
Extended content — theory, implementation, evidence.

## Connections
Prose description of how this relates to other concepts.
Use [[wikilinks]] for Obsidian navigation.

## Open Questions
Unresolved issues or areas for exploration.

## References
Links to papers, repos, external resources.
```

### Domain Values

| Domain | Scope |
|--------|-------|
| `physics` | Dawn Field Theory, PAC, SEC, turbulence, cosmology |
| `ai-systems` | GRIM, GAIA, Axiom, Kronos, ML architectures |
| `tools` | Fracton, CIP, infrastructure, devtools |
| `personal` | DFI organization, The Arithmetic, governance |

### Status Lifecycle

```
seed → developing → stable → archived
 │         │          │         │
 │         │          │         └─ Superseded or deprecated
 │         │          └─ Well-tested, cross-referenced
 │         └─ Actively being refined
 └─ Initial capture, rough
```

### Confidence Scale

| Range | Meaning | Example |
|-------|---------|---------|
| 0.0-0.2 | Speculative, untested | "Möbius topology might explain X" |
| 0.3-0.5 | Exploratory, some support | "PAC recursion appears in cellular automata" |
| 0.6-0.7 | Validated in limited domain | "φ clustering found in Rule 110 at p < 0.001" |
| 0.8-0.9 | Strong cross-domain evidence | "SEC predicts structure formation across 3+ domains" |
| 1.0 | Established / definitional | "PAC: f(Parent) = Σ f(Children)" |

### Naming Conventions

- **Filename**: `kebab-case.md` matching the `id` field
- **Specs**: prefix with `SPEC:` in title, add `spec` tag
- **Folders**: domain-level (`physics/`, `ai-systems/`) then project-level

## Connections

- Vault that uses this: [[kronos-vault]]
- Architecture: [[grim-architecture]]

## Status

- [x] Specified
- [x] Templates created
- [ ] Validated by creating 10+ real FDOs
- [ ] GRIM can parse and use schema
