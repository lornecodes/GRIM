# Kronos Vault

This is the Obsidian vault directory. Open this folder in Obsidian for visual graph navigation.

## Setup

1. Open Obsidian
2. "Open folder as vault" → select this `vault/` directory
3. Install the **Local REST API** community plugin
4. Enable it and copy the API key
5. Set `OBSIDIAN_API_KEY` in your environment or `.env`

## Structure

```
vault/
├── _meta/              # Vault-level metadata and conventions
├── physics/            # Dawn Field Theory, PAC, SEC, turbulence
├── ai-systems/         # GRIM, GAIA, Axiom, Kronos
├── tools/              # Fracton, CIP, infrastructure
├── personal/           # DFI, The Arithmetic, governance
└── templates/          # FDO note templates for Obsidian
```

## FDO Schema

Every note uses this frontmatter:

```yaml
---
id: <uuid>
title: <concept name>
domain: <physics|ai-systems|tools|personal>
created: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
status: <seed|developing|stable|archived>
confidence: <0.0-1.0>
related: []
source_repos: []
tags: []
---
```

## How GRIM Uses This

GRIM accesses this vault through the Obsidian MCP bridge (`mcp/obsidian/`). It can:
- Search for concepts by name or tag
- Read note contents for context
- Create new FDO notes from conversations
- Update relationships between concepts
- Track confidence levels as research evolves
