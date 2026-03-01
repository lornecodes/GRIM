# Capability Routing — How GRIM Routes Requests to Agents

> **Skill**: `capability-routing`
> **Version**: 1.0
> **Purpose**: Reference for understanding, debugging, and extending GRIM's request routing system.

---

## Architecture Overview

GRIM uses a **thinker/doer split**:

- **Companion** (thinker): Conversational responses, vault search, knowledge queries. Has only read-only Kronos tools.
- **Doer agents** (delegate): Action execution — code, shell, vault writes, research, sandboxed execution.

The **Router** decides every turn: companion or delegate? If delegate, which agent?

```
User message → skill_match → router → companion (think)
                                    → dispatch → agent (do) → integrate
```

---

## Capability Map

| Delegation Type | Agent | Tools | Use Cases |
|----------------|-------|-------|-----------|
| `memory` | Memory Agent | Kronos write tools | Create/update/link FDOs, vault operations |
| `code` | Coder Agent | File read/write, shell, Kronos read | Write code, refactor, debug, run tests |
| `research` | Research Agent | File read, Kronos read/write | Ingest papers, deep analysis, summarize |
| `operate` | Operator Agent | Git, shell, file, Kronos read | Shell commands, git, infrastructure, HTTP |
| `ironclaw` | IronClaw Agent | IronClaw bridge, Kronos read | Sandboxed execution, security-sensitive ops |
| `audit` | Audit Agent | Staging read, Kronos read | Review staged files, security audit |

---

## Routing Priority

The router evaluates in this order (first match wins):

1. **Skill consumer match** — If a matched skill has a known delegation target via `_skill_ctx_to_delegation()`
2. **Capability continuity** — If `last_delegation_type` is set and the message looks like a follow-up
3. **Keyword match** — Substring matching against `DELEGATION_KEYWORDS` dict
4. **Action-intent fallback** — Verb + target pattern matching for broader coverage
5. **Default** — Companion mode (no delegation)

---

## Routing Decision Points

### Skill-to-Delegation Mapping (`_skill_ctx_to_delegation`)

Located in `core/nodes/router.py`. Maps skill names to delegation types:

```
kronos-*          → memory
code-execution    → code
file-operations   → code
deep-ingest       → research
vault-sync        → operate
git-operations    → operate
shell-execution   → operate
sandboxed-*       → ironclaw
ironclaw-review   → audit
staging-*         → operate
```

Falls back to permission hints (`vault:write` → memory, `filesystem:write` → code, `shell:execute` → operate).

### Keyword Patterns (`DELEGATION_KEYWORDS`)

Each delegation type has a list of substring keywords. These are checked against `message.lower()`.

Key categories for `operate`:
- Shell: "run command", "bash", "terminal", "shell"
- Git: "git status", "commit", "push to github"
- Files: "list files", "show me the file"
- Network: "ip address", "ping", "hostname", "whoami"
- System: "what os", "system info", "disk space"

### Action-Intent Fallback

If no keyword matches, checks for verb + target combinations:
- Verbs: "run", "execute", "check", "test", "ping", "show me", "get me"
- Targets: "command", "shell", "ip", "system", "server", "network", "file"

If both a verb and target are found → delegates to `operate`.

### Capability Continuity

If `last_delegation_type` is set (from a previous successful delegation) and the message contains follow-up signals ("now", "also", "again", "try", "what about"), re-delegates to the same agent type.

---

## Common Routing Failures

### Problem: Companion denies capabilities it has

**Cause**: Message didn't match any keyword or skill, so router defaulted to companion. Companion only has Kronos read tools and can't execute the request.

**Fix**: Add keywords to `DELEGATION_KEYWORDS["operate"]` or another appropriate delegation type.

### Problem: Request routes to wrong agent

**Cause**: A keyword for one agent matched before the intended agent's keywords.

**Fix**: Keywords are checked in dict insertion order. Move specific keywords higher or make them more precise.

### Problem: Follow-up requests lose context

**Cause**: `last_delegation_type` wasn't set, or the follow-up signal wasn't recognized.

**Fix**: Add the missing follow-up signal to `_FOLLOW_UP_SIGNALS` in the router.

### Problem: New skill doesn't route correctly

**Cause**: Skill name not in `_skill_ctx_to_delegation()` mapping.

**Fix**: Add the skill name → delegation type mapping to `_skill_ctx_to_delegation()`.

---

## Adding a New Capability — Checklist

When adding a new capability to GRIM:

1. **System prompt** (`identity/system_prompt.md`)
   - [ ] Add the capability to the "What I Can Do" section
   - [ ] Keep it in first person, no architecture exposure

2. **Router keywords** (`core/nodes/router.py`)
   - [ ] Add keywords to appropriate delegation type in `DELEGATION_KEYWORDS`
   - [ ] Test that keywords don't conflict with other delegation types

3. **Skill mapping** (`core/nodes/router.py`)
   - [ ] If there's a skill for this capability, add it to `_skill_ctx_to_delegation()`
   - [ ] Match the skill name pattern or add an explicit entry

4. **Agent tools** (`core/agents/`)
   - [ ] Ensure the target agent has the tools needed for the capability
   - [ ] If new tools needed, add them to the agent's tool list

5. **Prompt builder** (`core/personality/prompt_builder.py`)
   - [ ] No changes usually needed — matched skills are auto-injected
   - [ ] If the capability needs special prompt hints, add to dynamic sections

---

## Key Files

| File | Role |
|------|------|
| `identity/system_prompt.md` | Capability claims (what GRIM tells users it can do) |
| `core/nodes/router.py` | Routing logic: skills → continuity → keywords → intent → companion |
| `core/nodes/dispatch.py` | Dispatches to the chosen agent |
| `core/nodes/integrate.py` | Formats agent results, persists `last_delegation_type` |
| `core/state.py` | `GrimState` TypedDict with routing fields |
| `core/personality/prompt_builder.py` | Assembles system prompt with matched skills |
| `core/agents/base.py` | Base agent with skill protocol injection |
| `core/agents/*.py` | Individual agent implementations with tool bindings |

---

## Rules

1. **Never claim capabilities the system can't deliver** — system prompt must match actual agent tools
2. **Never deny capabilities the system has** — if an agent can do it, the system prompt should say so
3. **Keywords should be specific enough to avoid false positives** — "run" alone is too broad, "run command" is better
4. **Continuity over re-evaluation** — if the last turn succeeded with an agent, follow-ups should use the same agent
5. **Skill consumers are the source of truth** — prefer skill-based routing over keyword matching
