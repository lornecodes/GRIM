"""Test GRIM with live Kronos MCP connection."""
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

grim_root = Path(__file__).resolve().parent.parent
load_dotenv(grim_root / ".env")

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)

from core.config import load_config
from core.graph import build_graph
from core.tools.workspace import set_workspace_root
from core.__main__ import kronos_mcp_session


async def main():
    config = load_config(grim_root=grim_root)
    set_workspace_root(grim_root.parent)

    print(f"Config: env={config.env}, model={config.model}")
    print(f"Vault: {config.vault_path}")
    print(f"Vault exists: {config.vault_path.exists()}")

    # Connect to real Kronos MCP
    async with kronos_mcp_session(config) as mcp:
        if mcp is None:
            print("\nFAILED: Could not connect to Kronos MCP")
            return

        print(f"\nMCP connected: {type(mcp).__name__}")

        # Quick MCP smoke test - list tools
        tools = await mcp.list_tools()
        print(f"MCP tools available: {[t.name for t in tools.tools]}")

        # Test search
        print("\n--- Testing kronos_search ---")
        result = await mcp.call_tool("kronos_search", {"query": "PAC framework", "semantic": True})
        if result.content:
            import json
            data = json.loads(result.content[0].text)
            if isinstance(data, list):
                print(f"Search returned {len(data)} results:")
                for r in data[:3]:
                    print(f"  {r.get('id', '?')} - {r.get('title', '?')} ({r.get('domain', '?')})")
            elif isinstance(data, dict) and "results" in data:
                results = data["results"]
                print(f"Search returned {len(results)} results:")
                for r in results[:3]:
                    print(f"  {r.get('id', '?')} - {r.get('title', '?')} ({r.get('domain', '?')})")
            else:
                print(f"Search returned: {str(data)[:200]}")
        else:
            print("Search returned empty")

        # Now test full graph with MCP
        print("\n" + "=" * 60)
        print("FULL GRAPH TEST: Companion with live Kronos")
        print("=" * 60)

        graph = build_graph(config, mcp_session=mcp)
        graph_config = {"configurable": {"thread_id": "mcp-test-1"}}

        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content="What do you know about PAC?")],
                "session_start": datetime.now(),
            },
            config=graph_config,
        )

        # Show knowledge context
        kc = result.get("knowledge_context", [])
        print(f"\nKnowledge context: {len(kc)} FDOs retrieved")
        for fdo in kc[:5]:
            print(f"  {fdo.id} ({fdo.domain}) conf={fdo.confidence}")

        # Show matched skills
        ms = result.get("matched_skills", [])
        print(f"Matched skills: {[s.name for s in ms]}")

        # Show routing
        print(f"Mode: {result.get('mode')}")

        # Show response
        msgs = result.get("messages", [])
        print(f"\nMessages: {len(msgs)} total")
        for m in msgs:
            mtype = getattr(m, "type", "?")
            content = m.content if hasattr(m, "content") else str(m)
            if mtype == "ai" and isinstance(content, str) and len(content) > 10:
                print(f"\n--- GRIM Response ---")
                print(content[:500])
                print("---")

        # Test delegation path with live MCP
        print("\n" + "=" * 60)
        print("DELEGATION TEST: Memory agent with live Kronos")
        print("=" * 60)

        graph_config2 = {"configurable": {"thread_id": "mcp-test-2"}}
        result2 = await graph.ainvoke(
            {
                "messages": [HumanMessage(content="remember this: GRIM core is now fully built with 4 agents and 11 skills")],
                "session_start": datetime.now(),
            },
            config=graph_config2,
        )

        print(f"Mode: {result2.get('mode')}")
        print(f"Delegation: {result2.get('delegation_type')}")

        msgs2 = result2.get("messages", [])
        for m in msgs2:
            mtype = getattr(m, "type", "?")
            content = m.content if hasattr(m, "content") else str(m)
            if mtype == "ai" and isinstance(content, str) and len(content) > 10:
                print(f"\n--- Agent Response ---")
                print(content[:500])
                print("---")

    print("\n\nDONE — MCP session cleaned up")


if __name__ == "__main__":
    asyncio.run(main())
