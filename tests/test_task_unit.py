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
PRIORITY_ORDER = _tasks_mod.PRIORITY_ORDER
BoardEngine = _board_mod.BoardEngine
COLUMNS = _board_mod.COLUMNS
COLUMN_STATUS = _board_mod.COLUMN_STATUS
CalendarEngine = _calendar_mod.CalendarEngine
_parse_date = _calendar_mod._parse_date
_add_workdays = _calendar_mod._add_workdays


# ── Fixtures ────────────────────────────────────────────────────────────────

MINIMAL_FEATURE_FDO = """\
---
id: feat-test-alpha
title: "Test Feature Alpha"
domain: ai-systems
created: "2026-03-01"
updated: "2026-03-01"
status: developing
confidence: 0.5
related:
  - proj-test
tags: [test]
stories: []
---

# Test Feature Alpha

## Summary
A minimal feature for unit tests.
"""

FEATURE_WITH_STORIES = """\
---
id: feat-test-beta
title: "Test Feature Beta"
domain: ai-systems
created: "2026-03-01"
updated: "2026-03-01"
status: developing
confidence: 0.7
related:
  - proj-test
tags: [test]
stories:
  - id: story-test-beta-001
    title: "Pre-existing story"
    status: active
    priority: high
    estimate_days: 3
    description: "A story that already exists"
    acceptance_criteria:
      - "Criterion 1"
    tasks:
      - id: task-001
        title: "Existing task"
        status: new
        estimate_days: 1
        assignee: ""
        notes: ""
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
    acceptance_criteria: []
    tasks: []
    tags: []
    created: "2026-03-01"
    updated: "2026-03-01"
    log:
      - "2026-03-01: Story created"
---

# Test Feature Beta

## Summary
Feature with pre-existing stories for testing.
"""

NON_FEATURE_FDO = """\
---
id: proj-test
title: "Test Project"
domain: projects
created: "2026-03-01"
updated: "2026-03-01"
status: developing
confidence: 0.5
related:
  - feat-test-alpha
tags: [test]
---

# Test Project
"""


def make_temp_vault(*features: str) -> tuple[str, Path]:
    """Create a temp vault with feature FDOs. Returns (vault_path, tmp_dir)."""
    tmp = Path(tempfile.mkdtemp(prefix="kronos-test-"))
    (tmp / "ai-systems").mkdir()
    (tmp / "projects").mkdir()
    (tmp / "calendar").mkdir()
    for feat_content in features:
        # Extract ID from frontmatter
        for line in feat_content.split("\n"):
            if line.startswith("id: "):
                fdo_id = line.split(": ", 1)[1].strip().strip('"')
                break
        if fdo_id.startswith("feat-"):
            (tmp / "ai-systems" / f"{fdo_id}.md").write_text(feat_content, encoding="utf-8")
        elif fdo_id.startswith("proj-"):
            (tmp / "projects" / f"{fdo_id}.md").write_text(feat_content, encoding="utf-8")
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
        self.vault_path, self.tmp = make_temp_vault(MINIMAL_FEATURE_FDO, NON_FEATURE_FDO)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_create_story_basic(self):
        result = self.engine.create_story("feat-test-alpha", "My first story")
        self.assertIn("created", result)
        self.assertEqual(result["created"], "story-test-alpha-001")
        self.assertEqual(result["feature"], "feat-test-alpha")

    def test_create_story_with_all_fields(self):
        result = self.engine.create_story(
            "feat-test-alpha", "Full story",
            priority="critical", estimate_days=5.0,
            description="Detailed description",
            acceptance_criteria=["AC1", "AC2", "AC3"],
            tags=["backend", "urgent"],
        )
        self.assertIn("created", result)
        story = result["story"]
        self.assertEqual(story["priority"], "critical")
        self.assertEqual(story["estimate_days"], 5.0)
        self.assertEqual(story["description"], "Detailed description")
        self.assertEqual(len(story["acceptance_criteria"]), 3)
        self.assertEqual(story["status"], "new")
        self.assertIn("Story created", story["log"][0])

    def test_create_story_default_values(self):
        result = self.engine.create_story("feat-test-alpha", "Defaults story")
        story = result["story"]
        self.assertEqual(story["priority"], "medium")
        self.assertEqual(story["estimate_days"], 1.0)
        self.assertEqual(story["description"], "")
        self.assertEqual(story["acceptance_criteria"], [])
        self.assertEqual(story["tasks"], [])

    def test_create_story_invalid_priority(self):
        result = self.engine.create_story("feat-test-alpha", "Bad priority", priority="ultra")
        self.assertIn("error", result)
        self.assertIn("ultra", result["error"])

    def test_create_story_missing_feature(self):
        result = self.engine.create_story("feat-nonexistent", "Orphan")
        self.assertIn("error", result)
        self.assertIn("not found", result["error"])

    def test_create_story_sequential_ids(self):
        r1 = self.engine.create_story("feat-test-alpha", "Story 1")
        r2 = self.engine.create_story("feat-test-alpha", "Story 2")
        r3 = self.engine.create_story("feat-test-alpha", "Story 3")
        self.assertEqual(r1["created"], "story-test-alpha-001")
        self.assertEqual(r2["created"], "story-test-alpha-002")
        self.assertEqual(r3["created"], "story-test-alpha-003")

    def test_create_story_non_feature_fdo_rejected(self):
        """proj-* FDOs should not accept stories."""
        result = self.engine.create_story("proj-test", "Bad story")
        self.assertIn("error", result)


class TestTaskEngineStoryWithExisting(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(FEATURE_WITH_STORIES, NON_FEATURE_FDO)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_create_story_continues_numbering(self):
        """New stories should continue from existing max ID."""
        result = self.engine.create_story("feat-test-beta", "Third story")
        self.assertEqual(result["created"], "story-test-beta-003")

    def test_get_existing_story(self):
        item = self.engine.get_item("story-test-beta-001")
        self.assertIsNotNone(item)
        self.assertEqual(item["type"], "story")
        self.assertEqual(item["title"], "Pre-existing story")
        self.assertEqual(item["priority"], "high")
        self.assertEqual(item["estimate_days"], 3)
        self.assertEqual(item["feature"], "feat-test-beta")
        self.assertEqual(item["project"], "proj-test")

    def test_get_nonexistent_story(self):
        item = self.engine.get_item("story-test-beta-999")
        self.assertIsNone(item)

    def test_list_items_all(self):
        items = self.engine.list_items(feat_id="feat-test-beta")
        self.assertEqual(len(items), 2)

    def test_list_items_by_status(self):
        items = self.engine.list_items(feat_id="feat-test-beta", status="active")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "story-test-beta-001")

    def test_list_items_by_priority(self):
        items = self.engine.list_items(feat_id="feat-test-beta", priority="high")
        self.assertEqual(len(items), 1)

    def test_list_items_by_project(self):
        items = self.engine.list_items(project_id="proj-test")
        self.assertEqual(len(items), 2)

    def test_list_items_sorted_by_priority(self):
        """Critical/high should come before medium/low."""
        items = self.engine.list_items(feat_id="feat-test-beta")
        priorities = [i["priority"] for i in items]
        self.assertEqual(priorities, ["high", "medium"])

    def test_list_items_empty_filter(self):
        items = self.engine.list_items(feat_id="feat-nonexistent")
        self.assertEqual(len(items), 0)


class TestTaskEngineTaskCRUD(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(FEATURE_WITH_STORIES)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_create_task(self):
        result = self.engine.create_task("story-test-beta-001", "New task")
        self.assertIn("created", result)
        # task-001 exists in legacy format; new tasks use story-namespaced format
        self.assertEqual(result["created"], "task-test-beta-001-002")
        self.assertEqual(result["story"], "story-test-beta-001")

    def test_create_task_with_fields(self):
        result = self.engine.create_task(
            "story-test-beta-001", "Detailed task",
            estimate_days=2.5, assignee="peter", notes="Important"
        )
        task = result["task"]
        self.assertEqual(task["estimate_days"], 2.5)
        self.assertEqual(task["assignee"], "peter")
        self.assertEqual(task["notes"], "Important")
        self.assertEqual(task["status"], "new")

    def test_create_task_missing_story(self):
        result = self.engine.create_task("story-nonexistent-999", "Orphan task")
        self.assertIn("error", result)

    def test_get_task(self):
        item = self.engine.get_item("task-001")
        self.assertIsNotNone(item)
        self.assertEqual(item["type"], "task")
        self.assertEqual(item["title"], "Existing task")
        self.assertEqual(item["story"], "story-test-beta-001")

    def test_get_nonexistent_task(self):
        item = self.engine.get_item("task-nonexistent-999")
        self.assertIsNone(item)

    def test_create_task_sequential_ids(self):
        r1 = self.engine.create_task("story-test-beta-001", "Task A")
        r2 = self.engine.create_task("story-test-beta-001", "Task B")
        self.assertEqual(r1["created"], "task-test-beta-001-002")
        self.assertEqual(r2["created"], "task-test-beta-001-003")

    def test_create_task_on_empty_story(self):
        """Create task on story-test-beta-002 which has no existing tasks."""
        result = self.engine.create_task("story-test-beta-002", "First task")
        self.assertEqual(result["created"], "task-test-beta-002-001")


class TestTaskEngineUpdate(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(FEATURE_WITH_STORIES)
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

    def test_update_task_status(self):
        # task-001 is a legacy-format ID from the fixture
        result = self.engine.update_item("task-001", {"status": "in_progress"})
        self.assertIn("updated", result)
        item = self.engine.get_item("task-001")
        self.assertEqual(item["status"], "in_progress")

    def test_update_task_notes(self):
        result = self.engine.update_item("task-001", {"notes": "Working on this"})
        self.assertIn("updated", result)
        item = self.engine.get_item("task-001")
        self.assertEqual(item["notes"], "Working on this")

    def test_update_nonexistent_story(self):
        result = self.engine.update_item("story-nonexistent-999", {"title": "X"})
        self.assertIn("error", result)

    def test_update_nonexistent_task(self):
        result = self.engine.update_item("task-nonexistent-999", {"title": "X"})
        self.assertIn("error", result)

    def test_update_empty_fields(self):
        result = self.engine.update_item("story-test-beta-001", {})
        self.assertIn("error", result)


class TestTaskEngineArchive(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(FEATURE_WITH_STORIES)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_archive_no_closed(self):
        result = self.engine.archive_closed("feat-test-beta")
        self.assertEqual(result["archived"], 0)

    def test_archive_closed_stories(self):
        # Close a story first
        self.engine.update_item("story-test-beta-002", {"status": "closed"})
        result = self.engine.archive_closed("feat-test-beta")
        self.assertEqual(result["archived"], 1)
        self.assertIn("feat-test-beta", result["features"])

        # Verify the closed story is gone from active stories
        items = self.engine.list_items(feat_id="feat-test-beta")
        ids = [i["id"] for i in items]
        self.assertNotIn("story-test-beta-002", ids)

    def test_archive_all_features(self):
        """archive_closed with no feat_id scans all features."""
        self.engine.update_item("story-test-beta-002", {"status": "closed"})
        result = self.engine.archive_closed()
        self.assertEqual(result["archived"], 1)

    def test_archive_nonexistent_feature(self):
        result = self.engine.archive_closed("feat-nonexistent")
        self.assertEqual(result["archived"], 0)


class TestTaskEngineFeatureDiscovery(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(
            MINIMAL_FEATURE_FDO, FEATURE_WITH_STORIES, NON_FEATURE_FDO
        )
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_get_all_features(self):
        features = self.engine.get_all_features()
        ids = [f["id"] for f in features]
        self.assertIn("feat-test-alpha", ids)
        self.assertIn("feat-test-beta", ids)
        self.assertNotIn("proj-test", ids)

    def test_feature_summary_includes_story_count(self):
        features = self.engine.get_all_features()
        beta = next(f for f in features if f["id"] == "feat-test-beta")
        self.assertEqual(beta["story_count"], 2)
        self.assertEqual(beta["stories_done"], 0)

    def test_feature_summary_project_link(self):
        features = self.engine.get_all_features()
        alpha = next(f for f in features if f["id"] == "feat-test-alpha")
        self.assertEqual(alpha["project"], "proj-test")


class TestTaskEngineThreadSafety(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(MINIMAL_FEATURE_FDO)
        self.engine = TaskEngine(self.vault_path)

    def tearDown(self):
        cleanup_vault(self.tmp)

    def test_concurrent_story_creates(self):
        """5 concurrent story creates should produce unique sequential IDs."""
        results = []

        def create(i):
            return self.engine.create_story("feat-test-alpha", f"Concurrent {i}")

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(create, i) for i in range(5)]
            for f in futures:
                results.append(f.result(timeout=10))

        ids = [r["created"] for r in results if "created" in r]
        self.assertEqual(len(ids), 5, f"Expected 5 created, got {len(ids)}")
        self.assertEqual(len(set(ids)), 5, "Duplicate IDs detected!")

    def test_concurrent_task_creates(self):
        """Concurrent task creates on same story."""
        self.engine.create_story("feat-test-alpha", "Concurrent target")
        story_id = "story-test-alpha-001"

        results = []
        def create(i):
            return self.engine.create_task(story_id, f"Task {i}")

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(create, i) for i in range(3)]
            for f in futures:
                results.append(f.result(timeout=10))

        ids = [r["created"] for r in results if "created" in r]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3, "Duplicate task IDs!")


# ── BoardEngine Unit Tests ──────────────────────────────────────────────────

class TestBoardEngineBasic(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(FEATURE_WITH_STORIES)
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
        self.vault_path, self.tmp = make_temp_vault(FEATURE_WITH_STORIES)
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
        self.vault_path, self.tmp = make_temp_vault(FEATURE_WITH_STORIES)
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
        self.assertIn("task_count", story)
        self.assertIn("tasks_done", story)

    def test_board_view_total(self):
        view = self.board.board_view()
        self.assertEqual(view["total_stories"], 2)

    def test_board_view_project_filter(self):
        view = self.board.board_view(project_id="proj-test")
        self.assertEqual(view["total_stories"], 2)

        view2 = self.board.board_view(project_id="proj-nonexistent")
        self.assertEqual(view2["total_stories"], 0)

    def test_backlog_view(self):
        """Backlog should exclude stories already on board."""
        backlog = self.board.backlog_view(feat_id="feat-test-beta")
        self.assertEqual(backlog["count"], 0)  # Both stories are on board

    def test_backlog_view_with_backlog_items(self):
        """Create a 3rd story that's NOT on the board."""
        self.engine.create_story("feat-test-beta", "Backlog story")
        backlog = self.board.backlog_view(feat_id="feat-test-beta")
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


# ── CalendarEngine Unit Tests ───────────────────────────────────────────────

class TestCalendarPersonalEvents(TestCase):
    def setUp(self):
        self.vault_path, self.tmp = make_temp_vault(FEATURE_WITH_STORIES)
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
        self.vault_path, self.tmp = make_temp_vault(FEATURE_WITH_STORIES)
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
        self.vault_path, self.tmp = make_temp_vault(FEATURE_WITH_STORIES)
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


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
