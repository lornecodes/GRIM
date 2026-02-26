# GRIM Skills

Skills are GRIM's behavioral firmware — the shared language between the thinker (GRIM) and the doers (agents).

**Every skill has consumers.** GRIM reads skills for *recognition* (when should this activate?). Agents read skills for *execution* (how do I do this?). Same protocol, two readers.

## Two-Consumer Model

```
GRIM (thinker)                    Agent (doer)
────────────────                  ────────────────
Reads: triggers, quality_gates    Reads: protocol.md, inputs, outputs
Purpose: RECOGNIZE when to act    Purpose: EXECUTE the work
Output: Route to correct agent    Output: Completed task + report
```

Skills declare their consumers in `manifest.yaml`:

```yaml
consumers:
  grim:
    role: recognition           # or delegation
    description: What GRIM uses this skill for
    reads: [triggers, quality_gates]
  memory-agent:
    role: execution
    description: What the agent uses this skill for
    reads: [protocol.md, inputs, outputs, quality_gates]
```

## Skill Index

### Shared Skills (GRIM recognizes → Agent executes)

| Skill | Status | GRIM Role | Agent | Description |
|-------|--------|-----------|-------|-------------|
| `kronos-capture/` | ✅ Built | recognition | memory-agent | Quick capture to `_inbox/` DMZ |
| `kronos-promote/` | ✅ Built | recognition | memory-agent | Promote inbox items to FDOs |
| `kronos-relate/` | ✅ Built | recognition | memory-agent | Wire bidirectional FDO connections |
| `kronos-recall/` | ✅ Built | recognition | memory-agent | Hybrid search + retrieval |
| `kronos-reflect/` | ✅ Built | recognition | memory-agent | Inbox triage, vault health |
| `deep-ingest/` | ✅ Built | delegation | research-agent | 7-phase deep FDO creation |
| `vault-sync/` | ✅ Built | delegation | ops-agent | 5-phase vault sync after code changes |

### Agent-Only Skills (agents execute, GRIM doesn't read)

| Skill | Status | Agent(s) | Description |
|-------|--------|----------|-------------|
| `code-execution/` | ✅ Built | coder-agent | Write, run, test code safely |
| `file-operations/` | ✅ Built | coder-agent, ops-agent, memory-agent | Safe file system interaction |
| `git-operations/` | ✅ Built | ops-agent | Commit, branch, push, PR |
| `shell-execution/` | ✅ Built | coder-agent, ops-agent | Run shell commands safely |

### Superseded / Planned

| Skill | Status | Notes |
|-------|--------|-------|
| `kronos-query/` | ✅ Superseded | Now handled by kronos-mcp (`kronos_search`, `kronos_get`) |
| `kronos-create/` | ✅ Superseded | Now handled by kronos-mcp (`kronos_create`) |
| `kronos-link/` | ✅ Superseded | Now handled by kronos-mcp (`kronos_graph`, `kronos_update`) |
| `vault-commit/` | 🔄 Planned | Auto-commit vault changes with meaningful messages |
| `repo-ingest/` | 🔄 Planned | Automated FDO creation from repositories |
| `proton-sync/` | 🔄 Planned | Backup vault to Proton Drive via rclone |

## Skill Loading

### At Boot
GRIM loads all skill manifests at startup. This populates the skill registry used by the `skill_match` node in the LangGraph graph.

### Per Turn
The `skill_match` node checks every user message against skill triggers. Matched skills get attached to the turn context and passed to agents along with the task.

### MCP Discovery
Skills are also discoverable via the **kronos-mcp** server:
1. `kronos_skills()` — list all available skills
2. `kronos_skill_load(name="deep-ingest")` — load the full protocol

### Claude/Copilot Deployment
Kronos skills are also deployed as `.github/instructions/kronos-*.instructions.md` for auto-loading in AI coding sessions.

**This directory is the canonical source.** Update `protocol.md` here first, then sync deployed copies.

## Skill Structure

```
skill-name/
├── manifest.yaml     # Metadata: name, version, permissions, consumers, triggers
├── protocol.md       # Canonical instruction protocol (source of truth)
└── src/              # Implementation (if applicable — Python, shell, or Rust)
```

## Agent Types

| Agent | Responsibility | Skills |
|-------|---------------|--------|
| **memory-agent** | All Kronos vault operations | kronos-capture, kronos-promote, kronos-relate, kronos-recall, kronos-reflect, file-operations |
| **coder-agent** | Code creation and modification | code-execution, file-operations, shell-execution |
| **ops-agent** | Infrastructure, git, deployment | git-operations, file-operations, shell-execution, vault-sync |
| **research-agent** | Deep analysis and ingestion | deep-ingest, kronos-recall |

## Development Mode

During development, `require_signatures: false` in `config/grim.yaml` allows unsigned skills. Enable signature verification before any production/public use.
