"""
Kronos Board Engine — kanban board state management.

The board is a cross-feature view stored in kronos-vault/projects/board.yaml.
It holds story IDs in ADO-style columns. Moving a story between columns
auto-updates its status in the parent feature FDO via TaskEngine.

Columns: NEW → ACTIVE → IN_PROGRESS → RESOLVED → CLOSED
"""

from __future__ import annotations

import logging
import threading
import yaml
from datetime import datetime
from pathlib import Path
from typing import Any

from .tasks import TaskEngine, PRIORITY_ORDER

logger = logging.getLogger("kronos-mcp.board")

COLUMNS = ["new", "active", "in_progress", "resolved", "closed"]

# Column → story status mapping (moving to column sets this status)
COLUMN_STATUS = {
    "new": "new",
    "active": "active",
    "in_progress": "in_progress",
    "resolved": "resolved",
    "closed": "closed",
}


class BoardEngine:
    """Kanban board backed by board.yaml + TaskEngine for story details."""

    def __init__(self, vault_path: str, task_engine: TaskEngine):
        self.vault_path = Path(vault_path)
        self.board_path = self.vault_path / "projects" / "board.yaml"
        self.task_engine = task_engine
        self._lock = threading.Lock()

    # ── Board I/O ────────────────────────────────────────────────────────

    def _load_board(self) -> dict:
        """Load board.yaml. Returns default structure if missing/empty."""
        if not self.board_path.exists():
            return self._default_board()
        try:
            data = yaml.safe_load(self.board_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "columns" not in data:
                return self._default_board()
            # Ensure all columns exist
            cols = data["columns"]
            for col in COLUMNS:
                if col not in cols:
                    cols[col] = []
            return data
        except Exception as e:
            logger.warning(f"Failed to load board.yaml: {e}")
            return self._default_board()

    def _save_board(self, data: dict) -> None:
        """Write board.yaml."""
        data["last_synced"] = datetime.now().isoformat(timespec="seconds")
        self.board_path.parent.mkdir(parents=True, exist_ok=True)
        self.board_path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def _default_board(self) -> dict:
        return {"columns": {col: [] for col in COLUMNS}, "last_synced": None}

    # ── Board Operations ─────────────────────────────────────────────────

    def add_to_board(self, story_id: str, column: str = "new") -> dict:
        """Add a story to the board in a specific column."""
        if column not in COLUMNS:
            return {"error": f"Invalid column: {column}", "valid": COLUMNS}

        # Verify story exists
        story = self.task_engine.get_item(story_id)
        if not story:
            return {"error": f"Story not found: {story_id}"}

        with self._lock:
            board = self._load_board()

            # Check if already on board
            for col, ids in board["columns"].items():
                if story_id in ids:
                    return {"error": f"Story already on board in column '{col}'"}

            board["columns"][column].append(story_id)
            self._save_board(board)

        # Update story status to match column
        self.task_engine.update_item(story_id, {"status": COLUMN_STATUS[column]})
        return {"added": story_id, "column": column}

    def remove_from_board(self, story_id: str) -> dict:
        """Remove a story from the board without changing its status."""
        with self._lock:
            board = self._load_board()
            removed_from = None
            for col, ids in board["columns"].items():
                if story_id in ids:
                    ids.remove(story_id)
                    removed_from = col
                    break

            if not removed_from:
                return {"error": f"Story not on board: {story_id}"}

            self._save_board(board)
        return {"removed": story_id, "was_in": removed_from}

    def move_story(self, story_id: str, column: str) -> dict:
        """Move a story between board columns. Updates story status."""
        if column not in COLUMNS:
            return {"error": f"Invalid column: {column}", "valid": COLUMNS}

        with self._lock:
            board = self._load_board()

            # Find and remove from current column
            current_col = None
            for col, ids in board["columns"].items():
                if story_id in ids:
                    ids.remove(story_id)
                    current_col = col
                    break

            if current_col is None:
                # Not on board yet — add it
                board["columns"][column].append(story_id)
                self._save_board(board)
                self.task_engine.update_item(story_id, {"status": COLUMN_STATUS[column]})
                return {"moved": story_id, "from": None, "to": column, "note": "added to board"}

            # Add to new column
            board["columns"][column].append(story_id)
            self._save_board(board)

        # Update story status
        self.task_engine.update_item(story_id, {"status": COLUMN_STATUS[column]})
        return {"moved": story_id, "from": current_col, "to": column}

    # ── Views ────────────────────────────────────────────────────────────

    def board_view(self, project_id: str | None = None) -> dict:
        """Get the full board with enriched story data per column."""
        board = self._load_board()
        result = {"columns": {}, "last_synced": board.get("last_synced")}

        for col in COLUMNS:
            story_ids = board["columns"].get(col, [])
            enriched = []
            for sid in story_ids:
                story = self.task_engine.get_item(sid)
                if not story:
                    enriched.append({"id": sid, "error": "story not found"})
                    continue

                # Filter by project if requested
                if project_id and story.get("project") != project_id:
                    continue

                tasks = story.get("tasks", [])
                enriched.append({
                    "id": sid,
                    "title": story.get("title", ""),
                    "priority": story.get("priority", "medium"),
                    "estimate_days": story.get("estimate_days", 0),
                    "feature": story.get("feature", ""),
                    "project": story.get("project", ""),
                    "task_count": len(tasks),
                    "tasks_done": sum(1 for t in tasks if t.get("status") in ("resolved", "closed")),
                    "tags": story.get("tags", []),
                })

            result["columns"][col] = enriched

        # Summary stats
        total = sum(len(v) for v in result["columns"].values())
        result["total_stories"] = total
        return result

    def backlog_view(
        self,
        project_id: str | None = None,
        feat_id: str | None = None,
        priority: str | None = None,
    ) -> dict:
        """Get stories NOT on the board (the backlog)."""
        board = self._load_board()
        on_board = set()
        for ids in board["columns"].values():
            on_board.update(ids)

        # Get all stories, filter out board ones
        all_stories = self.task_engine.list_items(
            project_id=project_id,
            feat_id=feat_id,
            priority=priority,
        )

        backlog = [s for s in all_stories if s["id"] not in on_board]
        return {"backlog": backlog, "count": len(backlog)}

    def get_board_story_ids(self, columns: list[str] | None = None) -> list[str]:
        """Get story IDs from specific columns (for calendar sync)."""
        board = self._load_board()
        target_cols = columns or COLUMNS
        ids = []
        for col in target_cols:
            ids.extend(board["columns"].get(col, []))
        return ids
