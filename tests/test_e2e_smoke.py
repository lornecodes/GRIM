"""End-to-end smoke test — invoke the full graph in debug mode."""
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

# Bootstrap
grim_root = Path(__file__).resolve().parent.parent
load_dotenv(grim_root / ".env")
os.environ["GRIM_ENV"] = "debug"

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)

from core.config import load_config
from core.graph import build_graph
from core.tools.workspace import set_workspace_root


async def main():
    config = load_config(grim_root=grim_root)
    set_workspace_root(grim_root.parent)

    print(f"Config: env={config.env}, model={config.model}")
    print(f"Vault: {config.vault_path}")

    # Build graph (no MCP in debug)
    graph = build_graph(config, mcp_session=None)

    graph_config = {"configurable": {"thread_id": "smoke-test"}}

    # Test 1: Pure companion mode
    print("\n" + "=" * 60)
    print("TEST 1: Companion mode (conversation)")
    print("=" * 60)
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="What do you know about Dawn Field Theory?")],
            "session_start": datetime.now(),
        },
        config=graph_config,
    )
    msgs = result.get("messages", [])
    print(f"Messages in state: {len(msgs)}")
    for m in msgs:
        mtype = getattr(m, "type", "?")
        content = m.content if hasattr(m, "content") else str(m)
        print(f"  [{mtype}] {content[:200]}")

    field = result.get("field_state")
    if field:
        print(f"Field state: {field.snapshot()}")

    print(f"Mode: {result.get('mode')}")
    print(f"Delegation: {result.get('delegation_type')}")

    # Test 2: Delegation mode (memory)
    print("\n" + "=" * 60)
    print("TEST 2: Delegation mode (remember this)")
    print("=" * 60)
    graph_config2 = {"configurable": {"thread_id": "smoke-test-2"}}
    result2 = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="remember this: The golden ratio appears in PAC recursion naturally")],
            "session_start": datetime.now(),
        },
        config=graph_config2,
    )
    msgs2 = result2.get("messages", [])
    print(f"Messages in state: {len(msgs2)}")
    for m in msgs2:
        mtype = getattr(m, "type", "?")
        content = m.content if hasattr(m, "content") else str(m)
        print(f"  [{mtype}] {content[:200]}")

    print(f"Mode: {result2.get('mode')}")
    print(f"Delegation: {result2.get('delegation_type')}")

    agent_result = result2.get("agent_result")
    if agent_result:
        print(f"Agent result: {agent_result.agent} success={agent_result.success}")
        print(f"  Summary: {agent_result.summary[:200]}")

    print("\n" + "=" * 60)
    print("SMOKE TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
