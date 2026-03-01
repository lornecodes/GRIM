"""
Kronos Calendar Engine — schedule computation and personal events.

Work schedule is computed from active board items + estimates.
Personal events are standalone entries for non-project items.

Both are stored as YAML in kronos-vault/calendar/.
"""

from __future__ import annotations

import logging
import threading
import yaml
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .tasks import TaskEngine, PRIORITY_ORDER
from .board import BoardEngine

logger = logging.getLogger("kronos-mcp.calendar")


class CalendarEngine:
    """Schedule sync + personal calendar event management."""

    def __init__(self, vault_path: str, board_engine: BoardEngine):
        self.vault_path = Path(vault_path)
        self.board_engine = board_engine
        self.task_engine = board_engine.task_engine
        self.schedule_path = self.vault_path / "calendar" / "schedule.yaml"
        self.personal_path = self.vault_path / "calendar" / "personal.yaml"
        self._lock = threading.Lock()

    # ── YAML I/O ─────────────────────────────────────────────────────────

    def _load_yaml(self, path: Path) -> dict:
        if not path.exists():
            return {"entries": []}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"entries": []}
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")
            return {"entries": []}

    def _save_yaml(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    # ── Schedule Sync ────────────────────────────────────────────────────

    def sync_schedule(self, start_date: str | None = None) -> dict:
        """Rebuild schedule.yaml from active board items + estimates.

        Algorithm:
        1. Get stories from active + in_progress board columns
        2. Sort by priority (critical > high > medium > low)
        3. Sequence from start_date, each spanning its estimate_days
        4. Write to schedule.yaml
        """
        start = _parse_date(start_date) if start_date else date.today()

        # Get active board stories
        active_ids = self.board_engine.get_board_story_ids(
            columns=["active", "in_progress"]
        )

        # Enrich with story details
        stories = []
        for sid in active_ids:
            item = self.task_engine.get_item(sid)
            if item:
                stories.append(item)

        # Sort by priority
        stories.sort(key=lambda s: PRIORITY_ORDER.get(s.get("priority", "medium"), 99))

        # Sequence items on calendar
        entries = []
        cursor = start
        for story in stories:
            est = float(story.get("estimate_days", 1))
            end = _add_workdays(cursor, est)
            entries.append({
                "story_id": story.get("id", ""),
                "title": story.get("title", ""),
                "feature": story.get("feature", ""),
                "project": story.get("project", ""),
                "start_date": str(cursor),
                "end_date": str(end),
                "estimate_days": est,
                "priority": story.get("priority", "medium"),
                "status": story.get("status", "active"),
            })
            # Next item starts the day after this one ends
            cursor = end + timedelta(days=1)

        schedule = {
            "entries": entries,
            "last_synced": datetime.now().isoformat(timespec="seconds"),
            "start_date": str(start),
        }

        with self._lock:
            self._save_yaml(self.schedule_path, schedule)

        return {"synced": len(entries), "schedule": schedule}

    # ── Calendar View ────────────────────────────────────────────────────

    def calendar_view(
        self,
        start_date: str,
        end_date: str,
        include_personal: bool = True,
    ) -> dict:
        """Get merged calendar for a date range."""
        start = _parse_date(start_date)
        end = _parse_date(end_date)

        result: dict[str, Any] = {"start": str(start), "end": str(end), "entries": []}

        # Work schedule entries
        schedule = self._load_yaml(self.schedule_path)
        for entry in schedule.get("entries", []):
            entry_start = _parse_date(entry.get("start_date", ""))
            entry_end = _parse_date(entry.get("end_date", ""))
            if entry_start and entry_end:
                # Include if any overlap with requested range
                if entry_end >= start and entry_start <= end:
                    result["entries"].append({
                        "type": "work",
                        **entry,
                    })

        # Personal events
        if include_personal:
            personal = self._load_yaml(self.personal_path)
            for entry in personal.get("entries", []):
                entry_date = _parse_date(entry.get("date", ""))
                if entry_date and start <= entry_date <= end:
                    result["entries"].append({
                        "type": "personal",
                        **entry,
                    })

        # Sort all entries by date
        result["entries"].sort(
            key=lambda e: e.get("start_date", e.get("date", "9999-99-99"))
        )
        result["total"] = len(result["entries"])
        return result

    # ── Personal Events ──────────────────────────────────────────────────

    def add_personal(
        self,
        title: str,
        event_date: str,
        time: str | None = None,
        duration_hours: float | None = None,
        recurring: bool = False,
        notes: str = "",
    ) -> dict:
        """Add a personal calendar event."""
        with self._lock:
            data = self._load_yaml(self.personal_path)
            entries = data.get("entries", [])

            # Generate sequential ID
            max_num = 0
            for e in entries:
                eid = e.get("id", "")
                if eid.startswith("personal-"):
                    try:
                        max_num = max(max_num, int(eid.split("-")[1]))
                    except (ValueError, IndexError):
                        pass
            new_id = f"personal-{max_num + 1:03d}"

            entry = {
                "id": new_id,
                "title": title,
                "date": event_date,
            }
            if time:
                entry["time"] = time
            if duration_hours is not None:
                entry["duration_hours"] = duration_hours
            entry["recurring"] = recurring
            if notes:
                entry["notes"] = notes

            entries.append(entry)
            data["entries"] = entries
            self._save_yaml(self.personal_path, data)

        return {"created": new_id, "event": entry}

    def update_personal(self, event_id: str, fields: dict) -> dict:
        """Update fields on a personal calendar event."""
        with self._lock:
            data = self._load_yaml(self.personal_path)
            entries = data.get("entries", [])

            for entry in entries:
                if entry.get("id") == event_id:
                    changed = []
                    for key, value in fields.items():
                        if key == "id":
                            continue
                        entry[key] = value
                        changed.append(key)
                    data["entries"] = entries
                    self._save_yaml(self.personal_path, data)
                    return {"updated": event_id, "fields_changed": changed}

        return {"error": f"Personal event not found: {event_id}"}

    def delete_personal(self, event_id: str) -> dict:
        """Delete a personal calendar event."""
        with self._lock:
            data = self._load_yaml(self.personal_path)
            entries = data.get("entries", [])
            original_count = len(entries)
            entries = [e for e in entries if e.get("id") != event_id]

            if len(entries) == original_count:
                return {"error": f"Personal event not found: {event_id}"}

            data["entries"] = entries
            self._save_yaml(self.personal_path, data)

        return {"deleted": event_id}


# ── Date helpers ─────────────────────────────────────────────────────────

def _parse_date(s: str | None) -> date | None:
    """Parse YYYY-MM-DD string to date."""
    if not s:
        return None
    try:
        return date.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None


def _add_workdays(start: date, days: float) -> date:
    """Add workdays to a date (skip weekends)."""
    if days <= 0:
        return start

    whole_days = int(days)
    # For fractional days (e.g., 0.5), count as 1 calendar day
    if days != whole_days:
        whole_days += 1

    cursor = start
    added = 0
    while added < whole_days:
        cursor += timedelta(days=1)
        # Skip weekends (5=Saturday, 6=Sunday)
        if cursor.weekday() < 5:
            added += 1

    return cursor
