# Kronos Relate — Wire the Knowledge Graph

> **Skill**: `kronos-relate`
> **Version**: 1.0
> **Purpose**: Connect FDOs with meaningful bidirectional links.
> **Deployed to**: `.github/instructions/kronos-relate.instructions.md`

---

## When This Applies

Activate this skill when:
- A new FDO is created or an existing one is enriched (post-promote)
- A connection between concepts surfaces during conversation
- User asks to link, connect, or relate concepts
- Cross-domain relationships are discovered

## How to Connect

Every connection requires **two updates** — one in each FDO:

### 1. Frontmatter `related` field
```yaml
related: [other-fdo-id, another-fdo-id]
```

### 2. Body `## Connections` section
```markdown
## Connections

- Related to: [[other-fdo-id]] — brief reason for connection
```

Both must stay in sync. If one exists without the other, add the missing one.

## Connection Quality

**Meaningful connections only.** Before linking, ask:
- Would someone following this link learn something useful?
- Is the relationship specific enough to articulate in a few words?
- Does this connection help navigate the knowledge graph?

All three yes → connect. Otherwise → skip.

### Good Connections
- `pac-framework` ↔ `golden-ratio-emergence` — "PAC recursion generates φ"
- `grim-architecture` ↔ `proj-grim` — "Architecture spec for this project"
- `john-doe` ↔ `topology-research` — "Collaborator on this topic"

### Bad Connections (too generic)
- Everything linked to `dawn-field-theory` just because it's in the repo
- Person linked to a concept because they "might be interested"

## Process

1. **Identify the two FDOs** to connect
2. **Describe the relationship** in a few words
3. **Update both FDOs**:
   - Add ID to `related:` list in frontmatter (plain IDs, not `[[bracketed]]`)
   - Add `[[wikilink]]` with description in `## Connections`
4. **Update `updated:` date** on both FDOs

## MCP Tools

- **Primary**: Use `kronos_search` to discover related FDOs, `kronos_update` to add links
- **Fallback**: Direct file editing in `kronos-vault/` directories

## Rules

1. **Always bidirectional** — if A links to B, B must link to A
2. **No orphan links** — verify the target FDO exists before linking
3. **No duplicate links** — check `related:` before adding
4. **Strip brackets** — `related` values are plain IDs, never `[[bracketed]]`
5. **Brief descriptions** — explain the relationship concisely in the body
6. **Quality over quantity** — a sparse, meaningful graph beats a dense, noisy one

## Currency Check

After completing this skill, verify the protocol is still accurate:
- [ ] Commands in this protocol match the actual codebase
- [ ] File paths referenced still exist
- [ ] Test counts and quality gates match current reality
- [ ] If anything is stale, update this protocol before finishing
