"""Full graph test with live Kronos MCP — tests the complete memory pipeline."""
import asyncio
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

grim_root = Path(__file__).resolve().parent.parent
load_dotenv(grim_root / ".env")

# Suppress TF noise
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import logging
logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)
logging.getLogger("tensorflow").setLevel(logging.ERROR)

from langchain_core.messages import HumanMessage

from core.config import load_config
from core.graph import build_graph
from core.tools.workspace import set_workspace_root
from core.__main__ import kronos_mcp_session


async def main():
    config = load_config(grim_root=grim_root)
    set_workspace_root(grim_root.parent)

    print(f"Config: env={config.env}, model={config.model}")
    print(f"MCP command: {config.kronos_mcp_command} {config.kronos_mcp_args}")
    print(f"Vault: {config.vault_path}")

    async with kronos_mcp_session(config) as mcp:
        if mcp is None:
            print("\nFAILED: Could not connect to Kronos MCP")
            return

        print(f"\nMCP connected: {type(mcp).__name__}")

        # Build the full graph
        graph = build_graph(config, mcp_session=mcp)
        print("Graph built successfully")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # TEST 1: Companion path — ask about something in vault
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        print("\n" + "=" * 60)
        print("TEST 1: Companion with live Kronos knowledge")
        print("=" * 60)

        graph_config = {"configurable": {"thread_id": "live-test-1"}}
        try:
            result = await asyncio.wait_for(
                graph.ainvoke(
                    {
                        "messages": [HumanMessage(content="What is the PAC framework and how does it relate to the golden ratio?")],
                        "session_start": datetime.now(),
                    },
                    config=graph_config,
                ),
                timeout=90,
            )

            # Check knowledge context
            kc = result.get("knowledge_context", [])
            print(f"\nKnowledge context: {len(kc)} FDOs retrieved")
            for fdo in kc[:5]:
                print(f"  {fdo.id} ({fdo.domain}) conf={fdo.confidence}")

            # Check skills
            ms = result.get("matched_skills", [])
            print(f"Matched skills: {[s.name for s in ms]}")
            print(f"Mode: {result.get('mode')}")

            # Show GRIM's response
            msgs = result.get("messages", [])
            print(f"Total messages: {len(msgs)}")
            for m in reversed(msgs):
                mtype = getattr(m, "type", "?")
                if mtype == "ai":
                    content = m.content if hasattr(m, "content") else str(m)
                    if isinstance(content, str) and len(content) > 10:
                        print(f"\n--- GRIM Response ---")
                        # Handle unicode safely on Windows console
                        safe = content[:600].encode("utf-8", errors="replace").decode("utf-8")
                        try:
                            print(safe)
                        except UnicodeEncodeError:
                            print(safe.encode("ascii", errors="replace").decode("ascii"))
                        if len(content) > 600:
                            print(f"... [{len(content)} chars total]")
                        print("---")
                        break

            print("\nTEST 1: PASSED" if kc else "\nTEST 1: WARNING - no knowledge context")

        except asyncio.TimeoutError:
            print("\nTEST 1: TIMEOUT (90s)")
        except Exception as e:
            print(f"\nTEST 1: ERROR - {e}")
            traceback.print_exc()

    print("\n\nDone — MCP session closed cleanly")


if __name__ == "__main__":
    asyncio.run(main())
