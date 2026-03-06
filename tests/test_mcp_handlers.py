"""
Unit tests for Kronos MCP handler functions.

Tests handlers directly (no subprocess/protocol overhead) to isolate:
  1. Handler timing -- each should complete fast after initial build
  2. Thread safety -- concurrent calls must not corrupt state
  3. Post-write correctness -- reads still work after a write
  4. Semantic preload non-blocking -- model load doesn't stall gets
  5. Write handlers don't call search_engine._ensure_indexed()
  6. Tags handler correctness and timing
  7. Create/update incremental index (no full rebuild on write)
  8. Concurrent creates don't corrupt state
  9. Skills handlers

Run:
    PYTHONPATH=mcp/kronos/src KRONOS_VAULT_PATH=../kronos-vault \\
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

# -- Import handlers ----------------------------------------------------------

print("Importing server module (preload thread starts)...")
t0 = time.time()
import kronos_mcp.server as _server
from kronos_mcp.server import (
    handle_get, handle_list, handle_graph, handle_validate,
    handle_search, handle_update, handle_create, handle_tags,
    handle_skills, handle_skill_load,
    handle_navigate, handle_read_source, handle_search_source,
    handle_deep_dive, handle_validate_sources,
    handle_find_implementation, handle_git_recent,
    _ensure_initialized,
)
# Engines are lazy-initialized — trigger init for direct handler testing
_ensure_initialized()
import_time = time.time() - t0
print(f"Import done in {import_time:.1f}s")


# -- Helpers ------------------------------------------------------------------

KNOWN_FDO_ID = "grim-architecture"
KNOWN_DOMAIN = "ai-systems"
TEST_FDO_ID = "_test-handler-temp-fdo"

PASS = "PASS"
FAIL = "FAIL"

results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = ""):
    status = PASS if ok else FAIL
    results.append((name, status, detail))
    icon = "[OK]" if ok else "[FAIL]"
    print(f"  {icon} {name}: {detail}")


def elapsed(fn, *args, **kwargs):
    t = time.time()
    result = fn(*args, **kwargs)
    return result, time.time() - t


def cleanup_fdo(fdo_id: str, domain: str = "ai-systems"):
    path = Path(vault_path) / domain / f"{fdo_id}.md"
    if path.exists():
        path.unlink()
    if _server.vault is not None and _server.vault._index is not None:
        _server.vault._index.pop(fdo_id, None)


# -- Test 1: handle_get timing ------------------------------------------------

def test_get_timing():
    print("\n[1] handle_get timing")
    import json

    resp, warmup_ms = elapsed(handle_get, {"id": KNOWN_FDO_ID})
    warmup_ms *= 1000
    print(f"    Warm-up: {warmup_ms:.0f}ms")

    times = []
    for _ in range(5):
        resp, ms = elapsed(handle_get, {"id": KNOWN_FDO_ID})
        times.append(ms * 1000)

    avg_ms = sum(times) / len(times)
    max_ms = max(times)
    print(f"    Subsequent: avg={avg_ms:.0f}ms max={max_ms:.0f}ms")

    data = json.loads(resp)
    record("get returns correct FDO", data.get("id") == KNOWN_FDO_ID, f"id={data.get('id')}")
    record("get subsequent < 100ms", max_ms < 100, f"max={max_ms:.0f}ms")


# -- Test 2: handle_list timing -----------------------------------------------

def test_list_timing():
    print("\n[2] handle_list timing")
    import json

    resp, ms = elapsed(handle_list, {})
    ms *= 1000
    data = json.loads(resp)
    count = data.get("count", 0)
    print(f"    Listed {count} FDOs in {ms:.0f}ms")
    record("list returns FDOs", count > 0, f"count={count}")
    record("list < 200ms", ms < 200, f"ms={ms:.0f}")

    resp2, ms2 = elapsed(handle_list, {"domain": KNOWN_DOMAIN})
    ms2 *= 1000
    data2 = json.loads(resp2)
    record("list domain filter works", data2.get("count", 0) > 0, f"count={data2.get('count',0)}")
    record("list domain < 100ms", ms2 < 100, f"ms={ms2:.0f}")


# -- Test 3: handle_search (no semantic) timing --------------------------------

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


# -- Test 4: concurrent handle_get --------------------------------------------

def test_concurrent_gets():
    print("\n[4] concurrent handle_get (10 threads)")
    import json

    ids_to_fetch = [
        "grim-architecture", "grim-identity", "grim-skills",
        "grim-langgraph", "grim-server-ui", "proj-grim",
        "kronos-vault", "coding-integration",
    ]
    errors: list[str] = []
    times: list[float] = []
    lock = threading.Lock()

    def fetch(fdo_id: str):
        t = time.time()
        try:
            resp = handle_get({"id": fdo_id})
            data = json.loads(resp)
            if data.get("id") != fdo_id:
                with lock:
                    errors.append(f"Wrong FDO for {fdo_id}: got {data.get('id')}")
        except Exception as e:
            with lock:
                errors.append(f"Exception for {fdo_id}: {e}")
        finally:
            with lock:
                times.append(time.time() - t)

    requests = (ids_to_fetch * 2)[:10]
    t_total = time.time()
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(fetch, fdo_id) for fdo_id in requests]
        for f in futures:
            f.result(timeout=10)
    total_ms = (time.time() - t_total) * 1000
    avg_ms = (sum(times) / len(times)) * 1000

    print(f"    Total: {total_ms:.0f}ms, avg per call: {avg_ms:.0f}ms")
    record("concurrent gets -- no exceptions", len(errors) == 0, f"errors={errors}")
    record("concurrent gets -- no deadlock (< 5s)", total_ms < 5000, f"total={total_ms:.0f}ms")


# -- Test 5: handle_get during semantic preload --------------------------------

def test_get_during_semantic_preload():
    print("\n[5] handle_get while semantic preload may still be running")
    import json

    t0 = time.time()
    resp = handle_get({"id": KNOWN_FDO_ID})
    ms = (time.time() - t0) * 1000
    data = json.loads(resp)

    sem_status = _server.search_engine._semantic_indexed
    sem_loading = _server.search_engine._semantic_loading
    print(f"    handle_get in {ms:.0f}ms, semantic_indexed={sem_status}, loading={sem_loading}")

    record("get during preload -- correct data", data.get("id") == KNOWN_FDO_ID)
    record("get during preload -- < 500ms", ms < 500, f"ms={ms:.0f}")


# -- Test 6: handle_update -> handle_get round-trip ---------------------------

def test_write_then_read():
    print("\n[6] handle_update -> handle_get round-trip")
    import json

    resp = handle_get({"id": KNOWN_FDO_ID})
    before = json.loads(resp)
    before_confidence = before.get("confidence")

    new_confidence = 0.88
    t_write = time.time()
    update_resp = handle_update({"id": KNOWN_FDO_ID, "fields": {"confidence": new_confidence}})
    write_ms = (time.time() - t_write) * 1000
    update_data = json.loads(update_resp)
    print(f"    Write: {write_ms:.0f}ms -> {update_data}")

    t_read = time.time()
    resp2 = handle_get({"id": KNOWN_FDO_ID})
    read_ms = (time.time() - t_read) * 1000
    after = json.loads(resp2)
    after_confidence = after.get("confidence")
    print(f"    Read-back: {read_ms:.0f}ms, confidence={after_confidence}")

    handle_update({"id": KNOWN_FDO_ID, "fields": {"confidence": before_confidence}})

    record("write succeeds", "updated" in update_data, f"resp={update_data}")
    record("read-back correct", after_confidence == new_confidence, f"got={after_confidence}")
    record("read-back after write < 100ms", read_ms < 100, f"read_ms={read_ms:.0f}")
    record("write < 500ms", write_ms < 500, f"write_ms={write_ms:.0f}")


# -- Test 7: _ensure_indexed thread safety ------------------------------------

def test_ensure_indexed_thread_safety():
    print("\n[7] _ensure_indexed() thread safety (20 concurrent threads)")

    _server.search_engine.invalidate()

    errors: list[str] = []
    lock = threading.Lock()

    def call_ensure_indexed():
        try:
            _server.search_engine._ensure_indexed()
        except Exception as e:
            with lock:
                errors.append(str(e))

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(call_ensure_indexed) for _ in range(20)]
        for f in futures:
            f.result(timeout=15)
    total_ms = (time.time() - t0) * 1000

    bm25_count = len(_server.search_engine._bm25._docs)
    graph_count = len(_server.search_engine._graph._adjacency)
    print(f"    Done in {total_ms:.0f}ms -- bm25={bm25_count}, graph={graph_count}")

    record("no exceptions in concurrent _ensure_indexed", len(errors) == 0, f"errors={errors}")
    record("bm25 index populated", bm25_count > 0, f"bm25_docs={bm25_count}")
    record("graph index populated", graph_count > 0, f"graph_nodes={graph_count}")


# -- Test 8: handle_validate --------------------------------------------------

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


# -- Test 9: missing FDO ------------------------------------------------------

def test_get_missing():
    print("\n[9] handle_get for non-existent FDO")
    import json

    resp, ms = elapsed(handle_get, {"id": "does-not-exist-xyz"})
    ms *= 1000
    data = json.loads(resp)
    print(f"    Response in {ms:.0f}ms: {data}")

    record("missing FDO returns error", "error" in data, f"resp={data}")
    record("missing FDO fast", ms < 100, f"ms={ms:.0f}")


# -- Test 10: handle_tags correctness and timing ------------------------------

def test_tags():
    print("\n[10] handle_tags correctness and timing")
    import json

    resp, ms = elapsed(handle_tags, {})
    ms *= 1000
    data = json.loads(resp)
    total_tags = data.get("total_tags", 0)
    top = data.get("top_tags", [])
    print(f"    {total_tags} tags in {ms:.0f}ms, top: {[t['tag'] for t in top[:5]]}")

    record("tags returns results", total_tags > 0, f"total_tags={total_tags}")
    record("tags has top_tags list", len(top) > 0)
    record("tags < 200ms", ms < 200, f"ms={ms:.0f}")

    resp2, ms2 = elapsed(handle_tags, {"domain": KNOWN_DOMAIN})
    ms2 *= 1000
    data2 = json.loads(resp2)
    record("tags domain filter works", data2.get("total_tags", 0) > 0, f"domain={KNOWN_DOMAIN}")
    record("tags domain filter < 100ms", ms2 < 100, f"ms={ms2:.0f}")


# -- Test 11: handle_create correctness and timing ----------------------------

def test_create():
    print("\n[11] handle_create correctness and timing")
    import json

    cleanup_fdo(TEST_FDO_ID)

    t_create = time.time()
    resp = handle_create({
        "id": TEST_FDO_ID,
        "title": "TEST: Temporary Handler Test FDO",
        "domain": "ai-systems",
        "status": "seed",
        "confidence": 0.5,
        "tags": ["test", "temporary"],
        "related": ["grim-architecture"],
        "source_repos": ["GRIM"],
        "body": "# TEST: Temporary Handler Test FDO\n\n## Summary\n\nTemporary FDO created by test_mcp_handlers.py.\n",
    })
    create_ms = (time.time() - t_create) * 1000
    data = json.loads(resp)
    print(f"    Create: {create_ms:.0f}ms -> {data}")

    record("create returns created key", "created" in data, f"resp={data}")
    record("create < 1000ms", create_ms < 1000, f"ms={create_ms:.0f}")
    record("no error in create", "error" not in data, f"resp={data}")

    # Verify immediate get
    t_get = time.time()
    get_resp = handle_get({"id": TEST_FDO_ID})
    get_ms = (time.time() - t_get) * 1000
    get_data = json.loads(get_resp)
    print(f"    Get after create: {get_ms:.0f}ms, id={get_data.get('id')}")

    record("get after create returns FDO", get_data.get("id") == TEST_FDO_ID, f"got={get_data.get('id')}")
    record("get after create < 100ms", get_ms < 100, f"ms={get_ms:.0f}")


# -- Test 12: duplicate create rejected ---------------------------------------

def test_create_duplicate_rejected():
    print("\n[12] handle_create duplicate ID rejected")
    import json

    resp = handle_create({
        "id": TEST_FDO_ID,
        "title": "Duplicate",
        "domain": "ai-systems",
        "confidence": 0.1,
        "body": "Duplicate body",
    })
    data = json.loads(resp)
    print(f"    Response: {data}")
    record("duplicate create returns error", "error" in data, f"resp={data}")


# -- Test 13: invalid domain rejected -----------------------------------------

def test_create_invalid_domain():
    print("\n[13] handle_create invalid domain rejected")
    import json

    resp = handle_create({
        "id": "_test-invalid-domain",
        "title": "Invalid Domain Test",
        "domain": "not-a-real-domain",
        "confidence": 0.1,
        "body": "Body",
    })
    data = json.loads(resp)
    print(f"    Response: {data}")
    record("invalid domain returns error", "error" in data, f"resp={data}")


# -- Test 14: update then search finds new content ----------------------------

def test_update_then_search():
    print("\n[14] handle_update then search finds updated content")
    import json

    unique_tag = "test-unique-tag-xyzzy-12345"
    handle_update({"id": TEST_FDO_ID, "fields": {"tags": ["test", "temporary", unique_tag]}})

    resp, ms = elapsed(handle_search, {"query": unique_tag, "semantic": False})
    ms *= 1000
    data = json.loads(resp)
    results_ids = [r["id"] for r in data.get("results", [])]
    print(f"    Search for unique tag in {ms:.0f}ms, found: {results_ids}")

    record("search finds updated FDO", TEST_FDO_ID in results_ids, f"results={results_ids}")
    record("search after update < 1000ms", ms < 1000, f"ms={ms:.0f}")


# -- Test 15: concurrent creates ----------------------------------------------

def test_concurrent_creates():
    print("\n[15] concurrent handle_create (5 threads, different IDs)")
    import json

    base_id = "_test-concurrent-create"
    created_ids = [f"{base_id}-{i}" for i in range(5)]

    for fdo_id in created_ids:
        cleanup_fdo(fdo_id)

    errors: list[str] = []
    successes: list[str] = []
    lock = threading.Lock()

    def do_create(fdo_id: str):
        try:
            resp = handle_create({
                "id": fdo_id,
                "title": f"Concurrent Create Test {fdo_id}",
                "domain": "ai-systems",
                "confidence": 0.1,
                "tags": ["test", "concurrent"],
                "body": f"# {fdo_id}\n\n## Summary\n\nConcurrent create test.\n",
            })
            data = json.loads(resp)
            with lock:
                if "created" in data:
                    successes.append(fdo_id)
                else:
                    errors.append(f"{fdo_id}: {data}")
        except Exception as e:
            with lock:
                errors.append(f"{fdo_id}: {e}")

    t_total = time.time()
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(do_create, fdo_id) for fdo_id in created_ids]
        for f in futures:
            f.result(timeout=10)
    total_ms = (time.time() - t_total) * 1000

    print(f"    Created {len(successes)}/5 in {total_ms:.0f}ms, errors={errors}")

    record("concurrent creates -- no exceptions", len(errors) == 0, f"errors={errors}")
    record("concurrent creates -- all 5 succeed", len(successes) == 5, f"successes={len(successes)}")
    record("concurrent creates -- total < 5s", total_ms < 5000, f"ms={total_ms:.0f}")

    for fdo_id in created_ids:
        resp = handle_get({"id": fdo_id})
        data = json.loads(resp)
        record(f"readable after concurrent create: {fdo_id[-1]}",
               data.get("id") == fdo_id, f"got={data.get('id')}")

    for fdo_id in created_ids:
        cleanup_fdo(fdo_id)


# -- Test 16: write does NOT trigger full BM25 rebuild ------------------------

def test_write_does_not_rebuild_search_index():
    """After a write, BM25 index should stay initialized (no wipe+rebuild)."""
    print("\n[16] write handlers don't trigger full BM25 rebuild")

    _server.search_engine._ensure_indexed()
    before_count = len(_server.search_engine._bm25._docs)
    before_initialized = _server.search_engine._initialized

    handle_update({"id": KNOWN_FDO_ID, "fields": {"status": "stable"}})

    after_count = len(_server.search_engine._bm25._docs)
    after_initialized = _server.search_engine._initialized

    print(f"    BM25 docs: before={before_count}, after={after_count}")
    print(f"    _initialized: before={before_initialized}, after={after_initialized}")

    record("write keeps search index initialized", after_initialized == True,
           f"_initialized={after_initialized}")
    record("write doesn't wipe BM25 docs", after_count >= before_count,
           f"before={before_count} after={after_count}")


# -- Test 17: handle_skills ---------------------------------------------------

def test_skills():
    print("\n[17] handle_skills + handle_skill_load")
    import json

    resp, ms = elapsed(handle_skills, {})
    ms *= 1000
    data = json.loads(resp)

    if "error" in data:
        print(f"    Skills not configured: {data['error']}")
        record("skills handler responds", True, "not configured -- ok")
        return

    skills = data.get("skills", [])
    print(f"    Found {len(skills)} skills in {ms:.0f}ms")
    record("skills returns list", isinstance(skills, list), f"count={len(skills)}")
    record("skills < 500ms", ms < 500, f"ms={ms:.0f}")

    if skills:
        skill_name = skills[0].get("name", "")
        resp2, ms2 = elapsed(handle_skill_load, {"name": skill_name})
        ms2 *= 1000
        data2 = json.loads(resp2)
        print(f"    Loaded skill '{skill_name}' in {ms2:.0f}ms")
        record("skill_load returns content", "protocol" in data2 or "name" in data2,
               f"keys={list(data2.keys())}")
        record("skill_load < 500ms", ms2 < 500, f"ms={ms2:.0f}")


# -- Test 18: handle_graph ----------------------------------------------------

def test_graph():
    print("\n[18] handle_graph timing and correctness")
    import json

    resp, ms = elapsed(handle_graph, {"id": KNOWN_FDO_ID, "depth": 1})
    ms *= 1000
    data = json.loads(resp)
    nodes = data.get("nodes", {})
    edges = data.get("edges", [])
    print(f"    Graph: {len(nodes)} nodes, {len(edges)} edges in {ms:.0f}ms")

    record("graph returns nodes", len(nodes) > 0, f"nodes={len(nodes)}")
    record("graph returns edges", len(edges) > 0, f"edges={len(edges)}")
    record("graph center in nodes", KNOWN_FDO_ID in nodes)
    record("graph < 200ms", ms < 200, f"ms={ms:.0f}")


# -- Test 19: create then search finds it -------------------------------------

def test_create_then_search():
    print("\n[19] create then search finds new FDO immediately")
    import json

    unique_tag = "test-create-search-verification-alpha99"
    temp_id = "_test-create-search-temp"
    cleanup_fdo(temp_id)

    handle_create({
        "id": temp_id,
        "title": "Temp Create-Search Test FDO",
        "domain": "ai-systems",
        "confidence": 0.1,
        "tags": ["test", unique_tag],
        "body": f"# Temp\n\n## Summary\n\nSearch verification FDO.\n",
    })

    resp, ms = elapsed(handle_search, {"query": unique_tag, "semantic": False})
    ms *= 1000
    data = json.loads(resp)
    found_ids = [r["id"] for r in data.get("results", [])]
    print(f"    Search in {ms:.0f}ms, found: {found_ids}")

    record("search finds newly created FDO", temp_id in found_ids, f"found={found_ids}")
    record("create-then-search < 1000ms", ms < 1000, f"ms={ms:.0f}")

    cleanup_fdo(temp_id)


# -- Test 20: handle_navigate ------------------------------------------------

def test_navigate():
    print("\n[20] handle_navigate")
    import json

    resp, ms = elapsed(handle_navigate, {"path": "GRIM/core/pool"})
    ms *= 1000
    data = json.loads(resp)
    print(f"    Navigate GRIM/core/pool in {ms:.0f}ms")

    record("navigate returns path", data.get("path") == "GRIM/core/pool")
    record("navigate has listing", "listing" in data)
    listing = data.get("listing", {})
    record("navigate has files", len(listing.get("files", [])) > 0,
           f"files={listing.get('files', [])}")
    record("navigate < 500ms", ms < 500, f"ms={ms:.0f}")

    # Non-existent path
    resp2 = handle_navigate({"path": "nonexistent-repo-xyz/abc"})
    data2 = json.loads(resp2)
    record("navigate missing path returns error", "error" in data2)


# -- Test 21: handle_read_source --------------------------------------------

def test_read_source():
    print("\n[21] handle_read_source")
    import json

    resp, ms = elapsed(handle_read_source, {
        "repo": "GRIM", "path": "mcp/kronos/src/kronos_mcp/vault.py",
    })
    ms *= 1000
    data = json.loads(resp)
    print(f"    Read vault.py in {ms:.0f}ms, {data.get('total_lines', 0)} lines")

    record("read_source returns content", len(data.get("content", "")) > 0)
    record("read_source returns total_lines", data.get("total_lines", 0) > 0)
    record("read_source offset is 0", data.get("offset") == 0)
    record("read_source < 500ms", ms < 500, f"ms={ms:.0f}")

    # Pagination test
    resp2 = handle_read_source({
        "repo": "GRIM", "path": "mcp/kronos/src/kronos_mcp/vault.py",
        "offset": 10, "max_lines": 20,
    })
    data2 = json.loads(resp2)
    record("read_source pagination offset works", data2.get("offset") == 10)
    record("read_source pagination lines_returned", data2.get("lines_returned") == 20)

    # Missing file
    resp3 = handle_read_source({"repo": "GRIM", "path": "nonexistent_file.py"})
    data3 = json.loads(resp3)
    record("read_source missing file returns error", "error" in data3)


# -- Test 22: handle_deep_dive ----------------------------------------------

def test_deep_dive():
    print("\n[22] handle_deep_dive")
    import json

    resp, ms = elapsed(handle_deep_dive, {"query": "grim-architecture"})
    ms *= 1000
    data = json.loads(resp)
    root = data.get("root", "")
    traversed = data.get("fdos_traversed", 0)
    print(f"    Deep dive '{root}' in {ms:.0f}ms, {traversed} FDOs traversed")

    record("deep_dive returns root", root == "grim-architecture")
    record("deep_dive traverses FDOs", traversed > 0)
    record("deep_dive has sources_by_fdo", "sources_by_fdo" in data)
    record("deep_dive has sources_by_repo", "sources_by_repo" in data)
    record("deep_dive < 2000ms", ms < 2000, f"ms={ms:.0f}")


# -- Test 23: handle_validate_sources ---------------------------------------

def test_validate_sources():
    print("\n[23] handle_validate_sources")
    import json

    resp, ms = elapsed(handle_validate_sources, {})
    ms *= 1000
    data = json.loads(resp)
    checked = data.get("total_fdos_checked", 0)
    paths = data.get("total_paths_checked", 0)
    broken = data.get("broken_count", 0)
    print(f"    Validated {checked} FDOs, {paths} paths, {broken} broken in {ms:.0f}ms")

    record("validate_sources checks FDOs", checked > 0, f"fdos={checked}")
    record("validate_sources checks paths", paths > 0, f"paths={paths}")
    record("validate_sources returns broken list", isinstance(data.get("broken"), list))
    record("validate_sources < 5000ms", ms < 5000, f"ms={ms:.0f}")

    # Domain filter
    resp2 = handle_validate_sources({"domain": "ai-systems"})
    data2 = json.loads(resp2)
    record("validate_sources domain filter works", data2.get("domain_filter") == "ai-systems")

    # Repo filter
    resp3 = handle_validate_sources({"repo": "GRIM"})
    data3 = json.loads(resp3)
    record("validate_sources repo filter works", data3.get("repo_filter") == "GRIM")


# -- Test 24: handle_find_implementation ------------------------------------

def test_find_implementation():
    print("\n[24] handle_find_implementation")
    import json

    # Find a known function
    resp, ms = elapsed(handle_find_implementation, {
        "repo": "GRIM", "symbol": "handle_search", "kind": "function",
    })
    ms *= 1000
    data = json.loads(resp)
    count = data.get("results_count", 0)
    print(f"    Found {count} results for 'handle_search' in {ms:.0f}ms")

    record("find_impl finds function", count > 0, f"count={count}")
    if count > 0:
        first = data["results"][0]
        record("find_impl returns file path", "file" in first)
        record("find_impl returns line number", first.get("line", 0) > 0)
        record("find_impl returns kind", first.get("kind") == "function")
        record("find_impl returns context", len(first.get("context", "")) > 0)

    # Find a known class
    resp2 = handle_find_implementation({
        "repo": "GRIM", "symbol": "BM25Index", "kind": "class",
    })
    data2 = json.loads(resp2)
    record("find_impl finds class", data2.get("results_count", 0) > 0)

    # Path filter
    resp3 = handle_find_implementation({
        "repo": "GRIM", "symbol": "JobType", "path_filter": "core/pool",
    })
    data3 = json.loads(resp3)
    record("find_impl path_filter works", data3.get("results_count", 0) > 0)
    if data3.get("results"):
        record("find_impl path_filter correct path",
               "core/pool" in data3["results"][0].get("file", ""))

    # Missing repo
    resp4 = handle_find_implementation({"repo": "nonexistent-xyz", "symbol": "foo"})
    data4 = json.loads(resp4)
    record("find_impl missing repo returns error", "error" in data4)

    # No results
    resp5 = handle_find_implementation({
        "repo": "GRIM", "symbol": "xyzzy_nonexistent_symbol_abc_999",
    })
    data5 = json.loads(resp5)
    record("find_impl no results returns zero", data5.get("results_count") == 0)
    record("find_impl no results no error", "error" not in data5)

    record("find_impl < 10000ms", ms < 10000, f"ms={ms:.0f}")


# -- Test 25: handle_git_recent ---------------------------------------------

def test_git_recent():
    print("\n[25] handle_git_recent")
    import json

    resp, ms = elapsed(handle_git_recent, {"repo": "GRIM", "days": 7})
    ms *= 1000
    data = json.loads(resp)
    count = data.get("commits_count", 0)
    print(f"    Got {count} commits from GRIM in {ms:.0f}ms")

    record("git_recent returns commits", count >= 0)
    record("git_recent returns repo", data.get("repo") == "GRIM")
    if count > 0:
        first = data["commits"][0]
        record("git_recent commit has hash", len(first.get("hash", "")) > 0)
        record("git_recent commit has message", len(first.get("message", "")) > 0)
        record("git_recent commit has date", len(first.get("date", "")) > 0)

    record("git_recent < 5000ms", ms < 5000, f"ms={ms:.0f}")

    # Path filter
    resp2 = handle_git_recent({"repo": "GRIM", "path": "core/pool", "days": 30})
    data2 = json.loads(resp2)
    record("git_recent path filter works", data2.get("path") == "core/pool")

    # Missing repo
    resp3 = handle_git_recent({"repo": "nonexistent-xyz"})
    data3 = json.loads(resp3)
    record("git_recent missing repo returns error", "error" in data3)


# -- Final cleanup + runner ---------------------------------------------------

def final_cleanup():
    cleanup_fdo(TEST_FDO_ID)
    cleanup_fdo("_test-invalid-domain")


def run_all():
    print("\n" + "=" * 60)
    print("KRONOS MCP HANDLER UNIT TESTS (FULL SUITE)")
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
    test_tags()
    test_create()
    test_create_duplicate_rejected()
    test_create_invalid_domain()
    test_update_then_search()
    test_concurrent_creates()
    test_write_does_not_rebuild_search_index()
    test_skills()
    test_graph()
    test_create_then_search()
    test_navigate()
    test_read_source()
    test_deep_dive()
    test_validate_sources()
    test_find_implementation()
    test_git_recent()

    final_cleanup()

    print("\n" + "=" * 60)
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = sum(1 for _, s, _ in results if s == FAIL)
    print(f"RESULTS: {passed} passed, {failed} failed")
    if failed:
        print("\nFailed tests:")
        for name, status, detail in results:
            if status == FAIL:
                print(f"  FAIL {name}: {detail}")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
