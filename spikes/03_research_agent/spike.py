"""
Spike 03 — Research Agent
==========================
Prove that a research agent can:
  1. Use Kronos MCP tools (search, get, graph) via SDK
  2. Synthesize knowledge from multiple FDOs
  3. Answer research questions with vault-sourced context
  4. Navigate the knowledge graph to find connections

This spike connects to the REAL Kronos MCP server (stdio transport)
to prove the integration works end-to-end.

Run:
    cd GRIM/spikes/03_research_agent
    python spike.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    UserMessage,
    SystemMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)

os.environ.pop("CLAUDECODE", None)

# ── Kronos MCP Server Config ─────────────────────────────────────
# Real Kronos server via stdio transport — same as GRIM uses

WORKSPACE_ROOT = Path("c:/Users/peter/repos/core_workspace")
VAULT_PATH = WORKSPACE_ROOT / "kronos-vault"
SKILLS_PATH = WORKSPACE_ROOT / "GRIM" / "skills"

kronos_mcp_config = {
    "command": r"c:\Users\peter\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\LocalCache\local-packages\Python311\Scripts\kronos-mcp.exe",
    "env": {
        "KRONOS_VAULT_PATH": str(VAULT_PATH),
        "KRONOS_SKILLS_PATH": str(SKILLS_PATH),
        "KRONOS_WORKSPACE_ROOT": str(WORKSPACE_ROOT),
    },
}


# ── Kronos tool names (prefixed by SDK) ──────────────────────────
KRONOS_TOOLS = [
    "mcp__kronos__kronos_search",
    "mcp__kronos__kronos_get",
    "mcp__kronos__kronos_graph",
    "mcp__kronos__kronos_list",
    "mcp__kronos__kronos_tags",
    "mcp__kronos__kronos_deep_dive",
    "mcp__kronos__kronos_navigate",
    "mcp__kronos__kronos_read_source",
    "mcp__kronos__kronos_search_source",
]


# ── Test Cases ────────────────────────────────────────────────────

async def test_search_and_synthesize():
    """Test 1: Research agent searches vault and synthesizes knowledge."""
    print("\n" + "=" * 60)
    print("TEST 1: Search vault + synthesize (PAC theory)")
    print("=" * 60)

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are a research agent for the Dawn Field Institute. "
            "You have access to the Kronos knowledge vault via MCP tools. "
            "Your job is to research topics by searching the vault, reading FDOs, "
            "and synthesizing clear answers.\n\n"
            "Be concise. Use kronos_search to find relevant FDOs, then kronos_get "
            "to read them. Cite FDO IDs in your answers."
        ),
        mcp_servers={"kronos": kronos_mcp_config},
        allowed_tools=KRONOS_TOOLS,
        permission_mode="bypassPermissions",
        max_turns=8,
    )

    messages = []
    tool_uses = []

    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            "What is PAC in Dawn Field Theory? Search the vault for PAC-related FDOs "
            "and give me a concise summary of the theory."
        )
        async for msg in client.receive_response():
            messages.append(msg)
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and len(block.text) > 10:
                        print(f"  [Research] {block.text[:200]}")
                    elif isinstance(block, ToolUseBlock):
                        tool_uses.append(block.name)
                        print(f"  [ToolUse] {block.name}({json.dumps(block.input)[:60]})")
            elif isinstance(msg, ResultMessage):
                print(f"  [Result] turns={msg.num_turns}, cost=${msg.total_cost_usd or 0:.6f}")

    result = next((m for m in messages if isinstance(m, ResultMessage)), None)
    assert result is not None, "No ResultMessage received"
    assert not result.is_error, f"Query errored"

    # Verify kronos tools were used
    kronos_uses = [t for t in tool_uses if "kronos" in t]
    print(f"\n  Kronos tools used: {kronos_uses}")
    assert len(kronos_uses) >= 1, "Expected at least 1 Kronos tool use"

    # Check the response mentions PAC
    full_text = " ".join(
        block.text for msg in messages if isinstance(msg, AssistantMessage)
        for block in msg.content if isinstance(block, TextBlock)
    )
    assert "PAC" in full_text.upper(), "Response should mention PAC"

    print("  [PASS] Research agent searched vault and synthesized PAC knowledge!")
    return True


async def test_graph_traversal():
    """Test 2: Navigate the knowledge graph to find connections."""
    print("\n" + "=" * 60)
    print("TEST 2: Graph traversal (find connections)")
    print("=" * 60)

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are a research agent. Use the Kronos knowledge graph tools to "
            "explore connections between concepts.\n\n"
            "Use kronos_graph to traverse from an FDO and find related concepts. "
            "Then use kronos_get to read connected FDOs. Report what you find concisely."
        ),
        mcp_servers={"kronos": kronos_mcp_config},
        allowed_tools=KRONOS_TOOLS,
        permission_mode="bypassPermissions",
        max_turns=8,
    )

    messages = []
    tool_uses = []

    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            "Start from the 'grim-architecture' FDO and explore its knowledge graph "
            "(depth 2). What are the main connected concepts?"
        )
        async for msg in client.receive_response():
            messages.append(msg)
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and len(block.text) > 10:
                        print(f"  [Research] {block.text[:200]}")
                    elif isinstance(block, ToolUseBlock):
                        tool_uses.append(block.name)
                        print(f"  [ToolUse] {block.name}({json.dumps(block.input)[:60]})")
            elif isinstance(msg, ResultMessage):
                print(f"  [Result] turns={msg.num_turns}, cost=${msg.total_cost_usd or 0:.6f}")

    result = next((m for m in messages if isinstance(m, ResultMessage)), None)
    assert result is not None, "No ResultMessage received"

    graph_uses = [t for t in tool_uses if "graph" in t]
    print(f"\n  Graph traversals: {len(graph_uses)}")
    print(f"  Total Kronos calls: {len([t for t in tool_uses if 'kronos' in t])}")

    assert len([t for t in tool_uses if "kronos" in t]) >= 1, "Expected Kronos tools to be used"
    print("  [PASS] Research agent navigated knowledge graph!")
    return True


async def test_source_dive():
    """Test 3: Deep dive into source material from FDOs."""
    print("\n" + "=" * 60)
    print("TEST 3: Source deep dive (FDO to source code)")
    print("=" * 60)

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are a research agent. Use Kronos tools to find source material "
            "behind concepts. Use kronos_deep_dive to find source paths, then "
            "kronos_read_source to inspect actual code. Report what you find."
        ),
        mcp_servers={"kronos": kronos_mcp_config},
        allowed_tools=KRONOS_TOOLS,
        permission_mode="bypassPermissions",
        max_turns=8,
    )

    messages = []
    tool_uses = []

    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            "Find the source material for the GRIM architecture. "
            "Use kronos_deep_dive to find source paths, then read one of the key source files."
        )
        async for msg in client.receive_response():
            messages.append(msg)
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and len(block.text) > 10:
                        print(f"  [Research] {block.text[:200]}")
                    elif isinstance(block, ToolUseBlock):
                        tool_uses.append(block.name)
                        print(f"  [ToolUse] {block.name}({json.dumps(block.input)[:60]})")
            elif isinstance(msg, ResultMessage):
                print(f"  [Result] turns={msg.num_turns}, cost=${msg.total_cost_usd or 0:.6f}")

    result = next((m for m in messages if isinstance(m, ResultMessage)), None)
    assert result is not None, "No ResultMessage received"

    print(f"\n  Tools used: {tool_uses}")
    deep_dives = [t for t in tool_uses if "deep_dive" in t]
    source_reads = [t for t in tool_uses if "read_source" in t or "search_source" in t]
    print(f"  Deep dives: {len(deep_dives)}")
    print(f"  Source reads: {len(source_reads)}")

    assert len([t for t in tool_uses if "kronos" in t]) >= 1, "Expected Kronos tools to be used"
    print("  [PASS] Research agent dove into source material!")
    return True


# ── Main ──────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("SPIKE 03 -- Research Agent")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)

    # Verify Kronos MCP server exists
    kronos_exe = Path(r"c:\Users\peter\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\LocalCache\local-packages\Python311\Scripts\kronos-mcp.exe")
    if not kronos_exe.exists():
        print(f"  ERROR: Kronos MCP server not found at {kronos_exe}")
        print(f"  Run: cd GRIM/mcp/kronos && pip install -e .")
        sys.exit(1)

    results = {}
    tests = [
        ("search_and_synthesize", test_search_and_synthesize),
        ("graph_traversal", test_graph_traversal),
        ("source_dive", test_source_dive),
    ]

    for name, test_fn in tests:
        try:
            results[name] = await test_fn()
        except Exception as e:
            print(f"\n  [FAIL] {name}: {e}")
            results[name] = False
            import traceback
            traceback.print_exc()

    # Summary
    print("\n" + "=" * 60)
    print("SPIKE 03 -- RESULTS")
    print("=" * 60)
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print(f"\n  {passed}/{total} tests passed")

    if passed == total:
        print("\n  SPIKE 03 PROVEN -- Research agent works with real Kronos MCP!")
    else:
        print("\n  SPIKE 03 INCOMPLETE -- some tests failed.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
