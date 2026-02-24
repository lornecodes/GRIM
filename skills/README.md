# GRIM Skills

Skills are the integration layer between GRIM's engine (IronClaw) and external tools.

Each skill is a self-contained module that the engine can discover, verify, and execute in its sandbox.

## Skill Index

| Skill | Status | Description |
|-------|--------|-------------|
| `kronos-query/` | 🔄 Planned | Search and retrieve from Obsidian vault via MCP |
| `kronos-create/` | 🔄 Planned | Create FDO notes with proper schema |
| `kronos-link/` | 🔄 Planned | Manage bidirectional relationships between FDOs |
| `claude-code/` | 🔄 Planned | Delegate coding tasks to Claude Code CLI |
| `copilot-cli/` | 🔄 Planned | GitHub Copilot CLI for shell/git suggestions |
| `vault-commit/` | 🔄 Planned | Auto-commit vault changes with meaningful messages |
| `repo-ingest/` | 🔄 Planned | Parse repositories into FDO knowledge nodes |
| `proton-sync/` | 🔄 Planned | Backup vault to Proton Drive via rclone |

## Skill Structure

```
skill-name/
├── manifest.yaml     # Skill metadata (name, version, permissions, entry point)
├── README.md         # What it does, how it works
└── src/              # Implementation (Python, shell, or Rust)
```

## Development Mode

During development, `require_signatures: false` in `config/grim.yaml` allows unsigned skills. Enable signature verification before any production/public use.
