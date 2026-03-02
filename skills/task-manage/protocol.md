# Task Management — ADO-Style Work Items

> **Skill**: `task-manage`
> **Version**: 1.0
> **Purpose**: Create, update, move, groom, and review stories/tasks on the kanban board
> **When to use**: Any time work items need to be created, tracked, or reorganized

---

## When This Applies

Activate when:
- User asks to create a story, task, or work item
- User asks to move items on the board or change status
- User wants to see the board or backlog
- User wants to groom or reprioritize work
- After completing work that should be tracked

## Architecture

```
Epic   (proj-*)    — persistent project FDOs
  └─ Feature (feat-*)  — FDOs with embedded stories YAML
      └─ Story          — in feat-* frontmatter
          └─ Task       — nested under story
```

Board columns: **NEW → ACTIVE → IN_PROGRESS → RESOLVED → CLOSED**

---

## Action: CREATE

### Create a Story

1. Identify or create the parent feature FDO (`feat-*`)
   - If no feature exists, create one using `kronos_create` with domain matching the project
   - Template: `kronos-vault/templates/feature-template.md`
2. Create the story:
   ```
   kronos_task_create(type="story", feat_id="feat-xxx", title="...",
                      priority="high", estimate_days=2,
                      description="...", acceptance_criteria=["..."])
   ```
3. Optionally add to board: `kronos_task_move(story_id="...", column="new")`

### Create a Task

1. Identify the parent story
2. Create the task:
   ```
   kronos_task_create(type="task", story_id="story-xxx", title="...",
                      estimate_days=0.5)
   ```

---

## Action: UPDATE

1. Get current state: `kronos_task_get(item_id="...")`
2. Update fields:
   ```
   kronos_task_update(item_id="story-xxx", fields={"priority": "critical", "estimate_days": 3})
   ```

---

## Action: MOVE

1. Move story to target column:
   ```
   kronos_task_move(story_id="story-xxx", column="in_progress")
   ```
2. Status is auto-updated to match the column
3. After moving to RESOLVED or CLOSED, consider syncing calendar:
   ```
   kronos_calendar_sync()
   ```

---

## Action: GROOM

1. Show the backlog: `kronos_backlog_view(project_id="proj-xxx")`
2. Review each story with user:
   - Is priority still correct?
   - Is estimate still accurate?
   - Should it be split into smaller stories?
   - Should it be moved to the board?
3. Update as needed: `kronos_task_update(...)`
4. Load selected stories onto board: `kronos_task_move(..., column="new")`

> **CHECKPOINT**: Confirm prioritization with user before making changes

---

## Action: REVIEW

1. Show board: `kronos_board_view(project_id="proj-xxx")`
2. For each column, summarize:
   - Story count and total estimate
   - Task completion percentage
   - Any blocked items
3. Optionally show calendar: `kronos_calendar_view(start_date="...", end_date="...")`

---

## Action: POST-CHANGE UPDATE

Run this after completing any meaningful code change (feature, fix, refactor). This is the most important habit — **do it at the end of every change**.

1. Check the board: `kronos_board_view()`
2. Find the story that corresponds to the work just completed
3. If found:
   - Update tasks within the story: `kronos_task_update(item_id="task-xxx", fields={"status": "resolved"})`
   - If all acceptance criteria met → move story: `kronos_task_move(story_id="...", column="resolved")`
4. If no story exists for the work:
   - Create one under the appropriate `feat-*` FDO
   - Move directly to CLOSED (retroactive tracking is better than no tracking)
5. Sync calendar if board changed: `kronos_calendar_sync()`

---

## Quality Gates

Before marking done:
- [ ] All created stories have title, priority, and estimate
- [ ] Stories moving past NEW have acceptance criteria
- [ ] Board state is consistent (no orphan IDs)
- [ ] Calendar synced if board changed
- [ ] Recent completed work has corresponding stories (no untracked changes)

## Currency Check

After completing this skill:
- [ ] MCP tools still respond correctly
- [ ] Board YAML is valid
- [ ] Feature FDO frontmatter is valid YAML
