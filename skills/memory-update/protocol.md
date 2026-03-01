# Memory Update Protocol

> Update GRIM's persistent working memory with new information.

## When This Applies

When the user explicitly asks GRIM to remember something, or when
important context (preferences, learnings, objectives) should be persisted.

## Pre-update Checklist

1. **Read current memory** — always read before writing to avoid data loss
2. **Identify target section** — which section should this go in?
3. **Avoid duplicates** — check if this information already exists
4. **Timestamp entries** — add ISO timestamps to time-sensitive entries

## Section Guide

| Section | What Goes Here | Format |
|---------|---------------|--------|
| Active Objectives | Current goals and tasks | Bullet list with status |
| Recent Topics | Conversation subjects | Timestamped entries |
| User Preferences | Workflow, style, tools | Key-value pairs |
| Key Learnings | Confirmed insights | Bullet list |
| Future Goals | Aspirations | Bullet list |
| Session Notes | Per-session summaries | Timestamped paragraphs |

## Execution

1. Use `read_grim_memory()` to load current content
2. Determine the appropriate section for the new information
3. Use `update_grim_memory(section, content)` to update
4. Confirm to the user what was saved and where

## Rules

- **Never delete existing entries** unless explicitly asked
- **Append to sections** rather than replacing them (except objectives which get synced)
- **Keep entries concise** — memory should be scannable, not verbose
- **Prune Session Notes** to last 10 entries when updating

## Currency Check

After completing this skill:
- [ ] Read memory before writing
- [ ] Correct section was targeted
- [ ] No data was lost
- [ ] User was informed of the update
