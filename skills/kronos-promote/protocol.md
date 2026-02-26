# Kronos Promote — Inbox to Knowledge Graph

> **Skill**: `kronos-promote`
> **Version**: 1.0
> **Purpose**: Move short-term captures into long-term FDO memory, incrementally.
> **Deployed to**: `.github/instructions/kronos-promote.instructions.md`

---

## When This Applies

Activate this skill when:
- User asks to "organize", "promote", "process inbox", "clean up captures"
- During a reflect session that identifies promotion candidates
- Enough context has accumulated on a topic to justify an FDO
- User confirms a capture is worth preserving long-term

## Promotion Decision

Before promoting, answer three questions:

### 1. Does an FDO already exist for this concept?
- **Yes** → Enrich the existing FDO (see Incremental Growth)
- **No** → Create a new FDO in the appropriate domain

### 2. Is there enough substance?
- **Yes** → Promote
- **No** → Leave in inbox, or merge with related inbox notes first

### 3. What domain does it belong to?
- Use `target_domain` from the inbox note as a starting hint
- Override if a better domain is apparent from context

## Incremental Growth Patterns

### Append — For raw or uncertain information

Add new observations to a `## Notes` section, preserving provenance:

```markdown
## Notes

### 2026-02-25
Captured from conversation: [context].
Interesting because [reason].
```

Use append when:
- First mention of something, not yet confirmed
- Adding raw observations or quotes
- Information contradicts existing FDO content (flag it)

### Enrich — For confirmed or structured knowledge

Weave information into existing sections:
- `## Summary` — refines the core description
- `## Details` — adds depth or specifics
- `## Connections` — reveals relationships
- `## References` — adds sources

Use enrich when:
- Information is confirmed by user or evidence
- Multiple inbox notes converge on the same topic
- Adding structure or clarity to existing content

### Decision Table

| Situation | Pattern |
|-----------|---------|
| First mention, uncertain | Append to Notes |
| Confirmed by user or evidence | Enrich existing sections |
| Multiple inbox notes converge | Merge + Enrich |
| Contradicts existing content | Append as Note with ⚠️ flag |

## Creating New FDOs

Use the standard FDO template from `kronos-vault/templates/fdo-template.md`:

```yaml
---
id: kebab-case-slug
title: "Human-Readable Title"
domain: [appropriate domain]
created: YYYY-MM-DD
updated: YYYY-MM-DD
status: seed
confidence: 0.5
related: []
source_repos: []
tags: [carried from inbox note + additional]
---
```

- **Status starts at `seed`** — newly promoted FDOs haven't been validated
- **Confidence starts at 0.5** — adjust based on certainty
- **Carry tags** from the inbox note
- **File goes in** `kronos-vault/{domain}/` directory
- **Filename matches id** — `kebab-case-slug.md`

## After Promotion

1. **Delete the inbox note** — it's been absorbed
2. **Update `updated:` date** on the target FDO
3. **Run the relate skill** — check for connections to wire up
4. **Confirm to user** — brief note on what was promoted and where

## MCP Tools

- **Primary**: Use `kronos_search` to find existing FDOs, `kronos_create` for new ones, `kronos_update` for enriching
- **Fallback**: Direct file read/write in `kronos-vault/` directories

## Rules

1. **Never promote without checking for duplicates** — search first
2. **Preserve provenance** — note where the information came from
3. **Don't over-promote** — not every inbox note needs to become an FDO
4. **Increment, don't replace** — add to FDOs, don't rewrite them
5. **Maintain frontmatter integrity** — always valid YAML, all required fields

## Currency Check

After completing this skill, verify the protocol is still accurate:
- [ ] Commands in this protocol match the actual codebase
- [ ] File paths referenced still exist
- [ ] Test counts and quality gates match current reality
- [ ] If anything is stale, update this protocol before finishing
