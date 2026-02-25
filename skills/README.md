# GRIM Skills

Skills are the integration layer between GRIM's engine (IronClaw) and external tools.

Each skill is a self-contained module that the engine can discover, verify, and execute in its sandbox.

## Skill Index

| Skill | Status | Description |
|-------|--------|-------------|
| `deep-ingest/` | ✅ Built | 7-phase instruction protocol for high-quality manual FDO creation |
| `vault-sync/` | ✅ Built | 5-phase protocol for keeping FDOs current as code evolves |
| `kronos-query/` | ✅ Superseded | Now handled by `kronos-mcp` server (`kronos_search`, `kronos_get`) |
| `kronos-create/` | ✅ Superseded | Now handled by `kronos-mcp` server (`kronos_create`) |
| `kronos-link/` | ✅ Superseded | Now handled by `kronos-mcp` server (`kronos_graph`, `kronos_update`) |
| `claude-code/` | 🔄 Planned | Delegate coding tasks to Claude Code CLI |
| `copilot-cli/` | 🔄 Planned | GitHub Copilot CLI for shell/git suggestions |
| `vault-commit/` | 🔄 Planned | Auto-commit vault changes with meaningful messages |
| `repo-ingest/` | 🔄 Planned | Parse repositories into FDO knowledge nodes (automated pipeline) |
| `proton-sync/` | 🔄 Planned | Backup vault to Proton Drive via rclone |

## MCP Server

Skills are also discoverable via the **kronos-mcp** server (`mcp/kronos/`). Any AI agent with MCP access can:

1. `kronos_skills()` — list all available skills
2. `kronos_skill_load(name="deep-ingest")` — load the full instruction protocol

This means AI agents can learn how to use the knowledge base properly before starting work.
See [mcp/kronos/README.md](../mcp/kronos/README.md) for setup.

## Skill Structure

```
skill-name/
├── manifest.yaml     # Skill metadata (name, version, permissions, entry point)
├── README.md         # What it does, how it works
└── src/              # Implementation (Python, shell, or Rust)
```

## Development Mode

During development, `require_signatures: false` in `config/grim.yaml` allows unsigned skills. Enable signature verification before any production/public use.
