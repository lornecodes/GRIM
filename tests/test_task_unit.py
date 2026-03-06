"""
Pure unit tests for TaskEngine, BoardEngine, CalendarEngine.

Uses temporary directories — no real vault, no side-effects.
Tests engine logic in isolation with minimal FDO fixtures.

Run:
    cd GRIM && python -m pytest tests/test_task_unit.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from unittest import TestCase

# Bootstrap — load engine modules directly from their file paths to avoid
# kronos_mcp.__init__.py which imports server.py (requires KRONOS_VAULT_PATH).
grim_root = Path(__file__).resolve().parent.parent
_mcp_src = grim_root / "mcp" / "kronos" / "src" / "kronos_mcp"

import importlib.util

def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

_tasks_mod = _load_module("kronos_mcp.tasks", _mcp_src / "tasks.py")
_board_mod = _load_module("kronos_mcp.board", _mcp_src / "board.py")
_calendar_mod = _load_module("kronos_mcp.calendar", _mcp_src / "calendar.py")

TaskEngine = _tasks_mod.TaskEngine
VALID_STATUSES = _tasks_mod.VALID_STATUSES
VALID_PRIORITIES = _tasks_mod.VALID_PRIORITIES
VALID_ASSIGNEES = _tasks_mod.VALID_ASSIGNEES
PRIORITY_ORDER = _tasks_mod.PRIORITY_ORDER
BoardEngine = _board_mod.BoardEngine
COLUMNS = _board_mod.COLUMNS
COLUMN_STATUS = _board_mod.COLUMN_STATUS
CalendarEngine = _calendar_mod.CalendarEngine
_parse_date = _calendar_mod._parse_date
_add_workdays = _calendar_mod._add_workdays


# ── Fixtures ────────────────────────────────────────────────────────────────

MINIMAL_PROJECT_FDO = """\
---
id: proj-test-alpha
title: "Test Project Alpha"
domain: projects
created: "2026-03-01"
updated: "2026-03-01"
status: developing
confidence: 0.5
related: []
tags: [test, epic]
stories: []
---

# Test Project Alpha

## Summary
A minimal project for unit tests.
"""

PROJECT_WITH_STORIES = """\
---
id: proj-test-beta
title: "Test Project Beta"
domain: projects
created: "2026-03-01"
updated: "2026-03-01"
status: developing
confidence: 0.7
related: []
tags: [test, epic]
stories:
  - id: story-test-beta-001
    title: "Pre-existing story"
    status: active
    priority: high
    estimate_days: 3
    description: "A story that already exists"
    assignee: "code"
    acceptance_criteria:
      - "Criterion 1"
    tags: [test]
    created: "2026-03-01"
    updated: "2026-03-01"
    log:
      - "2026-03-01: Story created"
  - id: story-test-beta-002
    title: "Second story"
    status: new
    priority: medium
    estimate_days: 1
    description: ""
    assignee: ""
    acceptance_criteria: []
    tags: []
    created: "2026-03-01"
    updated: "2026-03-01"
    log:
      - "2026-03-01: Story created"
---

# Test Project Beta

## Summary
Project with pre-existing stories for testing.
"""

PROJECT_WITH_CLOSED = """\
---
id: proj-test-gamma
title: "Test Project Gamma"
domain: projects
created: "2026-03-01"
updated: "2026-03-01"
status: developing
confidence: 0.6
related: []
tags: [test, epic]
stories:
  - id: story-test-gamma-001
    title: "Gamma story active"
    status: active
    priority: critical
    estimate_days: 2
    description: ""
    assignee: "research"
    acceptance_criteria: []
    tags: []
    created: "2026-03-01"
    updated: "2026-03-01"
    log: []
  - id: story-test-gamma-002
    title: "Gamma story closed"
    status: closed
    priority: low
    estimate_days: 1
    description: ""
    assignee: ""
    acceptance_criteria: []
    tags: []
    created: "2026-03-01"
    updated: "2026-03-01"
    log: []
---

# Test Project Gamma
"""

# A non-project FDO to verify it's excluded from scans
AI_SYSTEMS_FDO = """\
---
id: grim-test-fdo
title: "Non-project FDO"
domain: ai-systems
created: "2026-03-01"
updated: "2026-03-01"
status: developing
confidence: 0.5
related: []
tags: [test]
---

# Non-project FDO
"""


def make_temp_vault(*fdo_contents: str) -> tuple[str, Path]:
    """Create a temp vault with project FDOs. Returns (vault_path, tmp_dir)."""
    tmp = Path(tempfile.mkdtemp(prefix="kronos-test-"))
    (tmp / "projects").mkdir()
    (tmp / "calendar").mkdir()
    for content in fdo_contents:
        fdo_id = None
        domain = "projects"
        for line in content.split("\n"):
            if line.startswith("id: "):
                fdo_id = line.split(": ", 1)[1].strip().strip('"')
            if line.startswith("domain: "):
                domain = line.split(": ", 1)[1].strip().strip('"')
        if not fdo_id:
            continue
        domain_dir = tmp / domain
        domain_dir.mkdir(exist_ok=True)
        (domain_dir / f"{fdo_id}.md").write_text(content, encoding="utf-8")
    # Default board & calendar
    (tmp / "projects" / "board.yaml").write_text(
        "columns:\n  new: []\n  active: []\n  in_progress: []\n  resolved: []\n  closed: []\n",
        encoding="utf-8",
    )
    (tmp / "calendar" / "schedule.yaml").write_text("entries: []\n", encoding="utf-8")
    (tmp / "calendar" / "personal.yaml").write_text("entries: []\n", encoding="utf-8")
    return str(tmp), tmp


def cleanup_vault(tmp: Path):
    """Remove temp vault directory."""
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


# ── TaskEngine Unit Tests ───────────────────────────────────────────────────

class TestTaskEngineStoryCreate(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(MINIMAL_PROJECT_FDO)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_create_story_basic(self):
        result = self.engine.create_story("proj-test-alpha", "My first story")
        self.assertIn("created", result)
        self.assertEqual(result["created"], "story-test-alpha-001")
        self.assertEqual(result["project"], "proj-test-alpha")

    def test_create_story_with_all_fields(self):
        result = self.engine.create_story(
            "proj-test-alpha", "Full story",
            priority="critical", estimate_days=5.0,
            description="Detailed description",
            acceptance_criteria=["AC1", "AC2", "AC3"],
            assignee="code",
            tags=["backend", "urgent"],
        )
        self.assertIn("created", result)
        story = result["story"]
        self.assertEqual(story["priority"], "critical")
        self.assertEqual(story["estimate_days"], 5.0)
        self.assertEqual(story["description"], "Detailed description")
        self.assertEqual(len(story["acceptance_criteria"]), 3)
        self.assertEqual(story["status"], "new")
        self.assertEqual(story["assignee"], "code")
        self.assertIn("Story created", story["log"][0])

    def test_create_story_default_values(self):
        result = self.engine.create_story("proj-test-alpha", "Defaults story")
        story = result["story"]
        self.assertEqual(story["priority"], "medium")
        self.assertEqual(story["estimate_days"], 1.0)
        self.assertEqual(story["description"], "")
        self.assertEqual(story["acceptance_criteria"], [])
        self.assertEqual(story["assignee"], "")

    def test_create_story_invalid_priority(self):
        result = self.engine.create_story("proj-test-alpha", "Bad priority", priority="ultra")
        self.assertIn("error", result)
        self.assertIn("ultra", result["error"])

    def test_create_story_missing_project(self):
        result = self.engine.create_story("proj-nonexistent", "Orphan")
        self.assertIn("error", result)
        self.assertIn("not found", result["error"])

    def test_create_story_sequential_ids(self):
        r1 = self.engine.create_story("proj-test-alpha", "Story 1")
        r2 = self.engine.create_story("proj-test-alpha", "Story 2")
        r3 = self.engine.create_story("proj-test-alpha", "Story 3")
        self.assertEqual(r1["created"], "story-test-alpha-001")
        self.assertEqual(r2["created"], "story-test-alpha-002")
        self.assertEqual(r3["created"], "story-test-alpha-003")

    def test_create_story_with_assignee(self):
        result = self.engine.create_story(
            "proj-test-alpha", "Assigned story", assignee="research"
        )
        self.assertIn("created", result)
        story = result["story"]
        self.assertEqual(story["assignee"], "research")

    def test_create_story_invalid_assignee(self):
        result = self.engine.create_story(
            "proj-test-alpha", "Bad assignee", assignee="invalid_agent"
        )
        self.assertIn("error", result)
        self.assertIn("assignee", result["error"].lower())


class TestTaskEngineStoryWithExisting(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(PROJECT_WITH_STORIES)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_create_story_continues_numbering(self):
        """New stories should continue from existing max ID."""
        result = self.engine.create_story("proj-test-beta", "Third story")
        self.assertEqual(result["created"], "story-test-beta-003")

    def test_get_existing_story(self):
        item = self.engine.get_item("story-test-beta-001")
        self.assertIsNotNone(item)
        self.assertEqual(item["type"], "story")
        self.assertEqual(item["title"], "Pre-existing story")
        self.assertEqual(item["priority"], "high")
        self.assertEqual(item["estimate_days"], 3)
        self.assertEqual(item["project"], "proj-test-beta")
        self.assertEqual(item["assignee"], "code")

    def test_get_existing_story_has_domain(self):
        item = self.engine.get_item("story-test-beta-001")
        self.assertIn("domain", item)
        self.assertEqual(item["domain"], "projects")

    def test_get_nonexistent_story(self):
        item = self.engine.get_item("story-test-beta-999")
        self.assertIsNone(item)

    def test_list_items_all(self):
        items = self.engine.list_items(project_id="proj-test-beta")
        self.assertEqual(len(items), 2)

    def test_list_items_by_status(self):
        items = self.engine.list_items(project_id="proj-test-beta", status="active")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "story-test-beta-001")

    def test_list_items_by_priority(self):
        items = self.engine.list_items(project_id="proj-test-beta", priority="high")
        self.assertEqual(len(items), 1)

    def test_list_items_by_project(self):
        items = self.engine.list_items(project_id="proj-test-beta")
        self.assertEqual(len(items), 2)

    def test_list_items_sorted_by_priority(self):
        """Critical/high should come before medium/low."""
        items = self.engine.list_items(project_id="proj-test-beta")
        priorities = [i["priority"] for i in items]
        self.assertEqual(priorities, ["high", "medium"])

    def test_list_items_empty_filter(self):
        items = self.engine.list_items(project_id="proj-nonexistent")
        self.assertEqual(len(items), 0)

    def test_list_items_has_assignee_and_job_id(self):
        """list_items results should include assignee and job_id fields."""
        items = self.engine.list_items(project_id="proj-test-beta")
        for item in items:
            self.assertIn("assignee", item)
            self.assertIn("job_id", item)
            self.assertIn("domain", item)

    def test_list_items_by_domain(self):
        """Filter stories by domain."""
        items = self.engine.list_items(domain="projects")
        self.assertEqual(len(items), 2)
        items_bad = self.engine.list_items(domain="ai-systems")
        self.assertEqual(len(items_bad), 0)


class TestTaskEngineUpdate(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(PROJECT_WITH_STORIES)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_update_story_priority(self):
        result = self.engine.update_item("story-test-beta-001", {"priority": "critical"})
        self.assertIn("updated", result)
        self.assertIn("priority", result["fields_changed"])

        item = self.engine.get_item("story-test-beta-001")
        self.assertEqual(item["priority"], "critical")

    def test_update_story_status_with_log(self):
        result = self.engine.update_item("story-test-beta-001", {"status": "in_progress"})
        self.assertIn("updated", result)

        item = self.engine.get_item("story-test-beta-001")
        self.assertEqual(item["status"], "in_progress")
        log = item.get("log", [])
        self.assertTrue(any("in_progress" in entry for entry in log))

    def test_update_story_invalid_status(self):
        result = self.engine.update_item("story-test-beta-001", {"status": "bogus"})
        self.assertIn("error", result)

    def test_update_story_invalid_priority(self):
        result = self.engine.update_item("story-test-beta-001", {"priority": "mega"})
        self.assertIn("error", result)

    def test_update_story_multiple_fields(self):
        result = self.engine.update_item("story-test-beta-001", {
            "priority": "low",
            "estimate_days": 10,
            "description": "Updated description",
        })
        self.assertIn("updated", result)
        self.assertEqual(len(result["fields_changed"]), 3)

    def test_update_story_immutable_fields_ignored(self):
        """id, created, log should not be overwritten."""
        result = self.engine.update_item("story-test-beta-001", {
            "id": "story-hacked", "created": "1999-01-01", "title": "New title"
        })
        # Only title should change (id and created are immutable)
        item = self.engine.get_item("story-test-beta-001")
        self.assertEqual(item["id"], "story-test-beta-001")
        self.assertEqual(item["title"], "New title")

    def test_update_nonexistent_story(self):
        result = self.engine.update_item("story-nonexistent-999", {"title": "X"})
        self.assertIn("error", result)

    def test_update_empty_fields(self):
        result = self.engine.update_item("story-test-beta-001", {})
        self.assertIn("error", result)

    def test_update_assignee(self):
        result = self.engine.update_item("story-test-beta-001", {"assignee": "audit"})
        self.assertIn("updated", result)
        self.assertIn("assignee", result["fields_changed"])
        item = self.engine.get_item("story-test-beta-001")
        self.assertEqual(item["assignee"], "audit")
        # Check log entry
        log = item.get("log", [])
        self.assertTrue(any("Assigned" in entry for entry in log))

    def test_update_assignee_invalid(self):
        result = self.engine.update_item("story-test-beta-001", {"assignee": "invalid_agent"})
        self.assertIn("error", result)

    def test_update_assignee_clear(self):
        """Setting assignee to empty string should clear it."""
        result = self.engine.update_item("story-test-beta-001", {"assignee": ""})
        self.assertIn("updated", result)
        item = self.engine.get_item("story-test-beta-001")
        self.assertEqual(item["assignee"], "")

    def test_update_job_id(self):
        result = self.engine.update_item("story-test-beta-001", {"job_id": "job-abc-123"})
        self.assertIn("updated", result)
        self.assertIn("job_id", result["fields_changed"])
        item = self.engine.get_item("story-test-beta-001")
        self.assertEqual(item["job_id"], "job-abc-123")
        # Check log entry
        log = item.get("log", [])
        self.assertTrue(any("pool job" in entry for entry in log))

    def test_update_returns_project(self):
        """Update result should include the project ID."""
        result = self.engine.update_item("story-test-beta-001", {"title": "Updated title"})
        self.assertIn("project", result)
        self.assertEqual(result["project"], "proj-test-beta")


class TestTaskEngineArchive(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(PROJECT_WITH_STORIES)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_archive_no_closed(self):
        result = self.engine.archive_closed("proj-test-beta")
        self.assertEqual(result["archived"], 0)

    def test_archive_closed_stories(self):
        # Close a story first
        self.engine.update_item("story-test-beta-002", {"status": "closed"})
        result = self.engine.archive_closed("proj-test-beta")
        self.assertEqual(result["archived"], 1)
        self.assertIn("proj-test-beta", result["projects"])

        # Verify the closed story is gone from active stories
        items = self.engine.list_items(project_id="proj-test-beta")
        ids = [i["id"] for i in items]
        self.assertNotIn("story-test-beta-002", ids)

    def test_archive_all_projects(self):
        """archive_closed with no proj_id scans all projects."""
        self.engine.update_item("story-test-beta-002", {"status": "closed"})
        result = self.engine.archive_closed()
        self.assertEqual(result["archived"], 1)

    def test_archive_nonexistent_project(self):
        result = self.engine.archive_closed("proj-nonexistent")
        self.assertEqual(result["archived"], 0)


class TestTaskEngineProjectDiscovery(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(
            MINIMAL_PROJECT_FDO, PROJECT_WITH_STORIES, AI_SYSTEMS_FDO
        )
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_get_all_projects(self):
        projects = self.engine.get_all_projects()
        ids = [p["id"] for p in projects]
        self.assertIn("proj-test-alpha", ids)
        self.assertIn("proj-test-beta", ids)
        # Non-project FDOs should not appear
        self.assertNotIn("grim-test-fdo", ids)

    def test_project_summary_includes_story_count(self):
        projects = self.engine.get_all_projects()
        beta = next(p for p in projects if p["id"] == "proj-test-beta")
        self.assertEqual(beta["story_count"], 2)
        self.assertEqual(beta["stories_done"], 0)

    def test_project_summary_includes_domain(self):
        projects = self.engine.get_all_projects()
        alpha = next(p for p in projects if p["id"] == "proj-test-alpha")
        self.assertIn("domain", alpha)
        self.assertEqual(alpha["domain"], "projects")

    def test_legacy_get_all_features_alias(self):
        """get_all_features() should still work as a legacy alias."""
        features = self.engine.get_all_features()
        ids = [f["id"] for f in features]
        self.assertIn("proj-test-alpha", ids)


class TestTaskEngineThreadSafety(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(MINIMAL_PROJECT_FDO)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_concurrent_story_creates(self):
        """5 concurrent story creates should produce unique sequential IDs."""
        results = []

        def create(i):
            return self.engine.create_story("proj-test-alpha", f"Concurrent {i}")

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(create, i) for i in range(5)]
            for f in futures:
                results.append(f.result(timeout=10))

        ids = [r["created"] for r in results if "created" in r]
        self.assertEqual(len(ids), 5, f"Expected 5 created, got {len(ids)}")
        self.assertEqual(len(set(ids)), 5, "Duplicate IDs detected!")


# ── Assignee Tests ──────────────────────────────────────────────────────────

class TestTaskEngineAssignee(TestCase):
    """Test assignee field validation and behavior."""

    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(MINIMAL_PROJECT_FDO)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_valid_assignees_constant(self):
        """VALID_ASSIGNEES should include expected agent types."""
        self.assertIn("code", VALID_ASSIGNEES)
        self.assertIn("research", VALID_ASSIGNEES)
        self.assertIn("audit", VALID_ASSIGNEES)
        self.assertIn("plan", VALID_ASSIGNEES)
        self.assertIn("", VALID_ASSIGNEES)

    def test_create_with_each_valid_assignee(self):
        for assignee in sorted(VALID_ASSIGNEES - {""}):
            result = self.engine.create_story(
                "proj-test-alpha", f"Story for {assignee}", assignee=assignee,
            )
            self.assertIn("created", result, f"Failed for assignee={assignee}")
            story = result["story"]
            self.assertEqual(story["assignee"], assignee)

    def test_create_with_empty_assignee(self):
        result = self.engine.create_story("proj-test-alpha", "Unassigned story", assignee="")
        self.assertIn("created", result)
        self.assertEqual(result["story"]["assignee"], "")

    def test_create_rejects_invalid_assignee(self):
        result = self.engine.create_story(
            "proj-test-alpha", "Bad agent", assignee="copilot"
        )
        self.assertIn("error", result)
        self.assertIn("valid", result)

    def test_update_assignee_logs_change(self):
        result = self.engine.create_story("proj-test-alpha", "Story to reassign")
        story_id = result["created"]
        self.engine.update_item(story_id, {"assignee": "audit"})
        item = self.engine.get_item(story_id)
        self.assertEqual(item["assignee"], "audit")
        log = item.get("log", [])
        self.assertTrue(any("Assigned" in entry and "audit" in entry for entry in log))

    def test_update_assignee_unassign_logs(self):
        result = self.engine.create_story(
            "proj-test-alpha", "Assigned then cleared", assignee="code"
        )
        story_id = result["created"]
        self.engine.update_item(story_id, {"assignee": ""})
        item = self.engine.get_item(story_id)
        self.assertEqual(item["assignee"], "")
        log = item.get("log", [])
        self.assertTrue(any("unassigned" in entry for entry in log))

    def test_get_item_includes_assignee(self):
        result = self.engine.create_story(
            "proj-test-alpha", "Check get_item", assignee="research"
        )
        item = self.engine.get_item(result["created"])
        self.assertEqual(item["assignee"], "research")


# ── Job ID Tests ────────────────────────────────────────────────────────────

class TestTaskEngineJobId(TestCase):
    """Test job_id field for pool integration."""

    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(MINIMAL_PROJECT_FDO)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_new_story_has_no_job_id(self):
        result = self.engine.create_story("proj-test-alpha", "No job yet")
        item = self.engine.get_item(result["created"])
        # job_id may be None or absent
        self.assertFalse(item.get("job_id"))

    def test_set_job_id(self):
        result = self.engine.create_story("proj-test-alpha", "Will get a job")
        story_id = result["created"]
        update = self.engine.update_item(story_id, {"job_id": "job-pool-001"})
        self.assertIn("updated", update)
        item = self.engine.get_item(story_id)
        self.assertEqual(item["job_id"], "job-pool-001")

    def test_job_id_in_log(self):
        result = self.engine.create_story("proj-test-alpha", "Job log test")
        story_id = result["created"]
        self.engine.update_item(story_id, {"job_id": "job-xyz"})
        item = self.engine.get_item(story_id)
        log = item.get("log", [])
        self.assertTrue(any("job-xyz" in entry for entry in log))

    def test_job_id_in_list_items(self):
        result = self.engine.create_story("proj-test-alpha", "Job in list")
        story_id = result["created"]
        self.engine.update_item(story_id, {"job_id": "job-list-001"})
        items = self.engine.list_items(project_id="proj-test-alpha")
        found = next(i for i in items if i["id"] == story_id)
        self.assertEqual(found["job_id"], "job-list-001")


# ── Domain Tests ────────────────────────────────────────────────────────────

class TestTaskEngineDomain(TestCase):
    """Test domain field in story results."""

    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(
            MINIMAL_PROJECT_FDO, PROJECT_WITH_STORIES
        )
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_get_item_includes_domain(self):
        item = self.engine.get_item("story-test-beta-001")
        self.assertIn("domain", item)
        self.assertEqual(item["domain"], "projects")

    def test_list_items_includes_domain(self):
        items = self.engine.list_items(project_id="proj-test-beta")
        for item in items:
            self.assertIn("domain", item)
            self.assertEqual(item["domain"], "projects")

    def test_list_items_domain_filter(self):
        items = self.engine.list_items(domain="projects")
        self.assertGreater(len(items), 0)
        for item in items:
            self.assertEqual(item["domain"], "projects")

    def test_list_items_domain_filter_empty(self):
        items = self.engine.list_items(domain="physics")
        self.assertEqual(len(items), 0)

    def test_batch_includes_domain(self):
        result = self.engine.get_items_batch(["story-test-beta-001"])
        story = result["story-test-beta-001"]
        self.assertIn("domain", story)
        self.assertEqual(story["domain"], "projects")


# ── BoardEngine Unit Tests ──────────────────────────────────────────────────

class TestBoardEngineBasic(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(PROJECT_WITH_STORIES)
        self.engine = TaskEngine(self.vault_path)
        self.board = BoardEngine(self.vault_path, self.engine)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_add_to_board(self):
        result = self.board.add_to_board("story-test-beta-001")
        self.assertEqual(result["added"], "story-test-beta-001")
        self.assertEqual(result["column"], "new")

    def test_add_to_board_specific_column(self):
        result = self.board.add_to_board("story-test-beta-001", "active")
        self.assertEqual(result["column"], "active")

    def test_add_to_board_invalid_column(self):
        result = self.board.add_to_board("story-test-beta-001", "invalid")
        self.assertIn("error", result)

    def test_add_to_board_nonexistent_story(self):
        result = self.board.add_to_board("story-nonexistent-999")
        self.assertIn("error", result)

    def test_add_to_board_duplicate(self):
        self.board.add_to_board("story-test-beta-001")
        result = self.board.add_to_board("story-test-beta-001")
        self.assertIn("error", result)
        self.assertIn("already on board", result["error"])

    def test_remove_from_board(self):
        self.board.add_to_board("story-test-beta-001", "active")
        result = self.board.remove_from_board("story-test-beta-001")
        self.assertEqual(result["removed"], "story-test-beta-001")
        self.assertEqual(result["was_in"], "active")

    def test_remove_nonexistent(self):
        result = self.board.remove_from_board("story-nonexistent")
        self.assertIn("error", result)


class TestBoardEngineMove(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(PROJECT_WITH_STORIES)
        self.engine = TaskEngine(self.vault_path)
        self.board = BoardEngine(self.vault_path, self.engine)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_move_adds_if_not_on_board(self):
        result = self.board.move_story("story-test-beta-001", "active")
        self.assertEqual(result["to"], "active")
        self.assertIsNone(result["from"])

    def test_move_between_columns(self):
        self.board.move_story("story-test-beta-001", "new")
        result = self.board.move_story("story-test-beta-001", "in_progress")
        self.assertEqual(result["from"], "new")
        self.assertEqual(result["to"], "in_progress")

    def test_move_updates_story_status(self):
        self.board.move_story("story-test-beta-001", "in_progress")
        item = self.engine.get_item("story-test-beta-001")
        self.assertEqual(item["status"], "in_progress")

    def test_move_through_all_columns(self):
        """Story should be moveable through all 5 columns."""
        sid = "story-test-beta-001"
        for col in COLUMNS:
            result = self.board.move_story(sid, col)
            self.assertEqual(result["to"], col)
            item = self.engine.get_item(sid)
            self.assertEqual(item["status"], COLUMN_STATUS[col])

    def test_move_invalid_column(self):
        result = self.board.move_story("story-test-beta-001", "wontfix")
        self.assertIn("error", result)

    def test_move_same_column(self):
        """Moving to same column should still work (idempotent)."""
        self.board.move_story("story-test-beta-001", "active")
        result = self.board.move_story("story-test-beta-001", "active")
        self.assertEqual(result["to"], "active")


class TestBoardEngineViews(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(PROJECT_WITH_STORIES)
        self.engine = TaskEngine(self.vault_path)
        self.board = BoardEngine(self.vault_path, self.engine)
        # Put stories on board
        self.board.move_story("story-test-beta-001", "in_progress")
        self.board.move_story("story-test-beta-002", "new")

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_board_view_structure(self):
        view = self.board.board_view()
        self.assertIn("columns", view)
        self.assertIn("total_stories", view)
        for col in COLUMNS:
            self.assertIn(col, view["columns"])

    def test_board_view_enriched_data(self):
        view = self.board.board_view()
        ip_stories = view["columns"]["in_progress"]
        self.assertEqual(len(ip_stories), 1)
        story = ip_stories[0]
        self.assertEqual(story["id"], "story-test-beta-001")
        self.assertEqual(story["title"], "Pre-existing story")
        self.assertEqual(story["priority"], "high")
        self.assertIn("project", story)
        self.assertIn("domain", story)
        self.assertIn("assignee", story)

    def test_board_view_total(self):
        view = self.board.board_view()
        self.assertEqual(view["total_stories"], 2)

    def test_board_view_project_filter(self):
        view = self.board.board_view(project_id="proj-test-beta")
        self.assertEqual(view["total_stories"], 2)

        view2 = self.board.board_view(project_id="proj-nonexistent")
        self.assertEqual(view2["total_stories"], 0)

    def test_backlog_view(self):
        """Backlog should exclude stories already on board."""
        backlog = self.board.backlog_view(project_id="proj-test-beta")
        self.assertEqual(backlog["count"], 0)  # Both stories are on board

    def test_backlog_view_with_backlog_items(self):
        """Create a 3rd story that's NOT on the board."""
        self.engine.create_story("proj-test-beta", "Backlog story")
        backlog = self.board.backlog_view(project_id="proj-test-beta")
        self.assertEqual(backlog["count"], 1)
        self.assertEqual(backlog["backlog"][0]["id"], "story-test-beta-003")

    def test_get_board_story_ids(self):
        ids = self.board.get_board_story_ids()
        self.assertEqual(len(ids), 2)
        self.assertIn("story-test-beta-001", ids)
        self.assertIn("story-test-beta-002", ids)

    def test_get_board_story_ids_filtered(self):
        ids = self.board.get_board_story_ids(columns=["in_progress"])
        self.assertEqual(ids, ["story-test-beta-001"])

    def test_board_empty_view(self):
        """Empty board should have 0 total."""
        fresh_board = BoardEngine(self.vault_path, self.engine)
        # Reset board
        fresh_board._save_board(fresh_board._default_board())
        view = fresh_board.board_view()
        self.assertEqual(view["total_stories"], 0)

    def test_board_view_domain_filter(self):
        """Board view with domain filter should only include matching stories."""
        view = self.board.board_view(domain="projects")
        self.assertEqual(view["total_stories"], 2)

        view2 = self.board.board_view(domain="ai-systems")
        self.assertEqual(view2["total_stories"], 0)


# ── CalendarEngine Unit Tests ───────────────────────────────────────────────

class TestCalendarPersonalEvents(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(PROJECT_WITH_STORIES)
        self.engine = TaskEngine(self.vault_path)
        self.board = BoardEngine(self.vault_path, self.engine)
        self.calendar = CalendarEngine(self.vault_path, self.board)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_add_personal_basic(self):
        result = self.calendar.add_personal("Dentist", "2026-03-15")
        self.assertEqual(result["created"], "personal-001")
        self.assertEqual(result["event"]["title"], "Dentist")
        self.assertEqual(result["event"]["date"], "2026-03-15")

    def test_add_personal_full(self):
        result = self.calendar.add_personal(
            "Meeting", "2026-03-20",
            time="14:00", duration_hours=1.5,
            recurring=True, notes="Weekly standup"
        )
        event = result["event"]
        self.assertEqual(event["time"], "14:00")
        self.assertEqual(event["duration_hours"], 1.5)
        self.assertTrue(event["recurring"])
        self.assertEqual(event["notes"], "Weekly standup")

    def test_add_personal_sequential_ids(self):
        r1 = self.calendar.add_personal("Event 1", "2026-03-10")
        r2 = self.calendar.add_personal("Event 2", "2026-03-11")
        r3 = self.calendar.add_personal("Event 3", "2026-03-12")
        self.assertEqual(r1["created"], "personal-001")
        self.assertEqual(r2["created"], "personal-002")
        self.assertEqual(r3["created"], "personal-003")

    def test_update_personal(self):
        self.calendar.add_personal("Dentist", "2026-03-15", time="10:00")
        result = self.calendar.update_personal("personal-001", {"time": "14:00", "notes": "Rescheduled"})
        self.assertIn("updated", result)
        self.assertEqual(len(result["fields_changed"]), 2)

    def test_update_personal_id_immutable(self):
        self.calendar.add_personal("Event", "2026-03-15")
        result = self.calendar.update_personal("personal-001", {"id": "hacked"})
        # id field should be skipped
        self.assertNotIn("id", result.get("fields_changed", []))

    def test_update_nonexistent(self):
        result = self.calendar.update_personal("personal-999", {"notes": "X"})
        self.assertIn("error", result)

    def test_delete_personal(self):
        self.calendar.add_personal("Temp event", "2026-03-15")
        result = self.calendar.delete_personal("personal-001")
        self.assertEqual(result["deleted"], "personal-001")

    def test_delete_nonexistent(self):
        result = self.calendar.delete_personal("personal-999")
        self.assertIn("error", result)


class TestCalendarSync(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(PROJECT_WITH_STORIES)
        self.engine = TaskEngine(self.vault_path)
        self.board = BoardEngine(self.vault_path, self.engine)
        self.calendar = CalendarEngine(self.vault_path, self.board)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_sync_empty_board(self):
        result = self.calendar.sync_schedule("2026-03-01")
        self.assertEqual(result["synced"], 0)

    def test_sync_with_active_stories(self):
        self.board.move_story("story-test-beta-001", "active")
        result = self.calendar.sync_schedule("2026-03-03")
        self.assertEqual(result["synced"], 1)
        entry = result["schedule"]["entries"][0]
        self.assertEqual(entry["story_id"], "story-test-beta-001")
        self.assertEqual(entry["start_date"], "2026-03-03")
        self.assertEqual(entry["estimate_days"], 3)

    def test_sync_includes_assignee_and_domain(self):
        """Sync entries should include assignee and domain fields."""
        self.board.move_story("story-test-beta-001", "active")
        result = self.calendar.sync_schedule("2026-03-03")
        entry = result["schedule"]["entries"][0]
        self.assertIn("assignee", entry)
        self.assertIn("domain", entry)
        self.assertEqual(entry["assignee"], "code")
        self.assertEqual(entry["domain"], "projects")

    def test_sync_priority_ordering(self):
        """Higher priority stories should come first in schedule."""
        self.board.move_story("story-test-beta-001", "active")  # high
        self.board.move_story("story-test-beta-002", "active")  # medium
        result = self.calendar.sync_schedule("2026-03-03")
        self.assertEqual(result["synced"], 2)
        entries = result["schedule"]["entries"]
        self.assertEqual(entries[0]["story_id"], "story-test-beta-001")  # high first
        self.assertEqual(entries[1]["story_id"], "story-test-beta-002")  # medium second

    def test_sync_sequential_dates(self):
        """Stories should be sequenced — 2nd starts after 1st ends."""
        self.board.move_story("story-test-beta-001", "active")  # 3 days
        self.board.move_story("story-test-beta-002", "active")  # 1 day
        result = self.calendar.sync_schedule("2026-03-02")  # Monday
        entries = result["schedule"]["entries"]
        self.assertNotEqual(entries[0]["end_date"], entries[1]["start_date"])

    def test_sync_in_progress_included(self):
        self.board.move_story("story-test-beta-001", "in_progress")
        result = self.calendar.sync_schedule("2026-03-02")
        self.assertEqual(result["synced"], 1)

    def test_sync_resolved_excluded(self):
        """Resolved/closed stories should NOT appear in sync."""
        self.board.move_story("story-test-beta-001", "resolved")
        result = self.calendar.sync_schedule("2026-03-02")
        self.assertEqual(result["synced"], 0)


class TestCalendarView(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(PROJECT_WITH_STORIES)
        self.engine = TaskEngine(self.vault_path)
        self.board = BoardEngine(self.vault_path, self.engine)
        self.calendar = CalendarEngine(self.vault_path, self.board)
        # Add some data
        self.board.move_story("story-test-beta-001", "active")
        self.calendar.sync_schedule("2026-03-03")
        self.calendar.add_personal("Dentist", "2026-03-15", time="14:00")

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_view_full_range(self):
        view = self.calendar.calendar_view("2026-03-01", "2026-03-31")
        self.assertGreater(view["total"], 0)

    def test_view_work_entries(self):
        view = self.calendar.calendar_view("2026-03-01", "2026-03-31")
        work = [e for e in view["entries"] if e["type"] == "work"]
        self.assertGreater(len(work), 0)

    def test_view_personal_entries(self):
        view = self.calendar.calendar_view("2026-03-01", "2026-03-31")
        personal = [e for e in view["entries"] if e["type"] == "personal"]
        self.assertEqual(len(personal), 1)
        self.assertEqual(personal[0]["title"], "Dentist")

    def test_view_exclude_personal(self):
        view = self.calendar.calendar_view("2026-03-01", "2026-03-31", include_personal=False)
        personal = [e for e in view["entries"] if e["type"] == "personal"]
        self.assertEqual(len(personal), 0)

    def test_view_date_filtering(self):
        """Only events in range should appear."""
        view = self.calendar.calendar_view("2026-04-01", "2026-04-30")
        self.assertEqual(view["total"], 0)

    def test_view_sorted_by_date(self):
        """Entries should be sorted by date."""
        view = self.calendar.calendar_view("2026-03-01", "2026-03-31")
        dates = [e.get("start_date", e.get("date")) for e in view["entries"]]
        self.assertEqual(dates, sorted(dates))


# ── Date helper tests ───────────────────────────────────────────────────────

class TestDateHelpers(TestCase):
    def test_parse_date_valid(self):
        self.assertEqual(_parse_date("2026-03-15"), date(2026, 3, 15))

    def test_parse_date_none(self):
        self.assertIsNone(_parse_date(None))

    def test_parse_date_empty(self):
        self.assertIsNone(_parse_date(""))

    def test_parse_date_invalid(self):
        self.assertIsNone(_parse_date("not-a-date"))

    def test_add_workdays_simple(self):
        # Monday March 2, 2026 + 3 workdays = Thursday March 5
        result = _add_workdays(date(2026, 3, 2), 3)
        self.assertEqual(result, date(2026, 3, 5))

    def test_add_workdays_over_weekend(self):
        # Thursday March 5 + 2 workdays = Monday March 9 (skip Sat/Sun)
        result = _add_workdays(date(2026, 3, 5), 2)
        self.assertEqual(result, date(2026, 3, 9))

    def test_add_workdays_zero(self):
        result = _add_workdays(date(2026, 3, 2), 0)
        self.assertEqual(result, date(2026, 3, 2))

    def test_add_workdays_fractional(self):
        """Fractional days round up to whole day."""
        result = _add_workdays(date(2026, 3, 2), 0.5)
        self.assertEqual(result, date(2026, 3, 3))

    def test_add_workdays_negative(self):
        result = _add_workdays(date(2026, 3, 2), -1)
        self.assertEqual(result, date(2026, 3, 2))

    def test_add_workdays_start_on_friday(self):
        # Friday March 6 + 1 workday = Monday March 9
        result = _add_workdays(date(2026, 3, 6), 1)
        self.assertEqual(result, date(2026, 3, 9))


# ── Batch Loading Tests ──────────────────────────────────────────────────────

class TestTaskEngineBatch(TestCase):
    """Tests for get_items_batch() and _scan_all_projects()."""

    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(
            MINIMAL_PROJECT_FDO, PROJECT_WITH_STORIES, PROJECT_WITH_CLOSED
        )
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_batch_empty_list(self):
        result = self.engine.get_items_batch([])
        self.assertEqual(result, {})

    def test_batch_single_story(self):
        result = self.engine.get_items_batch(["story-test-beta-001"])
        self.assertIn("story-test-beta-001", result)
        story = result["story-test-beta-001"]
        self.assertEqual(story["title"], "Pre-existing story")
        self.assertEqual(story["priority"], "high")
        self.assertEqual(story["project"], "proj-test-beta")
        self.assertIn("domain", story)
        self.assertIn("assignee", story)

    def test_batch_multiple_stories(self):
        ids = ["story-test-beta-001", "story-test-beta-002", "story-test-gamma-001"]
        result = self.engine.get_items_batch(ids)
        self.assertEqual(len(result), 3)
        self.assertIn("story-test-beta-001", result)
        self.assertIn("story-test-beta-002", result)
        self.assertIn("story-test-gamma-001", result)

    def test_batch_missing_story(self):
        result = self.engine.get_items_batch(["story-nonexistent-999"])
        self.assertEqual(len(result), 0)

    def test_batch_mixed_found_and_missing(self):
        ids = ["story-test-beta-001", "story-nonexistent-999", "story-test-gamma-001"]
        result = self.engine.get_items_batch(ids)
        self.assertEqual(len(result), 2)
        self.assertIn("story-test-beta-001", result)
        self.assertIn("story-test-gamma-001", result)
        self.assertNotIn("story-nonexistent-999", result)

    def test_batch_story_fields(self):
        """Batch results should include all expected fields."""
        result = self.engine.get_items_batch(["story-test-gamma-001"])
        story = result["story-test-gamma-001"]
        self.assertEqual(story["assignee"], "research")
        self.assertIn("job_id", story)
        self.assertIn("domain", story)
        self.assertIn("project", story)

    def test_batch_closed_story(self):
        result = self.engine.get_items_batch(["story-test-gamma-002"])
        story = result["story-test-gamma-002"]
        self.assertEqual(story["status"], "closed")
        self.assertEqual(story["priority"], "low")

    def test_scan_all_projects(self):
        results = self.engine._scan_all_projects()
        proj_ids = {fm["id"] for fm, _, _ in results}
        self.assertIn("proj-test-alpha", proj_ids)
        self.assertIn("proj-test-beta", proj_ids)
        self.assertIn("proj-test-gamma", proj_ids)

    def test_batch_early_exit(self):
        """When all requested stories are found, scanning should stop early."""
        # Just verify correctness — the early-exit optimization is internal
        result = self.engine.get_items_batch(["story-test-beta-001"])
        self.assertEqual(len(result), 1)


class TestBoardEngineBatch(TestCase):
    """Tests that board_view and backlog_view use batch loading correctly."""

    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(
            MINIMAL_PROJECT_FDO, PROJECT_WITH_STORIES, PROJECT_WITH_CLOSED
        )
        self.task_engine = TaskEngine(self.vault_path)
        self.board_engine = BoardEngine(self.vault_path, self.task_engine)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_board_view_empty_board(self):
        """Empty board should return valid structure with all columns."""
        result = self.board_engine.board_view()
        self.assertIn("columns", result)
        for col in COLUMNS:
            self.assertIn(col, result["columns"])
            self.assertIsInstance(result["columns"][col], list)
        self.assertEqual(result["total_stories"], 0)

    def test_board_view_with_stories(self):
        """Board view with stories should enrich them via batch loading."""
        self.board_engine.move_story("story-test-beta-001", "active")
        self.board_engine.move_story("story-test-gamma-001", "in_progress")

        result = self.board_engine.board_view()
        self.assertEqual(result["total_stories"], 2)

        # Active column should have beta-001
        active = result["columns"]["active"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["id"], "story-test-beta-001")
        self.assertEqual(active[0]["title"], "Pre-existing story")
        self.assertIn("project", active[0])
        self.assertIn("domain", active[0])
        self.assertIn("assignee", active[0])

        # In progress should have gamma-001
        in_prog = result["columns"]["in_progress"]
        self.assertEqual(len(in_prog), 1)
        self.assertEqual(in_prog[0]["id"], "story-test-gamma-001")

    def test_board_view_missing_story(self):
        """Board with a story ID that doesn't exist should include error entry."""
        import yaml
        board_path = self.tmp / "projects" / "board.yaml"
        board_data = yaml.safe_load(board_path.read_text(encoding="utf-8"))
        board_data["columns"]["new"].append("story-nonexistent-999")
        board_path.write_text(yaml.dump(board_data), encoding="utf-8")

        result = self.board_engine.board_view()
        new_col = result["columns"]["new"]
        self.assertEqual(len(new_col), 1)
        self.assertEqual(new_col[0]["id"], "story-nonexistent-999")
        self.assertIn("error", new_col[0])

    def test_board_view_project_filter(self):
        """Board view with project filter should only include matching stories."""
        self.board_engine.move_story("story-test-beta-001", "active")
        self.board_engine.move_story("story-test-gamma-001", "active")

        # Both are from different projects
        result = self.board_engine.board_view(project_id="proj-test-beta")
        active = result["columns"]["active"]
        beta_stories = [s for s in active if s.get("project") == "proj-test-beta"]
        self.assertEqual(len(beta_stories), 1)

    def test_board_view_returns_all_columns(self):
        """board_view always returns all 5 columns even if empty."""
        result = self.board_engine.board_view()
        self.assertEqual(set(result["columns"].keys()), set(COLUMNS))

    def test_backlog_view_excludes_board_stories(self):
        """Backlog should not include stories already on the board."""
        self.board_engine.move_story("story-test-beta-001", "active")

        result = self.board_engine.backlog_view()
        backlog_ids = {s["id"] for s in result["backlog"]}
        self.assertNotIn("story-test-beta-001", backlog_ids)
        # Other stories should be in backlog
        self.assertIn("story-test-beta-002", backlog_ids)


# ── Draft Status Tests ──────────────────────────────────────────────────────

class TestDraftStatus(TestCase):
    """Test draft status for AI-created items."""

    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(MINIMAL_PROJECT_FDO)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_draft_in_valid_statuses(self):
        """Draft should be a valid status."""
        self.assertIn("draft", VALID_STATUSES)

    def test_create_story_with_draft_status(self):
        """Creating a story with status=draft should work."""
        result = self.engine.create_story(
            "proj-test-alpha", "Draft story", priority="medium",
            estimate_days=1, status="draft",
        )
        self.assertIn("created", result)
        story = self.engine.get_item(result["created"])
        self.assertEqual(story["status"], "draft")

    def test_create_story_default_status_is_new(self):
        """Default status for story creation should be 'new'."""
        result = self.engine.create_story(
            "proj-test-alpha", "Normal story", priority="medium",
            estimate_days=1,
        )
        story = self.engine.get_item(result["created"])
        self.assertEqual(story["status"], "new")

    def test_create_story_with_created_by(self):
        """Stories should track who created them."""
        result = self.engine.create_story(
            "proj-test-alpha", "Agent story", priority="medium",
            estimate_days=1, created_by="agent:planning",
        )
        story = self.engine.get_item(result["created"])
        self.assertEqual(story["created_by"], "agent:planning")

    def test_create_story_default_created_by_is_human(self):
        """Default created_by should be 'human'."""
        result = self.engine.create_story(
            "proj-test-alpha", "Human story", priority="medium",
            estimate_days=1,
        )
        story = self.engine.get_item(result["created"])
        self.assertEqual(story["created_by"], "human")

    def test_create_story_rejects_invalid_status(self):
        """Creating with an invalid status should fail."""
        result = self.engine.create_story(
            "proj-test-alpha", "Bad status", priority="medium",
            estimate_days=1, status="garbage",
        )
        self.assertIn("error", result)

    def test_draft_regression_blocked(self):
        """Cannot set status back to draft once promoted."""
        result = self.engine.create_story(
            "proj-test-alpha", "Story to promote", priority="medium",
            estimate_days=1, status="draft",
        )
        story_id = result["created"]
        # Promote to new
        self.engine.update_item(story_id, {"status": "new"})
        story = self.engine.get_item(story_id)
        self.assertEqual(story["status"], "new")
        # Try to regress to draft
        result = self.engine.update_item(story_id, {"status": "draft"})
        self.assertIn("error", result)

    def test_draft_to_draft_noop(self):
        """Setting draft to draft should be allowed (no-op)."""
        result = self.engine.create_story(
            "proj-test-alpha", "Draft story", priority="medium",
            estimate_days=1, status="draft",
        )
        story_id = result["created"]
        result = self.engine.update_item(story_id, {"status": "draft"})
        # Should not error (it's a no-op, status stays draft)
        self.assertNotIn("error", result)


# ── Validation Tests ────────────────────────────────────────────────────────

class TestValidateStoryCreation(TestCase):
    """Test validate_story_creation() pre-creation warnings."""

    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(MINIMAL_PROJECT_FDO)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_short_title_warning(self):
        """Titles under 10 chars should trigger a warning."""
        warnings = self.engine.validate_story_creation("proj-test-alpha", "Short")
        self.assertTrue(any("title" in w.lower() for w in warnings))

    def test_good_title_no_warning(self):
        """A good title should not trigger title warning."""
        warnings = self.engine.validate_story_creation(
            "proj-test-alpha", "A good descriptive story title",
        )
        self.assertFalse(any("title" in w.lower() for w in warnings))

    def test_duplicate_title_warning(self):
        """Creating a story with a duplicate title should warn."""
        self.engine.create_story(
            "proj-test-alpha", "Unique story title here", priority="medium",
            estimate_days=1,
        )
        warnings = self.engine.validate_story_creation(
            "proj-test-alpha", "Unique story title here",
        )
        self.assertTrue(any("duplicate" in w.lower() for w in warnings))

    def test_large_estimate_warning(self):
        """Estimate >10 days should suggest breaking into smaller stories."""
        warnings = self.engine.validate_story_creation(
            "proj-test-alpha", "A very large story", estimate_days=15,
        )
        self.assertTrue(any("large" in w.lower() for w in warnings))

    def test_all_clear_returns_empty(self):
        """No warnings when everything is fine."""
        warnings = self.engine.validate_story_creation(
            "proj-test-alpha", "A perfectly normal story", estimate_days=3,
        )
        self.assertEqual(warnings, [])


# ── Archived ID Collision Tests ─────────────────────────────────────────────

class TestArchivedIdCollision(TestCase):
    """Test that _next_story_id checks archived stories."""

    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(MINIMAL_PROJECT_FDO)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def _archive_a_story(self):
        """Create a story, close it, and archive it."""
        result = self.engine.create_story(
            "proj-test-alpha", "Story to archive", priority="medium",
            estimate_days=1,
        )
        story_id = result["created"]
        self.engine.update_item(story_id, {"status": "closed"})
        self.engine.archive_closed("proj-test-alpha")
        return story_id

    def test_next_id_skips_archived(self):
        """New story ID should not collide with archived story IDs."""
        archived_id = self._archive_a_story()
        result = self.engine.create_story(
            "proj-test-alpha", "New story after archive", priority="medium",
            estimate_days=1,
        )
        self.assertIn("created", result)
        new_id = result["created"]
        self.assertNotEqual(new_id, archived_id)


# ── Board Draft Guard Tests ─────────────────────────────────────────────────

class TestBoardDraftGuard(TestCase):
    """Test board rejects draft stories."""

    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(MINIMAL_PROJECT_FDO)
        self.engine = TaskEngine(self.vault_path)
        self.board_engine = BoardEngine(self.vault_path, self.engine)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_board_add_rejects_draft(self):
        """add_to_board should reject draft stories."""
        result = self.engine.create_story(
            "proj-test-alpha", "Draft story for board", priority="medium",
            estimate_days=1, status="draft",
        )
        story_id = result["created"]
        board_result = self.board_engine.add_to_board(story_id)
        self.assertIn("error", board_result)
        self.assertIn("draft", board_result["error"].lower())

    def test_board_move_rejects_draft(self):
        """move_story should reject draft stories not on board."""
        result = self.engine.create_story(
            "proj-test-alpha", "Draft story for move", priority="medium",
            estimate_days=1, status="draft",
        )
        story_id = result["created"]
        move_result = self.board_engine.move_story(story_id, "new")
        self.assertIn("error", move_result)
        self.assertIn("draft", move_result["error"].lower())

    def test_promote_then_board_succeeds(self):
        """After promoting draft to new, board should accept."""
        result = self.engine.create_story(
            "proj-test-alpha", "Draft to promote", priority="medium",
            estimate_days=1, status="draft",
        )
        story_id = result["created"]
        # Promote
        self.engine.update_item(story_id, {"status": "new"})
        # Now board should accept
        board_result = self.board_engine.move_story(story_id, "new")
        self.assertNotIn("error", board_result)

    def test_created_by_persists_through_get(self):
        """created_by should be visible when fetching the story."""
        result = self.engine.create_story(
            "proj-test-alpha", "Tracked story", priority="medium",
            estimate_days=1, created_by="agent:memory",
        )
        story = self.engine.get_item(result["created"])
        self.assertEqual(story["created_by"], "agent:memory")


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
