# Kronos Reflect — Vault Maintenance & Review

> **Skill**: `kronos-reflect`
> **Version**: 1.0
> **Purpose**: Periodic review, triage, and health monitoring of the knowledge graph.
> **Deployed to**: `.github/instructions/kronos-reflect.instructions.md`

---

## When This Applies

Activate this skill when:
- User asks to "review vault", "triage inbox", "check vault health"
- At natural breakpoints in a work session
- Before starting a new topic area
- User asks about vault statistics or growth

## Inbox Triage

### Process

1. **List all `_inbox/` notes** with dates and titles
2. **Group by target_domain** or topic similarity
3. **For each note, recommend one of:**
   - **Promote** — ready to become an FDO (hand off to promote skill)
   - **Merge** — combine with other inbox notes or existing FDO
   - **Keep** — not ready yet, needs more context or enrichment
   - **Discard** — no longer relevant, duplicate, or stale

### Presenting Triage

```
📥 Inbox: [N] items

Promote:
  - [title] → [target domain] (captured [date])

Merge:
  - [title] + [title] → [existing FDO or new concept]

Keep:
  - [title] — needs more context

Discard:
  - [title] — [reason]
```

## Vault Health Checks

### Orphan Detection
- FDOs with empty `related:` lists (no connections)
- Suggest possible links based on content similarity

### Stale Content
- FDOs with `status: seed` or `developing` and `updated:` > 30 days old
- Suggest: review and either develop further or archive

### Domain Distribution
- Count FDOs per domain
- Flag empty domains (newly created, need content)
- Flag imbalanced domains (too many seeds, too few stable)

### Broken Links
- `related:` entries pointing to non-existent FDO IDs
- `[[wikilinks]]` to non-existent files

## Growth Metrics

When asked for stats, present:

```
📊 Vault Health Report

Total FDOs: [N]
Inbox items: [N]

Domains:
  physics: [N]  |  ai-systems: [N]  |  computing: [N]
  modelling: [N]  |  projects: [N]  |  people: [N]
  interests: [N]  |  notes: [N]  |  media: [N]  |  journal: [N]

Status distribution:
  seed: [N]  |  developing: [N]  |  stable: [N]  |  archived: [N]

Orphaned FDOs: [N]
Recent activity: [N FDOs updated in last 7 days]
```

## MCP Tools

- **Primary**: `kronos_search` for broad queries, `kronos_list` for domain enumeration
- **Fallback**: Direct file listing and reading from `kronos-vault/`

## Rules

1. **Don't auto-fix** — present findings and recommendations, let user decide
2. **Prioritize inbox triage** — growing inbox means knowledge is leaking
3. **Be honest about gaps** — flag empty or weak domains
4. **Track trends** — compare current state to previous counts when available
5. **Suggest, don't demand** — reflection is advisory, user drives decisions

## Currency Check

After completing this skill, verify the protocol is still accurate:
- [ ] Commands in this protocol match the actual codebase
- [ ] File paths referenced still exist
- [ ] Test counts and quality gates match current reality
- [ ] If anything is stale, update this protocol before finishing
