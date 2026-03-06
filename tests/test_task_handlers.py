"""
Unit tests for Kronos Task Management MCP handlers.

Tests TaskEngine, BoardEngine, and CalendarEngine via handlers directly.
Uses a temporary project FDO for isolation — cleaned up after tests.

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
    handle_task_dispatch,
    handle_board_view, handle_backlog_view,
    handle_calendar_view, handle_calendar_add, handle_calendar_update,
    handle_calendar_sync,
    handle_create,
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

TEST_PROJ_ID = "proj-test-task-temp"
TEST_PROJ_DOMAIN = "projects"


def record(name: str, ok: bool, detail: str = ""):
    status = PASS if ok else FAIL
    results.append((name, status, detail))
    icon = "[OK]" if ok else "[FAIL]"
    print(f"  {icon} {name}: {detail}")


def elapsed(fn, *args, **kwargs):
    t = time.time()
    result = fn(*args, **kwargs)
    return result, time.time() - t


def setup_test_project():
    """Create a temporary project FDO for testing."""
    cleanup_test_project()
    resp = handle_create({
        "id": TEST_PROJ_ID,
        "title": "Test Project for Task Handlers",
        "domain": TEST_PROJ_DOMAIN,
        "confidence": 0.5,
        "body": "# Test Project\n\n## Summary\nTemporary project for task handler tests.",
        "related": [],
        "tags": ["test", "epic"],
    })
    data = json.loads(resp)
    if "error" in data:
        print(f"WARNING: Failed to create test project: {data['error']}")
        return False

    # Ensure the FDO has stories: [] in frontmatter by patching the file
    proj_path = Path(vault_path) / TEST_PROJ_DOMAIN / f"{TEST_PROJ_ID}.md"
    if proj_path.exists():
        content = proj_path.read_text(encoding="utf-8")
        # Add stories field to frontmatter if not present
        if "stories:" not in content:
            content = content.replace("---\n", "---\nstories: []\n", 1)
            # Only replace the second occurrence (closing ---)
            # Actually the frontmatter pattern is --- at start, then --- to close
            # handle_create should produce valid frontmatter, so let's insert
            # stories into the extra/frontmatter
            lines = content.split("\n")
            new_lines = []
            in_frontmatter = False
            inserted = False
            for i, line in enumerate(lines):
                if line.strip() == "---" and not in_frontmatter:
                    in_frontmatter = True
                    new_lines.append(line)
                    continue
                if line.strip() == "---" and in_frontmatter and not inserted:
                    new_lines.append("stories: []")
                    inserted = True
                    new_lines.append(line)
                    in_frontmatter = False
                    continue
                new_lines.append(line)
            if inserted:
                proj_path.write_text("\n".join(new_lines), encoding="utf-8")

    # Re-init engines to pick up the new FDO
    _reinit_engines()
    return True


def cleanup_test_project():
    """Remove test project FDO and clean up board references."""
    path = Path(vault_path) / TEST_PROJ_DOMAIN / f"{TEST_PROJ_ID}.md"
    if path.exists():
        path.unlink()
    if _server.vault is not None and _server.vault._index is not None:
        _server.vault._index.pop(TEST_PROJ_ID, None)

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
    """Create test project + story, return story ID. Cleaned up at module end."""
    setup_test_project()
    resp = handle_task_create({
        "proj_id": TEST_PROJ_ID,
        "title": "Test story one",
        "priority": "high",
        "estimate_days": 2,
        "description": "A test story",
        "acceptance_criteria": ["Criterion A", "Criterion B"],
        "assignee": "code",
        "tags": ["test"],
    })
    data = json.loads(resp)
    sid = data.get("created")
    yield sid
    cleanup_test_project()
    cleanup_test_personal()


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


# ── Story CRUD Tests ─────────────────────────────────────────────────────────

def test_story_create(story_id):
    """Verify story was created by the fixture."""
    print("\n[1] Verify story creation")
    assert story_id, "story_id fixture should have created a story"
    resp = handle_task_get({"item_id": story_id})
    data = json.loads(resp)
    record("story created", data.get("id") == story_id, f"id={story_id}")
    record("story has correct project", data.get("project") == TEST_PROJ_ID)


def test_story_get(story_id):
    print("\n[2] Get story")
    resp = handle_task_get({"item_id": story_id})
    data = json.loads(resp)
    record("story found", data.get("id") == story_id)
    record("story has acceptance criteria", len(data.get("acceptance_criteria", [])) == 2)
    record("story has assignee", data.get("assignee") == "code")


def test_story_update(story_id):
    print("\n[3] Update story")
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


def test_story_list():
    print("\n[4] List stories")
    resp = handle_task_list({"project_id": TEST_PROJ_ID})
    data = json.loads(resp)
    record("list returns stories", data.get("count", 0) > 0, f"count={data.get('count')}")
    stories = data.get("stories", [])
    if stories:
        record("list has priority", "priority" in stories[0])


def test_story_list_by_domain():
    print("\n[4b] List stories by domain")
    resp = handle_task_list({"domain": TEST_PROJ_DOMAIN})
    data = json.loads(resp)
    record("domain filter returns stories", data.get("count", 0) > 0, f"count={data.get('count')}")


def test_status_validation():
    print("\n[5] Validation — invalid priority")
    resp = handle_task_create({
        "proj_id": TEST_PROJ_ID,
        "title": "Bad priority test",
        "priority": "ultra",  # invalid
    })
    data = json.loads(resp)
    record("invalid priority rejected", "error" in data, data.get("error", ""))


def test_missing_project():
    print("\n[6] Missing project")
    resp = handle_task_create({
        "proj_id": "proj-nonexistent-xxx",
        "title": "Orphan story",
    })
    data = json.loads(resp)
    record("missing project rejected", "error" in data, data.get("error", ""))


def test_missing_proj_id():
    print("\n[6b] Missing proj_id param")
    resp = handle_task_create({
        "title": "No project specified",
    })
    data = json.loads(resp)
    record("missing proj_id rejected", "error" in data, data.get("error", ""))


def test_missing_title():
    print("\n[6c] Missing title param")
    resp = handle_task_create({
        "proj_id": TEST_PROJ_ID,
    })
    data = json.loads(resp)
    record("missing title rejected", "error" in data, data.get("error", ""))


def test_auto_id():
    print("\n[7] Auto ID generation")
    resp1 = handle_task_create({
        "proj_id": TEST_PROJ_ID, "title": "Second story",
    })
    resp2 = handle_task_create({
        "proj_id": TEST_PROJ_ID, "title": "Third story",
    })
    d1 = json.loads(resp1)
    d2 = json.loads(resp2)
    id1 = d1.get("created", "")
    id2 = d2.get("created", "")
    record("IDs are sequential", id1 < id2, f"{id1} < {id2}")
    record("IDs have correct prefix", id1.startswith("story-test-task-temp-"))


def test_status_log():
    print("\n[8] Status change logging")
    resp = handle_task_create({
        "proj_id": TEST_PROJ_ID, "title": "Log test story",
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
    print("\n[9] Concurrent story creates")
    ids = []

    def create_one(i):
        resp = handle_task_create({
            "proj_id": TEST_PROJ_ID,
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
    print("\n[10] Board move")
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
    print("\n[11] Board view")
    resp = handle_board_view({})
    data = json.loads(resp)
    record("board has columns", "columns" in data)
    record("board has total", "total_stories" in data, f"total={data.get('total_stories')}")
    # Check that in_progress column has our story
    ip = data.get("columns", {}).get("in_progress", [])
    record("in_progress has stories", len(ip) > 0, f"count={len(ip)}")


def test_board_view_with_domain():
    print("\n[11b] Board view with domain filter")
    resp = handle_board_view({"domain": TEST_PROJ_DOMAIN})
    data = json.loads(resp)
    record("board view with domain works", "columns" in data)


def test_backlog_view():
    print("\n[12] Backlog view")
    resp = handle_backlog_view({"project_id": TEST_PROJ_ID})
    data = json.loads(resp)
    record("backlog returns items", "backlog" in data, f"count={data.get('count')}")


def test_backlog_view_with_domain():
    print("\n[12b] Backlog view with domain filter")
    resp = handle_backlog_view({"domain": TEST_PROJ_DOMAIN})
    data = json.loads(resp)
    record("backlog with domain works", "backlog" in data, f"count={data.get('count')}")


def test_board_invalid_column():
    print("\n[13] Board invalid column")
    resp = handle_task_move({"story_id": "story-xxx", "column": "invalid"})
    data = json.loads(resp)
    record("invalid column rejected", "error" in data)


def test_board_resolve_close(story_id):
    print("\n[14] Board resolve and close")
    resp = handle_task_move({"story_id": story_id, "column": "resolved"})
    data = json.loads(resp)
    record("moved to resolved", data.get("to") == "resolved")

    resp2 = handle_task_move({"story_id": story_id, "column": "closed"})
    data2 = json.loads(resp2)
    record("moved to closed", data2.get("to") == "closed")


# ── Dispatch Tests ───────────────────────────────────────────────────────────

def test_dispatch_story(story_id):
    """Dispatch a story with an assignee to get pool params."""
    print("\n[15] Dispatch story")
    # First reset story to active state for dispatch
    handle_task_move({"story_id": story_id, "column": "active"})

    resp = handle_task_dispatch({"story_id": story_id})
    data = json.loads(resp)
    record("dispatch succeeded", "dispatched" in data, str(data.get("dispatched", data.get("error"))))
    record("dispatch has assignee", data.get("assignee") == "code")
    record("dispatch has pool_params", "pool_params" in data)
    pool_params = data.get("pool_params", {})
    record("pool_params has instructions", bool(pool_params.get("instructions")))
    record("pool_params has job_type", pool_params.get("job_type") == "code")


def test_dispatch_override_assignee(story_id):
    """Dispatch with override_assignee changes the assignee."""
    print("\n[16] Dispatch with override assignee")
    resp = handle_task_dispatch({
        "story_id": story_id,
        "override_assignee": "research",
    })
    data = json.loads(resp)
    record("dispatch with override succeeded", "dispatched" in data)
    record("override assignee applied", data.get("assignee") == "research")

    # Verify the story assignee was updated
    resp2 = handle_task_get({"item_id": story_id})
    data2 = json.loads(resp2)
    record("story assignee updated", data2.get("assignee") == "research")


def test_dispatch_missing_story():
    """Dispatch with a nonexistent story returns error."""
    print("\n[17] Dispatch missing story")
    resp = handle_task_dispatch({"story_id": "story-nonexistent-999"})
    data = json.loads(resp)
    record("missing story rejected", "error" in data, data.get("error", ""))


def test_dispatch_missing_story_id():
    """Dispatch without story_id returns error."""
    print("\n[18] Dispatch missing story_id")
    resp = handle_task_dispatch({})
    data = json.loads(resp)
    record("missing story_id rejected", "error" in data, data.get("error", ""))


def test_dispatch_invalid_assignee():
    """Dispatch with invalid assignee type returns error."""
    print("\n[19] Dispatch invalid assignee")
    # Create a story with no assignee
    resp = handle_task_create({
        "proj_id": TEST_PROJ_ID, "title": "No assignee story",
    })
    sid = json.loads(resp).get("created")
    if not sid:
        record("dispatch invalid - story created", False, "failed to create")
        return

    resp2 = handle_task_dispatch({
        "story_id": sid,
        "override_assignee": "invalid_type",
    })
    data = json.loads(resp2)
    record("invalid assignee rejected", "error" in data, data.get("error", ""))


def test_dispatch_no_assignee():
    """Dispatch a story with no assignee and no override returns error."""
    print("\n[20] Dispatch story with no assignee")
    resp = handle_task_create({
        "proj_id": TEST_PROJ_ID, "title": "Unassigned story for dispatch",
    })
    sid = json.loads(resp).get("created")
    if not sid:
        record("dispatch no assignee - story created", False, "failed to create")
        return

    resp2 = handle_task_dispatch({"story_id": sid})
    data = json.loads(resp2)
    record("no assignee rejected", "error" in data, data.get("error", ""))


# ── Calendar Tests ───────────────────────────────────────────────────────────

def test_calendar_add(event_id):
    """Verify personal event was created by the fixture."""
    print("\n[21] Verify calendar add")
    assert event_id, "event_id fixture should have created an event"
    record("personal event created", True, f"id={event_id}")


def test_calendar_update(event_id):
    print("\n[22] Calendar update personal event")
    resp = handle_calendar_update({
        "event_id": event_id,
        "action": "update",
        "fields": {"time": "15:00", "notes": "Rescheduled"},
    })
    data = json.loads(resp)
    record("personal event updated", "updated" in data)


def test_calendar_sync():
    print("\n[23] Calendar sync")
    resp = handle_calendar_sync({})
    data = json.loads(resp)
    record("sync returned entries", "synced" in data, f"count={data.get('synced')}")


def test_calendar_view():
    print("\n[24] Calendar view")
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
    print("\n[25] Calendar delete personal event")
    resp = handle_calendar_update({
        "event_id": event_id,
        "action": "delete",
    })
    data = json.loads(resp)
    record("personal event deleted", "deleted" in data)


# ── Archive Tests ────────────────────────────────────────────────────────────

def test_archive():
    print("\n[26] Archive closed stories")
    resp = handle_task_archive({"proj_id": TEST_PROJ_ID})
    data = json.loads(resp)
    record("archive ran", "archived" in data, f"count={data.get('archived')}")


# ── Integration Test ─────────────────────────────────────────────────────────

def test_full_workflow():
    print("\n[27] Full workflow integration")
    # Create story with assignee
    r1 = json.loads(handle_task_create({
        "proj_id": TEST_PROJ_ID,
        "title": "Integration test story",
        "priority": "high", "estimate_days": 2,
        "assignee": "code",
        "acceptance_criteria": ["Works end to end"],
    }))
    sid = r1.get("created")
    record("workflow: story created", sid is not None)

    # Move to board (active)
    r3 = json.loads(handle_task_move({"story_id": sid, "column": "active"}))
    record("workflow: moved to active", r3.get("to") == "active")

    # Dispatch to pool
    r_dispatch = json.loads(handle_task_dispatch({"story_id": sid}))
    record("workflow: dispatched", "dispatched" in r_dispatch)
    record("workflow: dispatch has pool_params", "pool_params" in r_dispatch)

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
    r6 = json.loads(handle_task_archive({"proj_id": TEST_PROJ_ID}))
    record("workflow: archived", r6.get("archived", 0) >= 1)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Kronos Task Handler Tests")
    print("=" * 60)

    # Setup
    print("\nSetup: Creating test project FDO...")
    if not setup_test_project():
        print("ABORT: Could not create test project")
        return

    try:
        # Story CRUD (tests 1-9)
        story_id = test_story_create()
        if story_id:
            test_story_get(story_id)
            test_story_update(story_id)
        test_story_list()
        test_story_list_by_domain()
        test_status_validation()
        test_missing_project()
        test_missing_proj_id()
        test_missing_title()
        test_auto_id()
        test_status_log()
        test_concurrent_creates()

        # Board (tests 10-14)
        if story_id:
            test_board_move(story_id)
        test_board_view()
        test_board_view_with_domain()
        test_backlog_view()
        test_backlog_view_with_domain()
        test_board_invalid_column()
        if story_id:
            test_board_resolve_close(story_id)

        # Dispatch (tests 15-20)
        if story_id:
            test_dispatch_story(story_id)
            test_dispatch_override_assignee(story_id)
        test_dispatch_missing_story()
        test_dispatch_missing_story_id()
        test_dispatch_invalid_assignee()
        test_dispatch_no_assignee()

        # Calendar (tests 21-25)
        event_id = test_calendar_add()
        if event_id:
            test_calendar_update(event_id)
        test_calendar_sync()
        test_calendar_view()
        if event_id:
            test_calendar_delete(event_id)

        # Archive (test 26)
        test_archive()

        # Integration (test 27)
        test_full_workflow()

    finally:
        # Cleanup
        print("\nCleanup: Removing test artifacts...")
        cleanup_test_project()
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
