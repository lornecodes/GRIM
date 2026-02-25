"""
Actualize agent test cases.

Each case: content + context → expected FDO fields.
Tests the quality of generated FDOs: titles, summaries,
tags, ID generation, acronym handling, proportionality.
"""

CASES = [
    # ============================================================
    # Case 1: Rich domain code — full featured FDO
    # ============================================================
    {
        "id": "actualize_field_engine",
        "description": "Complex simulation code — should produce rich, accurate FDO",
        "input": {
            "content": "",
            "_load_from": "mock_repo/src/field_engine.py",
            "meta": {
                "path": "src/field_engine.py",
                "source_type": "file",
                "language": "python",
                "size": 4500,
            },
            "source_id": "mock-repo",
            "domain": "physics",
            "vault_context": "No existing vault entries match this content.",
            "concepts": ["recursive balance field", "entropy", "PAC", "SEC"],
            "entities": ["FieldCenter", "RecursiveBalanceField"],
        },
        "expected": {
            "title_must_contain_one_of": ["field engine", "RBF", "recursive balance"],
            "summary_min_length": 80,
            "summary_max_length": 500,
            "tags_must_include": ["PAC", "SEC"],
            "tags_must_not_include": ["code", "file", "python"],  # Too generic
            "must_not_contain": [
                "Cardano Improvement Proposal",
                "C:\\Users",
                "C:/Users",
            ],
            "id_must_be": "mock-repo-src-field-engine",
            "domain_must_be": "physics",
        },
        "weight": 1.5,
    },

    # ============================================================
    # Case 2: Simple utils — proportionally brief FDO
    # ============================================================
    {
        "id": "actualize_utils",
        "description": "Generic utilities — FDO should be brief, not over-analyzed",
        "input": {
            "content": "",
            "_load_from": "mock_repo/src/utils.py",
            "meta": {
                "path": "src/utils.py",
                "source_type": "file",
                "language": "python",
                "size": 800,
            },
            "source_id": "mock-repo",
            "domain": "tools",
            "vault_context": "No existing vault entries match this content.",
            "concepts": ["utility", "hash", "JSON"],
            "entities": [],
        },
        "expected": {
            "summary_max_length": 300,  # Should be SHORT for simple code
            "details_max_length": 500,
            "tags_must_not_include": [
                "PAC", "SEC", "entropy", "quantum",  # Not in this file!
            ],
        },
        "weight": 1.0,
    },

    # ============================================================
    # Case 3: Acronym stress test — must not hallucinate expansions
    # ============================================================
    {
        "id": "actualize_acronyms",
        "description": "Acronym-heavy code — must handle CIP/MED/QPL correctly",
        "input": {
            "content": "",
            "_load_from": "mock_repo/src/metrics.py",
            "meta": {
                "path": "src/metrics.py",
                "source_type": "file",
                "language": "python",
                "size": 2000,
            },
            "source_id": "mock-repo",
            "domain": "tools",
            "vault_context": "No existing vault entries match this content.",
            "concepts": ["metrics", "SEC", "MED"],
            "entities": ["PAC", "QPL", "CIMM", "FDO", "CIP"],
        },
        "expected": {
            "must_not_contain": [
                "Cardano Improvement Proposal",
                "Content Identifier Protocol",
                "Customer Integration Platform",
                "Common Industrial Protocol",
            ],
            # If it expands CIP, it MUST be "Cognition Index Protocol"
            "if_expands_cip_must_be": "Cognition Index Protocol",
            "tags_must_include_some": ["metrics", "PAC", "SEC"],
        },
        "weight": 2.0,  # Highest weight — acronym hallucination is the #1 bug
    },

    # ============================================================
    # Case 4: YAML meta.yaml — unique ID (not just "mock-repo-meta")
    # ============================================================
    {
        "id": "actualize_nested_meta",
        "description": "Nested meta.yaml — ID must include parent dir to avoid collision",
        "input": {
            "content": "",
            "_load_from": "mock_repo/src/meta.yaml",
            "meta": {
                "path": "src/meta.yaml",
                "source_type": "file",
                "language": "yaml",
                "size": 250,
            },
            "source_id": "mock-repo",
            "domain": "tools",
            "vault_context": "No existing vault entries match this content.",
            "concepts": ["metadata", "schema"],
            "entities": [],
        },
        "expected": {
            # ID must include "src" to distinguish from root meta.yaml
            "id_must_contain": "src",
            "id_must_not_be": "mock-repo-meta",
        },
        "weight": 1.5,
    },

    # ============================================================
    # Case 5: Root meta.yaml — different ID from nested ones
    # ============================================================
    {
        "id": "actualize_root_meta",
        "description": "Root meta.yaml — gets its own unique ID",
        "input": {
            "content": "",
            "_load_from": "mock_repo/meta.yaml",
            "meta": {
                "path": "meta.yaml",
                "source_type": "file",
                "language": "yaml",
                "size": 200,
            },
            "source_id": "mock-repo",
            "domain": "tools",
            "vault_context": "No existing vault entries match this content.",
            "concepts": ["metadata", "schema", "repository"],
            "entities": [],
        },
        "expected": {
            "id_must_be": "mock-repo-meta",
        },
        "weight": 1.2,
    },

    # ============================================================
    # Case 6: Experiment markdown — research-quality FDO
    # ============================================================
    {
        "id": "actualize_experiment",
        "description": "Research experiment — should capture hypothesis and results",
        "input": {
            "content": "",
            "_load_from": "mock_repo/experiments/exp_01_fibonacci.md",
            "meta": {
                "path": "experiments/exp_01_fibonacci.md",
                "source_type": "file",
                "language": "markdown",
                "size": 1200,
            },
            "source_id": "mock-repo",
            "domain": "physics",
            "vault_context": "No existing vault entries match this content.",
            "concepts": ["fibonacci", "PAC", "golden ratio", "convergence"],
            "entities": [],
        },
        "expected": {
            "summary_must_mention": ["fibonacci", "golden ratio"],
            "must_not_contain": ["C:\\Users"],
            "tags_must_include_some": ["fibonacci", "PAC", "golden-ratio"],
        },
        "weight": 1.0,
    },

    # ============================================================
    # Case 7: Test file — should reference what it tests
    # ============================================================
    {
        "id": "actualize_tests",
        "description": "Test file — FDO should describe what's being tested",
        "input": {
            "content": "",
            "_load_from": "mock_repo/tests/test_field_engine.py",
            "meta": {
                "path": "tests/test_field_engine.py",
                "source_type": "file",
                "language": "python",
                "size": 2000,
            },
            "source_id": "mock-repo",
            "domain": "tools",
            "vault_context": "- **[[mock-repo-src-field-engine]]** (physics, seed) — RBF simulation engine",
            "concepts": ["field engine tests", "PAC conservation"],
            "entities": [],
        },
        "expected": {
            "related_must_include": ["mock-repo-src-field-engine"],
            "summary_must_mention": ["test"],
        },
        "weight": 1.0,
    },
]
