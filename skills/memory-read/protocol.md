# Memory Read Protocol

> Read and summarize GRIM's persistent working memory.

## When This Applies

When the user asks what GRIM remembers, what objectives are active,
what topics have been discussed, or wants to see GRIM's working memory.

## Execution

1. Use `read_grim_memory()` to load the full memory.md content
2. Parse the relevant sections based on the user's query:
   - "objectives" → focus on Active Objectives section
   - "recent topics" → focus on Recent Topics section
   - "preferences" → focus on User Preferences section
   - General query → summarize all sections
3. Present the information clearly, highlighting:
   - Active objectives with their status
   - Recent conversation topics with timestamps
   - Any relevant learnings or preferences

## Response Format

- Use the memory content to inform your response
- Don't just dump raw markdown — synthesize and present clearly
- If memory is empty or sparse, acknowledge it and suggest what could be captured
- Reference specific sections when relevant

## Currency Check

After completing this skill, verify:
- [ ] Memory file was readable
- [ ] Sections were parsed correctly
- [ ] Response reflects actual memory content
