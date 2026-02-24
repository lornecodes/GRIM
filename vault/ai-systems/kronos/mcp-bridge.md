---
id: mcp-bridge
title: "SPEC: Obsidian MCP Bridge"
domain: ai-systems
created: 2026-02-24
updated: 2026-02-24
status: developing
confidence: 0.8
related: [kronos-vault, engine-ironclaw, grim-architecture]
source_repos: [GRIM, MarkusPfundstein/mcp-obsidian]
tags: [spec, mcp, obsidian, bridge, integration]
---

# SPEC: Obsidian MCP Bridge

## Overview

The MCP bridge allows GRIM (via IronClaw) to read, write, search, and manage the Kronos vault programmatically through the Model Context Protocol. It uses `mcp-obsidian` (2.9k stars, Python, MIT) which talks to Obsidian's Local REST API plugin.

## Architecture

```
GRIM (IronClaw engine)
  → MCP client request
    → mcp-obsidian server (Python, stdio)
      → HTTP to Obsidian Local REST API (port 27124)
        → Obsidian vault filesystem
```

## Available MCP Tools

From `mcp-obsidian`:

| Tool | Description | Use Case |
|------|-------------|----------|
| `list_files_in_vault` | List all files/dirs in vault root | Discovery |
| `list_files_in_dir` | List files in a specific directory | Browse by domain |
| `get_file_contents` | Read a single file | Retrieve FDO content |
| `search` | Full-text search across vault | Find relevant concepts |
| `patch_content` | Insert content relative to heading/block | Update FDO sections |
| `append_content` | Append to existing or create new file | Create new FDOs |
| `delete_file` | Remove a file or directory | Clean up |

## Prerequisites

1. **Obsidian** installed and running
2. **Local REST API** community plugin installed + enabled
3. API key copied from plugin settings
4. `OBSIDIAN_API_KEY` set in environment

## Setup

```bash
# Install mcp-obsidian
pip install mcp-obsidian
# or
uvx mcp-obsidian

# Test connection
curl -H "Authorization: Bearer $OBSIDIAN_API_KEY" http://127.0.0.1:27124/
```

## Configuration

In `config/grim.yaml`:
```yaml
mcp:
  obsidian:
    command: "uvx"
    args: ["mcp-obsidian"]
    env:
      OBSIDIAN_API_KEY: "${OBSIDIAN_API_KEY}"
      OBSIDIAN_HOST: "127.0.0.1"
      OBSIDIAN_PORT: "27124"
```

## Requirements

- [ ] Obsidian opens vault/ directory
- [ ] Local REST API plugin installed
- [ ] MCP server starts without errors
- [ ] Can list vault files via MCP
- [ ] Can read an FDO via MCP
- [ ] Can search vault via MCP
- [ ] Can create a new FDO via MCP
- [ ] Can update an existing FDO via MCP
- [ ] IronClaw routes vault queries through MCP

## Fallback: Direct REST API

If IronClaw doesn't support MCP client natively, GRIM skills can call the Obsidian REST API directly:

```bash
# List files
curl http://127.0.0.1:27124/vault/ -H "Authorization: Bearer $KEY"

# Read a file
curl http://127.0.0.1:27124/vault/ai-systems/grim/grim-architecture.md -H "Authorization: Bearer $KEY"

# Search
curl "http://127.0.0.1:27124/search/simple/?query=PAC" -H "Authorization: Bearer $KEY"
```

This is a viable Plan B — skills written in Python/shell can use `requests` or `curl` directly.

## Connections

- Uses: [[kronos-vault]]
- Plugs into: [[engine-ironclaw]]
- Source: `mcp/obsidian/` in GRIM repo

## Status

- [x] Specified
- [x] mcp-obsidian cloned into mcp/obsidian/
- [ ] Obsidian opened with vault
- [ ] REST API plugin running
- [ ] MCP bridge tested
- [ ] Integrated with IronClaw
