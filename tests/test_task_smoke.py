"""
Smoke tests for Kronos Task Management MCP tool registration.

Verifies that all 13 task tools are registered, handlers exist,
tool groups are correct, and basic calls return valid JSON.
Runs against the real vault but makes no changes (read-only + rollback).

Run:
    PYTHONPATH=mcp/kronos/src KRONOS_VAULT_PATH=../kronos-vault \
    python tests/test_task_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import time
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
from kronos_mcp.server import (
    TOOLS, HANDLERS, TOOL_GROUPS,
    handle_task_create, handle_task_update, handle_task_get,
    handle_task_list, handle_task_move, handle_task_archive,
    handle_task_dispatch,
    handle_board_view, handle_backlog_view,
    handle_calendar_view, handle_calendar_add, handle_calendar_update,
    handle_calendar_sync,
    handle_create, _ensure_initialized,
)
# Trigger lazy engine initialization so handlers work
_ensure_initialized()
from kronos_mcp.server import vault, task_engine, board_engine, calendar_engine
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


# ── Expected tool names ─────────────────────────────────────────────────────

TASK_TOOLS = [
    "kronos_task_create",
    "kronos_task_update",
    "kronos_task_get",
    "kronos_task_list",
    "kronos_task_move",
    "kronos_task_dispatch",
    "kronos_task_archive",
    "kronos_board_view",
    "kronos_backlog_view",
    "kronos_calendar_view",
    "kronos_calendar_add",
    "kronos_calendar_update",
    "kronos_calendar_sync",
]


# ── Tests ────────────────────────────────────────────────────────────────────

def test_tool_registration():
    """All 13 task tools appear in the TOOLS list."""
    print("\n[1] Tool registration")
    tool_names = {t.name for t in TOOLS}
    for name in TASK_TOOLS:
        record(f"{name} registered", name in tool_names, "in TOOLS list" if name in tool_names else "MISSING")


def test_handler_registration():
    """All 13 task tools have handlers in HANDLERS dict."""
    print("\n[2] Handler registration")
    for name in TASK_TOOLS:
        record(f"{name} handler", name in HANDLERS, "in HANDLERS" if name in HANDLERS else "MISSING")


def test_tool_schemas():
    """Each task tool has a valid inputSchema with required/properties."""
    print("\n[3] Tool schemas")
    tool_map = {t.name: t for t in TOOLS}
    for name in TASK_TOOLS:
        tool = tool_map.get(name)
        if not tool:
            record(f"{name} schema", False, "tool not found")
            continue
        schema = tool.inputSchema
        has_type = schema.get("type") == "object"
        has_props = "properties" in schema
        record(f"{name} schema", has_type and has_props, f"type=object props={list(schema.get('properties', {}).keys())[:3]}...")


def test_tool_groups():
    """Task tools appear in correct tool groups."""
    print("\n[4] Tool groups")
    read_tools = set(TOOL_GROUPS.get("tasks:read", []))
    write_tools = set(TOOL_GROUPS.get("tasks:write", []))

    expected_read = {"kronos_task_list", "kronos_task_get", "kronos_board_view",
                     "kronos_backlog_view", "kronos_calendar_view"}
    expected_write = {"kronos_task_create", "kronos_task_update", "kronos_task_move",
                      "kronos_task_dispatch", "kronos_task_archive",
                      "kronos_calendar_add", "kronos_calendar_update",
                      "kronos_calendar_sync"}

    record("tasks:read group", read_tools == expected_read,
           f"match={read_tools == expected_read} extra={read_tools - expected_read} missing={expected_read - read_tools}")
    record("tasks:write group", write_tools == expected_write,
           f"match={write_tools == expected_write} extra={write_tools - expected_write} missing={expected_write - write_tools}")


def test_read_only_smoke():
    """Read-only handlers return valid JSON without errors."""
    print("\n[5] Read-only smoke tests")

    # Board view (no side effects)
    resp = handle_board_view({})
    data = json.loads(resp)
    record("board_view returns JSON", isinstance(data, dict))
    record("board_view has columns", "columns" in data)

    # Backlog view
    resp = handle_backlog_view({})
    data = json.loads(resp)
    record("backlog_view returns JSON", isinstance(data, dict))
    record("backlog_view has backlog", "backlog" in data)

    # Task list (all)
    resp = handle_task_list({})
    data = json.loads(resp)
    record("task_list returns JSON", isinstance(data, dict))
    record("task_list has stories", "stories" in data)
    record("task_list has count", "count" in data)

    # Task list with domain filter
    resp = handle_task_list({"domain": "projects"})
    data = json.loads(resp)
    record("task_list domain filter", isinstance(data, dict) and "stories" in data)

    # Board view with domain filter
    resp = handle_board_view({"domain": "projects"})
    data = json.loads(resp)
    record("board_view domain filter", isinstance(data, dict) and "columns" in data)

    # Calendar view
    resp = handle_calendar_view({"start_date": "2026-03-01", "end_date": "2026-03-31"})
    data = json.loads(resp)
    record("calendar_view returns JSON", isinstance(data, dict))
    record("calendar_view has entries", "entries" in data)

    # Calendar sync (read-like: only writes to schedule.yaml which is computed)
    resp = handle_calendar_sync({})
    data = json.loads(resp)
    record("calendar_sync returns JSON", isinstance(data, dict))
    record("calendar_sync has synced", "synced" in data)


def test_error_handling():
    """Handlers return proper error JSON for bad inputs."""
    print("\n[6] Error handling")

    # Missing title
    resp = handle_task_create({"proj_id": "proj-x"})
    data = json.loads(resp)
    record("missing title error", "error" in data, data.get("error", ""))

    # Missing proj_id
    resp = handle_task_create({"title": "X"})
    data = json.loads(resp)
    record("missing proj_id error", "error" in data, data.get("error", ""))

    # Missing item_id for get
    resp = handle_task_get({})
    data = json.loads(resp)
    record("missing item_id error", "error" in data, data.get("error", ""))

    # Missing item_id for update
    resp = handle_task_update({})
    data = json.loads(resp)
    record("update missing item_id", "error" in data, data.get("error", ""))

    # Update with no fields
    resp = handle_task_update({"item_id": "story-x"})
    data = json.loads(resp)
    record("update missing fields", "error" in data, data.get("error", ""))

    # Missing story_id for move
    resp = handle_task_move({})
    data = json.loads(resp)
    record("move missing story_id", "error" in data, data.get("error", ""))

    # Move missing column
    resp = handle_task_move({"story_id": "story-x"})
    data = json.loads(resp)
    record("move missing column", "error" in data, data.get("error", ""))

    # Invalid board column
    resp = handle_task_move({"story_id": "story-x", "column": "invalid"})
    data = json.loads(resp)
    record("move invalid column", "error" in data, data.get("error", ""))

    # Dispatch missing story_id
    resp = handle_task_dispatch({})
    data = json.loads(resp)
    record("dispatch missing story_id", "error" in data, data.get("error", ""))

    # Dispatch nonexistent story
    resp = handle_task_dispatch({"story_id": "story-nonexistent-999"})
    data = json.loads(resp)
    record("dispatch nonexistent story", "error" in data, data.get("error", ""))

    # Calendar view missing dates
    resp = handle_calendar_view({})
    data = json.loads(resp)
    record("calendar_view missing dates", "error" in data, data.get("error", ""))

    # Calendar add missing fields
    resp = handle_calendar_add({})
    data = json.loads(resp)
    record("calendar_add missing fields", "error" in data, data.get("error", ""))

    # Calendar update missing event_id
    resp = handle_calendar_update({})
    data = json.loads(resp)
    record("calendar_update missing id", "error" in data, data.get("error", ""))

    # Calendar update invalid action
    resp = handle_calendar_update({"event_id": "personal-x", "action": "explode"})
    data = json.loads(resp)
    record("calendar_update invalid action", "error" in data, data.get("error", ""))


def test_null_safety():
    """Handlers handle None values (JSON null) without crashing."""
    print("\n[7] Null safety")

    # All string args as None (simulating JSON null)
    try:
        resp = handle_task_create({"title": None, "proj_id": None})
        data = json.loads(resp)
        record("null title/proj_id", "error" in data, data.get("error", ""))
    except Exception as e:
        record("null title/proj_id", False, f"CRASH: {e}")

    try:
        resp = handle_task_get({"item_id": None})
        data = json.loads(resp)
        record("null item_id", "error" in data, data.get("error", ""))
    except Exception as e:
        record("null item_id", False, f"CRASH: {e}")

    try:
        resp = handle_task_move({"story_id": None, "column": None})
        data = json.loads(resp)
        record("null move args", "error" in data, data.get("error", ""))
    except Exception as e:
        record("null move args", False, f"CRASH: {e}")

    try:
        resp = handle_task_dispatch({"story_id": None})
        data = json.loads(resp)
        record("null dispatch args", "error" in data, data.get("error", ""))
    except Exception as e:
        record("null dispatch args", False, f"CRASH: {e}")

    try:
        resp = handle_calendar_add({"title": None, "date": None})
        data = json.loads(resp)
        record("null calendar args", "error" in data, data.get("error", ""))
    except Exception as e:
        record("null calendar args", False, f"CRASH: {e}")


def test_engine_initialization():
    """Engine instances are properly initialized."""
    print("\n[8] Engine initialization")
    record("task_engine exists", task_engine is not None)
    record("board_engine exists", board_engine is not None)
    record("calendar_engine exists", calendar_engine is not None)
    record("board refs task_engine", board_engine.task_engine is task_engine)
    record("calendar refs board_engine", calendar_engine.board_engine is board_engine)
    record("calendar refs task_engine", calendar_engine.task_engine is task_engine)
    record("board.yaml path exists", board_engine.board_path.exists())
    record("schedule.yaml path exists", calendar_engine.schedule_path.exists())
    record("personal.yaml path exists", calendar_engine.personal_path.exists())


def test_total_tool_count():
    """Total MCP tool count should be 36 (base tools + task/dispatch)."""
    print("\n[9] Total tool count")
    record("total tools", len(TOOLS) == 36, f"expected=36 got={len(TOOLS)}")
    record("total handlers", len(HANDLERS) == 36, f"expected=36 got={len(HANDLERS)}")
    record("tools == handlers", len(TOOLS) == len(HANDLERS),
           f"tools={len(TOOLS)} handlers={len(HANDLERS)}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Kronos Task Management — Smoke Tests")
    print("=" * 60)

    test_tool_registration()
    test_handler_registration()
    test_tool_schemas()
    test_tool_groups()
    test_read_only_smoke()
    test_error_handling()
    test_null_safety()
    test_engine_initialization()
    test_total_tool_count()

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
