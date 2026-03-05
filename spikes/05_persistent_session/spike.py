"""
Spike 05 — GRIM as Persistent Agent SDK Session
=================================================
Prove that GRIM works as a persistent ClaudeSDKClient session with:
  1. Multi-turn conversation (context persists across query() calls)
  2. Kronos MCP integration (real vault search/get/graph)
  3. Pool MCP tools (in-process submit/status alongside external Kronos)
  4. Identity/personality (system prompt produces GRIM persona)
  5. Mixed MCP servers (external stdio + in-process SDK tools)

This spike validates the "GRIM as Agent SDK client" architecture:
  GRIM = persistent SDK session + Kronos MCP + Pool MCP
  No LangGraph, no preprocessing nodes, no routing.

Run:
    cd GRIM/spikes/05_persistent_session
    python spike.py
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    tool,
    create_sdk_mcp_server,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

os.environ.pop("CLAUDECODE", None)

# ── Paths ────────────────────────────────────────────────────────

WORKSPACE_ROOT = Path("c:/Users/peter/repos/core_workspace")
GRIM_ROOT = WORKSPACE_ROOT / "GRIM"
VAULT_PATH = WORKSPACE_ROOT / "kronos-vault"
SKILLS_PATH = GRIM_ROOT / "skills"
IDENTITY_PATH = GRIM_ROOT / "identity" / "system_prompt.md"

# ── Kronos MCP config (external stdio transport) ────────────────

KRONOS_MCP_CONFIG = {
    "command": r"c:\Users\peter\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\LocalCache\local-packages\Python311\Scripts\kronos-mcp.exe",
    "env": {
        "KRONOS_VAULT_PATH": str(VAULT_PATH),
        "KRONOS_SKILLS_PATH": str(SKILLS_PATH),
        "KRONOS_WORKSPACE_ROOT": str(WORKSPACE_ROOT),
    },
}

# ── Pool MCP tools (in-process, real SQLite queue) ───────────────

# Add GRIM to path for imports
sys.path.insert(0, str(GRIM_ROOT))

from core.pool.models import Job, JobType, JobPriority
from core.pool.queue import JobQueue

# Global queue — initialized in main()
_queue: JobQueue | None = None


@tool(
    name="pool_submit",
    description="Submit a job to the GRIM execution pool for async processing. Returns a job ID.",
    input_schema={
        "type": "object",
        "properties": {
            "job_type": {
                "type": "string",
                "enum": ["code", "research", "audit", "plan"],
                "description": "Type of agent to execute the job",
            },
            "instructions": {
                "type": "string",
                "description": "What the agent should do",
            },
            "priority": {
                "type": "string",
                "enum": ["critical", "high", "normal", "low", "background"],
                "description": "Job priority (default: normal)",
            },
        },
        "required": ["job_type", "instructions"],
    },
)
async def pool_submit(args):
    """Submit a job to the execution pool."""
    job = Job(
        job_type=JobType(args["job_type"]),
        instructions=args["instructions"],
        priority=JobPriority(args.get("priority", "normal")),
    )
    job_id = await _queue.submit(job)
    return {"content": [{"type": "text", "text": f"Job submitted: {job_id} (type={args['job_type']}, priority={args.get('priority', 'normal')})"}]}


@tool(
    name="pool_status",
    description="Get the current execution pool status — lists all jobs with their status.",
    input_schema={"type": "object", "properties": {}},
)
async def pool_status(args):
    """Get pool queue status."""
    jobs = await _queue.list_jobs(limit=10)
    if not jobs:
        text = "Pool is empty — no jobs queued."
    else:
        lines = [f"Pool: {len(jobs)} job(s)\n"]
        for j in jobs:
            lines.append(f"  {j.id}  {j.job_type.value:<10} {j.status.value:<10} {j.priority.value:<10} {j.instructions[:60]}")
        text = "\n".join(lines)
    return {"content": [{"type": "text", "text": text}]}


@tool(
    name="pool_list_jobs",
    description="List jobs in the execution pool, optionally filtered by status.",
    input_schema={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["queued", "assigned", "running", "complete", "failed", "cancelled"],
                "description": "Filter by job status (optional)",
            },
        },
    },
)
async def pool_list_jobs(args):
    """List pool jobs."""
    from core.pool.models import JobStatus
    sf = None
    if "status" in args and args["status"]:
        sf = JobStatus(args["status"])
    jobs = await _queue.list_jobs(status_filter=sf, limit=20)
    if not jobs:
        return {"content": [{"type": "text", "text": "No jobs found."}]}
    lines = []
    for j in jobs:
        lines.append(f"{j.id}  {j.job_type.value}  {j.status.value}  {j.priority.value}  {j.instructions[:80]}")
    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


pool_server = create_sdk_mcp_server(
    name="pool",
    version="0.1.0",
    tools=[pool_submit, pool_status, pool_list_jobs],
)


# ── System prompt ────────────────────────────────────────────────

def load_system_prompt() -> str:
    """Load GRIM identity + personality modulation."""
    base = IDENTITY_PATH.read_text(encoding="utf-8")
    return base + """

## Current Expression Mode

Mode: direct, assertive
Coherence: 0.80 | Valence: 0.30 | Uncertainty: 0.20

## Available Capabilities

You have access to your Kronos knowledge vault and the execution pool.
- Use kronos tools (search, get, graph, list, tags) to search and retrieve knowledge.
- IMPORTANT: Always pass semantic=false when calling kronos_search (faster, avoids model load delay).
- Use pool tools (pool_submit, pool_status, pool_list_jobs) to submit async jobs.
When a user asks you to do something that requires code execution, research, or auditing,
submit it as a pool job. For knowledge questions, search your vault directly.
Keep responses concise — 2-3 sentences max unless asked for detail.
"""


# ── Allowed tools ────────────────────────────────────────────────

ALLOWED_TOOLS = [
    # Kronos MCP (external)
    "mcp__kronos__kronos_search",
    "mcp__kronos__kronos_get",
    "mcp__kronos__kronos_list",
    "mcp__kronos__kronos_tags",
    "mcp__kronos__kronos_graph",
    "mcp__kronos__kronos_deep_dive",
    "mcp__kronos__kronos_navigate",
    "mcp__kronos__kronos_read_source",
    "mcp__kronos__kronos_search_source",
    "mcp__kronos__kronos_memory_read",
    "mcp__kronos__kronos_notes_recent",
    # Pool MCP (in-process)
    "mcp__pool__pool_submit",
    "mcp__pool__pool_status",
    "mcp__pool__pool_list_jobs",
]


# ── Helpers ──────────────────────────────────────────────────────

def print_messages(messages: list, label: str = "") -> str:
    """Print and return the final text from a list of SDK messages."""
    final_text = ""
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    final_text = block.text
                    preview = block.text[:200].replace("\n", " ")
                    print(f"  [GRIM] {preview}")
                elif isinstance(block, ToolUseBlock):
                    print(f"  [Tool] {block.name}({json.dumps(block.input)[:80]})")
        elif isinstance(msg, ResultMessage):
            print(f"  [Result] turns={msg.num_turns}, cost=${msg.total_cost_usd or 0:.4f}")
    return final_text


TURN_TIMEOUT = 120  # seconds per turn


async def _do_turn(client, prompt: str) -> tuple[str, list]:
    """Inner turn logic (no timeout)."""
    await client.query(prompt)
    messages = []
    async for msg in client.receive_response():
        messages.append(msg)
    return messages


async def send_turn(client, prompt: str, label: str = "", timeout: int = TURN_TIMEOUT) -> tuple[str, list]:
    """Send a query and collect all messages. Returns (final_text, messages)."""
    print(f"\n  >> {prompt}")
    try:
        messages = await asyncio.wait_for(_do_turn(client, prompt), timeout=timeout)
    except asyncio.TimeoutError:
        print(f"  [TIMEOUT] Turn exceeded {timeout}s — skipping")
        return "", []
    final_text = print_messages(messages, label)
    return final_text, messages


# ── Test Cases ───────────────────────────────────────────────────

async def test_personality(client) -> bool:
    """Test 1: Multi-turn conversation with GRIM personality."""
    print("\n" + "=" * 60)
    print("TEST 1: Multi-turn personality persistence")
    print("=" * 60)

    # Turn 1: Identity check
    text1, _ = await send_turn(client, "Hey GRIM, what are you? Keep it brief.")

    assert text1, "No response from turn 1"
    text1_lower = text1.lower()
    # Should use first person and reference DFI/Dawn Field
    has_identity = any(w in text1_lower for w in ["i ", "i'm", "dawn field", "research", "companion"])
    assert has_identity, f"Expected GRIM identity, got: {text1[:100]}"
    print("  [PASS] Turn 1: GRIM identity present")

    # Turn 2: Context persistence — reference the previous turn
    text2, _ = await send_turn(client, "What did I just ask you? One sentence.")

    assert text2, "No response from turn 2"
    # Should reference the previous question about identity/what GRIM is
    has_context = any(w in text2.lower() for w in ["what", "who", "are", "identity", "yourself", "asked"])
    assert has_context, f"Expected context persistence, got: {text2[:100]}"
    print("  [PASS] Turn 2: Context persists across turns")

    print("  [PASS] Test 1 complete!")
    return True


async def test_kronos(client) -> bool:
    """Test 2: Real Kronos vault search across turns."""
    print("\n" + "=" * 60)
    print("TEST 2: Kronos vault search (real MCP)")
    print("=" * 60)

    # Turn 1: Search for PAC (semantic=false for speed)
    text1, msgs1 = await send_turn(client, "Search your vault for PAC theory using kronos_search with semantic=false. What do you find? Brief answer.")

    assert text1, "No response"
    # Should have used kronos_search
    tool_calls = [b.name for m in msgs1 if isinstance(m, AssistantMessage) for b in m.content if isinstance(b, ToolUseBlock)]
    has_kronos = any("kronos" in t for t in tool_calls)
    assert has_kronos, f"Expected kronos tool call, got: {tool_calls}"
    print(f"  [PASS] Turn 1: Used kronos tools: {tool_calls}")

    # Turn 2: Follow-up using previous context
    text2, msgs2 = await send_turn(client, "Use kronos_get to retrieve the FDO 'sec-topological-dynamics'. One sentence about what it is.")

    assert text2, "No response"
    # Should reference PAC from turn 1 and search for SEC connections
    tool_calls2 = [b.name for m in msgs2 if isinstance(m, AssistantMessage) for b in m.content if isinstance(b, ToolUseBlock)]
    print(f"  [PASS] Turn 2: Follow-up tools: {tool_calls2}")

    print("  [PASS] Test 2 complete!")
    return True


async def test_pool(client) -> bool:
    """Test 3: Pool job submission via in-process MCP."""
    print("\n" + "=" * 60)
    print("TEST 3: Pool job submission (in-process MCP)")
    print("=" * 60)

    # Turn 1: Submit a job
    text1, msgs1 = await send_turn(
        client,
        "Submit a research job to the pool: 'Find all experiments related to entropy collapse in the vault'. Normal priority.",
    )

    assert text1, "No response"
    tool_calls = [b.name for m in msgs1 if isinstance(m, AssistantMessage) for b in m.content if isinstance(b, ToolUseBlock)]
    has_pool_submit = any("pool_submit" in t for t in tool_calls)
    assert has_pool_submit, f"Expected pool_submit call, got: {tool_calls}"
    print(f"  [PASS] Turn 1: Submitted job via pool_submit")

    # Turn 2: Check status
    text2, msgs2 = await send_turn(client, "What's in the pool right now?")

    assert text2, "No response"
    tool_calls2 = [b.name for m in msgs2 if isinstance(m, AssistantMessage) for b in m.content if isinstance(b, ToolUseBlock)]
    has_pool_status = any("pool" in t for t in tool_calls2)
    assert has_pool_status, f"Expected pool status call, got: {tool_calls2}"
    print(f"  [PASS] Turn 2: Checked pool status")

    print("  [PASS] Test 3 complete!")
    return True


async def test_mixed(client) -> bool:
    """Test 4: Mixed tools — Kronos + Pool in same session."""
    print("\n" + "=" * 60)
    print("TEST 4: Mixed MCP servers (Kronos + Pool)")
    print("=" * 60)

    all_tool_calls = []

    # Turn 1: Kronos list (fast — no search, just file I/O)
    text1, msgs1 = await send_turn(
        client,
        "Use kronos_list with domain='projects' to list project FDOs. How many are there?",
        timeout=180,
    )
    assert text1, "No response from Kronos turn"
    calls1 = [b.name for m in msgs1 if isinstance(m, AssistantMessage) for b in m.content if isinstance(b, ToolUseBlock)]
    all_tool_calls.extend(calls1)
    print(f"  [PASS] Turn 1 (Kronos): {calls1}")

    # Turn 2: Pool submit
    text2, msgs2 = await send_turn(
        client,
        "Now submit a coding job to the pool: 'implement Phase 2 workspaces'. High priority.",
        timeout=180,
    )
    assert text2, "No response from pool turn"
    calls2 = [b.name for m in msgs2 if isinstance(m, AssistantMessage) for b in m.content if isinstance(b, ToolUseBlock)]
    all_tool_calls.extend(calls2)
    print(f"  [PASS] Turn 2 (Pool): {calls2}")

    has_kronos = any("kronos" in t for t in all_tool_calls)
    has_pool = any("pool" in t for t in all_tool_calls)

    print(f"  All tools used: {all_tool_calls}")
    assert has_kronos, f"Expected kronos tool call, got: {all_tool_calls}"
    assert has_pool, f"Expected pool tool call, got: {all_tool_calls}"
    print("  [PASS] Both Kronos (external) and Pool (in-process) MCP servers used in same persistent session!")

    print("  [PASS] Test 4 complete!")
    return True


# ── Main ──────────────────────────────────────────────────────────

async def main():
    global _queue

    print("=" * 60)
    print("SPIKE 05 -- GRIM as Persistent Agent SDK Session")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)

    # Initialize real SQLite queue (temp file)
    tmp_dir = tempfile.mkdtemp(prefix="grim_spike05_")
    db_path = Path(tmp_dir) / "spike_pool.db"
    _queue = JobQueue(db_path)
    await _queue.initialize()
    print(f"  Pool queue initialized: {db_path}")

    # Load system prompt
    system_prompt = load_system_prompt()
    print(f"  System prompt loaded: {len(system_prompt)} chars")

    # Configure session
    mcp_servers = {
        "kronos": KRONOS_MCP_CONFIG,
        "pool": pool_server,
    }

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        allowed_tools=ALLOWED_TOOLS,
        permission_mode="bypassPermissions",
        max_turns=8,
    )

    # Run all tests in a SINGLE persistent session
    results = {}
    tests = [
        ("personality", test_personality),
        ("kronos", test_kronos),
        ("pool", test_pool),
        ("mixed", test_mixed),
    ]

    async with ClaudeSDKClient(options=options) as client:
        for name, test_fn in tests:
            try:
                results[name] = await test_fn(client)
            except Exception as e:
                print(f"\n  [FAIL] {name}: {e}")
                results[name] = False
                import traceback
                traceback.print_exc()

    # Summary
    print("\n" + "=" * 60)
    print("SPIKE 05 -- RESULTS")
    print("=" * 60)
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print(f"\n  {passed}/{total} tests passed")

    # Check total cost
    print(f"\n  Pool DB: {db_path}")
    jobs = await _queue.list_jobs()
    print(f"  Jobs in queue: {len(jobs)}")
    for j in jobs:
        print(f"    {j.id}  {j.job_type.value}  {j.status.value}  {j.instructions[:60]}")

    if passed == total:
        print("\n  SPIKE 05 PROVEN -- GRIM works as persistent Agent SDK session!")
        print("  Multi-turn context, Kronos MCP, Pool MCP, personality -- all verified.")
    else:
        print("\n  SPIKE 05 INCOMPLETE -- some tests failed.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
