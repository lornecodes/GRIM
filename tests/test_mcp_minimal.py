"""Minimal MCP connection + search test."""
import asyncio
import json
import os
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv

grim_root = Path(__file__).resolve().parent.parent
load_dotenv(grim_root / ".env")


async def test():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    vault = (grim_root / ".." / "kronos-vault").resolve()
    skills = (grim_root / "skills").resolve()

    print(f"Vault: {vault} (exists: {vault.exists()})")
    print(f"Skills: {skills} (exists: {skills.exists()})")

    params = StdioServerParameters(
        command="python",
        args=["-m", "kronos_mcp"],
        env={
            "KRONOS_VAULT_PATH": str(vault),
            "KRONOS_SKILLS_PATH": str(skills),
            **os.environ,
        },
    )

    print("Connecting to Kronos MCP via python -m kronos_mcp ...")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Connected + initialized!")

            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print(f"Tools ({len(tool_names)}): {tool_names}")

            # Test 1: kronos_search
            print("\n--- Test: kronos_search ---")
            try:
                result = await asyncio.wait_for(
                    session.call_tool("kronos_search", {"query": "PAC"}),
                    timeout=30,
                )
                if result.content:
                    text = result.content[0].text
                    data = json.loads(text)
                    if isinstance(data, list):
                        print(f"  {len(data)} results")
                        for r in data[:3]:
                            rid = r.get("id", "?")
                            rtitle = r.get("title", "?")
                            print(f"    {rid} - {rtitle}")
                    elif isinstance(data, dict) and "results" in data:
                        results = data["results"]
                        print(f"  {len(results)} results")
                        for r in results[:3]:
                            rid = r.get("id", "?")
                            rtitle = r.get("title", "?")
                            print(f"    {rid} - {rtitle}")
                    else:
                        print(f"  Response: {str(data)[:300]}")
                else:
                    print("  Empty")
            except asyncio.TimeoutError:
                print("  TIMEOUT")
            except Exception as e:
                print(f"  ERROR: {e}")
                traceback.print_exc()

            # Test 2: kronos_get
            print("\n--- Test: kronos_get ---")
            try:
                result = await asyncio.wait_for(
                    session.call_tool("kronos_get", {"id": "pac-framework"}),
                    timeout=15,
                )
                if result.content:
                    text = result.content[0].text
                    data = json.loads(text)
                    title = data.get("title", data.get("id", "?"))
                    status = data.get("status", "?")
                    print(f"  Got: {title} (status: {status})")
                else:
                    print("  Empty")
            except asyncio.TimeoutError:
                print("  TIMEOUT")
            except Exception as e:
                print(f"  ERROR: {e}")
                traceback.print_exc()

            # Test 3: kronos_list
            print("\n--- Test: kronos_list ---")
            try:
                result = await asyncio.wait_for(
                    session.call_tool("kronos_list", {}),
                    timeout=15,
                )
                if result.content:
                    text = result.content[0].text
                    data = json.loads(text)
                    if isinstance(data, list):
                        print(f"  {len(data)} FDOs in vault")
                    elif isinstance(data, dict):
                        total = sum(len(v) if isinstance(v, list) else 1 for v in data.values())
                        print(f"  {total} FDOs across {len(data)} domains")
                    else:
                        print(f"  {str(data)[:200]}")
                else:
                    print("  Empty")
            except asyncio.TimeoutError:
                print("  TIMEOUT")
            except Exception as e:
                print(f"  ERROR: {e}")
                traceback.print_exc()

    print("\nDone — MCP session closed cleanly")


if __name__ == "__main__":
    asyncio.run(test())
