"""Vault Explorer endpoint tests — verify REST endpoints proxy to MCP correctly.

Mocks _mcp_task_call to test endpoint routing, query param forwarding,
Pydantic validation, full graph aggregation logic, and error propagation.

Run: cd GRIM && python -m pytest tests/test_vault_endpoints.py -v --tb=short
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Ensure GRIM root is on path
GRIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GRIM_ROOT))

# Suppress config warnings before importing app
os.environ.setdefault("GRIM_ENV", "debug")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import httpx
from server.app import app


def run_async(coro):
    """Run a coroutine synchronously. Uses asyncio.run() to avoid stale event loop
    issues when tests run after other async test modules."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test data fixtures
# ---------------------------------------------------------------------------

MOCK_FDO_LIST = {
    "fdos": [
        {"id": "start-here", "title": "Start Here", "domain": "personal",
         "status": "stable", "confidence": 0.9, "tags": ["orientation"]},
        {"id": "pac-comprehensive", "title": "PAC Theory", "domain": "physics",
         "status": "stable", "confidence": 0.85, "tags": ["pac", "theory"]},
        {"id": "grim-architecture", "title": "GRIM Architecture", "domain": "ai-systems",
         "status": "developing", "confidence": 0.8, "tags": ["grim"]},
    ]
}

MOCK_SEARCH_RESULTS = {
    "results": [
        {"id": "pac-comprehensive", "title": "PAC Theory", "domain": "physics",
         "score": 0.95},
    ]
}

MOCK_TAGS = {
    "total_tags": 50,
    "total_fdos": 120,
    "top_tags": [{"tag": "pac", "count": 12}, {"tag": "grim", "count": 8}],
    "by_domain": {"physics": [{"tag": "pac", "count": 12}]},
}

MOCK_GRAPH = {
    "nodes": {
        "start-here": {"id": "start-here", "title": "Start Here", "domain": "personal",
                       "status": "stable", "confidence": 0.9},
    },
    "edges": [
        {"from": "start-here", "to": "pac-comprehensive", "type": "related"},
    ],
}

MOCK_FDO_FULL = {
    "id": "pac-comprehensive", "title": "PAC Theory", "domain": "physics",
    "status": "stable", "confidence": 0.85, "tags": ["pac", "theory"],
    "body": "# PAC Theory\n\n## Summary\nPAC regulation...",
    "related": ["start-here"], "created": "2025-01-01", "updated": "2025-12-01",
}

MOCK_VALIDATE = {
    "total_fdos": 120,
    "domains": {"physics": 56, "ai-systems": 17, "tools": 12},
    "issues_count": 3,
}

MOCK_CREATE_RESULT = {"status": "created", "id": "test-new-fdo"}
MOCK_UPDATE_RESULT = {"status": "updated", "id": "pac-comprehensive"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestVaultList(unittest.TestCase):
    """GET /api/vault/list"""

    def test_list_all(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_FDO_LIST) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/list")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(len(data["fdos"]), 3)
                mock.assert_called_once_with("kronos_list", {})
        run_async(run())

    def test_list_with_domain_filter(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_FDO_LIST) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/list?domain=physics")
                self.assertEqual(resp.status_code, 200)
                mock.assert_called_once_with("kronos_list", {"domain": "physics"})
        run_async(run())

    def test_list_mcp_error(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value={"error": "MCP unavailable"}):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/list")
                self.assertEqual(resp.status_code, 500)
                self.assertIn("error", resp.json())
        run_async(run())


class TestVaultSearch(unittest.TestCase):
    """GET /api/vault/search"""

    def test_search_basic(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_SEARCH_RESULTS) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/search?q=pac")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(len(resp.json()["results"]), 1)
                mock.assert_called_once_with("kronos_search", {
                    "query": "pac", "semantic": True,
                })
        run_async(run())

    def test_search_semantic_false(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_SEARCH_RESULTS) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/search?q=pac&semantic=false")
                self.assertEqual(resp.status_code, 200)
                mock.assert_called_once_with("kronos_search", {
                    "query": "pac", "semantic": False,
                })
        run_async(run())

    def test_search_missing_query(self):
        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/vault/search")
            self.assertEqual(resp.status_code, 422)  # FastAPI validation
        run_async(run())


class TestVaultTags(unittest.TestCase):
    """GET /api/vault/tags"""

    def test_tags_all(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_TAGS) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/tags")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()["total_tags"], 50)
                mock.assert_called_once_with("kronos_tags", {})
        run_async(run())

    def test_tags_with_domain(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_TAGS) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/tags?domain=physics")
                mock.assert_called_once_with("kronos_tags", {"domain": "physics"})
        run_async(run())


class TestVaultGraph(unittest.TestCase):
    """GET /api/vault/graph"""

    def test_graph_single_node(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_GRAPH) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/graph?id=start-here&depth=2")
                self.assertEqual(resp.status_code, 200)
                mock.assert_called_once_with("kronos_graph", {
                    "id": "start-here", "depth": 2, "scope": "all",
                })
        run_async(run())

    def test_graph_single_with_scope(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_GRAPH) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/graph?id=start-here&scope=knowledge")
                self.assertEqual(resp.status_code, 200)
                mock.assert_called_once_with("kronos_graph", {
                    "id": "start-here", "depth": 1, "scope": "knowledge",
                })
        run_async(run())

    def test_full_graph_aggregation(self):
        """Full graph: list all → batch graph calls → aggregate edges."""
        call_count = {"list": 0, "graph": 0}

        async def mock_mcp(tool: str, args: dict):
            if tool == "kronos_list":
                call_count["list"] += 1
                return MOCK_FDO_LIST
            elif tool == "kronos_graph":
                call_count["graph"] += 1
                fdo_id = args.get("id", "")
                if fdo_id == "start-here":
                    return {"edges": [
                        {"from": "start-here", "to": "pac-comprehensive", "type": "related"},
                    ]}
                elif fdo_id == "pac-comprehensive":
                    return {"edges": [
                        {"from": "pac-comprehensive", "to": "start-here", "type": "related"},
                        {"from": "pac-comprehensive", "to": "grim-architecture", "type": "related"},
                    ]}
                return {"edges": []}
            return {}

        async def run():
            with patch("server.app._mcp_task_call", side_effect=mock_mcp):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/graph")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                # Should have all 3 nodes
                self.assertEqual(data["count"], 3)
                self.assertIn("start-here", data["nodes"])
                self.assertIn("pac-comprehensive", data["nodes"])
                # Edges should be deduplicated
                edge_keys = {(e["from"], e["to"]) for e in data["edges"]}
                self.assertIn(("start-here", "pac-comprehensive"), edge_keys)
                self.assertIn(("pac-comprehensive", "grim-architecture"), edge_keys)
                # Should have called list once and graph for each FDO
                self.assertEqual(call_count["list"], 1)
                self.assertEqual(call_count["graph"], 3)
        run_async(run())

    def test_full_graph_handles_exceptions(self):
        """Full graph should handle exceptions from individual graph calls gracefully."""
        async def mock_mcp(tool: str, args: dict):
            if tool == "kronos_list":
                return {"fdos": [
                    {"id": "a", "title": "A", "domain": "physics", "status": "seed"},
                    {"id": "b", "title": "B", "domain": "tools", "status": "seed"},
                ]}
            elif tool == "kronos_graph":
                if args.get("id") == "a":
                    raise Exception("MCP timeout")
                return {"edges": [{"from": "b", "to": "a", "type": "related"}]}
            return {}

        async def run():
            with patch("server.app._mcp_task_call", side_effect=mock_mcp):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/graph")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data["count"], 2)
                # Only edges from successful call
                self.assertEqual(len(data["edges"]), 1)
        run_async(run())

    def test_full_graph_list_error(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value={"error": "vault offline"}):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/graph")
                self.assertEqual(resp.status_code, 500)
        run_async(run())


class TestVaultStats(unittest.TestCase):
    """GET /api/vault/stats"""

    def test_stats(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_VALIDATE) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/stats")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()["total_fdos"], 120)
                mock.assert_called_once_with("kronos_validate", {})
        run_async(run())


class TestVaultGet(unittest.TestCase):
    """GET /api/vault/{fdo_id}"""

    def test_get_fdo(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_FDO_FULL) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/pac-comprehensive")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data["id"], "pac-comprehensive")
                self.assertEqual(data["domain"], "physics")
                mock.assert_called_once_with("kronos_get", {"id": "pac-comprehensive"})
        run_async(run())

    def test_get_fdo_error(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value={"error": "FDO not found"}):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/nonexistent")
                self.assertEqual(resp.status_code, 500)
        run_async(run())

    def test_get_fdo_url_encoded(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_FDO_FULL) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/vault/grim-architecture")
                self.assertEqual(resp.status_code, 200)
                mock.assert_called_once_with("kronos_get", {"id": "grim-architecture"})
        run_async(run())


class TestVaultCreate(unittest.TestCase):
    """POST /api/vault"""

    def test_create_fdo(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_CREATE_RESULT) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post("/api/vault", json={
                        "id": "test-new-fdo",
                        "title": "Test FDO",
                        "domain": "tools",
                        "confidence": 0.5,
                        "body": "# Test\n\n## Summary\nTest FDO",
                    })
                self.assertEqual(resp.status_code, 200)
                # Verify args forwarded to MCP
                call_args = mock.call_args[0]
                self.assertEqual(call_args[0], "kronos_create")
                self.assertEqual(call_args[1]["id"], "test-new-fdo")
                self.assertEqual(call_args[1]["domain"], "tools")
        run_async(run())

    def test_create_fdo_with_optional_fields(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_CREATE_RESULT) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post("/api/vault", json={
                        "id": "test-full-fdo",
                        "title": "Full FDO",
                        "domain": "physics",
                        "confidence": 0.7,
                        "body": "# Full\n\n## Summary\nFull FDO",
                        "status": "developing",
                        "tags": ["pac", "test"],
                        "related": ["start-here"],
                        "confidence_basis": "testing",
                    })
                self.assertEqual(resp.status_code, 200)
                call_args = mock.call_args[0][1]
                self.assertEqual(call_args["tags"], ["pac", "test"])
                self.assertEqual(call_args["related"], ["start-here"])
                self.assertEqual(call_args["status"], "developing")
        run_async(run())

    def test_create_missing_required_fields(self):
        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/vault", json={
                    "id": "test",
                    "title": "Missing fields",
                    # missing domain, confidence, body
                })
            self.assertEqual(resp.status_code, 422)
        run_async(run())

    def test_create_mcp_error(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value={"error": "ID already exists"}):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.post("/api/vault", json={
                        "id": "duplicate", "title": "Dup", "domain": "tools",
                        "confidence": 0.5, "body": "# Dup",
                    })
                self.assertEqual(resp.status_code, 500)
        run_async(run())


class TestVaultUpdate(unittest.TestCase):
    """PUT /api/vault/{fdo_id}"""

    def test_update_fdo(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_UPDATE_RESULT) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.put("/api/vault/pac-comprehensive", json={
                        "status": "validated",
                        "confidence": 0.95,
                    })
                self.assertEqual(resp.status_code, 200)
                call_args = mock.call_args[0]
                self.assertEqual(call_args[0], "kronos_update")
                self.assertEqual(call_args[1]["id"], "pac-comprehensive")
                self.assertEqual(call_args[1]["fields"]["status"], "validated")
                self.assertEqual(call_args[1]["fields"]["confidence"], 0.95)
        run_async(run())

    def test_update_empty_fields(self):
        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put("/api/vault/some-fdo", json={})
            self.assertEqual(resp.status_code, 400)
            self.assertIn("error", resp.json())
        run_async(run())

    def test_update_body_field(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_UPDATE_RESULT) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.put("/api/vault/pac-comprehensive", json={
                        "body": "# Updated\n\n## Summary\nNew content",
                    })
                self.assertEqual(resp.status_code, 200)
                call_args = mock.call_args[0][1]
                self.assertIn("body", call_args["fields"])
        run_async(run())

    def test_update_tags_and_related(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value=MOCK_UPDATE_RESULT) as mock:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.put("/api/vault/pac-comprehensive", json={
                        "tags": ["pac", "updated"],
                        "related": ["start-here", "grim-architecture"],
                    })
                self.assertEqual(resp.status_code, 200)
                fields = mock.call_args[0][1]["fields"]
                self.assertEqual(fields["tags"], ["pac", "updated"])
                self.assertEqual(fields["related"], ["start-here", "grim-architecture"])
        run_async(run())

    def test_update_mcp_error(self):
        async def run():
            with patch("server.app._mcp_task_call", new_callable=AsyncMock,
                       return_value={"error": "FDO not found"}):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.put("/api/vault/nonexistent", json={
                        "title": "Updated",
                    })
                self.assertEqual(resp.status_code, 500)
        run_async(run())


if __name__ == "__main__":
    unittest.main()
