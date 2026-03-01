# Staging Organize Protocol

> Operator agent protocol for placing accepted files into the workspace.

## When This Applies

After the audit agent passes staged files and the integrate node accepts them. Files need to move from the staging area to their correct project locations.

## Process

1. **Identify artifacts** — list accepted files from the staging job
2. **Determine destinations** — use project structure knowledge to find correct locations
3. **Check for conflicts** — verify destination doesn't already have a conflicting file
4. **Move files** — place each file in its destination
5. **Clean up** — remove empty staging directories

## Placement Rules

| File Type | Destination Pattern |
|-----------|-------------------|
| Python source | `{repo}/src/` or `{repo}/{package}/` |
| Python tests | `{repo}/tests/` |
| Config (YAML/TOML/JSON) | `{repo}/config/` or `{repo}/` root |
| Shell scripts | `{repo}/scripts/` |
| Documentation | `{repo}/docs/` or alongside source |
| Data files | `{repo}/data/` |

## Conflict Resolution

- If a file already exists at the destination, DO NOT overwrite
- Report the conflict and ask the user for guidance
- Never silently replace existing files

## Vault Sync

After organizing files, run the `vault-sync` skill if the changes affect:
- Project structure (new directories, moved files)
- Source paths referenced in FDOs
- Experiment structure
