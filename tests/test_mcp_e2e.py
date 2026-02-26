"""
End-to-end tests for the Kronos MCP server.

Spawns the server as a real subprocess and communicates via actual JSON-RPC
over stdin/stdout. This catches protocol-level bugs that unit tests can't
see — including the stdio hang bug observed in production.

Diagnostic goals:
  1. Does the server respond to all tool calls?
  2. Which payload sizes / response sizes trigger the hang?
  3. Is the hang in stdin reading, handler execution, or stdout writing?
  4. Do sequential calls accumulate errors?
  5. Does Windows newline translation (\r\n vs \n) affect parsing?

Run:
    PYTHONPATH=mcp/kronos/src python tests/test_mcp_e2e.py

Or with verbose stderr from server:
    PYTHONPATH=mcp/kronos/src python tests/test_mcp_e2e.py --server-stderr
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

GRIM_ROOT = Path(__file__).resolve().parent.parent
VAULT_PATH = str((GRIM_ROOT / ".." / "kronos-vault").resolve())
SKILLS_PATH = str((GRIM_ROOT / "skills").resolve())
MCP_SRC = str(GRIM_ROOT / "mcp" / "kronos" / "src")

SHOW_SERVER_STDERR = "--server-stderr" in sys.argv

# ── JSON-RPC helpers ──────────────────────────────────────────────────────────

def make_request(req_id: int, method: str, params: dict | None = None) -> bytes:
    msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params:
        msg["params"] = params
    return (json.dumps(msg) + "\n").encode("utf-8")


def make_notification(method: str, params: dict | None = None) -> bytes:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params:
        msg["params"] = params
    return (json.dumps(msg) + "\n").encode("utf-8")


# ── MCP test client ───────────────────────────────────────────────────────────

class MCPClient:
    """
    Spawns the kronos-mcp server as a subprocess and communicates via stdio.

    Uses a reader thread for stdout so we never deadlock the test process by
    alternating writes/reads (Windows pipe buffer hazard).

    Message framing: newline-delimited JSON (one JSON object per line).
    The server's TextIOWrapper may emit \\r\\n on Windows — we strip both.
    """

    def __init__(self, startup_timeout: float = 30.0):
        self._startup_timeout = startup_timeout
        self.proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._responses: queue.Queue = queue.Queue()
        self._next_id = 1
        self._stderr_lines: list[str] = []
        self._initialized = False

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        env = os.environ.copy()
        env["KRONOS_VAULT_PATH"] = VAULT_PATH
        env["KRONOS_SKILLS_PATH"] = SKILLS_PATH
        env["PYTHONPATH"] = MCP_SRC
        # Disable sentence-transformers for faster startup in E2E tests
        env["KRONOS_EMBED_MODEL"] = ""
        env["PYTHONUNBUFFERED"] = "1"

        stderr = None if SHOW_SERVER_STDERR else subprocess.PIPE

        self.proc = subprocess.Popen(
            [sys.executable, "-m", "kronos_mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            env=env,
        )

        # Stdout reader thread (binary)
        self._reader_thread = threading.Thread(
            target=self._read_stdout, daemon=True, name="mcp-stdout-reader"
        )
        self._reader_thread.start()

        # Stderr reader thread (if captured)
        if not SHOW_SERVER_STDERR and self.proc.stderr:
            threading.Thread(
                target=self._read_stderr, daemon=True, name="mcp-stderr-reader"
            ).start()

    def stop(self) -> None:
        if self.proc:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                pass

    def __enter__(self) -> "MCPClient":
        self.start()
        self.initialize()
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    # ── internal readers ──────────────────────────────────────────────────────

    def _read_stdout(self) -> None:
        """Read newline-delimited JSON from server stdout (binary pipe)."""
        try:
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                    self._responses.put(("ok", msg))
                except json.JSONDecodeError as e:
                    self._responses.put(("parse_error", {"_raw": repr(text), "_error": str(e)}))
        except Exception as e:
            self._responses.put(("thread_error", {"_error": str(e)}))

    def _read_stderr(self) -> None:
        try:
            for line in self.proc.stderr:
                self._stderr_lines.append(line.decode("utf-8", errors="replace").rstrip())
        except Exception:
            pass

    # ── protocol ──────────────────────────────────────────────────────────────

    def next_id(self) -> int:
        id_ = self._next_id
        self._next_id += 1
        return id_

    def send_raw(self, data: bytes) -> None:
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def recv(self, timeout: float = 15.0) -> dict | None:
        """Return next JSON message from server, or None on timeout."""
        try:
            tag, msg = self._responses.get(timeout=timeout)
            return msg
        except queue.Empty:
            return None

    def initialize(self) -> dict:
        """Perform MCP initialization handshake."""
        init_id = self.next_id()
        self.send_raw(make_request(init_id, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "e2e-test-client", "version": "1.0"},
        }))
        resp = self.recv(timeout=self._startup_timeout)
        if resp is None:
            raise RuntimeError(f"Server did not respond to initialize within {self._startup_timeout}s")
        if "error" in resp:
            raise RuntimeError(f"Initialize failed: {resp}")
        # Send initialized notification
        self.send_raw(make_notification("notifications/initialized"))
        self._initialized = True
        return resp

    def call_tool(
        self,
        name: str,
        arguments: dict,
        timeout: float = 20.0,
    ) -> tuple[dict | None, float]:
        """Call a tool and return (response, elapsed_ms). response=None means timeout."""
        req_id = self.next_id()
        t0 = time.perf_counter()
        self.send_raw(make_request(req_id, "tools/call", {
            "name": name,
            "arguments": arguments,
        }))
        resp = self.recv(timeout=timeout)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return resp, elapsed_ms

    def list_tools(self, timeout: float = 15.0) -> dict | None:
        req_id = self.next_id()
        self.send_raw(make_request(req_id, "tools/list"))
        return self.recv(timeout=timeout)

    # ── helpers ───────────────────────────────────────────────────────────────

    def tool_text(self, resp: dict | None) -> str | None:
        """Extract the text content from a tool call response."""
        if resp is None:
            return None
        try:
            return resp["result"]["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return None

    def tool_json(self, resp: dict | None) -> dict | None:
        """Parse the text content of a tool call response as JSON."""
        text = self.tool_text(resp)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None


# ── Test helpers ──────────────────────────────────────────────────────────────

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    status = PASS if ok else FAIL
    results.append((name, status, detail))
    icon = "[OK]" if ok else "[FAIL]"
    print(f"  {icon} {name}: {detail}")


def elapsed_tag(ms: float) -> str:
    return f"{ms:.0f}ms"


def body_of_size(n: int) -> str:
    """Generate a markdown body of approximately n bytes."""
    header = "# Test FDO\n\n## Summary\n\nTest body for E2E payload size test.\n\n## Details\n\n"
    filler = "This is filler content to reach the target size. " * (n // 50 + 1)
    raw = header + filler
    return raw[:max(n, len(header))]


# ── Test 1: Server startup and handshake ──────────────────────────────────────

def test_startup_and_handshake() -> None:
    print("\n[E2E-1] Server startup and MCP handshake")
    client = MCPClient(startup_timeout=30.0)
    try:
        client.start()
        init_id = client.next_id()
        client.send_raw(make_request(init_id, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "e2e-test", "version": "1.0"},
        }))
        resp = client.recv(timeout=30.0)
        record("server responds to initialize", resp is not None, repr(resp)[:80] if resp else "TIMEOUT")
        if resp:
            has_result = "result" in resp
            record("initialize has result", has_result, str(list(resp.keys())))
            if has_result:
                srv_name = resp.get("result", {}).get("serverInfo", {}).get("name", "")
                record("server name is kronos-mcp", srv_name == "kronos-mcp", f"name={srv_name!r}")

        client.send_raw(make_notification("notifications/initialized"))

        # tools/list
        tools_id = client.next_id()
        client.send_raw(make_request(tools_id, "tools/list"))
        tools_resp = client.recv(timeout=10.0)
        record("tools/list responds", tools_resp is not None, "TIMEOUT" if tools_resp is None else "ok")
        if tools_resp:
            tool_names = [t["name"] for t in tools_resp.get("result", {}).get("tools", [])]
            expected = {"kronos_search", "kronos_get", "kronos_list", "kronos_graph",
                        "kronos_validate", "kronos_create", "kronos_update",
                        "kronos_tags", "kronos_skills", "kronos_skill_load"}
            missing = expected - set(tool_names)
            record("all 10 tools listed", len(missing) == 0, f"missing={missing}" if missing else f"{len(tool_names)} tools")
    finally:
        client.stop()


# ── Test 2: All tools basic smoke ─────────────────────────────────────────────

def test_all_tools_basic() -> None:
    print("\n[E2E-2] All tools basic smoke (one call each, 15s timeout per call)")
    with MCPClient() as c:
        tools_to_test = [
            ("kronos_get", {"id": "grim-architecture"}, "get: grim-architecture"),
            ("kronos_list", {}, "list: all"),
            ("kronos_list", {"domain": "ai-systems"}, "list: ai-systems"),
            ("kronos_search", {"query": "GRIM LangGraph", "semantic": False}, "search: no-semantic"),
            ("kronos_graph", {"id": "grim-architecture", "depth": 1}, "graph: grim-architecture"),
            ("kronos_tags", {}, "tags: all"),
            ("kronos_tags", {"domain": "ai-systems"}, "tags: ai-systems"),
            ("kronos_skills", {}, "skills: list"),
            ("kronos_validate", {}, "validate: full vault"),
        ]
        for tool_name, args, label in tools_to_test:
            resp, ms = c.call_tool(tool_name, args, timeout=30.0)
            data = c.tool_json(resp)
            timed_out = resp is None
            has_error = data is not None and "error" in data
            record(
                f"{label} — responds",
                not timed_out,
                "TIMEOUT" if timed_out else elapsed_tag(ms),
            )
            if not timed_out:
                record(
                    f"{label} — no error",
                    not has_error,
                    f"error={data.get('error')!r}" if has_error else "ok",
                )


# ── Test 3: Small request, large response (kronos_get) ────────────────────────

def test_large_response() -> None:
    """Does the server hang when the RESPONSE is large?"""
    print("\n[E2E-3] Large response test (kronos_get for large FDOs)")
    with MCPClient() as c:
        # Get FDOs with large bodies to produce large responses
        large_fdos = [
            "grim-architecture",
            "grim-langgraph",
            "proj-grim",
            "dawn-field-theory",
        ]
        for fdo_id in large_fdos:
            resp, ms = c.call_tool("kronos_get", {"id": fdo_id}, timeout=15.0)
            text = c.tool_text(resp)
            resp_size = len(text.encode("utf-8")) if text else 0
            timed_out = resp is None
            record(
                f"get {fdo_id} (resp ~{resp_size//1024}KB) — responds",
                not timed_out,
                f"TIMEOUT after 15s" if timed_out else f"{elapsed_tag(ms)}, size={resp_size}B",
            )


# ── Test 4: Payload size sweep (kronos_update) ────────────────────────────────

def test_payload_size_sweep() -> None:
    """Find the exact request payload size threshold that triggers hangs.

    This is the key diagnostic: the summary says large-body updates hang.
    We sweep from 100 to 20000 bytes and measure response times.
    Any timeout here is the bug.
    """
    print("\n[E2E-4] Payload size sweep: kronos_update with increasing body sizes")
    SIZES = [100, 500, 1000, 2000, 3000, 4000, 5000, 8000, 12000, 20000]
    TIMEOUT = 15.0
    TEST_FDO = "_e2e-sweep-target"
    DOMAIN = "ai-systems"
    VAULT_FILE = Path(VAULT_PATH) / DOMAIN / f"{TEST_FDO}.md"

    with MCPClient() as c:
        # Create the target FDO first
        resp, ms = c.call_tool("kronos_create", {
            "id": TEST_FDO,
            "title": "E2E Sweep Target FDO",
            "domain": DOMAIN,
            "confidence": 0.1,
            "tags": ["test", "e2e"],
            "body": "# E2E\n\n## Summary\n\nInitial body.\n",
        }, timeout=30.0)
        data = c.tool_json(resp)
        if resp is None or (data and "error" in data):
            record("sweep: create target FDO", False, f"create failed: {data}")
            return
        record("sweep: create target FDO", True, elapsed_tag(ms))

        prev_timed_out = False
        for size in SIZES:
            if prev_timed_out:
                # Previous size already hung — skip larger sizes (server state is unknown)
                record(f"sweep: {size}B body", False, "SKIP — prev size timed out")
                continue

            body = body_of_size(size)
            req_json = json.dumps({"id": TEST_FDO, "fields": {"body": body}})
            req_bytes = len(make_request(999, "tools/call", {
                "name": "kronos_update",
                "arguments": {"id": TEST_FDO, "fields": {"body": body}},
            }))
            print(f"    Testing {size}B body (request ~{req_bytes}B total)...")

            resp, ms = c.call_tool("kronos_update", {
                "id": TEST_FDO,
                "fields": {"body": body},
            }, timeout=TIMEOUT)

            timed_out = resp is None
            prev_timed_out = timed_out

            if timed_out:
                # Check if the file was written (handler completed, hang in response path)
                handler_completed = VAULT_FILE.exists()
                record(
                    f"sweep: {size}B body (req ~{req_bytes}B) — responds",
                    False,
                    f"TIMEOUT after {TIMEOUT}s, file_written={handler_completed}",
                )
            else:
                record(
                    f"sweep: {size}B body (req ~{req_bytes}B) — responds",
                    True,
                    elapsed_tag(ms),
                )

        # Cleanup
        if VAULT_FILE.exists():
            VAULT_FILE.unlink()


# ── Test 5: Sequential calls ──────────────────────────────────────────────────

def test_sequential_calls() -> None:
    """10 sequential get calls — do cumulative issues appear?"""
    print("\n[E2E-5] Sequential calls (10x kronos_get)")
    with MCPClient() as c:
        times = []
        failures = []
        for i in range(10):
            resp, ms = c.call_tool("kronos_get", {"id": "grim-architecture"}, timeout=15.0)
            if resp is None:
                failures.append(i)
            else:
                times.append(ms)

        avg_ms = sum(times) / len(times) if times else 0
        record(
            "sequential 10x get — no timeouts",
            len(failures) == 0,
            f"failures at calls: {failures}" if failures else f"avg={avg_ms:.0f}ms",
        )
        record(
            "sequential 10x get — all fast (<2000ms)",
            all(t < 2000 for t in times),
            f"max={max(times):.0f}ms" if times else "no responses",
        )


# ── Test 6: Request vs Response size isolation ────────────────────────────────

def test_size_isolation() -> None:
    """Distinguish large-request vs large-response as the hang trigger."""
    print("\n[E2E-6] Size isolation: large request vs large response")
    with MCPClient() as c:
        # Large RESPONSE, small REQUEST: kronos_validate (response is ~5-20KB)
        resp, ms = c.call_tool("kronos_validate", {}, timeout=30.0)
        text = c.tool_text(resp)
        resp_size = len(text.encode("utf-8")) if text else 0
        record(
            f"large response ({resp_size}B) from small request — responds",
            resp is not None,
            f"TIMEOUT" if resp is None else elapsed_tag(ms),
        )

        # Large REQUEST, small RESPONSE: kronos_search with large query string
        large_query = "GRIM architecture LangGraph knowledge vault " * 100
        resp2, ms2 = c.call_tool("kronos_search", {
            "query": large_query,
            "semantic": False,
        }, timeout=15.0)
        req_size = len(make_request(999, "tools/call", {
            "name": "kronos_search",
            "arguments": {"query": large_query, "semantic": False},
        }))
        record(
            f"large request ({req_size}B) with small response — responds",
            resp2 is not None,
            f"TIMEOUT" if resp2 is None else elapsed_tag(ms2),
        )

        # Large request + large response: kronos_get for large FDO repeatedly
        for i in range(3):
            resp3, ms3 = c.call_tool("kronos_get", {"id": "grim-architecture"}, timeout=15.0)
            text3 = c.tool_text(resp3)
            size3 = len(text3.encode("utf-8")) if text3 else 0
            record(
                f"large response get #{i+1} ({size3}B) — responds",
                resp3 is not None,
                f"TIMEOUT" if resp3 is None else elapsed_tag(ms3),
            )


# ── Test 7: Windows newline detection ────────────────────────────────────────

def test_windows_newline() -> None:
    """Check if the server emits \\r\\n (Windows newlines) on stdout."""
    print("\n[E2E-7] Windows newline detection")
    client = MCPClient(startup_timeout=30.0)
    client.start()

    # Patch reader to capture raw bytes
    raw_lines: list[bytes] = []
    client.proc.stdout  # access to ensure started

    # Use a separate byte-level reader
    raw_q: queue.Queue = queue.Queue()

    def raw_reader():
        buf = b""
        while True:
            chunk = client.proc.stdout.read(1)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                idx = buf.index(b"\n")
                line = buf[:idx+1]
                raw_q.put(line)
                buf = buf[idx+1:]

    # Can't use raw reader if reader thread already started — check raw response queue instead
    # Instead, flush one message through and check what's in the raw line buffer
    # We'll just measure by looking at the first response from our existing queue

    init_id = client.next_id()
    client.send_raw(make_request(init_id, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "newline-test", "version": "1.0"},
    }))
    resp = client.recv(timeout=30.0)

    # The reader thread already stripped \r\n. To detect \r\n we need to
    # read a fresh subprocess without the stripping reader.
    # For this test, just check if stripping was needed by examining logged stderr.
    # We'll instead spawn a second mini-subprocess to probe this.

    client.stop()

    # Spawn a fresh server and capture raw bytes from its stdout
    env = os.environ.copy()
    env["KRONOS_VAULT_PATH"] = VAULT_PATH
    env["KRONOS_SKILLS_PATH"] = SKILLS_PATH
    env["PYTHONPATH"] = MCP_SRC
    env["KRONOS_EMBED_MODEL"] = ""

    proc = subprocess.Popen(
        [sys.executable, "-m", "kronos_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    # Send initialize
    msg = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "raw", "version": "1"}},
    }) + "\n"
    proc.stdin.write(msg.encode("utf-8"))
    proc.stdin.flush()

    # Read raw bytes until we get a newline
    raw_buf = b""
    proc.stdout_raw = proc.stdout
    deadline = time.time() + 20.0
    got_line = False
    while time.time() < deadline:
        chunk = proc.stdout.read(1)
        if not chunk:
            break
        raw_buf += chunk
        if b"\n" in raw_buf:
            got_line = True
            break

    proc.terminate()
    proc.wait(timeout=3)

    if got_line and raw_buf:
        has_crlf = b"\r\n" in raw_buf
        has_lf_only = b"\r\n" not in raw_buf and b"\n" in raw_buf
        # MCP protocol requires LF-only (\n) line endings. CRLF is a bug.
        record("server stdout is LF-only (correct for MCP/JSON-RPC)", has_lf_only,
               f"raw_suffix={repr(raw_buf[-4:])}")
        record("server stdout has NO CRLF (Windows translation suppressed)", not has_crlf,
               f"raw_suffix={repr(raw_buf[-4:])}")
        if has_crlf:
            print("    !! FAIL: Server writes \\r\\n — MCP protocol violation.")
        else:
            print("    Server writes LF-only (correct for JSON-RPC).")
    else:
        record("newline test: got raw output", False, "no bytes received")


# ── Test 8: create/update large FDOs ─────────────────────────────────────────

def test_create_and_update_large() -> None:
    """Create and update FDOs with increasingly large bodies, like the production hang."""
    print("\n[E2E-8] Create/update FDOs matching production hang scenario")
    DOMAIN = "ai-systems"
    TEST_ID = "_e2e-large-create-test"
    VAULT_FILE = Path(VAULT_PATH) / DOMAIN / f"{TEST_ID}.md"

    # Clean up first
    if VAULT_FILE.exists():
        VAULT_FILE.unlink()

    with MCPClient() as c:
        # Create with small body (baseline)
        resp, ms = c.call_tool("kronos_create", {
            "id": TEST_ID,
            "title": "E2E Large Create Test",
            "domain": DOMAIN,
            "confidence": 0.5,
            "tags": ["test", "e2e"],
            "body": "# E2E Large Create\n\n## Summary\n\nBaseline body.\n",
        }, timeout=20.0)
        data = c.tool_json(resp)
        record("create with small body — responds", resp is not None,
               "TIMEOUT" if resp is None else elapsed_tag(ms))
        if resp is None:
            return

        # Update with medium body (~1KB)
        body_1k = body_of_size(1000)
        resp2, ms2 = c.call_tool("kronos_update", {
            "id": TEST_ID,
            "fields": {"body": body_1k},
        }, timeout=15.0)
        record("update with ~1KB body — responds", resp2 is not None,
               "TIMEOUT" if resp2 is None else elapsed_tag(ms2))

        # Update with large body (~3.5KB, matching production hang)
        body_3k5 = body_of_size(3500)
        resp3, ms3 = c.call_tool("kronos_update", {
            "id": TEST_ID,
            "fields": {"body": body_3k5},
        }, timeout=15.0)
        handler_completed_3k5 = VAULT_FILE.exists()
        record("update with ~3.5KB body — responds", resp3 is not None,
               f"TIMEOUT (handler_done={handler_completed_3k5})" if resp3 is None else elapsed_tag(ms3))

        # Follow-up call — can we still talk to the server after the large update?
        resp4, ms4 = c.call_tool("kronos_get", {"id": TEST_ID}, timeout=15.0)
        record("get after large update — responds", resp4 is not None,
               "TIMEOUT" if resp4 is None else elapsed_tag(ms4))

        # Update with very large body (~10KB)
        body_10k = body_of_size(10000)
        resp5, ms5 = c.call_tool("kronos_update", {
            "id": TEST_ID,
            "fields": {"body": body_10k},
        }, timeout=15.0)
        record("update with ~10KB body — responds", resp5 is not None,
               "TIMEOUT" if resp5 is None else elapsed_tag(ms5))

    # Cleanup
    if VAULT_FILE.exists():
        VAULT_FILE.unlink()


# ── Test 9: Concurrent tool calls ─────────────────────────────────────────────

def test_concurrent_calls() -> None:
    """Send multiple tool calls without waiting for each response.
    MCP should handle concurrent requests (each gets its own task in tg).
    """
    print("\n[E2E-9] Concurrent tool calls (fire 5 without waiting)")
    with MCPClient() as c:
        # Fire 5 gets in rapid succession without reading responses between them
        ids = []
        t0 = time.perf_counter()
        for i in range(5):
            req_id = c.next_id()
            ids.append(req_id)
            c.send_raw(make_request(req_id, "tools/call", {
                "name": "kronos_get",
                "arguments": {"id": "grim-architecture"},
            }))

        # Now collect all 5 responses
        responses = {}
        deadline = time.time() + 30.0
        while len(responses) < 5 and time.time() < deadline:
            resp = c.recv(timeout=5.0)
            if resp is None:
                break
            resp_id = resp.get("id")
            if resp_id in ids:
                responses[resp_id] = resp

        total_ms = (time.perf_counter() - t0) * 1000
        got = len(responses)
        record("concurrent 5 gets — all respond", got == 5,
               f"got={got}/5 in {total_ms:.0f}ms")
        record("concurrent 5 gets — total <10s", total_ms < 10000,
               f"total={total_ms:.0f}ms")


# ── Test 10: Ping ──────────────────────────────────────────────────────────────

def test_ping() -> None:
    """MCP ping is a lightweight protocol-level check."""
    print("\n[E2E-10] MCP ping (ping/ping)")
    with MCPClient() as c:
        req_id = c.next_id()
        t0 = time.perf_counter()
        c.send_raw(make_request(req_id, "ping"))
        resp = c.recv(timeout=5.0)
        ms = (time.perf_counter() - t0) * 1000
        record("ping responds", resp is not None, "TIMEOUT" if resp is None else elapsed_tag(ms))
        if resp:
            record("ping has result", "result" in resp, str(list(resp.keys())))


# ── Test 11: Message size measurement ────────────────────────────────────────

def test_measure_message_sizes() -> None:
    """Print request/response sizes for all tools to understand the pipe load."""
    print("\n[E2E-11] Message size audit (requests + responses)")
    TIMEOUT = 30.0
    TEST_FDO = "_e2e-size-audit"
    DOMAIN = "ai-systems"

    with MCPClient() as c:
        # Create a temp FDO with a ~3KB body (matching production scenario)
        body_3k = body_of_size(3000)
        c.call_tool("kronos_create", {
            "id": TEST_FDO,
            "title": "E2E Size Audit FDO",
            "domain": DOMAIN,
            "confidence": 0.1,
            "tags": ["test"],
            "body": body_3k,
        }, timeout=TIMEOUT)

        calls = [
            ("kronos_get", {"id": "grim-architecture"}),
            ("kronos_get", {"id": TEST_FDO}),
            ("kronos_list", {}),
            ("kronos_tags", {}),
            ("kronos_validate", {}),
            ("kronos_update", {"id": TEST_FDO, "fields": {"confidence": 0.2}}),
            ("kronos_update", {"id": TEST_FDO, "fields": {"body": body_3k}}),
        ]

        print(f"    {'Tool':<35} {'Req':>8}B  {'Resp':>8}B  {'Time':>8}")
        print(f"    {'-'*35} {'-'*8}   {'-'*8}   {'-'*8}")

        for tool_name, args in calls:
            req_bytes = len(make_request(999, "tools/call", {"name": tool_name, "arguments": args}))
            resp, ms = c.call_tool(tool_name, args, timeout=TIMEOUT)
            text = c.tool_text(resp)
            resp_bytes = len(text.encode("utf-8")) if text else 0
            status = "OK" if resp is not None else "TIMEOUT"
            print(f"    {tool_name + ' ' + str(list(args.keys())):<35} {req_bytes:>8}   {resp_bytes:>8}   {ms:>7.0f}ms  {status}")

            record(f"size-audit {tool_name} — responds", resp is not None,
                   f"req={req_bytes}B, resp={resp_bytes}B, {elapsed_tag(ms)}" if resp else "TIMEOUT")

        # Cleanup
        vault_file = Path(VAULT_PATH) / DOMAIN / f"{TEST_FDO}.md"
        if vault_file.exists():
            vault_file.unlink()


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all() -> bool:
    print("=" * 70)
    print("KRONOS MCP END-TO-END TESTS")
    print(f"Vault: {VAULT_PATH}")
    print(f"Server stderr: {'shown' if SHOW_SERVER_STDERR else 'captured'}")
    print("=" * 70)

    # Run all tests
    test_startup_and_handshake()
    test_all_tools_basic()
    test_large_response()
    test_payload_size_sweep()
    test_sequential_calls()
    test_size_isolation()
    test_windows_newline()
    test_create_and_update_large()
    test_concurrent_calls()
    test_ping()
    test_measure_message_sizes()

    # Summary
    print("\n" + "=" * 70)
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = sum(1 for _, s, _ in results if s == FAIL)
    skipped = sum(1 for _, s, _ in results if s == SKIP)

    print(f"E2E RESULTS: {passed} passed, {failed} failed, {skipped} skipped")

    if failed:
        print("\nFailed tests:")
        for name, status, detail in results:
            if status == FAIL:
                print(f"  FAIL  {name}: {detail}")

    print("=" * 70)
    return failed == 0


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
