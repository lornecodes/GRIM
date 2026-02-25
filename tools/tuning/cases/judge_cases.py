"""
Judge agent test cases.

Each case: content + vault matches → expected decision.
The judge decides: new, duplicate, extend, or skip.
"""

CASES = [
    # ============================================================
    # Case 1: Boilerplate LICENSE — should skip
    # ============================================================
    {
        "id": "judge_license_skip",
        "description": "Standard MIT License should be skipped",
        "input": {
            "content": "MIT License\n\nCopyright (c) 2026 Test Author\n\nPermission is hereby granted...",
            "meta": {"path": "LICENSE", "language": "text", "size": 1070},
            "vault_matches": [],
            "concepts": ["license", "MIT"],
            "entities": [],
        },
        "expected": {
            "decision": "skip",
            "reason_must_mention": ["boilerplate", "license"],
        },
        "weight": 1.0,
    },

    # ============================================================
    # Case 2: .gitignore — auto-skip
    # ============================================================
    {
        "id": "judge_gitignore_auto",
        "description": ".gitignore should be auto-skipped (no Claude call)",
        "input": {
            "content": "__pycache__/\n*.pyc\n.env\n",
            "meta": {"path": ".gitignore", "language": "text", "size": 50},
            "vault_matches": [],
            "concepts": [],
            "entities": [],
        },
        "expected": {
            "decision": "skip",
            "should_use_api": False,  # Should be heuristic, no Claude call
        },
        "weight": 0.8,
    },

    # ============================================================
    # Case 3: Empty file — skip
    # ============================================================
    {
        "id": "judge_empty_skip",
        "description": "Empty file should be skipped without API call",
        "input": {
            "content": "",
            "meta": {"path": "tests/__init__.py", "language": "python", "size": 0},
            "vault_matches": [],
            "concepts": [],
            "entities": [],
        },
        "expected": {
            "decision": "skip",
            "should_use_api": False,
        },
        "weight": 0.6,
    },

    # ============================================================
    # Case 4: Rich novel code — should be "new"
    # ============================================================
    {
        "id": "judge_new_code",
        "description": "Novel domain code with no vault matches — create new FDO",
        "input": {
            "content": "",
            "_load_from": "mock_repo/src/field_engine.py",
            "meta": {"path": "src/field_engine.py", "language": "python", "size": 4500},
            "vault_matches": [],  # Nothing in vault yet
            "concepts": ["recursive balance field", "entropy", "PAC", "SEC"],
            "entities": ["FieldCenter", "RecursiveBalanceField"],
        },
        "expected": {
            "decision": "new",
        },
        "weight": 1.0,
    },

    # ============================================================
    # Case 5: High-scoring vault match — should be "duplicate"
    # ============================================================
    {
        "id": "judge_duplicate",
        "description": "Nearly identical content already in vault — duplicate",
        "input": {
            "content": "",
            "_load_from": "mock_repo/src/field_engine.py",
            "meta": {"path": "src/field_engine.py", "language": "python", "size": 4500},
            "vault_matches": [
                {
                    "id": "other-repo-field-engine",
                    "title": "Recursive Balance Field Engine",
                    "domain": "physics",
                    "summary": "RBF simulation engine implementing SEC dynamics with PAC conservation...",
                    "match_score": 3.2,
                    "tags": ["RBF", "SEC", "PAC", "entropy"],
                }
            ],
            "concepts": ["recursive balance field", "entropy", "PAC"],
            "entities": ["FieldCenter"],
        },
        "expected": {
            "decision": "duplicate",
            "duplicate_of": "other-repo-field-engine",
        },
        "weight": 1.2,
    },

    # ============================================================
    # Case 6: Related but different — should be "new" not "duplicate"
    # ============================================================
    {
        "id": "judge_related_not_duplicate",
        "description": "Same domain but different content — should be new, not duplicate",
        "input": {
            "content": "",
            "_load_from": "mock_repo/src/metrics.py",
            "meta": {"path": "src/metrics.py", "language": "python", "size": 2000},
            "vault_matches": [
                {
                    "id": "other-repo-field-engine",
                    "title": "Recursive Balance Field Engine",
                    "domain": "physics",
                    "summary": "RBF simulation engine...",
                    "match_score": 1.4,
                    "tags": ["RBF", "SEC"],
                }
            ],
            "concepts": ["metrics", "SEC phase", "MED depth"],
            "entities": ["PACMetricsCollector"],
        },
        "expected": {
            "decision": "new",
            # NOT duplicate — same domain but different functionality
        },
        "weight": 1.5,  # High weight — this is a subtle distinction
    },

    # ============================================================
    # Case 7: Similar content, new details — should be "extend"
    # ============================================================
    {
        "id": "judge_extend",
        "description": "Adds new info to existing FDO — extend",
        "input": {
            "content": "",
            "_load_from": "mock_repo/docs/architecture.md",
            "meta": {"path": "docs/architecture.md", "language": "markdown", "size": 1500},
            "vault_matches": [
                {
                    "id": "mock-repo-field-engine",
                    "title": "RBF Simulation Engine",
                    "domain": "tools",
                    "summary": "Core field engine implementing RBF...",
                    "match_score": 1.8,
                    "tags": ["RBF", "PAC", "field-engine"],
                }
            ],
            "concepts": ["architecture", "RBF", "PAC", "FastAPI"],
            "entities": ["CIP"],
        },
        "expected": {
            # Either "new" or "extend" is acceptable here — it's architecture docs
            # that reference the field engine but add architectural context
            "decision_one_of": ["new", "extend"],
        },
        "weight": 1.0,
    },

    # ============================================================
    # Case 8: package.json — borderline skip
    # ============================================================
    {
        "id": "judge_package_json",
        "description": "package.json with minimal info — should skip or create minimal",
        "input": {
            "content": "",
            "_load_from": "mock_repo/package.json",
            "meta": {"path": "package.json", "language": "json", "size": 250},
            "vault_matches": [],
            "concepts": ["package", "configuration"],
            "entities": [],
        },
        "expected": {
            "decision_one_of": ["skip", "new"],
            # Either is fine — it's borderline
        },
        "weight": 0.5,
    },

    # ============================================================
    # Case 9: meta.yaml — should be "new" (has meaningful metadata)
    # ============================================================
    {
        "id": "judge_meta_yaml",
        "description": "meta.yaml with schema info — meaningful content",
        "input": {
            "content": "",
            "_load_from": "mock_repo/meta.yaml",
            "meta": {"path": "meta.yaml", "language": "yaml", "size": 200},
            "vault_matches": [],
            "concepts": ["metadata", "schema", "repository"],
            "entities": [],
        },
        "expected": {
            "decision": "new",
        },
        "weight": 0.8,
    },
]
