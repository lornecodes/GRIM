# Kronos Recall — Knowledge Retrieval

> **Skill**: `kronos-recall`
> **Version**: 1.0
> **Purpose**: Search and surface knowledge from the vault and inbox.
> **Deployed to**: `.github/instructions/kronos-recall.instructions.md`

---

## When This Applies

Activate this skill when:
- User asks "what do I know about...", "remind me about...", "find..."
- Background context would help answer a question
- Looking up a person, concept, project, or reference
- Checking whether something already exists before creating it

## Search Strategy

### 1. MCP Search (Primary)

Use `kronos_search` with semantic enabled for natural language queries:

```
kronos_search(query="relevant search terms", semantic=true)
```

The search engine fuses 4 channels:
- **tag_exact** (weight 2.0) — exact tag matches
- **keyword/BM25** (weight 1.0) — token-based full-text search
- **semantic** (weight 1.2) — embedding similarity
- **graph** (weight 0.6) — related FDO traversal

Tips:
- Use specific terms over vague ones
- Include domain vocabulary for precision
- For known tags: search using the tag name directly

### 2. Inbox Scan

The MCP search does **NOT index** `_inbox/` notes.
For recent captures, also check `kronos-vault/_inbox/` directly:
- List files in `_inbox/`
- Read relevant ones based on filename or date

### 3. Direct File Access (Fallback)

If MCP is unavailable:
- Read files directly from `kronos-vault/{domain}/`
- Use file search or grep for keywords across the vault

## Presenting Results

When surfacing knowledge:

1. **Lead with the most relevant FDO** — title, summary, confidence level
2. **Show connections** — what it links to, broader context
3. **Include status** — is this seed / developing / stable?
4. **Mention inbox items** — if relevant captures exist that aren't promoted yet
5. **Link to source** — point to the FDO file for details

### Result Format

```
📍 **[FDO Title]** (domain/id.md)
   Status: stable | Confidence: 0.9
   Summary: [extracted summary]
   Related: [[link-1]], [[link-2]]
```

## When Nothing Is Found

1. Say so clearly — "Nothing in the vault on this topic"
2. Offer to capture — "Want me to capture this for later?"
3. Check if related concepts exist that might help tangentially

## MCP Tools

- **Primary**: `kronos_search` for searching FDOs
- **Secondary**: `kronos_get` for retrieving a specific FDO by ID
- **Fallback**: Direct file reads from `kronos-vault/`

## Rules

1. **Search before creating** — always check if something exists first
2. **Check inbox too** — don't forget `_inbox/` for recent captures
3. **Respect confidence levels** — note when information is low-confidence or seed status
4. **Don't fabricate** — only present what's actually in the vault
5. **Suggest capture** — if the query reveals a gap, offer to fill it
