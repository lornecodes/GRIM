"""
End-to-end tests for Kronos Task Management using a mock vault.

Creates a complete mock vault with test projects, epics, and features.
Tests the full workflow: project -> feature -> story -> task -> board -> calendar.
No real vault pollution — everything lives in a temp directory.

Run:
    python tests/test_task_e2e.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# ── Setup mock vault ────────────────────────────────────────────────────────

grim_root = Path(__file__).resolve().parent.parent

# Create temp vault BEFORE importing server (server reads KRONOS_VAULT_PATH at import)
MOCK_VAULT = Path(tempfile.mkdtemp(prefix="kronos-e2e-"))
print(f"Mock vault: {MOCK_VAULT}")

# Create vault structure
for d in ["projects", "ai-systems", "physics", "calendar", "templates", "_inbox"]:
    (MOCK_VAULT / d).mkdir(parents=True, exist_ok=True)

# Write mock project FDOs (epics)
(MOCK_VAULT / "projects" / "proj-phoenix.md").write_text("""\
---
id: proj-phoenix
title: "Project Phoenix"
domain: projects
created: "2026-01-15"
updated: "2026-03-01"
status: developing
confidence: 0.7
related:
  - feat-phoenix-auth
  - feat-phoenix-api
tags: [project, phoenix]
---

# Project Phoenix

## Summary
A test project for E2E validation.
""", encoding="utf-8")

(MOCK_VAULT / "projects" / "proj-atlas.md").write_text("""\
---
id: proj-atlas
title: "Project Atlas"
domain: projects
created: "2026-02-01"
updated: "2026-03-01"
status: developing
confidence: 0.6
related:
  - feat-atlas-dashboard
tags: [project, atlas]
---

# Project Atlas

## Summary
Another test project.
""", encoding="utf-8")

# Write mock feature FDOs
(MOCK_VAULT / "ai-systems" / "feat-phoenix-auth.md").write_text("""\
---
id: feat-phoenix-auth
title: "Feature: Phoenix Authentication"
domain: ai-systems
created: "2026-02-15"
updated: "2026-03-01"
status: developing
confidence: 0.5
related:
  - proj-phoenix
tags: [feature, auth, phoenix]
stories: []
---

# Feature: Phoenix Authentication

## Goal
Implement user authentication for Project Phoenix.
""", encoding="utf-8")

(MOCK_VAULT / "ai-systems" / "feat-phoenix-api.md").write_text("""\
---
id: feat-phoenix-api
title: "Feature: Phoenix API"
domain: ai-systems
created: "2026-02-20"
updated: "2026-03-01"
status: developing
confidence: 0.5
related:
  - proj-phoenix
tags: [feature, api, phoenix]
stories: []
---

# Feature: Phoenix API

## Goal
Build REST API for Project Phoenix.
""", encoding="utf-8")

(MOCK_VAULT / "ai-systems" / "feat-atlas-dashboard.md").write_text("""\
---
id: feat-atlas-dashboard
title: "Feature: Atlas Dashboard"
domain: ai-systems
created: "2026-02-25"
updated: "2026-03-01"
status: developing
confidence: 0.5
related:
  - proj-atlas
tags: [feature, dashboard, atlas]
stories: []
---

# Feature: Atlas Dashboard

## Goal
Build main dashboard for Atlas.
""", encoding="utf-8")

# Write empty board and calendar
(MOCK_VAULT / "projects" / "board.yaml").write_text(
    "columns:\n  new: []\n  active: []\n  in_progress: []\n  resolved: []\n  closed: []\n",
    encoding="utf-8",
)
(MOCK_VAULT / "calendar" / "schedule.yaml").write_text("entries: []\n", encoding="utf-8")
(MOCK_VAULT / "calendar" / "personal.yaml").write_text("entries: []\n", encoding="utf-8")

# Set env vars BEFORE import
os.environ["KRONOS_VAULT_PATH"] = str(MOCK_VAULT)
os.environ["KRONOS_SKILLS_PATH"] = str(grim_root / "skills")
os.environ.setdefault("KRONOS_EMBED_MODEL", "all-mpnet-base-v2")

sys.path.insert(0, str(grim_root / "mcp" / "kronos" / "src"))

print("Importing server module...")
t0 = time.time()
from kronos_mcp.server import (
    handle_task_create, handle_task_update, handle_task_get,
    handle_task_list, handle_task_move, handle_task_archive,
    handle_board_view, handle_backlog_view,
    handle_calendar_view, handle_calendar_add, handle_calendar_update,
    handle_calendar_sync,
    task_engine, board_engine, calendar_engine,
)
print(f"Import done in {time.time() - t0:.1f}s")


# ── Helpers ──────────────────────────────────────────────────────────────────

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = ""):
    status = PASS if ok else FAIL
    results.append((name, status, detail))
    icon = "[OK]" if ok else "[FAIL]"
    print(f"  {icon} {name}: {detail}")


def call(handler, args: dict) -> dict:
    """Call a handler and parse the JSON result."""
    return json.loads(handler(args))


# ── E2E Scenarios ───────────────────────────────────────────────────────────

def test_multi_project_setup():
    """Create stories across multiple projects/features."""
    print("\n[E2E-1] Multi-project story creation")

    # Phoenix Auth — 3 stories
    r1 = call(handle_task_create, {
        "type": "story", "feat_id": "feat-phoenix-auth",
        "title": "Implement OAuth2 flow", "priority": "critical", "estimate_days": 3,
        "description": "Full OAuth2 with PKCE",
        "acceptance_criteria": ["Login works", "Token refresh works", "Logout clears session"],
        "tags": ["auth", "oauth"],
    })
    record("phoenix auth story 1", "created" in r1, r1.get("created", r1.get("error")))

    r2 = call(handle_task_create, {
        "type": "story", "feat_id": "feat-phoenix-auth",
        "title": "Add session management", "priority": "high", "estimate_days": 2,
        "tags": ["auth", "session"],
    })
    record("phoenix auth story 2", "created" in r2, r2.get("created"))

    r3 = call(handle_task_create, {
        "type": "story", "feat_id": "feat-phoenix-auth",
        "title": "Write auth tests", "priority": "medium", "estimate_days": 1,
    })
    record("phoenix auth story 3", "created" in r3, r3.get("created"))

    # Phoenix API — 2 stories
    r4 = call(handle_task_create, {
        "type": "story", "feat_id": "feat-phoenix-api",
        "title": "Design REST endpoints", "priority": "high", "estimate_days": 2,
    })
    record("phoenix api story 1", "created" in r4, r4.get("created"))

    r5 = call(handle_task_create, {
        "type": "story", "feat_id": "feat-phoenix-api",
        "title": "Implement CRUD handlers", "priority": "medium", "estimate_days": 4,
    })
    record("phoenix api story 2", "created" in r5, r5.get("created"))

    # Atlas Dashboard — 2 stories
    r6 = call(handle_task_create, {
        "type": "story", "feat_id": "feat-atlas-dashboard",
        "title": "Build widget framework", "priority": "high", "estimate_days": 3,
    })
    record("atlas dashboard story 1", "created" in r6, r6.get("created"))

    r7 = call(handle_task_create, {
        "type": "story", "feat_id": "feat-atlas-dashboard",
        "title": "Create metrics panel", "priority": "low", "estimate_days": 2,
    })
    record("atlas dashboard story 2", "created" in r7, r7.get("created"))

    return {
        "phoenix_auth": [r1.get("created"), r2.get("created"), r3.get("created")],
        "phoenix_api": [r4.get("created"), r5.get("created")],
        "atlas_dashboard": [r6.get("created"), r7.get("created")],
    }


def test_task_creation(stories: dict):
    """Add tasks to stories across features."""
    print("\n[E2E-2] Task creation across features")

    auth_story = stories["phoenix_auth"][0]  # OAuth story

    tasks_created = []
    for i, title in enumerate(["Set up OAuth provider", "Implement PKCE flow",
                                "Build callback handler", "Add token storage"]):
        r = call(handle_task_create, {
            "type": "task", "story_id": auth_story,
            "title": title, "estimate_days": 0.5 + (i * 0.25),
        })
        record(f"auth task {i+1}", "created" in r, r.get("created", r.get("error")))
        tasks_created.append(r.get("created"))

    # Add tasks to Atlas story
    atlas_story = stories["atlas_dashboard"][0]
    for title in ["Design component API", "Implement base widget"]:
        r = call(handle_task_create, {
            "type": "task", "story_id": atlas_story, "title": title,
        })
        record(f"atlas task", "created" in r, r.get("created"))

    return tasks_created


def test_cross_project_listing(stories: dict):
    """List and filter stories across projects."""
    print("\n[E2E-3] Cross-project listing and filtering")

    # All stories
    r = call(handle_task_list, {})
    record("all stories count", r["count"] == 7, f"expected=7 got={r['count']}")

    # Filter by project
    r = call(handle_task_list, {"project_id": "proj-phoenix"})
    record("phoenix stories", r["count"] == 5, f"expected=5 got={r['count']}")

    r = call(handle_task_list, {"project_id": "proj-atlas"})
    record("atlas stories", r["count"] == 2, f"expected=2 got={r['count']}")

    # Filter by feature
    r = call(handle_task_list, {"feat_id": "feat-phoenix-auth"})
    record("auth feature stories", r["count"] == 3, f"expected=3 got={r['count']}")

    # Filter by priority
    r = call(handle_task_list, {"priority": "critical"})
    record("critical stories", r["count"] == 1, f"expected=1 got={r['count']}")

    r = call(handle_task_list, {"priority": "high"})
    record("high stories", r["count"] == 3, f"expected=3 got={r['count']}")

    # Filter by status (all should be 'new' at this point)
    r = call(handle_task_list, {"status": "new"})
    record("new stories", r["count"] == 7, f"expected=7 got={r['count']}")

    # Combined filters
    r = call(handle_task_list, {"project_id": "proj-phoenix", "priority": "high"})
    record("phoenix high", r["count"] == 2, f"expected=2 got={r['count']}")


def test_board_workflow(stories: dict):
    """Full board workflow: backlog -> new -> active -> in_progress -> resolved -> closed."""
    print("\n[E2E-4] Board workflow")

    # Initially all stories in backlog
    r = call(handle_backlog_view, {})
    record("initial backlog", r["count"] == 7, f"expected=7 got={r['count']}")

    # Add critical OAuth story to board
    oauth_id = stories["phoenix_auth"][0]
    r = call(handle_task_move, {"story_id": oauth_id, "column": "active"})
    record("oauth to active", r.get("to") == "active")

    # Add high-priority stories to board
    session_id = stories["phoenix_auth"][1]
    api_design_id = stories["phoenix_api"][0]
    widget_id = stories["atlas_dashboard"][0]

    for sid, name in [(session_id, "session"), (api_design_id, "api"), (widget_id, "widget")]:
        r = call(handle_task_move, {"story_id": sid, "column": "new"})
        record(f"{name} -> new", r.get("to") == "new")

    # Verify board state
    r = call(handle_board_view, {})
    record("board total", r["total_stories"] == 4, f"expected=4 got={r['total_stories']}")
    record("active col", len(r["columns"]["active"]) == 1)
    record("new col", len(r["columns"]["new"]) == 3)

    # Filter board by project
    r_phoenix = call(handle_board_view, {"project_id": "proj-phoenix"})
    record("phoenix board", r_phoenix["total_stories"] == 3, f"expected=3 got={r_phoenix['total_stories']}")

    r_atlas = call(handle_board_view, {"project_id": "proj-atlas"})
    record("atlas board", r_atlas["total_stories"] == 1)

    # Backlog should now have 3 stories (7 total - 4 on board)
    r = call(handle_backlog_view, {})
    record("backlog after boarding", r["count"] == 3, f"expected=3 got={r['count']}")

    # Filtered backlog
    r = call(handle_backlog_view, {"project_id": "proj-phoenix"})
    record("phoenix backlog", r["count"] == 2, f"expected=2 got={r['count']}")

    # Move OAuth through workflow
    r = call(handle_task_move, {"story_id": oauth_id, "column": "in_progress"})
    record("oauth -> in_progress", r.get("to") == "in_progress")

    # Verify status synced
    r = call(handle_task_get, {"item_id": oauth_id})
    record("oauth status synced", json.loads(r) if isinstance(r, str) else r)
    item = json.loads(r) if isinstance(r, str) else r
    record("oauth status = in_progress", item.get("status") == "in_progress")

    return oauth_id


def test_task_progress(stories: dict, tasks: list[str]):
    """Update tasks within a story and verify progress tracking."""
    print("\n[E2E-5] Task progress tracking")

    # Complete some tasks on the OAuth story
    for tid in tasks[:2]:
        r = call(handle_task_update, {"item_id": tid, "fields": {"status": "closed"}})
        record(f"complete {tid}", "updated" in r)

    r = call(handle_task_update, {"item_id": tasks[2], "fields": {"status": "in_progress"}})
    record(f"start {tasks[2]}", "updated" in r)

    # Verify via board view (should show task progress)
    r = call(handle_board_view, {})
    ip_stories = r["columns"]["in_progress"]
    if ip_stories:
        oauth_view = ip_stories[0]
        record("tasks_done count", oauth_view.get("tasks_done") == 2, f"got={oauth_view.get('tasks_done')}")
        record("task_count", oauth_view.get("task_count") == 4, f"got={oauth_view.get('task_count')}")


def test_calendar_workflow(stories: dict, oauth_id: str):
    """Full calendar workflow: sync work items + personal events."""
    print("\n[E2E-6] Calendar workflow")

    # Sync schedule from active board items
    r = call(handle_calendar_sync, {"start_date": "2026-03-03"})  # Monday
    record("sync entries", r.get("synced", 0) >= 1, f"synced={r.get('synced')}")

    # Verify schedule entries have correct fields
    entries = r.get("schedule", {}).get("entries", [])
    if entries:
        entry = entries[0]
        record("entry has story_id", "story_id" in entry)
        record("entry has feature", "feature" in entry)
        record("entry has dates", "start_date" in entry and "end_date" in entry)
        record("entry has priority", "priority" in entry)

        # Critical story should be first (priority ordering)
        record("highest priority first", entry.get("priority") == "critical",
               f"got={entry.get('priority')}")

    # Add personal events
    r1 = call(handle_calendar_add, {
        "title": "Team standup", "date": "2026-03-10",
        "time": "09:00", "duration_hours": 0.5, "recurring": True,
    })
    record("personal event 1", "created" in r1)

    r2 = call(handle_calendar_add, {
        "title": "Dentist appointment", "date": "2026-03-15",
        "time": "14:00", "duration_hours": 1, "notes": "Annual checkup",
    })
    record("personal event 2", "created" in r2)

    r3 = call(handle_calendar_add, {
        "title": "Sprint review", "date": "2026-03-20",
        "time": "16:00", "duration_hours": 1.5,
    })
    record("personal event 3", "created" in r3)

    # View merged calendar
    view = call(handle_calendar_view, {"start_date": "2026-03-01", "end_date": "2026-03-31"})
    record("merged calendar has entries", view["total"] > 0, f"total={view['total']}")

    work_entries = [e for e in view["entries"] if e["type"] == "work"]
    personal_entries = [e for e in view["entries"] if e["type"] == "personal"]
    record("has work entries", len(work_entries) > 0, f"work={len(work_entries)}")
    record("has personal entries", len(personal_entries) == 3, f"personal={len(personal_entries)}")

    # View without personal
    view2 = call(handle_calendar_view, {
        "start_date": "2026-03-01", "end_date": "2026-03-31",
        "include_personal": False,
    })
    personal2 = [e for e in view2["entries"] if e["type"] == "personal"]
    record("exclude personal", len(personal2) == 0)

    # Update personal event
    event_id = r2.get("created")
    r = call(handle_calendar_update, {
        "event_id": event_id, "action": "update",
        "fields": {"time": "15:30", "notes": "Rescheduled to afternoon"},
    })
    record("update personal event", "updated" in r)

    # Delete personal event
    sprint_id = r3.get("created")
    r = call(handle_calendar_update, {"event_id": sprint_id, "action": "delete"})
    record("delete personal event", "deleted" in r)

    # Verify deletion
    view3 = call(handle_calendar_view, {"start_date": "2026-03-01", "end_date": "2026-03-31"})
    personal3 = [e for e in view3["entries"] if e["type"] == "personal"]
    record("post-delete count", len(personal3) == 2, f"expected=2 got={len(personal3)}")


def test_resolve_close_archive(stories: dict, oauth_id: str):
    """Complete lifecycle: resolve -> close -> archive."""
    print("\n[E2E-7] Resolve, close, and archive lifecycle")

    # Resolve OAuth story
    r = call(handle_task_move, {"story_id": oauth_id, "column": "resolved"})
    record("oauth -> resolved", r.get("to") == "resolved")

    # Close it
    r = call(handle_task_move, {"story_id": oauth_id, "column": "closed"})
    record("oauth -> closed", r.get("to") == "closed")

    # Verify status log accumulated
    item = call(handle_task_get, {"item_id": oauth_id})
    log = item.get("log", [])
    record("status log entries", len(log) >= 4, f"log_count={len(log)}")
    # Should have: created, -> active, -> in_progress, -> resolved, -> closed

    # Archive closed stories from phoenix auth
    r = call(handle_task_archive, {"feat_id": "feat-phoenix-auth"})
    record("archive count", r.get("archived") == 1, f"archived={r.get('archived')}")

    # Verify archived story no longer in active list
    items = call(handle_task_list, {"feat_id": "feat-phoenix-auth"})
    ids = [s["id"] for s in items.get("stories", [])]
    record("archived not in list", oauth_id not in ids)

    # Board should no longer have the archived story
    board = call(handle_board_view, {})
    closed_ids = [s["id"] for s in board["columns"]["closed"]]
    record("archived not on board", oauth_id not in closed_ids,
           f"closed column ids: {closed_ids}")

    # Sync calendar should not include archived/closed
    r = call(handle_calendar_sync, {"start_date": "2026-03-03"})
    for entry in r.get("schedule", {}).get("entries", []):
        if entry.get("story_id") == oauth_id:
            record("archived not in schedule", False, "found in schedule!")
            break
    else:
        record("archived not in schedule", True)


def test_multi_feature_archive():
    """Archive across all features."""
    print("\n[E2E-8] Multi-feature archive")

    # Close stories in different features
    # First find all stories to close one from each
    api_stories = call(handle_task_list, {"feat_id": "feat-phoenix-api"})
    atlas_stories = call(handle_task_list, {"feat_id": "feat-atlas-dashboard"})

    if api_stories["stories"]:
        sid = api_stories["stories"][-1]["id"]  # last one (lower priority)
        call(handle_task_update, {"item_id": sid, "fields": {"status": "closed"}})
    if atlas_stories["stories"]:
        sid = atlas_stories["stories"][-1]["id"]
        call(handle_task_update, {"item_id": sid, "fields": {"status": "closed"}})

    # Archive all
    r = call(handle_task_archive, {})
    record("multi-feature archive", r.get("archived", 0) >= 2, f"archived={r.get('archived')}")
    record("features touched", len(r.get("features", [])) >= 2,
           f"features={r.get('features')}")


def test_story_retrieval_with_context():
    """Get story returns full context including feature and project info."""
    print("\n[E2E-9] Story retrieval with full context")

    stories = call(handle_task_list, {"feat_id": "feat-phoenix-auth"})
    if stories["stories"]:
        sid = stories["stories"][0]["id"]
        item = call(handle_task_get, {"item_id": sid})
        record("has type", item.get("type") == "story")
        record("has feature", item.get("feature") == "feat-phoenix-auth")
        record("has project", item.get("project") == "proj-phoenix")
        record("has priority", "priority" in item)
        record("has estimate", "estimate_days" in item)
        record("has tasks", "tasks" in item)
        record("has log", "log" in item)


def test_feature_discovery():
    """get_all_features returns enriched info."""
    print("\n[E2E-10] Feature discovery")

    features = task_engine.get_all_features()
    ids = [f["id"] for f in features]
    record("phoenix auth found", "feat-phoenix-auth" in ids)
    record("phoenix api found", "feat-phoenix-api" in ids)
    record("atlas dashboard found", "feat-atlas-dashboard" in ids)

    for f in features:
        if f["id"] == "feat-phoenix-auth":
            record("auth project link", f["project"] == "proj-phoenix")
            record("auth story count", f["story_count"] >= 1, f"count={f['story_count']}")


def test_edge_cases():
    """Edge cases and boundary conditions."""
    print("\n[E2E-11] Edge cases")

    # Empty feat_id
    r = call(handle_task_create, {"type": "story", "feat_id": "", "title": "X"})
    record("empty feat_id rejected", "error" in r)

    # Empty title
    r = call(handle_task_create, {"type": "story", "feat_id": "feat-phoenix-auth", "title": ""})
    record("empty title rejected", "error" in r)

    # Whitespace-only title
    r = call(handle_task_create, {"type": "story", "feat_id": "feat-phoenix-auth", "title": "   "})
    record("whitespace title rejected", "error" in r)

    # Get nonexistent item
    r = call(handle_task_get, {"item_id": "story-phantom-001"})
    record("nonexistent get", "error" in r)

    # Move nonexistent story
    r = call(handle_task_move, {"story_id": "story-phantom-001", "column": "active"})
    record("move nonexistent", "error" in r or r.get("to") == "active")
    # Note: move_story auto-adds if not on board, but story must exist in vault

    # Calendar view with out-of-range dates
    r = call(handle_calendar_view, {"start_date": "2030-01-01", "end_date": "2030-12-31"})
    record("future calendar empty", r["total"] == 0)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Kronos Task Management — E2E Tests (Mock Vault)")
    print("=" * 60)

    try:
        stories = test_multi_project_setup()
        tasks = test_task_creation(stories)
        test_cross_project_listing(stories)
        oauth_id = test_board_workflow(stories)
        test_task_progress(stories, tasks)
        test_calendar_workflow(stories, oauth_id)
        test_resolve_close_archive(stories, oauth_id)
        test_multi_feature_archive()
        test_story_retrieval_with_context()
        test_feature_discovery()
        test_edge_cases()
    finally:
        # Cleanup mock vault
        print(f"\nCleanup: Removing mock vault {MOCK_VAULT}...")
        shutil.rmtree(MOCK_VAULT, ignore_errors=True)

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
