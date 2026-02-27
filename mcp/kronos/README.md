# Kronos MCP Server

MCP server for the Kronos knowledge vault — gives any AI agent access to FDO search, graph traversal, creation, and GRIM skill discovery.

## Why

Every AI session starts cold. Kronos MCP gives agents persistent memory:

1. **Before coding** → Search the vault for relevant context (`kronos_search`)
2. **During work** → Traverse concept relationships (`kronos_graph`)
3. **After coding** → Update or create FDOs (`kronos_create`, `kronos_update`)
4. **For quality** → Load skill protocols that tell how to do it right (`kronos_skill_load`)

The loop: AI reads vault → does better work → updates vault → next AI session starts smarter.

## Tools

### Vault Tools

| Tool | Description |
|------|-------------|
| `kronos_search` | Full-text search across all FDOs (title, tags, summary, body) |
| `kronos_get` | Read a specific FDO by ID — full content with frontmatter |
| `kronos_list` | List FDOs, optionally filtered by domain |
| `kronos_graph` | Traverse the relationship graph (related, PAC hierarchy) |
| `kronos_validate` | Run schema compliance, link integrity, orphan detection |
| `kronos_create` | Create a new FDO with schema validation |
| `kronos_update` | Update fields on an existing FDO (auto-bumps updated date) |

### Skill Tools

| Tool | Description |
|------|-------------|
| `kronos_skills` | List all GRIM skills with summaries, phases, permissions |
| `kronos_skill_load` | Load the full instruction protocol for a skill |

## Setup

### 1. Environment Variables

Create a `.env` file or set environment variables:

```env
KRONOS_VAULT_PATH=C:\Users\you\repos\core_workspace\kronos-vault
KRONOS_SKILLS_PATH=C:\Users\you\repos\core_workspace\GRIM\skills
```

### 2. Install

```bash
cd GRIM/mcp/kronos
pip install -e .
```

### 3. Configure MCP Client

#### VS Code / Copilot

Add to your MCP server settings (`.vscode/mcp.json` or global settings):

```json
{
  "servers": {
    "kronos": {
      "command": "kronos-mcp",
      "env": {
        "KRONOS_VAULT_PATH": "C:\\Users\\you\\repos\\core_workspace\\kronos-vault",
        "KRONOS_SKILLS_PATH": "C:\\Users\\you\\repos\\core_workspace\\GRIM\\skills"
      }
    }
  }
}
```

#### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kronos": {
      "command": "kronos-mcp",
      "env": {
        "KRONOS_VAULT_PATH": "/path/to/kronos-vault",
        "KRONOS_SKILLS_PATH": "/path/to/GRIM/skills"
      }
    }
  }
}
```

## Usage Examples

### Search for knowledge before starting work

```
AI: kronos_search(query="PAC conservation")
→ Returns ranked list of FDOs about PAC conservation, ordered by relevance
```

### Get full context on a concept

```
AI: kronos_get(id="structure-cost-of-erasure")
→ Returns full FDO: summary, core claim, evidence, equations, connections, open questions
```

### Understand how concepts connect

```
AI: kronos_graph(id="pac-series", depth=2)
→ Returns graph with nodes (FDOs) and edges (related, pac_parent, pac_child)
```

### Learn how to do a task properly

```
AI: kronos_skills()
→ Lists: deep-ingest (7-phase protocol), vault-sync (5-phase protocol)

AI: kronos_skill_load(name="deep-ingest")
→ Returns full protocol: phases, quality gates, checkpoints, appendices
```

### Create knowledge after completing work

```
AI: kronos_create(
    id="new-concept",
    title="New Concept Name",
    domain="physics",
    confidence=0.5,
    body="# New Concept\n\n## Summary\n...",
    related=["existing-concept"],
    tags=["specific", "searchable", "terms"]
)
```

### Validate vault integrity

```
AI: kronos_validate()
→ Returns: total FDOs, domain counts, schema issues, broken links, orphans
```

## Architecture

```
kronos-mcp/
├── pyproject.toml              # Package config, entry point
├── README.md                   # This file
├── .env.example                # Environment template
└── src/kronos_mcp/
    ├── __init__.py             # Entry point
    ├── server.py               # MCP server — tool definitions + handlers
    ├── vault.py                # VaultEngine — filesystem FDO reader/writer
    └── skills.py               # SkillsEngine — skill protocol discovery
```

### No Obsidian Required

Kronos MCP works directly on the filesystem. It reads/writes markdown files with YAML frontmatter. Obsidian is a great viewer but not a dependency — the vault is just a folder of markdown files.

### FDO Schema

See `kronos-vault/ai-systems/kronos/kronos-fdo-schema.md` for the full schema, or use `kronos_get(id="kronos-fdo-schema")` from within an MCP session.

## Design Decisions

- **Filesystem-native**: No database, no server process for the vault. Just files.
- **Refresh on every call**: Re-indexes the vault on each tool call. Fast enough for hundreds of FDOs, avoids stale cache.
- **Skills are first-class**: Skill discovery is built into the same server, not a separate tool.
- **Write tools included**: AI agents can create/update FDOs directly, enabling the read→work→write loop.
- **Validation built-in**: Agents can self-check their work after creating FDOs.
