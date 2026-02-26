"""
Unit tests for Kronos MCP handler functions.

Tests handlers directly (no subprocess/protocol overhead) to isolate:
  1. Handler timing — each should complete fast after initial build
  2. Thread safety — concurrent calls must not corrupt state
  3. Post-invalidate correctness — reads still work after a write
  4. Semantic preload non-blocking — model load doesn't stall gets

Run:
    PYTHONPATH=mcp/kronos/src KRONOS_VAULT_PATH=../kronos-vault \
    python tests/test_mcp_handlers.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Bootstrap
grim_root = Path(__file__).resolve().parent.parent
vault_path = str((grim_root / ".." / "kronos-vault").resolve())
skills_path = str((grim_root / "skills").resolve())

os.environ["KRONOS_VAULT_PATH"] = vault_path
os.environ["KRONOS_SKILLS_PATH"] = skills_path
os.environ["KRONOS_EMBED_MODEL"] = "all-mpnet-base-v2"

# Add MCP src to path
sys.path.insert(0, str(grim_root / "mcp" / "kronos" / "src"))

# ── Import handlers (module-level setup runs: vault, search_engine, preload thread) ──

print("Importing server module (preload thread starts)...")
t0 = time.time()
from kronos_mcp.server import (
    handle_get, handle_list, handle_graph, handle_validate,
    handle_search, handle_update, handle_create,
    search_engine, vault,
)
import_time = time.time() - t0
print(f"Import done in {import_time:.1f}s")


# ── Helpers ──────────────────────────────────────────────────────────────────

KNOWN_FDO_ID = "grim-architecture"
KNOWN_DOMAIN = "ai-systems"

PASS = "PASS"
FAIL = "FAIL"

results: list[tuple[str, str, str]] = []  # (test_name, status, detail)


def record(name: str, ok: bool, detail: str = ""):
    status = PASS if ok else FAIL
    results.append((name, status, detail))
    icon = "[OK]" if ok else "[FAIL]"
    print(f"  {icon} {name}: {detail}")


def elapsed(fn, *args, **kwargs):
    t = time.time()
    result = fn(*args, **kwargs)
    return result, time.time() - t


# ── Test 1: handle_get is fast ───────────────────────────────────────────────

def test_get_timing():
    print("\n[1] handle_get timing")

    # Warm-up call (triggers first index build)
    resp, warmup_ms = elapsed(handle_get, {"id": KNOWN_FDO_ID})
    warmup_ms *= 1000
    print(f"    Warm-up: {warmup_ms:.0f}ms")

    # Subsequent calls should be fast (index already built)
    times = []
    for _ in range(5):
        resp, ms = elapsed(handle_get, {"id": KNOWN_FDO_ID})
        times.append(ms * 1000)

    avg_ms = sum(times) / len(times)
    max_ms = max(times)
    print(f"    Subsequent: avg={avg_ms:.0f}ms max={max_ms:.0f}ms")

    import json
    data = json.loads(resp)
    record("get returns correct FDO", data.get("id") == KNOWN_FDO_ID, f"id={data.get('id')}")
    record("get subsequent < 500ms", max_ms < 500, f"max={max_ms:.0f}ms")


# ── Test 2: handle_list is fast ──────────────────────────────────────────────

def test_list_timing():
    print("\n[2] handle_list timing")
    import json

    resp, ms = elapsed(handle_list, {})
    ms *= 1000
    data = json.loads(resp)
    count = data.get("count", 0)
    print(f"    Listed {count} FDOs in {ms:.0f}ms")
    record("list returns FDOs", count > 0, f"count={count}")
    record("list < 1000ms", ms < 1000, f"ms={ms:.0f}")

    resp2, ms2 = elapsed(handle_list, {"domain": KNOWN_DOMAIN})
    ms2 *= 1000
    data2 = json.loads(resp2)
    record("list domain filter works", data2.get("count", 0) > 0, f"domain={KNOWN_DOMAIN} count={data2.get('count',0)}")
    record("list domain < 500ms", ms2 < 500, f"ms={ms2:.0f}")


# ── Test 3: handle_search is fast (no semantic) ──────────────────────────────

def test_search_no_semantic():
    print("\n[3] handle_search (semantic=False) timing")
    import json

    resp, ms = elapsed(handle_search, {"query": "GRIM architecture", "semantic": False})
    ms *= 1000
    data = json.loads(resp)
    count = data.get("count", 0)
    print(f"    Got {count} results in {ms:.0f}ms")
    record("search returns results", count > 0, f"count={count}")
    record("search (no semantic) < 500ms", ms < 500, f"ms={ms:.0f}")


# ── Test 4: handle_get concurrency — no deadlock, no corruption ──────────────

def test_concurrent_gets():
    print("\n[4] concurrent handle_get (10 threads)")

    ids_to_fetch = [
        "grim-architecture", "grim-identity", "grim-skills",
        "grim-langgraph", "grim-server-ui", "proj-grim",
        "kronos-vault", "coding-integration",
    ]
    errors: list[str] = []
    times: list[float] = []
    lock = threading.Lock()

    def fetch(fdo_id: str):
        import json
        t = time.time()
        try:
            resp = handle_get({"id": fdo_id})
            data = json.loads(resp)
            if data.get("id") != fdo_id:
                with lock:
                    errors.append(f"Wrong FDO returned for {fdo_id}: got {data.get('id')}")
        except Exception as e:
            with lock:
                errors.append(f"Exception for {fdo_id}: {e}")
        finally:
            with lock:
                times.append(time.time() - t)

    # Run 10 threads fetching various FDOs
    requests = (ids_to_fetch * 2)[:10]
    t_total = time.time()
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(fetch, fdo_id) for fdo_id in requests]
        for f in futures:
            f.result(timeout=10)
    total_ms = (time.time() - t_total) * 1000
    avg_ms = (sum(times) / len(times)) * 1000

    print(f"    Total: {total_ms:.0f}ms, avg per call: {avg_ms:.0f}ms")
    record("concurrent gets — no exceptions", len(errors) == 0, f"errors={errors}")
    record("concurrent gets — no deadlock (< 5s)", total_ms < 5000, f"total={total_ms:.0f}ms")


# ── Test 5: handle_get while semantic preload may be running ─────────────────

def test_get_during_semantic_preload():
    print("\n[5] handle_get while semantic preload may still be running")

    # This tests that even if the background preload thread is active,
    # handle_get returns in a reasonable time.
    t0 = time.time()
    import json
    resp = handle_get({"id": KNOWN_FDO_ID})
    ms = (time.time() - t0) * 1000
    data = json.loads(resp)

    sem_status = search_engine._semantic_indexed
    sem_loading = search_engine._semantic_loading
    print(f"    handle_get in {ms:.0f}ms, semantic_indexed={sem_status}, semantic_loading={sem_loading}")

    record("get during preload — correct data", data.get("id") == KNOWN_FDO_ID)
    record("get during preload — < 2000ms", ms < 2000, f"ms={ms:.0f}")


# ── Test 6: write then read (post-invalidate correctness) ────────────────────

def test_write_then_read():
    print("\n[6] handle_update -> handle_get round-trip")
    import json

    # Get current state
    resp = handle_get({"id": KNOWN_FDO_ID})
    before = json.loads(resp)
    before_confidence = before.get("confidence")

    # Update something trivial
    new_confidence = 0.88
    t_write = time.time()
    update_resp = handle_update({"id": KNOWN_FDO_ID, "fields": {"confidence": new_confidence}})
    write_ms = (time.time() - t_write) * 1000
    update_data = json.loads(update_resp)
    print(f"    Write: {write_ms:.0f}ms -> {update_data}")

    # Read it back
    t_read = time.time()
    resp2 = handle_get({"id": KNOWN_FDO_ID})
    read_ms = (time.time() - t_read) * 1000
    after = json.loads(resp2)
    after_confidence = after.get("confidence")
    print(f"    Read-back: {read_ms:.0f}ms, confidence={after_confidence}")

    # Restore original
    handle_update({"id": KNOWN_FDO_ID, "fields": {"confidence": before_confidence}})

    record("write succeeds", "updated" in update_data, f"resp={update_data}")
    record("read-back correct", after_confidence == new_confidence, f"got={after_confidence}")
    record("read-back after write < 2000ms", read_ms < 2000, f"read_ms={read_ms:.0f}")
    record("write < 2000ms", write_ms < 2000, f"write_ms={write_ms:.0f}")


# ── Test 7: _ensure_indexed thread safety ────────────────────────────────────

def test_ensure_indexed_thread_safety():
    print("\n[7] _ensure_indexed() thread safety (20 concurrent threads)")

    # Force re-initialization to test the race
    search_engine.invalidate()

    errors: list[str] = []
    lock = threading.Lock()

    def call_ensure_indexed():
        try:
            search_engine._ensure_indexed()
        except Exception as e:
            with lock:
                errors.append(str(e))

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(call_ensure_indexed) for _ in range(20)]
        for f in futures:
            f.result(timeout=15)
    total_ms = (time.time() - t0) * 1000

    # Verify state is consistent
    bm25_count = len(search_engine._bm25._docs)
    graph_count = len(search_engine._graph._adjacency)
    print(f"    Done in {total_ms:.0f}ms — bm25={bm25_count}, graph={graph_count}")

    record("no exceptions in concurrent _ensure_indexed", len(errors) == 0, f"errors={errors}")
    record("bm25 index populated", bm25_count > 0, f"bm25_docs={bm25_count}")
    record("graph index populated", graph_count > 0, f"graph_nodes={graph_count}")


# ── Test 8: validate handler ──────────────────────────────────────────────────

def test_validate():
    print("\n[8] handle_validate timing")
    import json

    resp, ms = elapsed(handle_validate, {})
    ms *= 1000
    data = json.loads(resp)
    total = data.get("total_fdos", 0)
    issues = data.get("issues_count", 0)
    print(f"    Validated {total} FDOs, {issues} issues in {ms:.0f}ms")

    record("validate returns results", total > 0, f"fdos={total}")
    record("validate < 3000ms", ms < 3000, f"ms={ms:.0f}")


# ── Test 9: handle_get for non-existent FDO ──────────────────────────────────

def test_get_missing():
    print("\n[9] handle_get for non-existent FDO")
    import json

    resp, ms = elapsed(handle_get, {"id": "does-not-exist-xyz"})
    ms *= 1000
    data = json.loads(resp)
    print(f"    Response in {ms:.0f}ms: {data}")

    record("missing FDO returns error", "error" in data, f"resp={data}")
    record("missing FDO fast", ms < 500, f"ms={ms:.0f}")


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all():
    print("\n" + "=" * 60)
    print("KRONOS MCP HANDLER UNIT TESTS")
    print("=" * 60)

    test_get_timing()
    test_list_timing()
    test_search_no_semantic()
    test_concurrent_gets()
    test_get_during_semantic_preload()
    test_write_then_read()
    test_ensure_indexed_thread_safety()
    test_validate()
    test_get_missing()

    print("\n" + "=" * 60)
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = sum(1 for _, s, _ in results if s == FAIL)
    print(f"RESULTS: {passed} passed, {failed} failed")
    if failed:
        print("\nFailed tests:")
        for name, status, detail in results:
            if status == FAIL:
                print(f"  ✗ {name}: {detail}")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
