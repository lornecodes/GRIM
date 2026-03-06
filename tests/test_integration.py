"""
GRIM Integration Tests — verify the running container end-to-end.

Starts GRIM via docker compose, tests all endpoints, then stops it.
Tests are grouped into tiers:
  1. Infrastructure — health, static files, server config
  2. MCP connectivity — direct MCP test endpoint
  3. REST chat — POST /api/chat (requires ANTHROPIC_API_KEY)
  4. WebSocket chat — WS /ws/{sid} with streaming (requires ANTHROPIC_API_KEY)
  5. Session persistence — reconnect and verify state

Run:
    python tests/test_integration.py                  # All tests
    python tests/test_integration.py --no-llm         # Skip LLM-dependent tests
    python tests/test_integration.py --keep            # Don't stop container after
    python tests/test_integration.py --port 9090       # Custom port
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────────────────

GRIM_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8080
STARTUP_TIMEOUT = 120    # max seconds to wait for health check
HEALTH_INTERVAL = 2      # seconds between health polls
WS_TIMEOUT = 60          # WebSocket response timeout
REST_TIMEOUT = 60        # REST chat timeout

# ── Results tracking ─────────────────────────────────────────────────────────

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
_results: list[tuple[str, str, str]] = []  # (name, status, detail)


def record(name: str, passed: bool, detail: str = "") -> None:
    status = PASS if passed else FAIL
    _results.append((name, status, detail))
    icon = "[OK]" if passed else "[FAIL]"
    print(f"  {icon} {name}" + (f": {detail}" if detail else ""))
    if not passed:
        raise AssertionError(f"{name}: {detail}")


def skip(name: str, reason: str = "") -> None:
    _results.append((name, SKIP, reason))
    print(f"  [SKIP] {name}" + (f": {reason}" if reason else ""))


class AssertionError(Exception):
    """Non-fatal test assertion — caught by test runner."""
    pass


# ── Docker helpers ───────────────────────────────────────────────────────────

def container_running() -> bool:
    """Check if GRIM container is already running."""
    try:
        out = subprocess.check_output(
            ["docker", "ps", "--filter", "name=grim", "--format", "{{.Names}}"],
            stderr=subprocess.DEVNULL, text=True,
        )
        return "grim" in out
    except Exception:
        return False


def start_grim(port: int) -> bool:
    """Start GRIM via docker compose. Returns True if started."""
    env = os.environ.copy()
    env["MSYS_NO_PATHCONV"] = "1"
    env["GRIM_PORT"] = str(port)

    print(f"  Starting GRIM on port {port} ...")
    result = subprocess.run(
        ["docker", "compose", "-f", str(GRIM_ROOT / "docker-compose.yml"),
         "up", "-d", "--build"],
        cwd=str(GRIM_ROOT), env=env,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  ERROR: docker compose up failed:\n{result.stderr[:500]}")
        return False
    return True


def stop_grim() -> None:
    """Stop GRIM container."""
    env = os.environ.copy()
    env["MSYS_NO_PATHCONV"] = "1"
    subprocess.run(
        ["docker", "compose", "-f", str(GRIM_ROOT / "docker-compose.yml"), "down"],
        cwd=str(GRIM_ROOT), env=env,
        capture_output=True, text=True,
    )


def wait_for_health(port: int, timeout: int = STARTUP_TIMEOUT) -> bool:
    """Poll health endpoint until it responds or timeout."""
    url = f"http://localhost:{port}/health"
    deadline = time.time() + timeout
    last_error = ""

    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read())
                    if data.get("status") == "ok":
                        return True
        except Exception as e:
            last_error = str(e)
        time.sleep(HEALTH_INTERVAL)

    print(f"  Health check timeout after {timeout}s: {last_error}")
    return False


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def http_get(port: int, path: str, timeout: int = 10) -> tuple[int, str, dict]:
    """GET request. Returns (status_code, body, headers)."""
    url = f"http://localhost:{port}{path}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            headers = dict(resp.headers)
            return resp.status, body, headers
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        return e.code, body, {}
    except Exception as e:
        return 0, str(e), {}


def http_post_json(port: int, path: str, data: dict, timeout: int = 60) -> tuple[int, dict]:
    """POST JSON. Returns (status_code, json_body)."""
    url = f"http://localhost:{port}{path}"
    body = json.dumps(data).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode()) if e.fp else {}
        return e.code, body
    except Exception as e:
        return 0, {"error": str(e)}


# ── WebSocket helper (minimal, no deps) ─────────────────────────────────────

class SimpleWebSocket:
    """Minimal WebSocket client using only stdlib (for integration tests).

    Uses subprocess to run a small Python script that does the WS exchange,
    since stdlib doesn't have WebSocket support.
    """

    @staticmethod
    def chat(port: int, session_id: str, message: str,
             timeout: int = WS_TIMEOUT) -> dict:
        """Send a message via WebSocket, collect all responses until 'response' type.

        Returns dict with keys: connected, messages, response, error, elapsed_ms
        """
        script = f'''
import asyncio, json, sys
try:
    import websockets
except ImportError:
    print(json.dumps({{"error": "websockets not installed"}}))
    sys.exit(0)

async def run():
    uri = "ws://localhost:{port}/ws/{session_id}"
    msgs = []
    try:
        async with websockets.connect(uri, close_timeout=5) as ws:
            await ws.send(json.dumps({{"message": {json.dumps(message)}}}))
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout={timeout})
                    data = json.loads(raw)
                    msgs.append(data)
                    if data.get("type") in ("response", "error"):
                        break
                except asyncio.TimeoutError:
                    msgs.append({{"type": "error", "content": "WS_TIMEOUT"}})
                    break
        print(json.dumps({{"connected": True, "messages": msgs}}))
    except Exception as e:
        print(json.dumps({{"connected": False, "error": str(e), "messages": msgs}}))

asyncio.run(run())
'''
        t0 = time.monotonic()
        try:
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, timeout=timeout + 10,
            )
            elapsed = round((time.monotonic() - t0) * 1000)
            if result.returncode != 0:
                return {"error": f"subprocess failed: {result.stderr[:300]}",
                        "elapsed_ms": elapsed}
            output = result.stdout.strip()
            if not output:
                return {"error": "no output from WS script", "elapsed_ms": elapsed}
            data = json.loads(output)
            data["elapsed_ms"] = elapsed
            return data
        except subprocess.TimeoutExpired:
            return {"error": "subprocess timeout", "elapsed_ms": timeout * 1000}
        except Exception as e:
            return {"error": str(e), "elapsed_ms": 0}


# ── Test suites ──────────────────────────────────────────────────────────────

def test_infrastructure(port: int) -> int:
    """Tier 1: Health check, static files, server config."""
    print("\n[1] Infrastructure")
    failed = 0

    # Health endpoint
    status, body, _ = http_get(port, "/health")
    try:
        record("health returns 200", status == 200, f"status={status}")
    except AssertionError:
        failed += 1

    try:
        data = json.loads(body)
        record("health status is ok", data.get("status") == "ok", f"data={data}")
    except (json.JSONDecodeError, AssertionError) as e:
        if isinstance(e, AssertionError):
            failed += 1
        else:
            record("health returns valid JSON", False, f"body={body[:100]}")
            failed += 1

    try:
        record("health reports graph ready", data.get("graph") is True,
               f"graph={data.get('graph')}")
    except AssertionError:
        failed += 1

    try:
        record("health reports vault path", data.get("vault") is not None,
               f"vault={data.get('vault')}")
    except AssertionError:
        failed += 1

    # Root serves UI
    status, body, headers = http_get(port, "/")
    try:
        record("root returns 200", status == 200, f"status={status}")
    except AssertionError:
        failed += 1

    try:
        record("root serves HTML", "<html" in body.lower() or "<!doctype" in body.lower(),
               f"length={len(body)}")
    except AssertionError:
        failed += 1

    try:
        record("HTML has GRIM reference", "grim" in body.lower(),
               f"body_preview={body[:200]}")
    except AssertionError:
        failed += 1

    # FastAPI docs (auto-generated)
    status, body, _ = http_get(port, "/docs")
    try:
        record("OpenAPI docs accessible", status == 200, f"status={status}")
    except AssertionError:
        failed += 1

    # 404 for bad routes
    status, body, _ = http_get(port, "/nonexistent-route-xyz")
    try:
        record("unknown route returns 404", status == 404, f"status={status}")
    except AssertionError:
        failed += 1

    return failed


def test_mcp_connectivity(port: int) -> int:
    """Tier 2: MCP test endpoint (direct Kronos calls)."""
    print("\n[2] MCP Connectivity")
    failed = 0

    status, body, _ = http_get(port, "/api/test-mcp", timeout=30)

    if status == 503:
        skip("MCP test endpoint", "MCP not connected (expected without vault mount)")
        return 0

    try:
        record("test-mcp returns 200", status == 200, f"status={status}")
    except AssertionError:
        failed += 1
        return failed

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        try:
            record("test-mcp returns JSON", False, f"body={body[:200]}")
        except AssertionError:
            failed += 1
        return failed

    # Direct call
    dc = data.get("direct_call", {})
    try:
        record("direct MCP call works", "error" not in dc,
               f"elapsed={dc.get('elapsed_ms')}ms, length={dc.get('length')}")
    except AssertionError:
        failed += 1

    try:
        record("direct call < 5s", dc.get("elapsed_ms", 99999) < 5000,
               f"ms={dc.get('elapsed_ms')}")
    except AssertionError:
        failed += 1

    # Wait-for call
    wf = data.get("wait_for_call", {})
    try:
        record("wait_for MCP call works", "error" not in wf,
               f"elapsed={wf.get('elapsed_ms')}ms")
    except AssertionError:
        failed += 1

    # Get call
    gc = data.get("get_call", {})
    try:
        record("kronos_get works", "error" not in gc,
               f"elapsed={gc.get('elapsed_ms')}ms, length={gc.get('length')}")
    except AssertionError:
        failed += 1

    return failed


def test_rest_chat(port: int) -> int:
    """Tier 3: REST chat endpoint (POST /api/chat)."""
    print("\n[3] REST Chat (POST /api/chat)")
    failed = 0

    # Simple greeting
    t0 = time.monotonic()
    status, data = http_post_json(port, "/api/chat",
                                  {"message": "Hello, what are you?"},
                                  timeout=REST_TIMEOUT)
    elapsed = round((time.monotonic() - t0) * 1000)

    try:
        record("chat returns 200", status == 200, f"status={status}")
    except AssertionError:
        failed += 1
        if status == 500:
            print(f"    Error: {data}")
        return failed

    try:
        record("chat has response field", "response" in data,
               f"keys={list(data.keys())}")
    except AssertionError:
        failed += 1

    response = data.get("response", "")
    try:
        record("response is non-empty", len(response) > 10,
               f"length={len(response)}")
    except AssertionError:
        failed += 1

    try:
        record("chat has session_id", "session_id" in data,
               f"sid={data.get('session_id')}")
    except AssertionError:
        failed += 1

    try:
        record("chat has mode field", "mode" in data,
               f"mode={data.get('mode')}")
    except AssertionError:
        failed += 1

    try:
        record(f"chat response time ({elapsed}ms)", elapsed < REST_TIMEOUT * 1000,
               f"ms={elapsed}")
    except AssertionError:
        failed += 1

    print(f"    Response preview: {response[:150]}...")

    # Session continuity — send with explicit session ID
    sid = data.get("session_id", "test-session")
    status2, data2 = http_post_json(port, "/api/chat",
                                    {"message": "What did I just ask you?",
                                     "session_id": sid},
                                    timeout=REST_TIMEOUT)
    try:
        record("follow-up returns 200", status2 == 200, f"status={status2}")
    except AssertionError:
        failed += 1

    try:
        record("follow-up uses same session", data2.get("session_id") == sid,
               f"expected={sid}, got={data2.get('session_id')}")
    except AssertionError:
        failed += 1

    return failed


def test_websocket_chat(port: int) -> int:
    """Tier 4: WebSocket chat with streaming."""
    print("\n[4] WebSocket Chat (WS /ws/{sid})")
    failed = 0

    result = SimpleWebSocket.chat(port, "integration-test", "What is your name?")

    if result.get("error") == "websockets not installed":
        skip("WebSocket tests", "websockets package not available")
        return 0

    try:
        record("WebSocket connects", result.get("connected", False),
               f"error={result.get('error', 'none')}")
    except AssertionError:
        failed += 1
        return failed

    messages = result.get("messages", [])
    msg_types = [m.get("type") for m in messages]

    try:
        record("received messages", len(messages) > 0,
               f"count={len(messages)}, types={msg_types}")
    except AssertionError:
        failed += 1

    # Should have trace events (node lifecycle)
    traces = [m for m in messages if m.get("type") == "trace"]
    try:
        record("received trace events", len(traces) > 0,
               f"count={len(traces)}")
    except AssertionError:
        failed += 1

    # Should see node events for key pipeline stages
    trace_texts = [t.get("text", "") for t in traces]
    node_names = [t.get("node", "") for t in traces if t.get("node")]
    expected_nodes = {"identity", "memory", "router"}
    found_nodes = expected_nodes.intersection(set(node_names))
    try:
        record("pipeline nodes observed", len(found_nodes) >= 2,
               f"expected>={2}, found={found_nodes}")
    except AssertionError:
        failed += 1

    # Should have a final response
    responses = [m for m in messages if m.get("type") == "response"]
    try:
        record("received final response", len(responses) == 1,
               f"count={len(responses)}")
    except AssertionError:
        failed += 1

    if responses:
        resp = responses[0]
        content = resp.get("content", "")
        meta = resp.get("meta", {})

        try:
            record("response has content", len(content) > 10,
                   f"length={len(content)}")
        except AssertionError:
            failed += 1

        try:
            record("response has meta", "mode" in meta,
                   f"meta_keys={list(meta.keys())}")
        except AssertionError:
            failed += 1

        try:
            record("meta has total_ms", "total_ms" in meta,
                   f"total_ms={meta.get('total_ms')}")
        except AssertionError:
            failed += 1

        print(f"    Response preview: {content[:150]}...")

    # Check for stream tokens
    streams = [m for m in messages if m.get("type") == "stream"]
    try:
        record("received stream tokens", len(streams) > 0,
               f"count={len(streams)}")
    except AssertionError:
        failed += 1

    elapsed = result.get("elapsed_ms", 0)
    try:
        record(f"WebSocket round-trip ({elapsed}ms)", elapsed < WS_TIMEOUT * 1000,
               f"ms={elapsed}")
    except AssertionError:
        failed += 1

    return failed


def test_error_handling(port: int) -> int:
    """Tier 5: Error handling and edge cases."""
    print("\n[5] Error Handling")
    failed = 0

    # Empty message to REST
    status, data = http_post_json(port, "/api/chat", {"message": ""}, timeout=30)
    try:
        record("empty message handled", status in (200, 422, 500),
               f"status={status}")
    except AssertionError:
        failed += 1

    # Missing message field
    status, data = http_post_json(port, "/api/chat", {}, timeout=30)
    try:
        record("missing message returns 422", status == 422,
               f"status={status}")
    except AssertionError:
        failed += 1

    # Invalid JSON to REST
    try:
        url = f"http://localhost:{port}/api/chat"
        req = urllib.request.Request(url, data=b"not json", method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception:
        status = 0

    try:
        record("invalid JSON returns 422", status == 422,
               f"status={status}")
    except AssertionError:
        failed += 1

    return failed


def test_memory_system(port: int) -> int:
    """Tier 6: Memory system — read-only verification (no writes to memory.md)."""
    print("\n[6] Memory System (read-only)")
    failed = 0

    # 6a. GET /api/memory returns content
    status, body, _ = http_get(port, "/api/memory", timeout=15)
    try:
        record("memory endpoint returns 200", status == 200, f"status={status}")
    except AssertionError:
        failed += 1
        return failed

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        try:
            record("memory returns JSON", False, f"body={body[:200]}")
        except AssertionError:
            failed += 1
        return failed

    content = data.get("content", "")
    try:
        record("memory has content", len(content) > 50,
               f"length={len(content)}")
    except AssertionError:
        failed += 1

    # Should have the expected header
    try:
        record("memory has GRIM header", "GRIM Working Memory" in content,
               f"preview={content[:80]}")
    except AssertionError:
        failed += 1

    # 6b. Sections parsed
    sections = data.get("sections", {})
    try:
        record("memory has sections", len(sections) > 0,
               f"count={len(sections)}, keys={list(sections.keys())[:5]}")
    except AssertionError:
        failed += 1

    expected_sections = {"Active Objectives", "Recent Topics", "User Preferences"}
    found = expected_sections.intersection(set(sections.keys()))
    try:
        record("expected sections present", len(found) >= 2,
               f"found={found}")
    except AssertionError:
        failed += 1

    # 6c. Health endpoint confirms vault is mounted
    status, body, _ = http_get(port, "/health")
    try:
        health = json.loads(body)
        vault_path = health.get("vault", "")
        record("vault path is /vault (not test fallback)",
               vault_path == "/vault",
               f"vault={vault_path}")
    except (json.JSONDecodeError, AssertionError) as e:
        if isinstance(e, AssertionError):
            failed += 1
        else:
            record("health JSON parse", False, f"body={body[:100]}")
            failed += 1

    return failed


def test_pool_smoke(port: int) -> int:
    """Tier 7: Pool smoke test — submit a lightweight job and verify completion.

    Submits a minimal RESEARCH job (read-only, no file writes) with a trivial
    instruction that should resolve in 1-2 turns. Verifies:
    - Pool status endpoint works
    - Job submission works
    - Job completes within timeout
    - Result has content
    """
    print("\n[7] Pool Smoke Test")
    failed = 0

    POOL_TIMEOUT = 120  # seconds to wait for job completion
    POLL_INTERVAL = 3   # seconds between polls

    # 7a. Check pool status
    status, body, _ = http_get(port, "/api/pool/status", timeout=10)
    if status == 503:
        skip("pool smoke test", "Pool not enabled (503)")
        return 0

    try:
        record("pool status returns 200", status == 200, f"status={status}")
    except AssertionError:
        failed += 1
        return failed

    try:
        data = json.loads(body)
        record("pool is running", data.get("running", False),
               f"slots={len(data.get('slots', []))}")
    except (json.JSONDecodeError, AssertionError) as e:
        if isinstance(e, AssertionError):
            failed += 1
        else:
            record("pool status JSON", False, f"body={body[:200]}")
            failed += 1
        return failed

    # 7b. Check metrics endpoint works
    status, body, _ = http_get(port, "/api/pool/metrics", timeout=10)
    try:
        record("pool metrics returns 200", status == 200, f"status={status}")
    except AssertionError:
        failed += 1

    # 7c. Submit a minimal research job (cheapest — no file tools, just 1-2 turns)
    status, data = http_post_json(port, "/api/pool/jobs", {
        "job_type": "research",
        "instructions": "What is 2 + 2? Reply with ONLY the number, nothing else.",
        "priority": "low",
    }, timeout=30)

    try:
        record("job submission returns 200", status == 200,
               f"status={status}, data={str(data)[:200]}")
    except AssertionError:
        failed += 1
        return failed

    job_id = data.get("id", "")
    try:
        record("job has ID", len(job_id) > 0, f"id={job_id}")
    except AssertionError:
        failed += 1
        return failed

    print(f"    Submitted job: {job_id}")

    # 7d. Poll until completion
    t0 = time.monotonic()
    final_status = None
    final_data = None

    while time.monotonic() - t0 < POOL_TIMEOUT:
        time.sleep(POLL_INTERVAL)
        s, body, _ = http_get(port, f"/api/pool/jobs/{job_id}", timeout=10)
        if s == 200:
            try:
                jdata = json.loads(body)
                js = jdata.get("status", "")
                elapsed = round(time.monotonic() - t0, 1)
                print(f"    [{elapsed}s] Job status: {js}")
                if js in ("complete", "failed", "cancelled"):
                    final_status = js
                    final_data = jdata
                    break
            except json.JSONDecodeError:
                pass

    elapsed = round(time.monotonic() - t0, 1)

    if final_status is None:
        try:
            record(f"job completed within {POOL_TIMEOUT}s", False,
                   f"timed out after {elapsed}s")
        except AssertionError:
            failed += 1
        return failed

    try:
        record("job completed successfully", final_status == "complete",
               f"status={final_status}, elapsed={elapsed}s")
    except AssertionError:
        failed += 1
        if final_data:
            print(f"    Error: {final_data.get('error', 'none')}")

    if final_data and final_status == "complete":
        result = final_data.get("result", "")
        try:
            record("job has result content", len(result) > 0,
                   f"length={len(result)}")
        except AssertionError:
            failed += 1

        transcript = final_data.get("transcript", [])
        try:
            record("job has transcript entries", len(transcript) > 0,
                   f"count={len(transcript)}")
        except AssertionError:
            failed += 1

        print(f"    Result: {result[:200]}")
        print(f"    Transcript entries: {len(transcript)}")

    return failed


# ── Main ─────────────────────────────────────────────────────────────────────

def run_all(port: int, no_llm: bool = False) -> int:
    """Run all integration tests. Returns total failures."""
    total_failed = 0

    # Tier 1: Always run
    total_failed += test_infrastructure(port)

    # Tier 2: MCP connectivity (works without API key)
    total_failed += test_mcp_connectivity(port)

    # Tier 3-4: Need LLM (API key)
    if no_llm:
        print("\n[3] REST Chat — SKIPPED (--no-llm)")
        skip("REST chat tests", "no-llm flag")
        print("\n[4] WebSocket Chat — SKIPPED (--no-llm)")
        skip("WebSocket chat tests", "no-llm flag")
    else:
        total_failed += test_rest_chat(port)
        total_failed += test_websocket_chat(port)

    # Tier 5: Error handling (no API key needed)
    total_failed += test_error_handling(port)

    # Tier 6: Memory system (read-only, no API key needed)
    total_failed += test_memory_system(port)

    # Tier 7: Pool smoke test (needs LLM — submits a real job)
    if no_llm:
        print("\n[7] Pool Smoke Test — SKIPPED (--no-llm)")
        skip("Pool smoke test", "no-llm flag")
    else:
        total_failed += test_pool_smoke(port)

    return total_failed


def main():
    parser = argparse.ArgumentParser(description="GRIM Integration Tests")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Port to test on (default: {DEFAULT_PORT})")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip tests that require LLM API key")
    parser.add_argument("--keep", action="store_true",
                        help="Don't stop container after tests")
    parser.add_argument("--no-start", action="store_true",
                        help="Don't start container (assume already running)")
    args = parser.parse_args()

    port = args.port
    started_by_us = False

    print("=" * 60)
    print("GRIM INTEGRATION TESTS")
    print(f"Target: http://localhost:{port}")
    print("=" * 60)

    # Start container if needed
    if not args.no_start:
        if container_running():
            print("  Container already running")
        else:
            if not start_grim(port):
                print("FATAL: Could not start GRIM container")
                sys.exit(2)
            started_by_us = True
    else:
        print("  Skipping container start (--no-start)")

    # Wait for health
    print("\n  Waiting for GRIM to be healthy ...")
    if not wait_for_health(port):
        print("FATAL: GRIM did not become healthy")
        if started_by_us and not args.keep:
            stop_grim()
        sys.exit(2)
    print("  GRIM is healthy!")

    # Run tests
    t0 = time.time()
    total_failed = run_all(port, no_llm=args.no_llm)
    elapsed = round(time.time() - t0, 1)

    # Stop container
    if started_by_us and not args.keep:
        print("\n  Stopping GRIM container ...")
        stop_grim()
    elif args.keep:
        print(f"\n  Container left running on port {port}")

    # Summary
    passed = sum(1 for _, s, _ in _results if s == PASS)
    failed = sum(1 for _, s, _ in _results if s == FAIL)
    skipped = sum(1 for _, s, _ in _results if s == SKIP)
    total = len(_results)

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed, {skipped} skipped ({elapsed}s)")

    if failed:
        print("\nFailed tests:")
        for name, status, detail in _results:
            if status == FAIL:
                print(f"  FAIL {name}: {detail}")

    print("=" * 60)

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
