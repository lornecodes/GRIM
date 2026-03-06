"""
Kronos Board Engine — kanban board state management.

The board is a cross-project view stored in kronos-vault/projects/board.yaml.
It holds story IDs in ADO-style columns. Moving a story between columns
auto-updates its status in the parent project FDO via TaskEngine.

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
        """Write board.yaml atomically."""
        from .fileutil import atomic_write
        data["last_synced"] = datetime.now().isoformat(timespec="seconds")
        content = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
        atomic_write(self.board_path, content)

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

        # Draft guard — must promote to 'new' before board placement
        if story.get("status") == "draft":
            return {
                "error": f"Cannot place draft story on board. Promote to 'new' first: "
                f"kronos_task_update(item_id='{story_id}', fields={{\"status\": \"new\"}})"
            }

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
        """Move a story between board columns. Updates story status.

        IMPORTANT: Never hold self._lock while calling task_engine methods
        to avoid deadlock (board lock → task lock ordering conflict).
        """
        if column not in COLUMNS:
            return {"error": f"Invalid column: {column}", "valid": COLUMNS}

        # Phase 1: Board mutation under lock
        with self._lock:
            board = self._load_board()

            # Find and remove from current column
            current_col = None
            for col, ids in board["columns"].items():
                if story_id in ids:
                    ids.remove(story_id)
                    current_col = col
                    break

            if current_col is not None:
                # Already on board — move it
                board["columns"][column].append(story_id)
                self._save_board(board)

        # Phase 2: "Not on board" branch — draft guard (outside lock)
        if current_col is None:
            story = self.task_engine.get_item(story_id)
            if story and story.get("status") == "draft":
                return {
                    "error": f"Cannot place draft story on board. Promote to 'new' first: "
                    f"kronos_task_update(item_id='{story_id}', fields={{\"status\": \"new\"}})"
                }
            # Re-acquire lock to add to board
            with self._lock:
                board = self._load_board()
                # Double-check not added by another thread
                for col, ids in board["columns"].items():
                    if story_id in ids:
                        # Another thread already added it — skip
                        break
                else:
                    board["columns"][column].append(story_id)
                    self._save_board(board)

        # Phase 3: Status sync (always outside lock)
        self.task_engine.update_item(story_id, {"status": COLUMN_STATUS[column]})

        if current_col is None:
            return {"moved": story_id, "from": None, "to": column, "note": "added to board"}
        return {"moved": story_id, "from": current_col, "to": column}

    def cleanup_archived(self) -> list[str]:
        """Remove dead story IDs from the board. Thread-safe.

        Returns list of removed story IDs. Used by handle_task_archive
        after stories are archived from feature FDOs.
        """
        # Collect all board IDs under lock
        with self._lock:
            board = self._load_board()
            all_ids = set()
            for ids in board["columns"].values():
                all_ids.update(ids)

        if not all_ids:
            return []

        # Check existence outside lock (calls task_engine)
        dead = {sid for sid in all_ids if not self.task_engine.get_item(sid)}
        if not dead:
            return []

        # Remove dead IDs under lock
        removed = []
        with self._lock:
            board = self._load_board()
            for col, ids in board["columns"].items():
                before = list(ids)
                board["columns"][col] = [s for s in ids if s not in dead]
                removed.extend(s for s in before if s in dead)
            if removed:
                self._save_board(board)

        return list(set(removed))

    # ── Views ────────────────────────────────────────────────────────────

    def board_view(
        self,
        project_id: str | None = None,
        domain: str | None = None,
    ) -> dict:
        """Get the full board with enriched story data per column.

        Lock held only for board YAML read; task_engine calls happen
        outside the lock to avoid board→task lock ordering issues.
        """
        with self._lock:
            board = self._load_board()

        result: dict[str, Any] = {"columns": {}, "last_synced": board.get("last_synced")}

        # Collect all story IDs and batch-load in one vault scan
        all_ids: list[str] = []
        for col in COLUMNS:
            all_ids.extend(board["columns"].get(col, []))

        stories_by_id = self.task_engine.get_items_batch(all_ids) if all_ids else {}

        for col in COLUMNS:
            story_ids = board["columns"].get(col, [])
            enriched = []
            for sid in story_ids:
                story = stories_by_id.get(sid)
                if not story:
                    enriched.append({"id": sid, "error": "story not found"})
                    continue

                # Filter by project if requested
                if project_id and story.get("project") != project_id:
                    continue

                # Filter by domain if requested
                if domain and story.get("domain") != domain:
                    continue

                enriched.append(story)

            result["columns"][col] = enriched

        # Summary stats
        total = sum(len(v) for v in result["columns"].values())
        result["total_stories"] = total
        return result

    def backlog_view(
        self,
        project_id: str | None = None,
        domain: str | None = None,
        priority: str | None = None,
    ) -> dict:
        """Get stories NOT on the board (the backlog)."""
        with self._lock:
            board = self._load_board()
        on_board = set()
        for ids in board["columns"].values():
            on_board.update(ids)

        # Get all stories, filter out board ones
        all_stories = self.task_engine.list_items(
            project_id=project_id,
            domain=domain,
            priority=priority,
        )

        backlog = [s for s in all_stories if s["id"] not in on_board]
        return {"backlog": backlog, "count": len(backlog)}

    def get_board_story_ids(self, columns: list[str] | None = None) -> list[str]:
        """Get story IDs from specific columns (for calendar sync)."""
        with self._lock:
            board = self._load_board()
        target_cols = columns or COLUMNS
        ids = []
        for col in target_cols:
            ids.extend(board["columns"].get(col, []))
        return ids
