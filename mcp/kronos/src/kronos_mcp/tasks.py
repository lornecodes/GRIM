"""
Kronos Task Engine — story management embedded in project FDOs.

Stories live as YAML arrays in proj-* FDO frontmatter (the `stories`
field). Each story is a dispatchable work order that can be assigned
to a pool agent.  This engine provides CRUD, search, and archival.

Work item hierarchy:
    Project (proj-*)  — FDOs with embedded stories
    Story             — frontmatter YAML in project FDO, dispatchable to pool
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
VALID_ASSIGNEES = {"code", "research", "audit", "plan", ""}
PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# Map vault directory names to domain labels
DOMAIN_DIRS = {
    "ai-systems", "computing", "interests", "journal", "media",
    "modelling", "notes", "people", "personal", "physics", "projects", "tools",
}


# ── Task Engine ──────────────────────────────────────────────────────────────

class TaskEngine:
    """Manages stories embedded in proj-* FDO frontmatter."""

    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path)
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def _get_lock(self, proj_id: str) -> threading.Lock:
        """Get or create a per-project lock for thread safety."""
        with self._global_lock:
            if proj_id not in self._locks:
                self._locks[proj_id] = threading.Lock()
            return self._locks[proj_id]

    # ── FDO I/O ──────────────────────────────────────────────────────────

    def _find_project_file(self, proj_id: str) -> Path | None:
        """Find a proj-* FDO file anywhere in the vault."""
        for md_path in self.vault_path.rglob(f"{proj_id}.md"):
            return md_path
        return None

    def _parse_project(self, path: Path) -> tuple[dict, str] | None:
        """Parse a project FDO file. Returns (frontmatter_dict, body) or None."""
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

        if not isinstance(fm, dict) or not fm.get("id", "").startswith("proj-"):
            return None

        return fm, m.group(2).strip()

    def _write_project(self, path: Path, fm: dict, body: str) -> None:
        """Write updated frontmatter + body back to project FDO file."""
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

    def _domain_for_project(self, path: Path) -> str:
        """Extract domain from project FDO's vault directory."""
        rel = path.relative_to(self.vault_path)
        parts = rel.parts
        if parts and parts[0] in DOMAIN_DIRS:
            return parts[0]
        return "projects"  # default

    # ── Batch Operations ─────────────────────────────────────────────────

    def _scan_all_projects(self) -> list[tuple[dict, str, Path]]:
        """Scan vault once and return all parsed projects.

        Returns list of (frontmatter, body, path) tuples.
        """
        results = []
        for md_path in self.vault_path.rglob("proj-*.md"):
            parsed = self._parse_project(md_path)
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

        for fm, body, md_path in self._scan_all_projects():
            proj_id = fm["id"]
            domain = self._domain_for_project(md_path)

            for story in self._get_stories(fm):
                sid = story.get("id", "")
                if sid in wanted:
                    found[sid] = {
                        "id": sid,
                        "title": story.get("title", ""),
                        "status": story.get("status", "new"),
                        "priority": story.get("priority", "medium"),
                        "estimate_days": story.get("estimate_days", 0),
                        "description": story.get("description", ""),
                        "assignee": story.get("assignee", ""),
                        "job_id": story.get("job_id"),
                        "project": proj_id,
                        "domain": domain,
                        "tags": story.get("tags", []),
                        "acceptance_criteria": story.get("acceptance_criteria", []),
                    }
                    if len(found) == len(wanted):
                        return found

        return found

    # ── Project Discovery ─────────────────────────────────────────────────

    def get_all_projects(self) -> list[dict]:
        """List all proj-* FDOs with summary info."""
        projects = []
        for fm, body, md_path in self._scan_all_projects():
            stories = self._get_stories(fm)
            domain = self._domain_for_project(md_path)
            projects.append({
                "id": fm["id"],
                "title": fm.get("title", ""),
                "status": fm.get("status", ""),
                "domain": domain,
                "story_count": len(stories),
                "stories_done": sum(1 for s in stories if s.get("status") == "closed"),
            })
        return projects

    # ── Story CRUD ───────────────────────────────────────────────────────

    def create_story(
        self,
        proj_id: str,
        title: str,
        priority: str = "medium",
        estimate_days: float = 1.0,
        description: str = "",
        acceptance_criteria: list[str] | None = None,
        assignee: str = "",
        tags: list[str] | None = None,
        created_by: str = "human",
        status: str = "new",
    ) -> dict:
        """Create a new story in a project FDO."""
        if status not in VALID_STATUSES:
            return {"error": f"Invalid status: {status}", "valid": sorted(VALID_STATUSES)}
        if priority not in VALID_PRIORITIES:
            return {"error": f"Invalid priority: {priority}", "valid": list(VALID_PRIORITIES)}
        if assignee and assignee not in VALID_ASSIGNEES:
            return {"error": f"Invalid assignee: {assignee}", "valid": sorted(VALID_ASSIGNEES - {""})}

        lock = self._get_lock(proj_id)
        with lock:
            path = self._find_project_file(proj_id)
            if not path:
                return {"error": f"Project FDO not found: {proj_id}"}

            result = self._parse_project(path)
            if not result:
                return {"error": f"Failed to parse project: {proj_id}"}

            fm, body = result
            stories = self._get_stories(fm)

            story_id = self._next_story_id(proj_id, fm)
            today = str(date.today())

            story: dict[str, Any] = {
                "id": story_id,
                "title": title,
                "status": status,
                "priority": priority,
                "created_by": created_by,
                "estimate_days": estimate_days,
                "description": description,
                "acceptance_criteria": acceptance_criteria or [],
                "assignee": assignee,
                "tags": tags or [],
                "created": today,
                "updated": today,
                "log": [f"{today}: Story created"],
            }

            stories.append(story)
            self._set_stories(fm, stories)
            self._write_project(path, fm, body)

            return {"created": story_id, "project": proj_id, "story": story}

    # ── Update ───────────────────────────────────────────────────────────

    def update_item(self, item_id: str, fields: dict) -> dict:
        """Update fields on a story."""
        if not fields:
            return {"error": "No fields to update"}

        if "status" in fields and fields["status"] not in VALID_STATUSES:
            return {"error": f"Invalid status: {fields['status']}", "valid": list(VALID_STATUSES)}
        if "priority" in fields and fields["priority"] not in VALID_PRIORITIES:
            return {"error": f"Invalid priority: {fields['priority']}", "valid": list(VALID_PRIORITIES)}
        if "assignee" in fields and fields["assignee"] and fields["assignee"] not in VALID_ASSIGNEES:
            return {"error": f"Invalid assignee: {fields['assignee']}", "valid": sorted(VALID_ASSIGNEES - {""})}

        return self._update_story(item_id, fields)

    def _update_story(self, story_id: str, fields: dict) -> dict:
        """Update fields on a story."""
        # Block regression to draft — draft is creation-only
        if "status" in fields and fields["status"] == "draft":
            found = self._find_story(story_id)
            if found:
                _, path, fm, body, _ = found
                result = self._parse_project(path)
                if result:
                    fm2, _ = result
                    for s in self._get_stories(fm2):
                        if s["id"] == story_id and s.get("status") != "draft":
                            return {"error": "Cannot set status back to 'draft'. Draft is an initial creation-only status."}

        found = self._find_story(story_id)
        if not found:
            return {"error": f"Story not found: {story_id}"}

        proj_id, path, fm, body, story_idx = found
        lock = self._get_lock(proj_id)
        with lock:
            result = self._parse_project(path)
            if not result:
                return {"error": f"Failed to parse project: {proj_id}"}
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
                if key == "assignee" and old_val != value:
                    story.setdefault("log", []).append(f"{today}: Assigned → {value or 'unassigned'}")
                if key == "job_id" and old_val != value:
                    story.setdefault("log", []).append(f"{today}: Linked to pool job {value}")

            story["updated"] = today
            self._set_stories(fm, stories)
            self._write_project(path, fm, body)

            return {"updated": story_id, "project": proj_id, "fields_changed": changed}

    # ── Read ─────────────────────────────────────────────────────────────

    def get_item(self, item_id: str) -> dict | None:
        """Get a story by ID."""
        found = self._find_story(item_id)
        if not found:
            return None
        proj_id, path, fm, body, story_idx = found
        stories = self._get_stories(fm)
        domain = self._domain_for_project(path)
        for s in stories:
            if s["id"] == item_id:
                return {"type": "story", "project": proj_id, "domain": domain, **s}
        return None

    def list_items(
        self,
        project_id: str | None = None,
        domain: str | None = None,
        status: str | None = None,
        priority: str | None = None,
    ) -> list[dict]:
        """List stories with optional filters."""
        results = []

        for fm, body, md_path in self._scan_all_projects():
            proj_id = fm["id"]
            proj_domain = self._domain_for_project(md_path)

            # Filter by project
            if project_id and proj_id != project_id:
                continue

            # Filter by domain
            if domain and proj_domain != domain:
                continue

            for story in self._get_stories(fm):
                if status and story.get("status") != status:
                    continue
                if priority and story.get("priority") != priority:
                    continue

                results.append({
                    "id": story["id"],
                    "title": story.get("title", ""),
                    "status": story.get("status", "new"),
                    "priority": story.get("priority", "medium"),
                    "estimate_days": story.get("estimate_days", 0),
                    "assignee": story.get("assignee", ""),
                    "job_id": story.get("job_id"),
                    "project": proj_id,
                    "domain": proj_domain,
                    "tags": story.get("tags", []),
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
        proj_id: str,
        title: str,
        estimate_days: float = 1.0,
    ) -> list[str]:
        """Pre-creation validation. Returns list of warning strings. Empty = OK."""
        warnings = []

        stripped = title.strip()
        if len(stripped) < self.MIN_TITLE_LENGTH:
            warnings.append(
                f"Title too short ({len(stripped)} chars, min {self.MIN_TITLE_LENGTH})"
            )

        path = self._find_project_file(proj_id)
        if path:
            result = self._parse_project(path)
            if result:
                fm, _ = result
                existing_titles = [
                    s.get("title", "").lower().strip()
                    for s in self._get_stories(fm)
                ]
                if stripped.lower() in existing_titles:
                    warnings.append(
                        f"Duplicate title: a story with this exact title already exists in {proj_id}"
                    )

        if estimate_days > 10:
            warnings.append(
                f"Large estimate ({estimate_days} days) — consider breaking into smaller stories"
            )

        return warnings

    # ── Archive ──────────────────────────────────────────────────────────

    def archive_closed(self, proj_id: str | None = None) -> dict:
        """Move closed stories to an archive section in the project FDO."""
        archived_count = 0
        projects_touched = []

        paths = []
        if proj_id:
            p = self._find_project_file(proj_id)
            if p:
                paths.append(p)
        else:
            paths = list(self.vault_path.rglob("proj-*.md"))

        for md_path in paths:
            parsed = self._parse_project(md_path)
            if not parsed:
                continue
            fm, body = parsed
            pid = fm["id"]
            lock = self._get_lock(pid)

            with lock:
                parsed = self._parse_project(md_path)
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
                self._write_project(md_path, fm, body)

                archived_count += len(closed)
                projects_touched.append(pid)

        return {"archived": archived_count, "projects": projects_touched}

    # ── ID Generation ────────────────────────────────────────────────────

    def _next_story_id(self, proj_id: str, fm: dict) -> str:
        """Generate sequential story ID: story-{proj-short}-NNN.

        Scans both active and archived stories to prevent ID collisions.
        """
        short = proj_id.replace("proj-", "")
        prefix = f"story-{short}-"
        existing_nums = []

        for s in self._get_stories(fm):
            sid = s.get("id", "")
            if sid.startswith(prefix):
                num_part = sid[len(prefix):]
                if num_part.isdigit():
                    existing_nums.append(int(num_part))

        for s in fm.get("archived_stories", []) or []:
            sid = s.get("id", "")
            if sid.startswith(prefix):
                num_part = sid[len(prefix):]
                if num_part.isdigit():
                    existing_nums.append(int(num_part))

        next_num = max(existing_nums, default=0) + 1
        return f"story-{short}-{next_num:03d}"

    # ── Lookup helpers ───────────────────────────────────────────────────

    def _find_story(self, story_id: str) -> tuple[str, Path, dict, str, int] | None:
        """Find a story across all proj-* FDOs.
        Returns (proj_id, path, frontmatter, body, story_index) or None.
        """
        for md_path in self.vault_path.rglob("proj-*.md"):
            parsed = self._parse_project(md_path)
            if not parsed:
                continue
            fm, body = parsed
            for idx, story in enumerate(self._get_stories(fm)):
                if story.get("id") == story_id:
                    return (fm["id"], md_path, fm, body, idx)
        return None

    # ── Legacy compatibility ─────────────────────────────────────────────

    def get_all_features(self) -> list[dict]:
        """Legacy alias — returns projects instead of features."""
        return self.get_all_projects()

    def _project_for_feature(self, fm: dict) -> str | None:
        """Legacy — extract parent project ID from related list."""
        for rel in fm.get("related", []):
            if isinstance(rel, str) and rel.startswith("proj-"):
                return rel
        return None
