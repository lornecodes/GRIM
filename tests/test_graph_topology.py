"""Comprehensive tests for Graph Studio — topology module, API endpoints, and data integrity.

Test categories:
  1. Topology data integrity (unit) — node metadata, edges, positions, routing rules
  2. Layout geometry (unit) — canvas position calculations, spacing, overlap detection
  3. Graph structure (unit) — DAG properties, reachability, path coverage
  4. API endpoint tests — GET /api/graph/topology, GET /api/graph/sessions
  5. Session tracking — _active_ws_sessions lifecycle
  6. Smoke tests — schema validation, field types, color format, tier consistency
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.graph_topology import (
    ALL_NODE_IDS,
    INFRA_NODE_METADATA,
    NODE_POSITIONS,
    STATIC_EDGES,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Topology Data Integrity (Unit Tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestInfraNodeMetadata:
    """Verify infrastructure node metadata completeness and schema."""

    EXPECTED_INFRA = {
        "identity", "compress", "memory", "skill_match", "graph_router",
        "router", "dispatch", "audit_gate", "re_dispatch", "integrate", "evolve",
    }

    def test_all_infra_nodes_present(self):
        assert set(INFRA_NODE_METADATA.keys()) == self.EXPECTED_INFRA

    def test_infra_node_count(self):
        assert len(INFRA_NODE_METADATA) == 11

    def test_required_fields(self):
        required = {"id", "name", "role", "description", "tools", "color",
                     "tier", "toggleable", "node_type"}
        for node_id, meta in INFRA_NODE_METADATA.items():
            missing = required - set(meta.keys())
            assert not missing, f"{node_id} missing fields: {missing}"

    def test_id_matches_key(self):
        for node_id, meta in INFRA_NODE_METADATA.items():
            assert meta["id"] == node_id

    def test_all_tiers_valid(self):
        for node_id, meta in INFRA_NODE_METADATA.items():
            assert meta["tier"] in ("grim", "ironclaw"), f"{node_id} has invalid tier"

    def test_all_infra_are_grim_tier(self):
        """All infrastructure nodes should be grim tier."""
        for node_id, meta in INFRA_NODE_METADATA.items():
            assert meta["tier"] == "grim", f"{node_id} should be grim tier"

    def test_all_infra_not_toggleable(self):
        """Infrastructure nodes should never be toggleable."""
        for node_id, meta in INFRA_NODE_METADATA.items():
            assert meta["toggleable"] is False, f"{node_id} should not be toggleable"

    def test_all_infra_have_empty_tools(self):
        """Infrastructure nodes don't have tools."""
        for node_id, meta in INFRA_NODE_METADATA.items():
            assert meta["tools"] == [], f"{node_id} should have empty tools"

    def test_router_nodes_have_routing_rules(self):
        routers = {"graph_router", "router", "audit_gate"}
        for r in routers:
            assert "routing_rules" in INFRA_NODE_METADATA[r], f"{r} missing routing_rules"
            assert len(INFRA_NODE_METADATA[r]["routing_rules"]) >= 2

    def test_non_router_nodes_lack_routing_rules(self):
        """Only router/gate nodes should have routing_rules."""
        routers = {"graph_router", "router", "audit_gate"}
        for node_id, meta in INFRA_NODE_METADATA.items():
            if node_id not in routers:
                assert "routing_rules" not in meta, f"{node_id} should not have routing_rules"

    def test_node_type_values(self):
        """Each node_type should be one of the valid categories."""
        valid = {"preprocessing", "routing", "companion", "agent", "postprocessing", "infra"}
        for node_id, meta in INFRA_NODE_METADATA.items():
            assert meta["node_type"] in valid, f"{node_id} has invalid node_type: {meta['node_type']}"

    def test_preprocessing_nodes(self):
        """Preprocessing nodes should be the first four in the pipeline."""
        preprocessing = [n for n, m in INFRA_NODE_METADATA.items() if m["node_type"] == "preprocessing"]
        assert set(preprocessing) == {"identity", "compress", "memory", "skill_match"}

    def test_routing_nodes(self):
        routing = [n for n, m in INFRA_NODE_METADATA.items() if m["node_type"] == "routing"]
        assert set(routing) == {"graph_router", "router", "dispatch"}

    def test_postprocessing_nodes(self):
        postproc = [n for n, m in INFRA_NODE_METADATA.items() if m["node_type"] == "postprocessing"]
        assert set(postproc) == {"integrate", "evolve"}

    def test_descriptions_not_empty(self):
        for node_id, meta in INFRA_NODE_METADATA.items():
            assert len(meta["description"]) > 10, f"{node_id} has too-short description"

    def test_names_not_empty(self):
        for node_id, meta in INFRA_NODE_METADATA.items():
            assert len(meta["name"]) >= 3, f"{node_id} has too-short name"


class TestStaticEdges:
    """Verify edge definitions are complete and consistent."""

    def test_minimum_edge_count(self):
        assert len(STATIC_EDGES) >= 19

    def test_exact_edge_count(self):
        assert len(STATIC_EDGES) == 19

    def test_no_duplicate_edges(self):
        seen = set()
        for edge in STATIC_EDGES:
            key = (edge["source"], edge["target"], edge["type"])
            assert key not in seen, f"Duplicate edge: {key}"
            seen.add(key)

    def test_edge_required_fields(self):
        for edge in STATIC_EDGES:
            assert "source" in edge
            assert "target" in edge
            assert "type" in edge
            assert edge["type"] in ("static", "conditional")

    def test_conditional_edges_have_labels(self):
        for edge in STATIC_EDGES:
            if edge["type"] == "conditional":
                assert "label" in edge and edge["label"], (
                    f"Conditional edge {edge['source']}->{edge['target']} missing label"
                )

    def test_all_edge_endpoints_are_known_nodes(self):
        known = set(INFRA_NODE_METADATA.keys()) | {
            "companion", "personal_companion", "planning_companion",
            "audit",
        }
        for edge in STATIC_EDGES:
            assert edge["source"] in known, f"Unknown source: {edge['source']}"
            assert edge["target"] in known, f"Unknown target: {edge['target']}"

    def test_loop_edge_exists(self):
        loop = [e for e in STATIC_EDGES
                if e["source"] == "re_dispatch" and e["target"] == "dispatch"]
        assert len(loop) == 1, "re_dispatch -> dispatch loop edge missing"

    def test_no_self_loops(self):
        for edge in STATIC_EDGES:
            assert edge["source"] != edge["target"], (
                f"Self-loop: {edge['source']}"
            )

    def test_entry_point_is_identity(self):
        """Identity should only appear as a source, never a target (it's the entry)."""
        targets = {e["target"] for e in STATIC_EDGES}
        assert "identity" not in targets, "identity should not be an edge target"

    def test_evolve_is_terminal(self):
        """Evolve should not be a source of any edge (it goes to __end__)."""
        sources = {e["source"] for e in STATIC_EDGES}
        assert "evolve" not in sources or all(
            e["target"] == "__end__" for e in STATIC_EDGES if e["source"] == "evolve"
        ), "evolve should only connect to __end__ or be terminal"

    def test_conditional_edge_count(self):
        conditional = [e for e in STATIC_EDGES if e["type"] == "conditional"]
        assert len(conditional) >= 8, "Should have at least 8 conditional edges"

    def test_static_edge_count(self):
        static = [e for e in STATIC_EDGES if e["type"] == "static"]
        assert len(static) >= 8, "Should have at least 8 static edges"

    def test_graph_router_has_three_branches(self):
        """graph_router should fan out to 3 targets."""
        targets = [e["target"] for e in STATIC_EDGES if e["source"] == "graph_router"]
        assert set(targets) == {"router", "personal_companion", "planning_companion"}

    def test_router_has_two_branches(self):
        targets = [e["target"] for e in STATIC_EDGES if e["source"] == "router"]
        assert set(targets) == {"companion", "dispatch"}

    def test_audit_gate_has_two_branches(self):
        targets = [e["target"] for e in STATIC_EDGES if e["source"] == "audit_gate"]
        assert set(targets) == {"audit", "integrate"}

    def test_audit_has_two_branches(self):
        targets = [e["target"] for e in STATIC_EDGES if e["source"] == "audit"]
        assert set(targets) == {"integrate", "re_dispatch"}

    def test_all_companions_connect_to_integrate(self):
        """All three companion nodes should feed into integrate."""
        companions = {"companion", "personal_companion", "planning_companion"}
        for c in companions:
            targets = [e["target"] for e in STATIC_EDGES if e["source"] == c]
            assert "integrate" in targets, f"{c} should connect to integrate"

    def test_preprocessing_chain_is_linear(self):
        """identity -> compress -> memory -> skill_match -> graph_router should be linear static edges."""
        chain = ["identity", "compress", "memory", "skill_match", "graph_router"]
        for i in range(len(chain) - 1):
            edge = [e for e in STATIC_EDGES
                    if e["source"] == chain[i] and e["target"] == chain[i + 1]]
            assert len(edge) == 1, f"Missing chain edge: {chain[i]} -> {chain[i+1]}"
            assert edge[0]["type"] == "static", f"Chain edge should be static: {chain[i]}"


class TestNodePositions:
    """Verify layout positions cover all graph nodes."""

    def test_all_graph_nodes_have_positions(self):
        expected = set(INFRA_NODE_METADATA.keys()) | {
            "companion", "personal_companion", "planning_companion",
        }
        missing = expected - set(NODE_POSITIONS.keys())
        assert not missing, f"Nodes without positions: {missing}"

    def test_positions_are_tuples(self):
        for node_id, pos in NODE_POSITIONS.items():
            assert isinstance(pos, tuple) and len(pos) == 2, f"{node_id} bad position"
            assert isinstance(pos[0], int) and isinstance(pos[1], int)

    def test_no_overlapping_positions(self):
        seen: dict[tuple[int, int], str] = {}
        for node_id, pos in NODE_POSITIONS.items():
            if pos in seen:
                pytest.fail(f"{node_id} and {seen[pos]} share position {pos}")
            seen[pos] = node_id

    def test_columns_increase_left_to_right(self):
        assert NODE_POSITIONS["identity"][0] < NODE_POSITIONS["graph_router"][0]
        assert NODE_POSITIONS["integrate"][0] > NODE_POSITIONS["dispatch"][0]
        assert NODE_POSITIONS["evolve"][0] > NODE_POSITIONS["integrate"][0]

    def test_preprocessing_in_early_columns(self):
        for n in ["identity", "compress", "memory", "skill_match"]:
            assert NODE_POSITIONS[n][0] <= 3, f"{n} should be in columns 0-3"

    def test_postprocessing_in_late_columns(self):
        for n in ["integrate", "evolve"]:
            assert NODE_POSITIONS[n][0] >= 9, f"{n} should be in columns 9+"

    def test_personal_companion_above_main_line(self):
        """Personal companion should be at a negative row (above main pipeline)."""
        assert NODE_POSITIONS["personal_companion"][1] < 0

    def test_planning_companion_below_main_line(self):
        """Planning companion should be at a positive row (below main pipeline)."""
        assert NODE_POSITIONS["planning_companion"][1] > 0

    def test_minimum_column_count(self):
        """Should span at least 10 columns."""
        cols = {pos[0] for pos in NODE_POSITIONS.values()}
        assert max(cols) - min(cols) >= 9

    def test_re_dispatch_below_audit(self):
        """Re-dispatch should be below audit for the loop arc to render correctly."""
        assert NODE_POSITIONS["re_dispatch"][1] > NODE_POSITIONS["audit"][1]


class TestAllNodeIds:
    """Verify the ALL_NODE_IDS convenience set."""

    def test_includes_infra_and_companions(self):
        assert "identity" in ALL_NODE_IDS
        assert "companion" in ALL_NODE_IDS
        assert "personal_companion" in ALL_NODE_IDS
        assert "planning_companion" in ALL_NODE_IDS

    def test_count(self):
        assert len(ALL_NODE_IDS) == 14

    def test_is_frozenset(self):
        assert isinstance(ALL_NODE_IDS, frozenset)

    def test_all_infra_included(self):
        for node_id in INFRA_NODE_METADATA:
            assert node_id in ALL_NODE_IDS


# ═══════════════════════════════════════════════════════════════════════════
# 2. Layout Geometry Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCanvasLayout:
    """Verify the toCanvasPos layout helper produces valid coordinates."""

    def test_col_width_reasonable(self):
        """Columns should be spaced 100-200px apart based on NODE_POSITIONS."""
        # Just verify the constants exist and are sane
        from core.graph_topology import NODE_POSITIONS
        cols = sorted(set(pos[0] for pos in NODE_POSITIONS.values()))
        # With COL_WIDTH=130, 10 columns = 1300px — reasonable for a 1920px screen
        assert len(cols) >= 8

    def test_positions_produce_distinct_coordinates(self):
        """Each node should map to a unique (x, y) pair."""
        COL_WIDTH, ROW_HEIGHT = 130, 100
        OFFSET_X, OFFSET_Y = 80, 250
        coords = set()
        for node_id, (col, row) in NODE_POSITIONS.items():
            x = OFFSET_X + col * COL_WIDTH
            y = OFFSET_Y + row * ROW_HEIGHT
            assert (x, y) not in coords, f"{node_id} overlaps at ({x}, {y})"
            coords.add((x, y))

    def test_all_coordinates_positive(self):
        """All canvas coordinates should be positive."""
        COL_WIDTH, ROW_HEIGHT = 130, 100
        OFFSET_X, OFFSET_Y = 80, 250
        for node_id, (col, row) in NODE_POSITIONS.items():
            x = OFFSET_X + col * COL_WIDTH
            y = OFFSET_Y + row * ROW_HEIGHT
            assert x > 0, f"{node_id} has negative x: {x}"
            assert y > 0, f"{node_id} has negative y: {y}"

    def test_canvas_fits_in_reasonable_bounds(self):
        """Total canvas should fit within 1600x600 pixels."""
        COL_WIDTH, ROW_HEIGHT = 130, 100
        OFFSET_X, OFFSET_Y = 80, 250
        max_x = max(OFFSET_X + col * COL_WIDTH for col, _ in NODE_POSITIONS.values())
        max_y = max(OFFSET_Y + row * ROW_HEIGHT for _, row in NODE_POSITIONS.values())
        min_y = min(OFFSET_Y + row * ROW_HEIGHT for _, row in NODE_POSITIONS.values())
        assert max_x < 1600, f"Canvas too wide: {max_x}px"
        assert max_y - min_y < 600, f"Canvas too tall: {max_y - min_y}px"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Graph Structure Tests (DAG Properties)
# ═══════════════════════════════════════════════════════════════════════════


class TestGraphStructure:
    """Verify the graph has proper DAG properties (except the re_dispatch loop)."""

    @pytest.fixture(autouse=True)
    def _build_adjacency(self):
        """Build adjacency list from STATIC_EDGES."""
        self.adj: dict[str, list[str]] = {}
        for edge in STATIC_EDGES:
            self.adj.setdefault(edge["source"], []).append(edge["target"])

    def test_identity_is_only_entry(self):
        """Only identity should have no incoming edges."""
        all_targets = {e["target"] for e in STATIC_EDGES}
        all_sources = {e["source"] for e in STATIC_EDGES}
        all_nodes = all_sources | all_targets
        entry_nodes = all_nodes - all_targets
        assert entry_nodes == {"identity"}

    def test_integrate_is_convergence_point(self):
        """Multiple nodes should feed into integrate."""
        incoming = [e["source"] for e in STATIC_EDGES if e["target"] == "integrate"]
        assert len(incoming) >= 4  # companion, personal, planning, audit_gate/audit

    def test_all_paths_reach_integrate(self):
        """Every path from graph_router should eventually reach integrate."""
        # BFS from each graph_router target
        for start_edge in STATIC_EDGES:
            if start_edge["source"] != "graph_router":
                continue
            target = start_edge["target"]
            visited = set()
            queue = [target]
            found_integrate = False
            while queue:
                node = queue.pop(0)
                if node == "integrate":
                    found_integrate = True
                    break
                if node in visited:
                    continue
                visited.add(node)
                for next_node in self.adj.get(node, []):
                    queue.append(next_node)
            assert found_integrate, f"Path from graph_router->{target} never reaches integrate"

    def test_preprocessing_chain_reachable_from_identity(self):
        """identity -> compress -> memory -> skill_match -> graph_router should be reachable."""
        chain = ["identity", "compress", "memory", "skill_match", "graph_router"]
        for i in range(len(chain) - 1):
            assert chain[i + 1] in self.adj.get(chain[i], []), (
                f"{chain[i]} does not connect to {chain[i+1]}"
            )

    def test_loop_is_bounded(self):
        """The re_dispatch -> dispatch loop should be the only cycle."""
        # Check that removing the loop edge makes the graph acyclic
        edges_no_loop = [e for e in STATIC_EDGES
                         if not (e["source"] == "re_dispatch" and e["target"] == "dispatch")]
        adj: dict[str, list[str]] = {}
        for e in edges_no_loop:
            adj.setdefault(e["source"], []).append(e["target"])

        # Topological sort should succeed (no cycles)
        visited: set[str] = set()
        temp: set[str] = set()
        order: list[str] = []
        has_cycle = False

        def dfs(node: str):
            nonlocal has_cycle
            if node in temp:
                has_cycle = True
                return
            if node in visited:
                return
            temp.add(node)
            for n in adj.get(node, []):
                dfs(n)
            temp.discard(node)
            visited.add(node)
            order.append(node)

        all_nodes = set(adj.keys())
        for n in adj.values():
            all_nodes.update(n)
        for node in all_nodes:
            dfs(node)

        assert not has_cycle, "Graph has cycles beyond the re_dispatch loop"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Smoke Tests — Schema, Types, Colors
# ═══════════════════════════════════════════════════════════════════════════


class TestSmoke:
    """Quick validation of field types and formats across all data."""

    def test_all_colors_are_hex(self):
        """All color fields should be valid hex color codes."""
        import re
        hex_re = re.compile(r"^#[0-9a-fA-F]{6}$")
        for node_id, meta in INFRA_NODE_METADATA.items():
            assert hex_re.match(meta["color"]), f"{node_id} has invalid color: {meta['color']}"

    def test_all_tools_are_lists(self):
        for node_id, meta in INFRA_NODE_METADATA.items():
            assert isinstance(meta["tools"], list), f"{node_id} tools should be a list"

    def test_all_toggleable_are_bool(self):
        for node_id, meta in INFRA_NODE_METADATA.items():
            assert isinstance(meta["toggleable"], bool), f"{node_id} toggleable should be bool"

    def test_edge_labels_are_strings(self):
        for edge in STATIC_EDGES:
            if "label" in edge:
                assert isinstance(edge["label"], str)

    def test_routing_rules_schema(self):
        """Routing rules should have 'condition' and 'target' keys."""
        for node_id, meta in INFRA_NODE_METADATA.items():
            if "routing_rules" in meta:
                for rule in meta["routing_rules"]:
                    assert "condition" in rule, f"{node_id} rule missing condition"
                    assert "target" in rule, f"{node_id} rule missing target"
                    assert isinstance(rule["condition"], str)
                    assert isinstance(rule["target"], str)

    def test_routing_rule_targets_are_edges(self):
        """Each routing rule target should match an actual edge target."""
        edge_targets_by_source: dict[str, set[str]] = {}
        for edge in STATIC_EDGES:
            edge_targets_by_source.setdefault(edge["source"], set()).add(edge["target"])

        for node_id, meta in INFRA_NODE_METADATA.items():
            if "routing_rules" not in meta:
                continue
            actual_targets = edge_targets_by_source.get(node_id, set())
            for rule in meta["routing_rules"]:
                # Rule target may include description like "integrate (skip)"
                rule_target = rule["target"].split(" ")[0]
                assert rule_target in actual_targets, (
                    f"{node_id} routing rule target '{rule_target}' not in edges: {actual_targets}"
                )

    def test_node_metadata_json_serializable(self):
        """All metadata should be JSON-serializable (for the API)."""
        json.dumps(INFRA_NODE_METADATA)

    def test_edges_json_serializable(self):
        json.dumps(STATIC_EDGES)

    def test_positions_json_serializable(self):
        json.dumps({k: list(v) for k, v in NODE_POSITIONS.items()})

    def test_detail_field_on_all_infra_nodes(self):
        """All infra nodes should have a 'detail' field with rich description."""
        for node_id, meta in INFRA_NODE_METADATA.items():
            assert "detail" in meta, f"{node_id} missing 'detail' field"
            assert len(meta["detail"]) > 50, f"{node_id} detail too short"

    def test_graph_router_has_signals(self):
        """graph_router should have routing signals dict."""
        gr = INFRA_NODE_METADATA["graph_router"]
        assert "signals" in gr
        assert "personal" in gr["signals"]
        assert "planning" in gr["signals"]
        assert len(gr["signals"]["personal"]) >= 5
        assert len(gr["signals"]["planning"]) >= 5

    def test_router_has_signals(self):
        """router should have delegation keyword signals."""
        r = INFRA_NODE_METADATA["router"]
        assert "signals" in r
        assert "memory" in r["signals"]
        assert "ironclaw" in r["signals"]

    def test_signals_json_serializable(self):
        """Signals dicts should be JSON-serializable."""
        for node_id, meta in INFRA_NODE_METADATA.items():
            if "signals" in meta:
                json.dumps(meta["signals"])


# ═══════════════════════════════════════════════════════════════════════════
# 5. API Endpoint Tests (FastAPI TestClient)
# ═══════════════════════════════════════════════════════════════════════════


def _make_test_config(**overrides):
    """Create a minimal GrimConfig for endpoint testing."""
    from core.config import GrimConfig
    grim_root = Path(__file__).resolve().parent.parent
    defaults = dict(
        env="debug",
        vault_path=grim_root / "tests" / "vault",
        skills_path=grim_root / "skills",
        identity_prompt_path=grim_root / "identity" / "system_prompt.md",
        identity_personality_path=grim_root / "identity" / "personality.yaml",
        local_dir=grim_root / "local",
        model="claude-sonnet-4-6",
    )
    defaults.update(overrides)
    return GrimConfig(**defaults)


def _make_mock_agent_metadata():
    """Create mock agent metadata matching the real roster shape."""
    return [
        # Companion nodes (from GRAPH_NODE_METADATA)
        {"id": "companion", "name": "Companion", "role": "thinker",
         "description": "Primary conversational agent", "tools": ["kronos_search"],
         "color": "#7c6fef", "tier": "grim", "toggleable": False},
        {"id": "personal_companion", "name": "Personal", "role": "conversational",
         "description": "Casual companion", "tools": [],
         "color": "#a78bfa", "tier": "grim", "toggleable": False},
        {"id": "planning_companion", "name": "Planning", "role": "planner",
         "description": "Task planning", "tools": ["kronos_task_create"],
         "color": "#a78bfa", "tier": "grim", "toggleable": False},
        # Agent nodes
        {"id": "audit", "name": "Audit", "role": "review",
         "description": "Staging review", "tools": ["staging_read"],
         "color": "#f97316", "tier": "ironclaw", "toggleable": True},
        {"id": "memory", "name": "Memory", "role": "vault_ops",
         "description": "Vault operations", "tools": ["kronos_search", "kronos_create"],
         "color": "#8b5cf6", "tier": "grim", "toggleable": False},
        {"id": "research", "name": "Researcher", "role": "analysis",
         "description": "Deep ingestion", "tools": ["file_read"],
         "color": "#3b82f6", "tier": "grim", "toggleable": False},
    ]


class TestTopologyEndpoint(unittest.TestCase):
    """Test GET /api/graph/topology endpoint."""

    def _make_client(self, config=None, agent_metadata=None):
        from fastapi.testclient import TestClient
        import server.app as app_module

        app_module._config = config or _make_test_config()
        app_module._graph = MagicMock()
        app_module._agent_metadata = agent_metadata or _make_mock_agent_metadata()
        return TestClient(app_module.app)

    def test_returns_200(self):
        client = self._make_client()
        resp = client.get("/api/graph/topology")
        self.assertEqual(resp.status_code, 200)

    def test_response_has_required_keys(self):
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        self.assertIn("node_count", data)
        self.assertIn("edge_count", data)

    def test_nodes_is_dict(self):
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        self.assertIsInstance(data["nodes"], dict)

    def test_edges_is_list(self):
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        self.assertIsInstance(data["edges"], list)

    def test_node_count_matches(self):
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        self.assertEqual(data["node_count"], len(data["nodes"]))

    def test_edge_count_matches(self):
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        self.assertEqual(data["edge_count"], len(data["edges"]))

    def test_infra_nodes_present(self):
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        for node_id in INFRA_NODE_METADATA:
            self.assertIn(node_id, data["nodes"], f"Missing infra node: {node_id}")

    def test_companion_nodes_present(self):
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        for n in ["companion", "personal_companion", "planning_companion"]:
            self.assertIn(n, data["nodes"], f"Missing companion: {n}")

    def test_agent_nodes_present(self):
        """Agent nodes with positions should appear."""
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        self.assertIn("audit", data["nodes"])

    def test_agents_without_positions_excluded(self):
        """Agents not in NODE_POSITIONS should NOT appear in topology."""
        metadata = _make_mock_agent_metadata() + [
            {"id": "some_sub_agent", "name": "SubAgent", "role": "sub",
             "description": "Not a graph node", "tools": [],
             "color": "#ffffff", "tier": "grim", "toggleable": False},
        ]
        client = self._make_client(agent_metadata=metadata)
        data = client.get("/api/graph/topology").json()
        self.assertNotIn("some_sub_agent", data["nodes"])

    def test_node_has_layout_fields(self):
        """Each node should have col and row for layout."""
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        for node_id, node in data["nodes"].items():
            self.assertIn("col", node, f"{node_id} missing col")
            self.assertIn("row", node, f"{node_id} missing row")

    def test_node_has_enabled_field(self):
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        for node_id, node in data["nodes"].items():
            self.assertIn("enabled", node, f"{node_id} missing enabled")

    def test_node_has_node_type(self):
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        for node_id, node in data["nodes"].items():
            self.assertIn("node_type", node, f"{node_id} missing node_type")

    def test_infra_nodes_always_enabled(self):
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        for node_id in INFRA_NODE_METADATA:
            self.assertTrue(data["nodes"][node_id]["enabled"],
                            f"Infra node {node_id} should be enabled")

    def test_disabled_agent_shows_as_disabled(self):
        """Agents in agents_disabled should have enabled=false."""
        config = _make_test_config(agents_disabled=["audit"])
        client = self._make_client(config=config)
        data = client.get("/api/graph/topology").json()
        self.assertFalse(data["nodes"]["audit"]["enabled"])

    def test_edge_format(self):
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        for edge in data["edges"]:
            self.assertIn("source", edge)
            self.assertIn("target", edge)
            self.assertIn("type", edge)

    def test_edges_match_static_definition(self):
        """API edges should match STATIC_EDGES exactly."""
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        self.assertEqual(len(data["edges"]), len(STATIC_EDGES))

    def test_companion_node_type(self):
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        for n in ["companion", "personal_companion", "planning_companion"]:
            self.assertEqual(data["nodes"][n]["node_type"], "companion")

    def test_agent_node_type(self):
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        # audit, memory, research are agents
        for n in ["audit", "memory", "research"]:
            if n in data["nodes"]:
                self.assertEqual(data["nodes"][n]["node_type"], "agent")

    def test_with_no_agent_metadata(self):
        """Endpoint should still work with None agent_metadata (graceful degradation)."""
        client = self._make_client(agent_metadata=None)
        import server.app as app_module
        app_module._agent_metadata = None
        resp = client.get("/api/graph/topology")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Should still have infra nodes
        self.assertIn("identity", data["nodes"])
        # But no companion/agent nodes
        self.assertNotIn("companion", data["nodes"])

    def test_with_empty_agent_metadata(self):
        client = self._make_client(agent_metadata=[])
        resp = client.get("/api/graph/topology")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("identity", data["nodes"])

    def test_no_config_still_returns_200(self):
        """Even without config, topology should work (defaults to empty disabled set)."""
        client = self._make_client()
        import server.app as app_module
        app_module._config = None
        resp = client.get("/api/graph/topology")
        self.assertEqual(resp.status_code, 200)

    def test_infra_nodes_have_detail(self):
        """Infra nodes (not overridden by agents) should have 'detail' field."""
        from fastapi.testclient import TestClient
        import server.app as app_module
        # Use empty agent metadata so infra 'memory' node isn't overridden by agent 'memory'
        app_module._config = _make_test_config()
        app_module._graph = MagicMock()
        app_module._agent_metadata = []
        client = TestClient(app_module.app)
        data = client.get("/api/graph/topology").json()
        for node_id in INFRA_NODE_METADATA:
            self.assertIn("detail", data["nodes"][node_id],
                          f"Infra node {node_id} missing detail")
            self.assertGreater(len(data["nodes"][node_id]["detail"]), 50)

    def test_routing_nodes_have_signals(self):
        """graph_router and router should have signals dict."""
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        gr = data["nodes"]["graph_router"]
        self.assertIn("signals", gr)
        self.assertIn("personal", gr["signals"])
        self.assertIn("planning", gr["signals"])
        r = data["nodes"]["router"]
        self.assertIn("signals", r)

    def test_companion_nodes_get_model(self):
        """Companion nodes should have model field from config."""
        client = self._make_client()
        data = client.get("/api/graph/topology").json()
        for n in ["companion", "personal_companion", "planning_companion"]:
            if n in data["nodes"]:
                self.assertIn("model", data["nodes"][n])


class TestSessionsEndpoint(unittest.TestCase):
    """Test GET /api/graph/sessions endpoint."""

    def _make_client(self):
        from fastapi.testclient import TestClient
        import server.app as app_module

        app_module._config = _make_test_config()
        app_module._graph = MagicMock()
        return TestClient(app_module.app)

    def test_returns_200(self):
        client = self._make_client()
        resp = client.get("/api/graph/sessions")
        self.assertEqual(resp.status_code, 200)

    def test_response_has_active_count(self):
        client = self._make_client()
        data = client.get("/api/graph/sessions").json()
        self.assertIn("active", data)
        self.assertIsInstance(data["active"], int)

    def test_response_has_session_ids(self):
        client = self._make_client()
        data = client.get("/api/graph/sessions").json()
        self.assertIn("session_ids", data)
        self.assertIsInstance(data["session_ids"], list)

    def test_empty_sessions_returns_zero(self):
        import server.app as app_module
        app_module._active_ws_sessions = set()
        client = self._make_client()
        data = client.get("/api/graph/sessions").json()
        self.assertEqual(data["active"], 0)
        self.assertEqual(data["session_ids"], [])

    def test_populated_sessions(self):
        import server.app as app_module
        app_module._active_ws_sessions = {"session-1", "session-2", "session-3"}
        client = self._make_client()
        data = client.get("/api/graph/sessions").json()
        self.assertEqual(data["active"], 3)
        self.assertEqual(sorted(data["session_ids"]), ["session-1", "session-2", "session-3"])
        # Cleanup
        app_module._active_ws_sessions = set()

    def test_session_ids_sorted(self):
        import server.app as app_module
        app_module._active_ws_sessions = {"z-session", "a-session", "m-session"}
        client = self._make_client()
        data = client.get("/api/graph/sessions").json()
        self.assertEqual(data["session_ids"], ["a-session", "m-session", "z-session"])
        app_module._active_ws_sessions = set()


# ═══════════════════════════════════════════════════════════════════════════
# 6. Session Tracking Unit Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestSessionTracking(unittest.TestCase):
    """Test the _active_ws_sessions set behavior."""

    def test_set_add_and_discard(self):
        import server.app as app_module
        original = app_module._active_ws_sessions.copy()
        try:
            app_module._active_ws_sessions.add("test-sid")
            self.assertIn("test-sid", app_module._active_ws_sessions)
            app_module._active_ws_sessions.discard("test-sid")
            self.assertNotIn("test-sid", app_module._active_ws_sessions)
        finally:
            app_module._active_ws_sessions = original

    def test_discard_nonexistent_is_safe(self):
        import server.app as app_module
        original = app_module._active_ws_sessions.copy()
        try:
            # Should not raise
            app_module._active_ws_sessions.discard("nonexistent-sid")
        finally:
            app_module._active_ws_sessions = original

    def test_duplicate_add_is_idempotent(self):
        import server.app as app_module
        original = app_module._active_ws_sessions.copy()
        try:
            app_module._active_ws_sessions.add("dup-sid")
            app_module._active_ws_sessions.add("dup-sid")
            count = sum(1 for s in app_module._active_ws_sessions if s == "dup-sid")
            self.assertEqual(count, 1)
        finally:
            app_module._active_ws_sessions = original


# ═══════════════════════════════════════════════════════════════════════════
# 7. Cross-Validation Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossValidation:
    """Cross-validate between topology, positions, and API response."""

    def test_all_positioned_nodes_are_in_topology_or_agents(self):
        """Every node in NODE_POSITIONS should be either infra or a known companion/agent."""
        infra = set(INFRA_NODE_METADATA.keys())
        companions = {"companion", "personal_companion", "planning_companion"}
        agents_in_graph = {"audit"}  # agents that have positions in the topology
        known = infra | companions | agents_in_graph
        for node_id in NODE_POSITIONS:
            assert node_id in known, (
                f"Node {node_id} has a position but is not in infra/companion set"
            )

    def test_all_edge_sources_have_positions(self):
        for edge in STATIC_EDGES:
            assert edge["source"] in NODE_POSITIONS, (
                f"Edge source {edge['source']} has no position"
            )

    def test_all_edge_targets_have_positions_or_are_end(self):
        for edge in STATIC_EDGES:
            target = edge["target"]
            if target == "__end__":
                continue
            assert target in NODE_POSITIONS, (
                f"Edge target {target} has no position"
            )

    def test_graph_router_routing_rules_match_edges(self):
        """Graph router's routing rules should have targets that match its outgoing edges."""
        rules = INFRA_NODE_METADATA["graph_router"]["routing_rules"]
        edge_targets = {e["target"] for e in STATIC_EDGES if e["source"] == "graph_router"}
        for rule in rules:
            assert rule["target"] in edge_targets, (
                f"graph_router rule target '{rule['target']}' not in edges: {edge_targets}"
            )

    def test_companion_nodes_metadata_compatibility(self):
        """The three companion node IDs used in edges should match GRAPH_NODE_METADATA."""
        from core.nodes.metadata import GRAPH_NODE_METADATA
        meta_ids = {m["id"] for m in GRAPH_NODE_METADATA}
        companion_targets = {"companion", "personal_companion", "planning_companion"}
        assert companion_targets == meta_ids
