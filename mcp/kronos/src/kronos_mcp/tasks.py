"""
Kronos Task Engine — story/task management embedded in feature FDOs.

Stories and tasks live as YAML arrays in feat-* FDO frontmatter (the `stories`
field in `extra`). This engine provides CRUD, search, and archival without
polluting the vault with hundreds of ephemeral FDOs.

Work item hierarchy:
    Epic   (proj-*)   — existing project FDOs
    Feature (feat-*)  — FDOs with embedded stories
    Story             — frontmatter YAML in feature FDO
    Task              — nested under parent story
"""

from __future__ import annotations

import logging
import re
import threading
import yaml
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger("kronos-mcp.tasks")

# ── Constants ────────────────────────────────────────────────────────────────

VALID_STATUSES = {"draft", "new", "active", "in_progress", "resolved", "closed"}
VALID_PRIORITIES = {"critical", "high", "medium", "low"}
PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


# ── Task Engine ──────────────────────────────────────────────────────────────

class TaskEngine:
    """Manages stories/tasks embedded in feat-* FDO frontmatter."""

    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path)
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def _get_lock(self, feat_id: str) -> threading.Lock:
        """Get or create a per-feature lock for thread safety."""
        with self._global_lock:
            if feat_id not in self._locks:
                self._locks[feat_id] = threading.Lock()
            return self._locks[feat_id]

    # ── FDO I/O ──────────────────────────────────────────────────────────

    def _find_feature_file(self, feat_id: str) -> Path | None:
        """Find a feat-* FDO file anywhere in the vault."""
        for md_path in self.vault_path.rglob(f"{feat_id}.md"):
            return md_path
        return None

    def _parse_feature(self, path: Path) -> tuple[dict, str] | None:
        """Parse a feature FDO file. Returns (frontmatter_dict, body) or None."""
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read {path}: {e}")
            return None

        m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
        if not m:
            return None

        try:
            fm = yaml.safe_load(m.group(1))
        except yaml.YAMLError as e:
            logger.warning(f"Invalid YAML in {path}: {e}")
            return None

        if not isinstance(fm, dict) or not fm.get("id", "").startswith("feat-"):
            return None

        return fm, m.group(2).strip()

    def _write_feature(self, path: Path, fm: dict, body: str) -> None:
        """Write updated frontmatter + body back to feature FDO file."""
        fm["updated"] = str(date.today())
        fm_yaml = yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True)
        from .fileutil import atomic_write
        atomic_write(path, f"---\n{fm_yaml}---\n\n{body}")

    def _get_stories(self, fm: dict) -> list[dict]:
        """Extract stories list from frontmatter."""
        return fm.get("stories", []) or []

    def _set_stories(self, fm: dict, stories: list[dict]) -> None:
        """Set stories list in frontmatter."""
        fm["stories"] = stories

    # ── Batch Operations ─────────────────────────────────────────────────

    def _scan_all_features(self) -> list[tuple[dict, str, "Path"]]:
        """Scan vault once and return all parsed features.

        Returns list of (frontmatter, body, path) tuples.
        """
        results = []
        for md_path in self.vault_path.rglob("feat-*.md"):
            parsed = self._parse_feature(md_path)
            if parsed:
                fm, body = parsed
                results.append((fm, body, md_path))
        return results

    def get_items_batch(self, story_ids: list[str]) -> dict[str, dict]:
        """Get multiple stories by ID in a single vault scan.

        Returns {story_id: enriched_story_dict} for found stories.
        Missing stories are omitted from the result.
        """
        if not story_ids:
            return {}

        wanted = set(story_ids)
        found: dict[str, dict] = {}

        for fm, body, md_path in self._scan_all_features():
            feat_id = fm["id"]
            project_id = self._project_for_feature(fm)

            for story in self._get_stories(fm):
                sid = story.get("id", "")
                if sid in wanted:
                    tasks = story.get("tasks", [])
                    found[sid] = {
                        "id": sid,
                        "title": story.get("title", ""),
                        "status": story.get("status", "new"),
                        "priority": story.get("priority", "medium"),
                        "estimate_days": story.get("estimate_days", 0),
                        "description": story.get("description", ""),
                        "feature": feat_id,
                        "project": project_id,
                        "tasks": tasks,
                        "task_count": len(tasks),
                        "tasks_done": sum(
                            1 for t in tasks if t.get("status") in ("resolved", "closed")
                        ),
                        "tags": story.get("tags", []),
                    }
                    # Early exit if we found everything
                    if len(found) == len(wanted):
                        return found

        return found

    # ── Feature Discovery ────────────────────────────────────────────────

    def get_all_features(self) -> list[dict]:
        """List all feat-* FDOs with summary info."""
        features = []
        for md_path in self.vault_path.rglob("feat-*.md"):
            result = self._parse_feature(md_path)
            if result:
                fm, _ = result
                stories = self._get_stories(fm)
                project_id = self._project_for_feature(fm)
                features.append({
                    "id": fm["id"],
                    "title": fm.get("title", ""),
                    "status": fm.get("status", ""),
                    "project": project_id,
                    "story_count": len(stories),
                    "stories_done": sum(1 for s in stories if s.get("status") == "closed"),
                })
        return features

    def _project_for_feature(self, fm: dict) -> str | None:
        """Extract parent project ID from feature's related list."""
        for rel in fm.get("related", []):
            if isinstance(rel, str) and rel.startswith("proj-"):
                return rel
        return None

    # ── Story CRUD ───────────────────────────────────────────────────────

    def create_story(
        self,
        feat_id: str,
        title: str,
        priority: str = "medium",
        estimate_days: float = 1.0,
        description: str = "",
        acceptance_criteria: list[str] | None = None,
        tags: list[str] | None = None,
        created_by: str = "human",
        status: str = "new",
    ) -> dict:
        """Create a new story in a feature FDO."""
        if status not in VALID_STATUSES:
            return {"error": f"Invalid status: {status}", "valid": sorted(VALID_STATUSES)}
        if priority not in VALID_PRIORITIES:
            return {"error": f"Invalid priority: {priority}", "valid": list(VALID_PRIORITIES)}

        lock = self._get_lock(feat_id)
        with lock:
            path = self._find_feature_file(feat_id)
            if not path:
                return {"error": f"Feature FDO not found: {feat_id}"}

            result = self._parse_feature(path)
            if not result:
                return {"error": f"Failed to parse feature: {feat_id}"}

            fm, body = result
            stories = self._get_stories(fm)

            # Generate sequential ID (checks archived stories too)
            story_id = self._next_story_id(feat_id, fm)
            today = str(date.today())

            story = {
                "id": story_id,
                "title": title,
                "status": status,
                "priority": priority,
                "created_by": created_by,
                "estimate_days": estimate_days,
                "description": description,
                "acceptance_criteria": acceptance_criteria or [],
                "tasks": [],
                "tags": tags or [],
                "created": today,
                "updated": today,
                "log": [f"{today}: Story created"],
            }

            stories.append(story)
            self._set_stories(fm, stories)
            self._write_feature(path, fm, body)

            return {"created": story_id, "feature": feat_id, "story": story}

    def create_task(
        self,
        story_id: str,
        title: str,
        estimate_days: float = 0.5,
        assignee: str = "",
        notes: str = "",
        created_by: str = "human",
    ) -> dict:
        """Create a new task under a story."""
        found = self._find_story(story_id)
        if not found:
            return {"error": f"Story not found: {story_id}"}

        feat_id, path, fm, body, story_idx = found
        lock = self._get_lock(feat_id)
        with lock:
            # Re-read to avoid stale data
            result = self._parse_feature(path)
            if not result:
                return {"error": f"Failed to parse feature: {feat_id}"}
            fm, body = result
            stories = self._get_stories(fm)

            story = None
            for s in stories:
                if s["id"] == story_id:
                    story = s
                    break
            if not story:
                return {"error": f"Story not found after re-read: {story_id}"}

            tasks = story.get("tasks", [])
            task_id = self._next_task_id(story)
            today = str(date.today())

            task = {
                "id": task_id,
                "title": title,
                "status": "new",
                "estimate_days": estimate_days,
                "assignee": assignee,
                "notes": notes,
                "created_by": created_by,
            }

            tasks.append(task)
            story["tasks"] = tasks
            story["updated"] = today
            story["log"] = story.get("log", [])
            story["log"].append(f"{today}: Task added — {task_id}: {title}")

            self._set_stories(fm, stories)
            self._write_feature(path, fm, body)

            return {"created": task_id, "story": story_id, "feature": feat_id, "task": task}

    # ── Update ───────────────────────────────────────────────────────────

    def update_item(self, item_id: str, fields: dict) -> dict:
        """Update fields on a story or task."""
        if not fields:
            return {"error": "No fields to update"}

        # Validate status/priority if being set
        if "status" in fields and fields["status"] not in VALID_STATUSES:
            return {"error": f"Invalid status: {fields['status']}", "valid": list(VALID_STATUSES)}
        if "priority" in fields and fields["priority"] not in VALID_PRIORITIES:
            return {"error": f"Invalid priority: {fields['priority']}", "valid": list(VALID_PRIORITIES)}

        # Is this a task ID (task-NNN) or a story ID (story-*)?
        is_task = item_id.startswith("task-")

        if is_task:
            return self._update_task(item_id, fields)
        else:
            return self._update_story(item_id, fields)

    def _update_story(self, story_id: str, fields: dict) -> dict:
        """Update fields on a story."""
        # Block regression to draft — draft is creation-only
        if "status" in fields and fields["status"] == "draft":
            found = self._find_story(story_id)
            if found:
                _, path, fm, body, _ = found
                result = self._parse_feature(path)
                if result:
                    fm2, _ = result
                    for s in self._get_stories(fm2):
                        if s["id"] == story_id and s.get("status") != "draft":
                            return {"error": "Cannot set status back to 'draft'. Draft is an initial creation-only status."}

        found = self._find_story(story_id)
        if not found:
            return {"error": f"Story not found: {story_id}"}

        feat_id, path, fm, body, story_idx = found
        lock = self._get_lock(feat_id)
        with lock:
            result = self._parse_feature(path)
            if not result:
                return {"error": f"Failed to parse feature: {feat_id}"}
            fm, body = result
            stories = self._get_stories(fm)

            story = None
            for s in stories:
                if s["id"] == story_id:
                    story = s
                    break
            if not story:
                return {"error": f"Story not found after re-read: {story_id}"}

            today = str(date.today())
            changed = []
            for key, value in fields.items():
                if key in ("id", "created", "log"):
                    continue  # Immutable fields
                old_val = story.get(key)
                story[key] = value
                changed.append(key)
                if key == "status" and old_val != value:
                    story.setdefault("log", []).append(f"{today}: Status → {value}")

            story["updated"] = today
            self._set_stories(fm, stories)
            self._write_feature(path, fm, body)

            return {"updated": story_id, "feature": feat_id, "fields_changed": changed}

    def _update_task(self, task_id: str, fields: dict) -> dict:
        """Update fields on a task (searches across all stories in all features)."""
        found = self._find_task(task_id)
        if not found:
            return {"error": f"Task not found: {task_id}"}

        feat_id, path, fm, body, story_id, task_idx = found
        lock = self._get_lock(feat_id)
        with lock:
            result = self._parse_feature(path)
            if not result:
                return {"error": f"Failed to parse feature: {feat_id}"}
            fm, body = result
            stories = self._get_stories(fm)

            # Find the story and task again
            task = None
            for story in stories:
                if story["id"] == story_id:
                    for t in story.get("tasks", []):
                        if t["id"] == task_id:
                            task = t
                            break
                    break

            if not task:
                return {"error": f"Task not found after re-read: {task_id}"}

            changed = []
            for key, value in fields.items():
                if key == "id":
                    continue
                task[key] = value
                changed.append(key)

            self._set_stories(fm, stories)
            self._write_feature(path, fm, body)

            return {"updated": task_id, "story": story_id, "feature": feat_id, "fields_changed": changed}

    # ── Read ─────────────────────────────────────────────────────────────

    def get_item(self, item_id: str) -> dict | None:
        """Get a story or task by ID."""
        is_task = item_id.startswith("task-")

        if is_task:
            found = self._find_task(item_id)
            if not found:
                return None
            feat_id, path, fm, body, story_id, task_idx = found
            stories = self._get_stories(fm)
            for story in stories:
                if story["id"] == story_id:
                    for t in story.get("tasks", []):
                        if t["id"] == item_id:
                            return {"type": "task", "feature": feat_id, "story": story_id, **t}
            return None
        else:
            found = self._find_story(item_id)
            if not found:
                return None
            feat_id, path, fm, body, story_idx = found
            stories = self._get_stories(fm)
            for s in stories:
                if s["id"] == item_id:
                    project_id = self._project_for_feature(fm)
                    return {"type": "story", "feature": feat_id, "project": project_id, **s}
            return None

    def list_items(
        self,
        project_id: str | None = None,
        feat_id: str | None = None,
        status: str | None = None,
        priority: str | None = None,
    ) -> list[dict]:
        """List stories with optional filters."""
        results = []

        for md_path in self.vault_path.rglob("feat-*.md"):
            parsed = self._parse_feature(md_path)
            if not parsed:
                continue
            fm, _ = parsed

            # Filter by feature
            if feat_id and fm["id"] != feat_id:
                continue

            # Filter by project
            if project_id:
                proj = self._project_for_feature(fm)
                if proj != project_id:
                    continue

            feature_id = fm["id"]
            proj = self._project_for_feature(fm)

            for story in self._get_stories(fm):
                if status and story.get("status") != status:
                    continue
                if priority and story.get("priority") != priority:
                    continue

                tasks = story.get("tasks", [])
                results.append({
                    "id": story["id"],
                    "title": story.get("title", ""),
                    "status": story.get("status", "new"),
                    "priority": story.get("priority", "medium"),
                    "estimate_days": story.get("estimate_days", 0),
                    "feature": feature_id,
                    "project": proj,
                    "task_count": len(tasks),
                    "tasks_done": sum(1 for t in tasks if t.get("status") in ("resolved", "closed")),
                    "created": story.get("created", ""),
                    "updated": story.get("updated", ""),
                })

        # Sort by priority then creation date
        results.sort(key=lambda s: (PRIORITY_ORDER.get(s["priority"], 99), s.get("created", "")))
        return results

    # ── Validation ─────────────────────────────────────────────────────

    MIN_TITLE_LENGTH = 10

    def validate_story_creation(
        self,
        feat_id: str,
        title: str,
        estimate_days: float = 1.0,
    ) -> list[str]:
        """Pre-creation validation. Returns list of warning strings. Empty = OK.

        Non-blocking — caller decides whether to proceed despite warnings.
        """
        warnings = []

        # Title quality
        stripped = title.strip()
        if len(stripped) < self.MIN_TITLE_LENGTH:
            warnings.append(
                f"Title too short ({len(stripped)} chars, min {self.MIN_TITLE_LENGTH})"
            )

        # Duplicate detection within the feature
        path = self._find_feature_file(feat_id)
        if path:
            result = self._parse_feature(path)
            if result:
                fm, _ = result
                existing_titles = [
                    s.get("title", "").lower().strip()
                    for s in self._get_stories(fm)
                ]
                if stripped.lower() in existing_titles:
                    warnings.append(
                        f"Duplicate title: a story with this exact title already exists in {feat_id}"
                    )

        # Scope suggestion
        if estimate_days > 10:
            warnings.append(
                f"Large estimate ({estimate_days} days) — consider creating a feature instead of a story"
            )

        return warnings

    # ── Archive ──────────────────────────────────────────────────────────

    def archive_closed(self, feat_id: str | None = None) -> dict:
        """Move closed stories to an archive section in the feature FDO."""
        archived_count = 0
        features_touched = []

        paths = []
        if feat_id:
            p = self._find_feature_file(feat_id)
            if p:
                paths.append(p)
        else:
            paths = list(self.vault_path.rglob("feat-*.md"))

        for md_path in paths:
            parsed = self._parse_feature(md_path)
            if not parsed:
                continue
            fm, body = parsed
            fid = fm["id"]
            lock = self._get_lock(fid)

            with lock:
                # Re-read under lock
                parsed = self._parse_feature(md_path)
                if not parsed:
                    continue
                fm, body = parsed
                stories = self._get_stories(fm)

                active = []
                closed = []
                for s in stories:
                    if s.get("status") == "closed":
                        closed.append(s)
                    else:
                        active.append(s)

                if not closed:
                    continue

                archived = fm.get("archived_stories", []) or []
                archived.extend(closed)

                fm["stories"] = active
                fm["archived_stories"] = archived
                self._write_feature(md_path, fm, body)

                archived_count += len(closed)
                features_touched.append(fid)

        return {"archived": archived_count, "features": features_touched}

    # ── ID Generation ────────────────────────────────────────────────────

    def _next_story_id(self, feat_id: str, fm: dict) -> str:
        """Generate sequential story ID: story-{feat-short}-NNN.

        Scans both active and archived stories to prevent ID collisions
        after archival.
        """
        short = feat_id.replace("feat-", "")
        prefix = f"story-{short}-"
        existing_nums = []

        # Check active stories
        for s in self._get_stories(fm):
            sid = s.get("id", "")
            if sid.startswith(prefix):
                num_part = sid[len(prefix):]
                if num_part.isdigit():
                    existing_nums.append(int(num_part))

        # Check archived stories
        for s in fm.get("archived_stories", []) or []:
            sid = s.get("id", "")
            if sid.startswith(prefix):
                num_part = sid[len(prefix):]
                if num_part.isdigit():
                    existing_nums.append(int(num_part))

        next_num = max(existing_nums, default=0) + 1
        return f"story-{short}-{next_num:03d}"

    def _next_task_id(self, story: dict) -> str:
        """Generate sequential task ID: task-{story-suffix}-NNN.

        Uses the story's numeric suffix (e.g., story-phoenix-auth-001 -> 001)
        to namespace task IDs, making them globally unique across stories.
        """
        # Extract story suffix for namespacing (e.g., "phoenix-auth-001")
        story_id = story.get("id", "")
        story_suffix = story_id.replace("story-", "") if story_id.startswith("story-") else story_id

        tasks = story.get("tasks", [])
        existing_nums = []
        prefix = f"task-{story_suffix}-"
        for t in tasks:
            tid = t.get("id", "")
            if tid.startswith(prefix):
                num_part = tid[len(prefix):]
                if num_part.isdigit():
                    existing_nums.append(int(num_part))
            else:
                # Also handle legacy task-NNN format for backward compat
                m = re.match(r"task-(\d+)$", tid)
                if m:
                    existing_nums.append(int(m.group(1)))

        next_num = max(existing_nums, default=0) + 1
        return f"task-{story_suffix}-{next_num:03d}"

    # ── Lookup helpers ───────────────────────────────────────────────────

    def _find_story(self, story_id: str) -> tuple[str, Path, dict, str, int] | None:
        """Find a story across all feat-* FDOs.
        Returns (feat_id, path, frontmatter, body, story_index) or None.
        """
        for md_path in self.vault_path.rglob("feat-*.md"):
            parsed = self._parse_feature(md_path)
            if not parsed:
                continue
            fm, body = parsed
            for idx, story in enumerate(self._get_stories(fm)):
                if story.get("id") == story_id:
                    return (fm["id"], md_path, fm, body, idx)
        return None

    def _find_task(self, task_id: str) -> tuple[str, Path, dict, str, str, int] | None:
        """Find a task across all feat-* FDOs.
        Returns (feat_id, path, frontmatter, body, story_id, task_index) or None.
        """
        for md_path in self.vault_path.rglob("feat-*.md"):
            parsed = self._parse_feature(md_path)
            if not parsed:
                continue
            fm, body = parsed
            for story in self._get_stories(fm):
                for idx, task in enumerate(story.get("tasks", [])):
                    if task.get("id") == task_id:
                        return (fm["id"], md_path, fm, body, story["id"], idx)
        return None
