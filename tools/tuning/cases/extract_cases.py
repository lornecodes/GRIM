"""
Extract agent test cases.

Each case: input content → expected concepts + entities.
The extract agent should identify the key searchable terms
without hallucinating concepts that aren't in the text.

Golden outputs show the IDEAL response for each case — the optimizer
uses these to understand exactly what success looks like, not just criteria.
"""

CASES = [
    # ============================================================
    # Case 1: Rich domain code — should extract domain concepts
    # ============================================================
    {
        "id": "extract_rich_code",
        "description": "Complex Python with domain-specific terminology",
        "input": {
            "source_id": "mock-repo",
            "chunk_path": "src/field_engine.py",
            "source_type": "file",
            "content": open(
                __file__.replace("extract_cases.py", "")
                + "/../mock_repo/src/field_engine.py"
            ).read()
            if False  # Lazy — loaded at runtime by runner
            else "",
            "_load_from": "mock_repo/src/field_engine.py",
        },
        "expected": {
            "concepts_must_include": [
                "recursive balance field",
                "field center",
                "entropy",
                "golden ratio",
            ],
            "concepts_must_not_include": [
                "machine learning",
                "neural network",
                "blockchain",
            ],
            "entities_must_include": ["PAC", "SEC", "RBF"],
            "min_concepts": 3,
            "max_concepts": 12,
        },
        "golden_output": {
            "concepts": ["recursive balance field", "field center", "entropy", "golden ratio",
                         "Poincaré activation", "Möbius topology", "SEC phase-shifting",
                         "far-from-equilibrium dynamics", "PAC conservation"],
            "entities": ["PAC", "SEC", "RBF"],
        },
        "weight": 1.0,
    },

    # ============================================================
    # Case 2: Simple utility code — few concepts
    # ============================================================
    {
        "id": "extract_simple_utils",
        "description": "Generic utility functions — should extract minimal concepts",
        "input": {
            "source_id": "mock-repo",
            "chunk_path": "src/utils.py",
            "source_type": "file",
            "content": "",
            "_load_from": "mock_repo/src/utils.py",
        },
        "expected": {
            "concepts_must_include": ["utility", "hash"],
            "concepts_must_not_include": [
                "field",
                "entropy",
                "PAC",
                "quantum",
            ],
            "min_concepts": 1,
            "max_concepts": 6,
        },
        "golden_output": {
            "concepts": ["utility", "hash", "slugify", "json parsing", "moving average"],
            "entities": [],
        },
        "weight": 0.8,
    },

    # ============================================================
    # Case 3: Acronym-heavy code — must not fabricate expansions
    # ============================================================
    {
        "id": "extract_acronyms",
        "description": "Domain-specific acronyms — extract as-is, don't expand wrongly",
        "input": {
            "source_id": "mock-repo",
            "chunk_path": "src/metrics.py",
            "source_type": "file",
            "content": "",
            "_load_from": "mock_repo/src/metrics.py",
        },
        "expected": {
            "concepts_must_include": ["metrics", "SEC", "PAC"],
            "entities_must_include": ["MED", "QPL", "FDO", "CIP"],
            "concepts_must_not_include": [
                "Cardano Improvement Proposal",  # WRONG expansion
                "Content Identifier Protocol",   # WRONG expansion
            ],
            "min_concepts": 3,
            "max_concepts": 10,
        },
        "golden_output": {
            "concepts": ["metrics", "SEC phase analysis", "PAC conservation",
                         "MED depth convergence", "QPL stability"],
            "entities": ["DFT-PAC", "RBF", "MED", "QPL", "FDO", "CIP", "CIMM"],
        },
        "weight": 1.2,  # Extra weight — acronym handling is critical
    },

    # ============================================================
    # Case 4: YAML config — extract structural concepts
    # ============================================================
    {
        "id": "extract_yaml_config",
        "description": "Configuration file — should extract parameter names",
        "input": {
            "source_id": "mock-repo",
            "chunk_path": "config/settings.yaml",
            "source_type": "file",
            "content": "",
            "_load_from": "mock_repo/config/settings.yaml",
        },
        "expected": {
            "concepts_must_include": ["configuration", "simulation"],
            "min_concepts": 2,
            "max_concepts": 8,
        },
        "golden_output": {
            "concepts": ["configuration", "simulation", "convergence threshold",
                         "field parameters", "logging"],
            "entities": [],
        },
        "weight": 0.6,
    },

    # ============================================================
    # Case 5: Markdown documentation — rich concept extraction
    # ============================================================
    {
        "id": "extract_docs",
        "description": "Architecture doc with multiple concepts",
        "input": {
            "source_id": "mock-repo",
            "chunk_path": "docs/architecture.md",
            "source_type": "file",
            "content": "",
            "_load_from": "mock_repo/docs/architecture.md",
        },
        "expected": {
            "concepts_must_include": [
                "architecture",
                "PAC",
                "RBF",
            ],
            "entities_must_include": ["CIP"],
            "concepts_must_not_include": ["Cardano"],
            "min_concepts": 3,
            "max_concepts": 10,
        },
        "golden_output": {
            "concepts": ["architecture", "PAC", "RBF", "field engine",
                         "far-from-equilibrium dynamics", "REST API"],
            "entities": ["PAC", "RBF", "CIP", "FastAPI", "NumPy"],
        },
        "weight": 1.0,
    },

    # ============================================================
    # Case 6: Test file — should extract test subjects
    # ============================================================
    {
        "id": "extract_tests",
        "description": "Test file — extract what's being tested",
        "input": {
            "source_id": "mock-repo",
            "chunk_path": "tests/test_field_engine.py",
            "source_type": "file",
            "content": "",
            "_load_from": "mock_repo/tests/test_field_engine.py",
        },
        "expected": {
            "concepts_must_include": ["field engine", "PAC conservation"],
            "min_concepts": 2,
            "max_concepts": 10,
        },
        "golden_output": {
            "concepts": ["field engine", "PAC conservation", "balance ratio",
                         "entropy diffusion"],
            "entities": ["PAC", "RBF"],
        },
        "weight": 0.8,
    },

    # ============================================================
    # Case 7: Experiment markdown — research concepts
    # ============================================================
    {
        "id": "extract_experiment",
        "description": "Research experiment with hypothesis and results",
        "input": {
            "source_id": "mock-repo",
            "chunk_path": "experiments/exp_01_fibonacci.md",
            "source_type": "file",
            "content": "",
            "_load_from": "mock_repo/experiments/exp_01_fibonacci.md",
        },
        "expected": {
            "concepts_must_include": [
                "fibonacci",
                "PAC",
                "golden ratio",
            ],
            "min_concepts": 3,
            "max_concepts": 10,
        },
        "golden_output": {
            "concepts": ["fibonacci", "PAC", "golden ratio",
                         "recursive structure", "convergence"],
            "entities": ["PAC"],
        },
        "weight": 1.0,
    },

    # ============================================================
    # Case 8: Near-empty file — should return minimal
    # ============================================================
    {
        "id": "extract_near_empty",
        "description": "Near-empty init file — should not hallucinate concepts",
        "input": {
            "source_id": "mock-repo",
            "chunk_path": "src/__init__.py",
            "source_type": "file",
            "content": '"""Mock repo core package."""\n',
        },
        "expected": {
            "max_concepts": 2,
            "concepts_must_not_include": [
                "field",
                "entropy",
                "simulation",
                "quantum",
            ],
        },
        "golden_output": {
            "concepts": [],
            "entities": [],
        },
        "weight": 0.7,
    },
]
