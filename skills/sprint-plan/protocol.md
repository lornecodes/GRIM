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

### Phase 0: Board Check (Always First)

Before planning new work, review what's already in flight:

1. Show current board:
   ```
   kronos_board_view()
   ```
2. Check for:
   - **Stale items**: Stories in ACTIVE or IN_PROGRESS that may be done — move to RESOLVED
   - **Completed work**: Stories in RESOLVED that should be CLOSED and archived
   - **Blockers**: Items stuck in the same column for too long
3. Clean up:
   - Move done stories: `kronos_task_move(story_id="...", column="closed")`
   - Archive: `kronos_task_archive()`
4. Check for draft items in the backlog:
   ```
   kronos_task_list(status="draft")
   ```
   - If drafts exist, prompt user to review and promote before planning
5. Now you know the true state of active work before adding more

### Phase 1: Review Backlog

1. Show backlog sorted by priority:
   ```
   kronos_backlog_view(project_id="proj-xxx")
   ```
2. Separate **draft** items from **ready** items:
   - **Ready** (status=new): available for board placement
   - **Pending Approval** (status=draft): need promotion first — show under separate heading
3. Present ready stories to user with priority, estimate, and feature grouping
4. Note total estimate days in backlog

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
   > **Draft guard**: If user picks a draft item, prompt to promote first:
   > `kronos_task_update(item_id="story-xxx", fields={"status": "new"})`
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
3. **Reconcile with recent work** — Check if any completed changes (commits, deploys) correspond to stories that should be moved forward
4. Show calendar vs actuals
5. Identify blockers or reprioritization needs

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
