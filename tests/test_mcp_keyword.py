"""Test MCP with keyword-only search (no semantic) + correct FDO ID."""
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

    params = StdioServerParameters(
        command="python",
        args=["-m", "kronos_mcp"],
        env={
            "KRONOS_VAULT_PATH": str(vault),
            "KRONOS_SKILLS_PATH": str(skills),
            "TF_ENABLE_ONEDNN_OPTS": "0",  # suppress TF noise
            **os.environ,
        },
    )

    print("Connecting...")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Connected!\n")

            # Test 1: keyword-only search (no semantic embedding)
            print("--- kronos_search (keyword only) ---")
            try:
                result = await asyncio.wait_for(
                    session.call_tool("kronos_search", {"query": "PAC framework", "semantic": False}),
                    timeout=15,
                )
                if result.content:
                    data = json.loads(result.content[0].text)
                    results = data if isinstance(data, list) else data.get("results", [])
                    print(f"  {len(results)} results")
                    for r in results[:5]:
                        rid = r.get("id", "?")
                        rtitle = r.get("title", "?")
                        rdomain = r.get("domain", "?")
                        rscore = r.get("score", "?")
                        print(f"    [{rscore:.2f}] {rid} ({rdomain}) - {rtitle}" if isinstance(rscore, float) else f"    {rid} ({rdomain}) - {rtitle}")
                else:
                    print("  Empty")
            except asyncio.TimeoutError:
                print("  TIMEOUT")
            except Exception as e:
                print(f"  ERROR: {e}")
                traceback.print_exc()

            # Test 2: list to find actual FDO IDs
            print("\n--- kronos_list (physics domain) ---")
            try:
                result = await asyncio.wait_for(
                    session.call_tool("kronos_list", {"domain": "physics"}),
                    timeout=15,
                )
                if result.content:
                    data = json.loads(result.content[0].text)
                    items = data if isinstance(data, list) else data.get("results", data.get("items", []))
                    if isinstance(items, list):
                        print(f"  {len(items)} FDOs in physics")
                        for item in items[:8]:
                            if isinstance(item, dict):
                                print(f"    {item.get('id', '?')} - {item.get('title', '?')}")
                            else:
                                print(f"    {item}")
                    else:
                        print(f"  {str(data)[:300]}")
                else:
                    print("  Empty")
            except asyncio.TimeoutError:
                print("  TIMEOUT")
            except Exception as e:
                print(f"  ERROR: {e}")
                traceback.print_exc()

            # Test 3: list ai-systems domain
            print("\n--- kronos_list (ai-systems domain) ---")
            try:
                result = await asyncio.wait_for(
                    session.call_tool("kronos_list", {"domain": "ai-systems"}),
                    timeout=15,
                )
                if result.content:
                    data = json.loads(result.content[0].text)
                    items = data if isinstance(data, list) else data.get("results", data.get("items", []))
                    if isinstance(items, list):
                        print(f"  {len(items)} FDOs in ai-systems")
                        for item in items[:8]:
                            if isinstance(item, dict):
                                print(f"    {item.get('id', '?')} - {item.get('title', '?')}")
                            else:
                                print(f"    {item}")
                    else:
                        print(f"  {str(data)[:300]}")
                else:
                    print("  Empty")
            except asyncio.TimeoutError:
                print("  TIMEOUT")
            except Exception as e:
                print(f"  ERROR: {e}")
                traceback.print_exc()

            # Test 4: get a specific FDO (use grim-architecture which we know exists)
            print("\n--- kronos_get (grim-architecture) ---")
            try:
                result = await asyncio.wait_for(
                    session.call_tool("kronos_get", {"id": "grim-architecture"}),
                    timeout=15,
                )
                if result.content:
                    data = json.loads(result.content[0].text)
                    print(f"  Title: {data.get('title', '?')}")
                    print(f"  Domain: {data.get('domain', '?')}")
                    print(f"  Status: {data.get('status', '?')}")
                    print(f"  Confidence: {data.get('confidence', '?')}")
                    related = data.get("related", [])
                    print(f"  Related: {related}")
                else:
                    print("  Empty")
            except asyncio.TimeoutError:
                print("  TIMEOUT")
            except Exception as e:
                print(f"  ERROR: {e}")
                traceback.print_exc()

            # Test 5: kronos_skills
            print("\n--- kronos_skills ---")
            try:
                result = await asyncio.wait_for(
                    session.call_tool("kronos_skills", {}),
                    timeout=15,
                )
                if result.content:
                    data = json.loads(result.content[0].text)
                    skills_list = data if isinstance(data, list) else data.get("skills", [])
                    print(f"  {len(skills_list)} skills")
                    for s in skills_list[:5]:
                        if isinstance(s, dict):
                            print(f"    {s.get('name', '?')}: {s.get('description', '?')[:60]}")
                        else:
                            print(f"    {s}")
                else:
                    print("  Empty")
            except asyncio.TimeoutError:
                print("  TIMEOUT")
            except Exception as e:
                print(f"  ERROR: {e}")
                traceback.print_exc()

    print("\n=== ALL TESTS COMPLETE ===")


if __name__ == "__main__":
    asyncio.run(test())
