# Kronos Note — Quick Capture Protocol

> **Skill**: kronos-note
> **Version**: 1.0
> **Purpose**: Append a quick note to the monthly rolling log. For fixes, workarounds, gotchas, and small learnings. Stack Overflow-style: specific and searchable.

## When This Applies

- A fix or workaround was just discovered
- A non-obvious configuration step was identified
- A gotcha or edge case was encountered
- User explicitly asks to note something down

## Protocol

### Step 1: Check for duplicates

Search existing notes and FDOs to avoid re-recording known information:

```
kronos_search(query="<topic keywords>", semantic=false)
```

If a matching note or FDO already exists, inform the user and ask if they want to add to it or skip.

### Step 2: Compose the note

Keep it tight — think Stack Overflow answer, not blog post:

- **Title**: Specific, searchable (e.g., "MSYS_NO_PATHCONV required for Docker on Windows Git Bash")
- **Body**: Problem → Solution → Why (2-5 sentences)
- **Tags**: Library/tool names, error types, platforms (e.g., `docker`, `windows`, `git-bash`, `path-conversion`)
- **Related**: Link to relevant FDOs if applicable
- **Source paths**: Files involved (e.g., `GRIM/scripts/release.sh`)

### Step 3: Append to rolling log

```
kronos_note_append(
    title="...",
    body="...",
    tags=[...],
    related=[...],
    source_paths=[...]
)
```

The tool handles file creation, timestamping, and indexing automatically.

### Step 4: Confirm

Report the anchor ID and month file to the user. Example:

> Noted: "MSYS_NO_PATHCONV Scoping for Docker" → notes-2026-03 (anchor: note-20260301-143022)

## Note Promotion

When a note proves broadly useful (referenced multiple times, applies across projects), promote it:

1. Create a full FDO via `kronos_create` with the appropriate domain
2. Tag it with `best-practice` if it represents a stable pattern (use `/kronos-bp`)
3. Reference the original note anchor in the FDO body

## Quality Checklist

- [ ] Title is specific and searchable
- [ ] Body captures problem + solution + why
- [ ] Tags include tool/library names
- [ ] No duplicate exists in vault
- [ ] Source paths are accurate
