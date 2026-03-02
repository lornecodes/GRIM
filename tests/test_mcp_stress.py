"""Stress test for Kronos MCP server -reproduces intermittent hangs.

Spawns a fresh kronos-mcp process and hammers it with concurrent read requests
to find deadlocks, protocol corruption, or thread pool exhaustion.

Usage:
    python tests/test_mcp_stress.py [--rounds 50] [--parallel 4] [--timeout 10]
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# ── MCP Protocol helpers ─────────────────────────────────────────────────────

_ID = 0
_ID_LOCK = threading.Lock()

def _next_id():
    global _ID
    with _ID_LOCK:
        _ID += 1
        return _ID


def mcp_request(method: str, params: dict | None = None) -> str:
    """Build a JSON-RPC 2.0 request line."""
    msg = {"jsonrpc": "2.0", "id": _next_id(), "method": method}
    if params:
        msg["params"] = params
    return json.dumps(msg) + "\n"


def mcp_notification(method: str, params: dict | None = None) -> str:
    """Build a JSON-RPC 2.0 notification (no id)."""
    msg = {"jsonrpc": "2.0", "method": method}
    if params:
        msg["params"] = params
    return json.dumps(msg) + "\n"


# ── Server process management ────────────────────────────────────────────────

class MCPServerProcess:
    """Manages a kronos-mcp child process over stdio."""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.proc: subprocess.Popen | None = None
        self._read_lock = threading.Lock()
        self._write_lock = threading.Lock()

    def start(self):
        """Spawn the kronos-mcp process."""
        exe_env = os.environ.get("KRONOS_MCP_EXE", "")
        if exe_env:
            exe = Path(exe_env)
        else:
            exe = (
                Path.home()
                / "AppData/Local/Packages/PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0"
                / "LocalCache/local-packages/Python311/Scripts/kronos-mcp.exe"
            )
        env = {
            **os.environ,
            "KRONOS_VAULT_PATH": os.environ.get(
                "KRONOS_VAULT_PATH",
                str(Path(__file__).resolve().parents[2] / "kronos-vault"),
            ),
            "KRONOS_SKILLS_PATH": os.environ.get(
                "KRONOS_SKILLS_PATH",
                str(Path(__file__).resolve().parents[1] / "skills"),
            ),
            # Disable Redis for clean test -isolate server from external deps
            # Comment out to test WITH Redis
            # "KRONOS_REDIS_URL": "",
        }
        # Keep existing Redis config if set, to test realistic conditions
        if "KRONOS_REDIS_URL" not in os.environ:
            env["KRONOS_REDIS_URL"] = "redis://localhost:6379/0"

        print(f"[server] Starting {exe}")
        print(f"[server] Vault: {env['KRONOS_VAULT_PATH']}")
        print(f"[server] Redis: {env.get('KRONOS_REDIS_URL', 'disabled')}")

        self.proc = subprocess.Popen(
            [str(exe)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,  # Unbuffered
        )

    def send(self, data: str):
        """Write raw string to server stdin."""
        with self._write_lock:
            self.proc.stdin.write(data.encode("utf-8"))
            self.proc.stdin.flush()

    def read_response(self, timeout: float | None = None) -> dict | None:
        """Read one JSON-RPC response line from stdout."""
        timeout = timeout or self.timeout
        result = [None]
        error = [None]

        def _read():
            try:
                line = self.proc.stdout.readline()
                if line:
                    result[0] = json.loads(line.decode("utf-8").strip())
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout)

        if t.is_alive():
            return None  # Timed out
        if error[0]:
            raise error[0]
        return result[0]

    def initialize(self) -> dict | None:
        """Perform MCP initialization handshake."""
        self.send(mcp_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "stress-test", "version": "1.0"},
        }))
        resp = self.read_response(timeout=30)  # First response can be slow (model loading)
        if resp and "result" in resp:
            # Send initialized notification
            self.send(mcp_notification("notifications/initialized"))
            return resp
        return None

    def call_tool(self, tool_name: str, arguments: dict) -> tuple[dict | None, float]:
        """Call a tool and return (response, elapsed_seconds)."""
        t0 = time.perf_counter()
        self.send(mcp_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        }))
        resp = self.read_response()
        elapsed = time.perf_counter() - t0
        return resp, elapsed

    def stop(self):
        """Kill the server process."""
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            # Drain stderr for diagnostics
            stderr = self.proc.stderr.read().decode("utf-8", errors="replace")
            if stderr.strip():
                safe_stderr = stderr[-2000:].encode("ascii", errors="replace").decode("ascii")
                print(f"\n[server stderr] (last 2000 chars):\n{safe_stderr}")


# ── Test scenarios ───────────────────────────────────────────────────────────

READ_TOOLS = [
    ("kronos_list", {"domain": "projects"}),
    ("kronos_list", {"domain": "ai-systems"}),
    ("kronos_task_list", {}),
    ("kronos_task_get", {"item_id": "story-grim-006-002"}),
    ("kronos_board_view", {}),
    ("kronos_board_view", {"project_id": "proj-grim"}),
    ("kronos_backlog_view", {}),
    ("kronos_get", {"id": "proj-grim"}),
    ("kronos_search", {"query": "grim", "semantic": False}),
    ("kronos_tags", {}),
]


def run_sequential(server: MCPServerProcess, rounds: int) -> list[dict]:
    """Fire sequential read requests."""
    results = []
    for i in range(rounds):
        tool_name, args = READ_TOOLS[i % len(READ_TOOLS)]
        resp, elapsed = server.call_tool(tool_name, args)
        status = "OK" if resp and "result" in resp else "HANG" if resp is None else "ERROR"
        results.append({
            "round": i + 1,
            "tool": tool_name,
            "status": status,
            "elapsed": round(elapsed, 3),
        })
        marker = "+" if status == "OK" else "X" if status == "HANG" else "!"
        print(f"  [{marker}] {tool_name:30s} {elapsed:.3f}s  {status}")
        if status == "HANG":
            print(f"  *** HANG DETECTED at round {i+1} -stopping sequential test ***")
            break
    return results


def run_parallel(server: MCPServerProcess, rounds: int, parallelism: int) -> list[dict]:
    """Fire parallel read requests (simulates Claude Code parallel tool calls).

    NOTE: MCP over stdio is inherently sequential at the transport level.
    This test sends requests as fast as possible and reads responses,
    which tests the server's ability to handle queued requests.
    """
    results = []
    results_lock = threading.Lock()

    def _fire(round_idx: int):
        tool_name, args = READ_TOOLS[round_idx % len(READ_TOOLS)]
        try:
            resp, elapsed = server.call_tool(tool_name, args)
            status = "OK" if resp and "result" in resp else "HANG" if resp is None else "ERROR"
        except Exception as e:
            status = f"EXCEPTION: {e}"
            elapsed = -1
        with results_lock:
            results.append({
                "round": round_idx + 1,
                "tool": tool_name,
                "status": status,
                "elapsed": round(elapsed, 3),
            })
            marker = "+" if status == "OK" else "X" if "HANG" in status else "!"
            print(f"  [{marker}] R{round_idx+1:03d} {tool_name:30s} {elapsed:.3f}s  {status}")

    # Fire in batches of `parallelism`
    for batch_start in range(0, rounds, parallelism):
        batch_end = min(batch_start + parallelism, rounds)
        threads = []
        for i in range(batch_start, batch_end):
            t = threading.Thread(target=_fire, args=(i,), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=15)
            if t.is_alive():
                print(f"  *** Thread hung at batch starting {batch_start} ***")
                return results

    return results


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Kronos MCP stress test")
    parser.add_argument("--rounds", type=int, default=30, help="Number of requests per test")
    parser.add_argument("--parallel", type=int, default=4, help="Parallelism level for concurrent test")
    parser.add_argument("--timeout", type=float, default=10, help="Per-request timeout in seconds")
    parser.add_argument("--no-redis", action="store_true", help="Disable Redis cache")
    parser.add_argument("--sequential-only", action="store_true", help="Skip parallel test")
    parser.add_argument("--parallel-only", action="store_true", help="Skip sequential test")
    args = parser.parse_args()

    if args.no_redis:
        os.environ["KRONOS_REDIS_URL"] = ""

    server = MCPServerProcess(timeout=args.timeout)
    server.start()

    print(f"\n{'='*70}")
    print("Kronos MCP Stress Test")
    print(f"  Rounds: {args.rounds}  Parallelism: {args.parallel}  Timeout: {args.timeout}s")
    print(f"{'='*70}")

    # Initialize
    print("\n[init] Performing MCP handshake...")
    t0 = time.perf_counter()
    init_resp = server.initialize()
    init_time = time.perf_counter() - t0
    if not init_resp:
        print(f"[init] FAILED -server didn't respond to initialize ({init_time:.1f}s)")
        server.stop()
        sys.exit(1)
    print(f"[init] OK in {init_time:.1f}s")

    # Wait for server warmup (semantic preload etc.)
    print("[init] Waiting 3s for server warmup...")
    time.sleep(3)

    # Sequential test
    if not args.parallel_only:
        print(f"\n{'-'*70}")
        print(f"SEQUENTIAL TEST -{args.rounds} requests, one at a time")
        print(f"{'-'*70}")
        seq_results = run_sequential(server, args.rounds)
        hangs = sum(1 for r in seq_results if r["status"] == "HANG")
        errors = sum(1 for r in seq_results if r["status"] not in ("OK", "HANG"))
        avg = sum(r["elapsed"] for r in seq_results if r["status"] == "OK") / max(1, len(seq_results) - hangs - errors)
        print(f"\n  Summary: {len(seq_results) - hangs - errors}/{args.rounds} OK, {hangs} HANG, {errors} ERROR")
        print(f"  Avg response time: {avg:.3f}s")

    # Parallel test
    if not args.sequential_only:
        print(f"\n{'-'*70}")
        print(f"PARALLEL TEST -{args.rounds} requests, {args.parallel} concurrent")
        print(f"{'-'*70}")
        par_results = run_parallel(server, args.rounds, args.parallel)
        hangs = sum(1 for r in par_results if "HANG" in r["status"])
        errors = sum(1 for r in par_results if r["status"] not in ("OK",) and "HANG" not in r["status"])
        ok = len(par_results) - hangs - errors
        avg = sum(r["elapsed"] for r in par_results if r["status"] == "OK") / max(1, ok)
        print(f"\n  Summary: {ok}/{args.rounds} OK, {hangs} HANG, {errors} ERROR")
        print(f"  Avg response time: {avg:.3f}s")

    # Clean up
    print(f"\n{'='*70}")
    print("Stopping server...")
    server.stop()
    print("Done.")


if __name__ == "__main__":
    main()
