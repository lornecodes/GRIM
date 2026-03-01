# Sprint Planning — Load, Review, Close

> **Skill**: `sprint-plan`
> **Version**: 1.0
> **Purpose**: Plan sprints by selecting backlog stories, loading the board, and syncing the calendar
> **When to use**: When deciding what to work on next, reviewing progress, or closing a sprint

---

## When This Applies

Activate when:
- User asks "what should I work on next?"
- User wants to plan upcoming work
- Board is empty but backlog has stories
- User wants to review or close a sprint

---

## Action: PLAN

### Phase 1: Review Backlog

1. Show backlog sorted by priority:
   ```
   kronos_backlog_view(project_id="proj-xxx")
   ```
2. Present stories to user with priority, estimate, and feature grouping
3. Note total estimate days in backlog

### Phase 2: Select Stories

> **CHECKPOINT**: User selects which stories to work on

1. User picks stories from the backlog
2. Check capacity:
   - Sum selected estimates
   - Warn if total > 10 days (2 weeks solo capacity)
   - Suggest trimming if overloaded

### Phase 3: Load Board

1. Move selected stories to the board:
   ```
   kronos_task_move(story_id="story-xxx", column="new")
   ```
2. Optionally move highest priority to ACTIVE:
   ```
   kronos_task_move(story_id="story-xxx", column="active")
   ```

### Phase 4: Sync Calendar

1. Rebuild schedule:
   ```
   kronos_calendar_sync()
   ```
2. Show the calendar for the next 2 weeks:
   ```
   kronos_calendar_view(start_date="YYYY-MM-DD", end_date="YYYY-MM-DD")
   ```

---

## Action: REVIEW

1. Show board: `kronos_board_view()`
2. For each column, report:
   - Count and total estimate
   - Task completion %
   - Any items stuck (in same column for > 3 days)
3. Show calendar vs actuals
4. Identify blockers or reprioritization needs

---

## Action: CLOSE

1. Show board: `kronos_board_view()`
2. Move all RESOLVED stories to CLOSED:
   ```
   kronos_task_move(story_id="...", column="closed")
   ```
3. Archive closed stories:
   ```
   kronos_task_archive()
   ```
4. Sync calendar:
   ```
   kronos_calendar_sync()
   ```
5. Show summary: stories completed, total estimate vs actual

---

## Quality Gates

- [ ] All board stories have estimates
- [ ] Calendar synced after changes
- [ ] No more than 10 active stories on board (warn if exceeded)
