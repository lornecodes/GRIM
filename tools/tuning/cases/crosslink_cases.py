"""
Crosslink agent test cases.

Each case: FDO draft + vault matches → expected cross-link decisions.
Tests whether the agent correctly identifies which existing FDOs
should receive backlinks and which should be listed as related.
"""

CASES = [
    # ============================================================
    # Case 1: Clear related FDO — should create backlink
    # ============================================================
    {
        "id": "crosslink_clear_match",
        "description": "Test file clearly related to the module it tests",
        "input": {
            "fdo_draft": {
                "id": "mock-repo-tests-field-engine",
                "title": "Field Engine Test Suite",
                "summary": "Tests for PAC conservation, SEC collapse, and RBF convergence.",
                "tags": ["testing", "PAC", "SEC", "RBF"],
                "related": ["mock-repo-src-field-engine"],
            },
            "vault_matches": [
                {
                    "id": "mock-repo-src-field-engine",
                    "title": "Recursive Balance Field Engine",
                    "domain": "physics",
                    "summary": "Core simulation engine implementing RBF dynamics.",
                    "match_score": 2.1,
                    "tags": ["RBF", "PAC", "SEC"],
                    "path": "repos/mock-repo/src-field-engine.md",
                },
                {
                    "id": "mock-repo-src-utils",
                    "title": "Utility Functions",
                    "domain": "tools",
                    "summary": "Generic helper functions.",
                    "match_score": 0.3,
                    "tags": ["utility"],
                    "path": "repos/mock-repo/src-utils.md",
                },
            ],
        },
        "expected": {
            "must_link_to": ["mock-repo-src-field-engine"],
            "must_not_link_to": ["mock-repo-src-utils"],
            "min_links": 1,
            "max_links": 2,
        },
        "weight": 1.0,
    },

    # ============================================================
    # Case 2: Metrics related to engine — should link
    # ============================================================
    {
        "id": "crosslink_metrics_to_engine",
        "description": "Metrics collector related to field engine",
        "input": {
            "fdo_draft": {
                "id": "mock-repo-src-metrics",
                "title": "PAC Metrics Collector",
                "summary": "Collects PAC conservation and SEC phase metrics from field snapshots.",
                "tags": ["metrics", "PAC", "SEC", "MED"],
                "related": [],
            },
            "vault_matches": [
                {
                    "id": "mock-repo-src-field-engine",
                    "title": "Recursive Balance Field Engine",
                    "domain": "physics",
                    "summary": "Core simulation engine.",
                    "match_score": 1.8,
                    "tags": ["RBF", "PAC", "SEC"],
                    "path": "repos/mock-repo/src-field-engine.md",
                },
                {
                    "id": "mock-repo-docs-architecture",
                    "title": "Architecture Overview",
                    "domain": "tools",
                    "summary": "Three-layer architecture with RBF engine.",
                    "match_score": 1.2,
                    "tags": ["architecture", "RBF"],
                    "path": "repos/mock-repo/docs-architecture.md",
                },
            ],
        },
        "expected": {
            "must_link_to": ["mock-repo-src-field-engine"],
            # Architecture is related but not directly — link is optional
            "min_links": 1,
            "max_links": 3,
        },
        "weight": 1.0,
    },

    # ============================================================
    # Case 3: Unrelated FDOs — should NOT link
    # ============================================================
    {
        "id": "crosslink_no_false_positives",
        "description": "FDO with no meaningful relationship to vault matches",
        "input": {
            "fdo_draft": {
                "id": "mock-repo-package-json",
                "title": "Package Configuration",
                "summary": "NPM package manifest with project metadata.",
                "tags": ["package-management", "configuration"],
                "related": [],
            },
            "vault_matches": [
                {
                    "id": "mock-repo-src-field-engine",
                    "title": "Recursive Balance Field Engine",
                    "domain": "physics",
                    "summary": "Core simulation engine.",
                    "match_score": 0.3,
                    "tags": ["RBF", "PAC"],
                    "path": "repos/mock-repo/src-field-engine.md",
                },
            ],
        },
        "expected": {
            "must_not_link_to": ["mock-repo-src-field-engine"],
            "max_links": 0,
        },
        "weight": 1.2,
    },

    # ============================================================
    # Case 4: Multiple valid links
    # ============================================================
    {
        "id": "crosslink_multiple",
        "description": "Architecture doc references multiple modules",
        "input": {
            "fdo_draft": {
                "id": "mock-repo-docs-architecture",
                "title": "Architecture Overview",
                "summary": "Three-layer architecture: RBF engine, API gateway, JSON storage. Uses PAC conservation and CIP metadata.",
                "tags": ["architecture", "RBF", "PAC", "CIP"],
                "related": ["mock-repo-src-field-engine"],
            },
            "vault_matches": [
                {
                    "id": "mock-repo-src-field-engine",
                    "title": "Recursive Balance Field Engine",
                    "domain": "physics",
                    "summary": "Core simulation engine.",
                    "match_score": 2.0,
                    "tags": ["RBF", "PAC"],
                    "path": "repos/mock-repo/src-field-engine.md",
                },
                {
                    "id": "mock-repo-src-metrics",
                    "title": "PAC Metrics Collector",
                    "domain": "tools",
                    "summary": "SEC phase and MED depth metrics.",
                    "match_score": 1.5,
                    "tags": ["metrics", "PAC", "SEC"],
                    "path": "repos/mock-repo/src-metrics.md",
                },
                {
                    "id": "mock-repo-config-settings",
                    "title": "Simulation Configuration",
                    "domain": "tools",
                    "summary": "YAML config with field parameters.",
                    "match_score": 0.8,
                    "tags": ["configuration"],
                    "path": "repos/mock-repo/config-settings.md",
                },
            ],
        },
        "expected": {
            "must_link_to": ["mock-repo-src-field-engine"],
            "min_links": 1,
            "max_links": 3,
        },
        "weight": 1.0,
    },
]
