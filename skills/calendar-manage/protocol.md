# Calendar Management

> **Skill**: `calendar-manage`
> **Version**: 1.0
> **Purpose**: View, add, and manage calendar events (work + personal)
> **When to use**: When the user asks about their schedule or needs to add events

---

## When This Applies

Activate when:
- User asks "what's on my calendar?"
- User wants to add a personal event (appointment, meeting, etc.)
- User wants to see upcoming work schedule
- After board changes that affect the schedule

---

## Action: VIEW

1. Determine date range (default: next 7 days)
2. Show calendar:
   ```
   kronos_calendar_view(start_date="YYYY-MM-DD", end_date="YYYY-MM-DD")
   ```
3. Present in chronological order, distinguishing work vs personal items

---

## Action: ADD

1. Collect event details from user:
   - Title (required)
   - Date (required, YYYY-MM-DD)
   - Time (optional, HH:MM)
   - Duration (optional, hours)
   - Recurring (optional, default: false)
   - Notes (optional)
2. Create event:
   ```
   kronos_calendar_add(title="...", date="YYYY-MM-DD", time="HH:MM")
   ```

---

## Action: UPDATE

1. Get current event: identify by ID or title search
2. Update fields:
   ```
   kronos_calendar_update(event_id="personal-001", action="update",
                          fields={"time": "15:00", "notes": "Moved to 3pm"})
   ```

---

## Action: DELETE

1. Confirm with user before deleting
2. Delete:
   ```
   kronos_calendar_update(event_id="personal-001", action="delete")
   ```

---

## Action: SYNC

1. Rebuild work schedule from board:
   ```
   kronos_calendar_sync()
   ```
2. Show updated calendar for next 2 weeks

---

## Quality Gates

- [ ] Events have at minimum title and date
- [ ] Calendar shown after any modification
