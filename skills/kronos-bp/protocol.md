# Kronos Best Practice — Promotion Protocol

> **Skill**: kronos-bp
> **Version**: 1.0
> **Purpose**: Distill stable patterns from notes and journals into best-practice FDOs. Best practices are the workspace's institutional memory.

## When This Applies

- A pattern has proven stable across multiple sessions
- The same fix/approach has been noted 2+ times
- User explicitly requests best-practice creation
- A principle emerges from a journal entry that applies broadly

## Protocol

### Step 1: Gather evidence

Search for the pattern across notes and journals:

```
kronos_search(query="<pattern keywords>")
kronos_notes_recent(days=90, tags=["<relevant-tags>"])
```

Collect at least 2 sources of evidence (notes, journal entries, FDOs, or direct experience from this session).

### Step 2: Verify stability

The pattern should:
- Be observed in 2+ distinct contexts
- Not have been contradicted or superseded
- Be general enough to apply beyond one specific case

If evidence is thin, suggest creating a note instead and revisiting later.

### Step 3: Create the FDO

Use this template structure:

```
kronos_create(
    id="bp-<slug>",
    title="Best Practice: <Pattern Name>",
    domain="<relevant-domain>",
    status="stable",
    confidence=0.85,
    confidence_basis="Observed in N contexts: <list evidence>",
    tags=["best-practice", ...other-relevant-tags],
    related=[...evidence-fdo-ids],
    body=<body below>
)
```

**Body template:**

```markdown
# bp-<slug>

## Summary
One-paragraph overview of the practice.

## When to Use
- Bullet list of situations where this practice applies
- Include scope: which repos, which domains, which contexts

## The Practice
Detailed description of what to do and why.

## Examples
Concrete examples showing the practice in action.
Include code snippets, commands, or configuration where applicable.

## Anti-Patterns
What NOT to do. Common mistakes this practice prevents.

## Evidence
- [[journal-YYYY-MM-DD-example]] — discovered during X
- notes-YYYY-MM#note-YYYYMMDD-HHMMSS — confirmed in Y context
- Direct experience from Z sessions

## Connections
- [[related-fdo-1]] — relationship description
- [[related-fdo-2]] — relationship description
```

### Step 4: Backlink evidence

Update source notes/journals to reference the new best practice:

```
kronos_update(id="<source-fdo>", fields={"related": ["bp-<slug>", ...existing]})
```

### Step 5: Confirm

Report the new FDO ID, path, and a one-line summary to the user.

## Quality Checklist

- [ ] Evidence from 2+ sources
- [ ] `best-practice` tag present
- [ ] Domain is content-appropriate (not `notes` or `journal`)
- [ ] Has concrete examples
- [ ] Has anti-patterns section
- [ ] Source FDOs are backlinked
- [ ] Title starts with "Best Practice:"
