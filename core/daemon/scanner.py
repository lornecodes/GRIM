"""Project scanner — reads stories from vault and syncs into the pipeline."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.daemon.models import PRIORITY_ORDER, PipelineStatus
from core.daemon.pipeline import PipelineStore

logger = logging.getLogger(__name__)

# Statuses that qualify a story for the daemon pipeline
ELIGIBLE_STATUSES = {"active", "in_progress"}


class ScannedStory:
    """A story discovered in the vault, eligible for pipeline tracking."""

    __slots__ = ("id", "project_id", "title", "status", "priority", "assignee",
                 "owner", "depends_on", "description", "acceptance_criteria",
                 "job_id", "estimate_days", "tags")

    def __init__(self, data: dict, project_id: str):
        self.id: str = data.get("id", "")
        self.project_id: str = project_id
        self.title: str = data.get("title", "")
        self.status: str = data.get("status", "new")
        self.priority: str = data.get("priority", "medium")
        self.assignee: str = data.get("assignee", "")
        self.owner: str = data.get("owner", "")
        self.depends_on: list[str] = data.get("depends_on", []) or []
        self.description: str = data.get("description", "")
        self.acceptance_criteria: list[str] = data.get("acceptance_criteria", [])
        self.job_id: str | None = data.get("job_id")
        self.estimate_days: float = data.get("estimate_days", 1.0)
        self.tags: list[str] = data.get("tags", [])

    @property
    def priority_int(self) -> int:
        """Convert string priority to integer for pipeline ordering."""
        return PRIORITY_ORDER.get(self.priority, 2)

    @property
    def is_eligible(self) -> bool:
        """Whether this story qualifies for daemon pipeline tracking.

        All active/in_progress stories with an assignee are eligible for
        tracking. Ownership filtering (grim vs human) happens at the
        promote stage, not here — human stories enter the pipeline for
        visibility but are never auto-promoted.
        """
        return self.status in ELIGIBLE_STATUSES and bool(self.assignee)


class ProjectScanner:
    """Scans vault project FDOs for dispatchable stories.

    Uses TaskEngine for vault I/O. The scanner does NOT import TaskEngine
    directly — it accepts a callable that returns project data, making it
    testable without a real vault.
    """

    def __init__(self, vault_path: Path, project_filter: list[str] | None = None):
        self._vault_path = vault_path
        self._project_filter = set(project_filter) if project_filter else None

    def scan(self) -> list[ScannedStory]:
        """Scan vault for eligible stories.

        Returns stories with status in {active, in_progress} and a non-empty assignee.
        """
        stories: list[ScannedStory] = []

        try:
            from kronos_mcp.tasks import TaskEngine
            engine = TaskEngine(str(self._vault_path))
        except ImportError:
            logger.error("Cannot import TaskEngine — kronos_mcp not available")
            return stories

        for fm, body, md_path in engine._scan_all_projects():
            proj_id = fm.get("id", "")
            if not proj_id:
                continue

            # Apply project filter
            if self._project_filter and proj_id not in self._project_filter:
                continue

            for story_data in engine._get_stories(fm):
                story = ScannedStory(story_data, proj_id)
                if story.is_eligible:
                    stories.append(story)

        logger.info("Scanned vault: %d eligible stories found", len(stories))
        return stories

    async def sync_pipeline(self, store: PipelineStore) -> dict[str, int]:
        """Synchronize vault stories with pipeline state.

        - Adds new eligible stories as BACKLOG
        - Removes pipeline items whose stories are no longer eligible
          (e.g., manually resolved in vault)
        - Updates priority/assignee if changed in vault

        Returns counts: {"added": N, "removed": N, "updated": N}
        """
        eligible = self.scan()
        eligible_by_id = {s.id: s for s in eligible}

        # Get current pipeline items
        all_items = await store.list_items()
        tracked_by_story = {item.story_id: item for item in all_items}

        added = 0
        removed = 0
        updated = 0

        # Add new stories not yet in pipeline
        for story_id, story in eligible_by_id.items():
            if story_id not in tracked_by_story:
                deps_json = json.dumps(story.depends_on) if story.depends_on else ""
                await store.add(
                    story_id=story_id,
                    project_id=story.project_id,
                    priority=story.priority_int,
                    assignee=story.assignee,
                    owner=story.owner,
                    depends_on=deps_json,
                )
                added += 1

        # Remove pipeline items that are no longer eligible (only non-terminal)
        for story_id, item in tracked_by_story.items():
            if story_id not in eligible_by_id:
                # Only remove if not in a terminal or active state
                if item.status in (PipelineStatus.BACKLOG, PipelineStatus.READY):
                    await store.remove(item.id)
                    removed += 1

        # Update priority/assignee/owner/depends_on on existing BACKLOG/READY items
        for story_id, story in eligible_by_id.items():
            if story_id in tracked_by_story:
                item = tracked_by_story[story_id]
                if item.status in (PipelineStatus.BACKLOG, PipelineStatus.READY):
                    new_deps_json = json.dumps(story.depends_on) if story.depends_on else ""
                    needs_update = (
                        item.priority != story.priority_int
                        or item.assignee != story.assignee
                        or item.owner != story.owner
                        or item.depends_on != new_deps_json
                    )
                    if needs_update:
                        # Direct SQL update for non-status fields
                        await _update_item_fields(
                            store, item.id,
                            priority=story.priority_int,
                            assignee=story.assignee,
                            owner=story.owner,
                            depends_on=new_deps_json,
                        )
                        updated += 1

        result = {"added": added, "removed": removed, "updated": updated}
        if added or removed or updated:
            logger.info("Pipeline sync: %s", result)
        return result


async def _update_item_fields(store: PipelineStore, item_id: str, **fields: Any) -> None:
    """Update non-status fields on a pipeline item (priority, assignee)."""
    import aiosqlite
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    sets = ["updated_at = ?"]
    params: list[Any] = [now]

    for key, value in fields.items():
        if key in ("priority", "assignee", "owner", "depends_on"):
            sets.append(f"{key} = ?")
            params.append(value)

    params.append(item_id)
    sql = f"UPDATE pipeline SET {', '.join(sets)} WHERE id = ?"

    async with aiosqlite.connect(str(store._db_path)) as db:
        await db.execute(sql, params)
        await db.commit()


# ── Dependency Checking ──────────────────────────────────────────────────────

# Vault statuses that satisfy a dependency
_SATISFIED_STATUSES = {"resolved", "closed"}


def check_dependencies(
    depends_on_json: str,
    story_statuses: dict[str, str],
) -> tuple[bool, list[str]]:
    """Check whether all dependencies are satisfied.

    Args:
        depends_on_json: JSON string of dependency story IDs (from pipeline column).
        story_statuses: {story_id: vault_status} map for all relevant stories.

    Returns:
        (all_satisfied, blocking_ids) — True if all deps met, plus list of blockers.
    """
    if not depends_on_json:
        return True, []

    try:
        dep_ids = json.loads(depends_on_json)
    except (json.JSONDecodeError, TypeError):
        return True, []  # malformed → treat as no deps

    if not dep_ids:
        return True, []

    blocking: list[str] = []
    for dep_id in dep_ids:
        status = story_statuses.get(dep_id, "")
        if status not in _SATISFIED_STATUSES:
            blocking.append(dep_id)

    return len(blocking) == 0, blocking


def detect_dependency_cycle(
    stories: list[ScannedStory],
) -> list[list[str]]:
    """Detect cycles in story dependency graph via DFS.

    Returns list of cycles found (each cycle is a list of story IDs).
    Empty list means no cycles.
    """
    deps_map: dict[str, list[str]] = {}
    for s in stories:
        if s.depends_on:
            deps_map[s.id] = s.depends_on

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {s.id: WHITE for s in stories}
    cycles: list[list[str]] = []
    path: list[str] = []

    def dfs(node: str) -> None:
        if node not in color:
            return  # dependency references unknown story
        if color[node] == GRAY:
            # Found a cycle — extract it from path
            idx = path.index(node)
            cycles.append(path[idx:] + [node])
            return
        if color[node] == BLACK:
            return

        color[node] = GRAY
        path.append(node)
        for dep in deps_map.get(node, []):
            dfs(dep)
        path.pop()
        color[node] = BLACK

    for s in stories:
        if color.get(s.id) == WHITE:
            dfs(s.id)

    return cycles
