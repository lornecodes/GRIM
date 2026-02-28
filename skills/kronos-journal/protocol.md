# Kronos Journal & Notes Protocol

> Create structured journal entries and technical notes as full FDOs in the Kronos vault.

## When This Applies

- A debugging session reveals non-obvious insights (workarounds, root causes, gotchas)
- An architectural decision is made that future sessions need to know about
- A learning or discovery should be preserved as permanent record
- User explicitly asks to journal or note something

## Two Types

### Journal Entry (`journal/`)
- **Dated**: tied to a specific session or day
- **Narrative**: captures the story — what happened, what was tried, what worked
- **ID format**: `journal-YYYY-MM-DD-slug`
- **File format**: `journal/YYYY-MM-DD_slug.md`
- **Best for**: debugging sessions, deployment learnings, discovery stories

### Technical Note (`notes/`)
- **Timeless**: reference material not tied to a date
- **Factual**: captures how something works, a pattern, a technique
- **ID format**: `note-slug`
- **File format**: `notes/slug.md`
- **Best for**: configuration references, workarounds, how-to guides

## Protocol

### Step 1: Search for duplicates
```
kronos_search(query="<topic keywords>")
```
If a similar entry exists, consider updating it rather than creating a new one.

### Step 2: Gather context
- What FDOs are related? (check `kronos_graph` for connections)
- What source files were involved?
- What was the reasoning / debugging timeline?

### Step 3: Create the FDO

Use `kronos_create` with complete frontmatter:

```yaml
---
id: journal-2026-02-28-topic-slug    # or note-topic-slug
title: "Descriptive Title"
domain: journal                       # or notes
created: 'YYYY-MM-DD'
updated: 'YYYY-MM-DD'
status: stable                        # journals are usually stable on creation
confidence: 0.9                       # high for direct experience
related:
- related-fdo-1
- related-fdo-2
tags: [relevant, searchable, tags]
source_repos: [RepoName]
source_paths:
- Repo/path/to/file.py
- Repo/path/to/config.yaml
---
```

### Step 4: Write the body

For **journal entries**, use this structure:
1. **Context** — what were we trying to do?
2. **Problem** — what went wrong or what was discovered?
3. **Investigation** — what was tried, what didn't work?
4. **Solution** — what worked and why?
5. **Implications** — what does this mean going forward?
6. **References** — links to issues, docs, related FDOs

For **technical notes**, use this structure:
1. **Summary** — one-paragraph overview
2. **Details** — the technical content
3. **Configuration** — relevant config snippets
4. **Gotchas** — things that aren't obvious
5. **References** — links and related FDOs

### Step 5: Update related FDOs

If the journal/note references existing FDOs, add a backlink:
```
kronos_update(id="related-fdo", related=["journal-2026-02-28-slug"])
```

## Quality Checklist

- [ ] Searched vault first — no duplicate exists
- [ ] Frontmatter is complete with all required fields
- [ ] Body captures the WHY, not just the what
- [ ] source_paths point to real files
- [ ] Related FDOs are linked bidirectionally
- [ ] Tags are specific and searchable (not generic like "coding" or "fix")
