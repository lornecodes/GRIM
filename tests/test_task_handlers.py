"""
Unit tests for Kronos Task Management MCP handlers.

Tests TaskEngine, BoardEngine, and CalendarEngine via handlers directly.
Uses a temporary feature FDO for isolation — cleaned up after tests.

Run:
    PYTHONPATH=mcp/kronos/src KRONOS_VAULT_PATH=../kronos-vault \
    python tests/test_task_handlers.py
"""
from __future__ import annotations

import json
import os
import pytest
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Bootstrap
grim_root = Path(__file__).resolve().parent.parent
vault_path = str((grim_root / ".." / "kronos-vault").resolve())
skills_path = str((grim_root / "skills").resolve())

os.environ["KRONOS_VAULT_PATH"] = vault_path
os.environ["KRONOS_SKILLS_PATH"] = skills_path
os.environ.setdefault("KRONOS_EMBED_MODEL", "all-mpnet-base-v2")

sys.path.insert(0, str(grim_root / "mcp" / "kronos" / "src"))

print("Importing server module...")
t0 = time.time()
import kronos_mcp.server as _server
from kronos_mcp.tasks import TaskEngine
from kronos_mcp.board import BoardEngine
from kronos_mcp.calendar import CalendarEngine
from kronos_mcp.server import (
    handle_task_create, handle_task_update, handle_task_get,
    handle_task_list, handle_task_move, handle_task_archive,
    handle_board_view, handle_backlog_view,
    handle_calendar_view, handle_calendar_add, handle_calendar_update,
    handle_calendar_sync,
    handle_create, vault, search_engine,
)
print(f"Import done in {time.time() - t0:.1f}s")


def _reinit_engines():
    """Reinitialize server engines to point to real vault."""
    _server.task_engine = TaskEngine(vault_path)
    _server.board_engine = BoardEngine(vault_path, _server.task_engine)
    _server.calendar_engine = CalendarEngine(vault_path, _server.board_engine)


@pytest.fixture(scope="module", autouse=True)
def _real_vault_engines():
    """Ensure server engines use the real vault for all tests in this module."""
    _reinit_engines()
    yield

# ── Helpers ──────────────────────────────────────────────────────────────────

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []

TEST_FEAT_ID = "feat-test-task-temp"
TEST_FEAT_DOMAIN = "ai-systems"


def record(name: str, ok: bool, detail: str = ""):
    status = PASS if ok else FAIL
    results.append((name, status, detail))
    icon = "[OK]" if ok else "[FAIL]"
    print(f"  {icon} {name}: {detail}")


def elapsed(fn, *args, **kwargs):
    t = time.time()
    result = fn(*args, **kwargs)
    return result, time.time() - t


def setup_test_feature():
    """Create a temporary feature FDO for testing."""
    cleanup_test_feature()
    resp = handle_create({
        "id": TEST_FEAT_ID,
        "title": "Test Feature for Task Handlers",
        "domain": TEST_FEAT_DOMAIN,
        "confidence": 0.5,
        "body": "# Test Feature\n\n## Summary\nTemporary feature for task handler tests.",
        "related": [],
        "tags": ["test"],
    })
    data = json.loads(resp)
    if "error" in data:
        print(f"WARNING: Failed to create test feature: {data['error']}")
        return False
    return True


def cleanup_test_feature():
    """Remove test feature FDO and clean up board references."""
    path = Path(vault_path) / TEST_FEAT_DOMAIN / f"{TEST_FEAT_ID}.md"
    if path.exists():
        path.unlink()
    if vault._index is not None:
        vault._index.pop(TEST_FEAT_ID, None)

    # Clean board of any test story IDs
    board = _server.board_engine._load_board()
    changed = False
    for col, ids in board["columns"].items():
        before = len(ids)
        board["columns"][col] = [sid for sid in ids if not sid.startswith("story-test-task-temp")]
        if len(board["columns"][col]) != before:
            changed = True
    if changed:
        _server.board_engine._save_board(board)


def cleanup_test_personal():
    """Remove test personal calendar events."""
    data = _server.calendar_engine._load_yaml(_server.calendar_engine.personal_path)
    entries = data.get("entries", [])
    data["entries"] = [e for e in entries if not e.get("title", "").startswith("_test")]
    _server.calendar_engine._save_yaml(_server.calendar_engine.personal_path, data)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def story_id():
    """Create test feature + story, return story ID. Cleaned up at module end."""
    setup_test_feature()
    resp = handle_task_create({
        "type": "story",
        "feat_id": TEST_FEAT_ID,
        "title": "Test story one",
        "priority": "high",
        "estimate_days": 2,
        "description": "A test story",
        "acceptance_criteria": ["Criterion A", "Criterion B"],
        "tags": ["test"],
    })
    data = json.loads(resp)
    sid = data.get("created")
    yield sid
    cleanup_test_feature()
    cleanup_test_personal()


@pytest.fixture(scope="module")
def task_id(story_id):
    """Create a task on the test story, return task ID."""
    resp = handle_task_create({
        "type": "task",
        "story_id": story_id,
        "title": "Test task alpha",
        "estimate_days": 0.5,
    })
    data = json.loads(resp)
    return data.get("created")


@pytest.fixture(scope="module")
def event_id():
    """Create a test personal calendar event, return event ID."""
    resp = handle_calendar_add({
        "title": "_test dentist appointment",
        "date": "2026-03-15",
        "time": "14:00",
        "duration_hours": 1,
        "notes": "Test event",
    })
    data = json.loads(resp)
    return data.get("created")


# ── Tests ────────────────────────────────────────────────────────────────────

def test_story_create(story_id):
    """Verify story was created by the fixture."""
    print("\n[1] Verify story creation")
    assert story_id, "story_id fixture should have created a story"
    resp = handle_task_get({"item_id": story_id})
    data = json.loads(resp)
    record("story created", data.get("id") == story_id, f"id={story_id}")
    record("story has correct feature", data.get("feature") == TEST_FEAT_ID)


def test_task_create(task_id, story_id):
    """Verify task was created by the fixture."""
    print("\n[2] Verify task creation")
    assert task_id, "task_id fixture should have created a task"
    resp = handle_task_get({"item_id": task_id})
    data = json.loads(resp)
    record("task created", data.get("id") == task_id, f"id={task_id}")
    record("task linked to story", data.get("story") == story_id)


def test_story_get(story_id):
    print("\n[3] Get story")
    resp = handle_task_get({"item_id": story_id})
    data = json.loads(resp)
    record("story found", data.get("id") == story_id)
    record("story has tasks", len(data.get("tasks", [])) > 0, f"task_count={len(data.get('tasks', []))}")
    record("story has acceptance criteria", len(data.get("acceptance_criteria", [])) == 2)
    record("story type is story", data.get("type") == "story")


def test_task_get(task_id):
    print("\n[4] Get task")
    resp = handle_task_get({"item_id": task_id})
    data = json.loads(resp)
    record("task found", data.get("id") == task_id)
    record("task type is task", data.get("type") == "task")


def test_story_update(story_id):
    print("\n[5] Update story")
    resp = handle_task_update({
        "item_id": story_id,
        "fields": {"priority": "critical", "estimate_days": 5},
    })
    data = json.loads(resp)
    record("story updated", "updated" in data, str(data.get("fields_changed", data.get("error"))))

    # Verify
    resp2 = handle_task_get({"item_id": story_id})
    data2 = json.loads(resp2)
    record("priority changed", data2.get("priority") == "critical")
    record("estimate changed", data2.get("estimate_days") == 5)


def test_task_update(task_id):
    print("\n[6] Update task")
    resp = handle_task_update({
        "item_id": task_id,
        "fields": {"status": "in_progress", "notes": "Working on it"},
    })
    data = json.loads(resp)
    record("task updated", "updated" in data, str(data.get("fields_changed", data.get("error"))))


def test_story_list():
    print("\n[7] List stories")
    resp = handle_task_list({"feat_id": TEST_FEAT_ID})
    data = json.loads(resp)
    record("list returns stories", data.get("count", 0) > 0, f"count={data.get('count')}")
    stories = data.get("stories", [])
    if stories:
        record("list has priority", "priority" in stories[0])
        record("list has task_count", "task_count" in stories[0])


def test_status_validation():
    print("\n[8] Validation")
    resp = handle_task_create({
        "type": "story",
        "feat_id": TEST_FEAT_ID,
        "title": "Bad status test",
        "priority": "ultra",  # invalid
    })
    data = json.loads(resp)
    record("invalid priority rejected", "error" in data, data.get("error", ""))


def test_missing_feat():
    print("\n[9] Missing feature")
    resp = handle_task_create({
        "type": "story",
        "feat_id": "feat-nonexistent-xxx",
        "title": "Orphan story",
    })
    data = json.loads(resp)
    record("missing feature rejected", "error" in data, data.get("error", ""))


def test_auto_id():
    print("\n[10] Auto ID generation")
    resp1 = handle_task_create({
        "type": "story", "feat_id": TEST_FEAT_ID, "title": "Second story",
    })
    resp2 = handle_task_create({
        "type": "story", "feat_id": TEST_FEAT_ID, "title": "Third story",
    })
    d1 = json.loads(resp1)
    d2 = json.loads(resp2)
    id1 = d1.get("created", "")
    id2 = d2.get("created", "")
    record("IDs are sequential", id1 < id2, f"{id1} < {id2}")
    record("IDs have correct prefix", id1.startswith("story-test-task-temp-"))


def test_status_log():
    print("\n[11] Status change logging")
    # Create a story, change status, check log
    resp = handle_task_create({
        "type": "story", "feat_id": TEST_FEAT_ID, "title": "Log test story",
        "priority": "low", "estimate_days": 1,
    })
    sid = json.loads(resp).get("created")
    if not sid:
        record("log test - story created", False, "failed to create")
        return

    handle_task_update({"item_id": sid, "fields": {"status": "active"}})
    resp2 = handle_task_get({"item_id": sid})
    data = json.loads(resp2)
    log = data.get("log", [])
    has_status_log = any("Status" in entry for entry in log)
    record("status change logged", has_status_log, f"log entries: {len(log)}")


def test_concurrent_creates():
    print("\n[12] Concurrent story creates")
    ids = []

    def create_one(i):
        resp = handle_task_create({
            "type": "story", "feat_id": TEST_FEAT_ID,
            "title": f"Concurrent story {i}",
            "priority": "medium", "estimate_days": 1,
        })
        return json.loads(resp).get("created")

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(create_one, i) for i in range(5)]
        for f in futures:
            sid = f.result(timeout=10)
            if sid:
                ids.append(sid)

    record("all concurrent creates succeeded", len(ids) == 5, f"created={len(ids)}")
    record("no duplicate IDs", len(set(ids)) == len(ids), f"unique={len(set(ids))}")


# ── Board Tests ──────────────────────────────────────────────────────────────

def test_board_move(story_id):
    print("\n[13] Board move")
    resp = handle_task_move({"story_id": story_id, "column": "active"})
    data = json.loads(resp)
    record("story moved to active", data.get("to") == "active", str(data))

    # Move to in_progress
    resp2 = handle_task_move({"story_id": story_id, "column": "in_progress"})
    data2 = json.loads(resp2)
    record("story moved to in_progress", data2.get("from") == "active" and data2.get("to") == "in_progress")

    # Verify status synced
    resp3 = handle_task_get({"item_id": story_id})
    data3 = json.loads(resp3)
    record("status synced with column", data3.get("status") == "in_progress")


def test_board_view():
    print("\n[14] Board view")
    resp = handle_board_view({})
    data = json.loads(resp)
    record("board has columns", "columns" in data)
    record("board has total", "total_stories" in data, f"total={data.get('total_stories')}")
    # Check that in_progress column has our story
    ip = data.get("columns", {}).get("in_progress", [])
    record("in_progress has stories", len(ip) > 0, f"count={len(ip)}")


def test_backlog_view():
    print("\n[15] Backlog view")
    resp = handle_backlog_view({"feat_id": TEST_FEAT_ID})
    data = json.loads(resp)
    record("backlog returns items", "backlog" in data, f"count={data.get('count')}")


def test_board_invalid_column():
    print("\n[16] Board invalid column")
    resp = handle_task_move({"story_id": "story-xxx", "column": "invalid"})
    data = json.loads(resp)
    record("invalid column rejected", "error" in data)


def test_board_resolve_close(story_id):
    print("\n[17] Board resolve and close")
    resp = handle_task_move({"story_id": story_id, "column": "resolved"})
    data = json.loads(resp)
    record("moved to resolved", data.get("to") == "resolved")

    resp2 = handle_task_move({"story_id": story_id, "column": "closed"})
    data2 = json.loads(resp2)
    record("moved to closed", data2.get("to") == "closed")


# ── Calendar Tests ───────────────────────────────────────────────────────────

def test_calendar_add(event_id):
    """Verify personal event was created by the fixture."""
    print("\n[18] Verify calendar add")
    assert event_id, "event_id fixture should have created an event"
    record("personal event created", True, f"id={event_id}")


def test_calendar_update(event_id):
    print("\n[19] Calendar update personal event")
    resp = handle_calendar_update({
        "event_id": event_id,
        "action": "update",
        "fields": {"time": "15:00", "notes": "Rescheduled"},
    })
    data = json.loads(resp)
    record("personal event updated", "updated" in data)


def test_calendar_sync():
    print("\n[20] Calendar sync")
    resp = handle_calendar_sync({})
    data = json.loads(resp)
    record("sync returned entries", "synced" in data, f"count={data.get('synced')}")


def test_calendar_view():
    print("\n[21] Calendar view")
    resp = handle_calendar_view({
        "start_date": "2026-03-01",
        "end_date": "2026-03-31",
    })
    data = json.loads(resp)
    record("calendar has entries", "entries" in data, f"total={data.get('total')}")
    # Should have at least the personal event
    personal = [e for e in data.get("entries", []) if e.get("type") == "personal"]
    record("includes personal events", len(personal) > 0, f"personal={len(personal)}")


def test_calendar_delete(event_id):
    print("\n[22] Calendar delete personal event")
    resp = handle_calendar_update({
        "event_id": event_id,
        "action": "delete",
    })
    data = json.loads(resp)
    record("personal event deleted", "deleted" in data)


# ── Archive Tests ────────────────────────────────────────────────────────────

def test_archive():
    print("\n[23] Archive closed stories")
    resp = handle_task_archive({"feat_id": TEST_FEAT_ID})
    data = json.loads(resp)
    record("archive ran", "archived" in data, f"count={data.get('archived')}")


# ── Integration Test ─────────────────────────────────────────────────────────

def test_full_workflow():
    print("\n[24] Full workflow integration")
    # Create story
    r1 = json.loads(handle_task_create({
        "type": "story", "feat_id": TEST_FEAT_ID,
        "title": "Integration test story",
        "priority": "high", "estimate_days": 2,
        "acceptance_criteria": ["Works end to end"],
    }))
    sid = r1.get("created")
    record("workflow: story created", sid is not None)

    # Add task
    r2 = json.loads(handle_task_create({
        "type": "task", "story_id": sid, "title": "Integration task",
        "estimate_days": 1,
    }))
    record("workflow: task created", "created" in r2)

    # Move to board (active)
    r3 = json.loads(handle_task_move({"story_id": sid, "column": "active"}))
    record("workflow: moved to active", r3.get("to") == "active")

    # Sync calendar
    r4 = json.loads(handle_calendar_sync({}))
    record("workflow: calendar synced", r4.get("synced", 0) >= 1)

    # Check calendar
    r5 = json.loads(handle_calendar_view({
        "start_date": "2026-03-01", "end_date": "2026-12-31",
    }))
    work_items = [e for e in r5.get("entries", []) if e.get("type") == "work"]
    record("workflow: story in calendar", len(work_items) >= 1)

    # Resolve and close
    handle_task_move({"story_id": sid, "column": "resolved"})
    handle_task_move({"story_id": sid, "column": "closed"})

    # Archive
    r6 = json.loads(handle_task_archive({"feat_id": TEST_FEAT_ID}))
    record("workflow: archived", r6.get("archived", 0) >= 1)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Kronos Task Handler Tests")
    print("=" * 60)

    # Setup
    print("\nSetup: Creating test feature FDO...")
    if not setup_test_feature():
        print("ABORT: Could not create test feature")
        return

    try:
        # Task CRUD (tests 1-12)
        story_id = test_story_create()
        if story_id:
            task_id = test_task_create(story_id)
            test_story_get(story_id)
            if task_id:
                test_task_get(task_id)
            test_story_update(story_id)
            if task_id:
                test_task_update(task_id)
        test_story_list()
        test_status_validation()
        test_missing_feat()
        test_auto_id()
        test_status_log()
        test_concurrent_creates()

        # Board (tests 13-17)
        if story_id:
            test_board_move(story_id)
        test_board_view()
        test_backlog_view()
        test_board_invalid_column()
        if story_id:
            test_board_resolve_close(story_id)

        # Calendar (tests 18-22)
        event_id = test_calendar_add()
        if event_id:
            test_calendar_update(event_id)
        test_calendar_sync()
        test_calendar_view()
        if event_id:
            test_calendar_delete(event_id)

        # Archive (test 23)
        test_archive()

        # Integration (test 24)
        test_full_workflow()

    finally:
        # Cleanup
        print("\nCleanup: Removing test artifacts...")
        cleanup_test_feature()
        cleanup_test_personal()

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = sum(1 for _, s, _ in results if s == FAIL)
    total = len(results)
    print(f"Results: {passed}/{total} passed, {failed} failed")

    if failed > 0:
        print("\nFailed tests:")
        for name, status, detail in results:
            if status == FAIL:
                print(f"  FAIL: {name} — {detail}")

    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
