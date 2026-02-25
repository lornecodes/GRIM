"""
Validate agent test cases.

Each case: an FDO draft (potentially with issues) → expected validation result.
The validate agent should catch: abs paths, generic tags, over-analysis,
acronym hallucination, broken wikilinks.
"""

CASES = [
    # ============================================================
    # Case 1: Clean FDO — should pass validation
    # ============================================================
    {
        "id": "validate_clean_fdo",
        "description": "Well-formed FDO with no issues",
        "input": {
            "fdo_draft": {
                "id": "mock-repo-src-field-engine",
                "title": "Recursive Balance Field Engine",
                "domain": "physics",
                "summary": "Core simulation engine implementing RBF dynamics with PAC conservation and SEC collapse mechanics.",
                "details": "Implements the field evolution equation ∂S/∂t = α∇I - β∇H. Uses golden ratio (φ) as the balance parameter.",
                "connections": "Related to [[mock-repo-src-metrics]] for metric collection.",
                "tags": ["RBF", "PAC", "SEC", "field-engine", "entropy-dynamics"],
                "status": "seed",
                "confidence": 0.6,
                "source_path": "src/field_engine.py",
            },
        },
        "expected": {
            "passed": True,
            "max_errors": 0,
            "max_warnings": 1,
        },
        "weight": 0.8,
    },

    # ============================================================
    # Case 2: Absolute path leak — must catch and fix
    # ============================================================
    {
        "id": "validate_abs_path",
        "description": "FDO with Windows absolute path in content",
        "input": {
            "fdo_draft": {
                "id": "mock-repo-meta",
                "title": "Repository Metadata",
                "domain": "tools",
                "summary": "Root metadata for mock-repo at C:\\Users\\peter\\repos\\mock-repo\\meta.yaml",
                "details": "Located at C:/Users/peter/repos/Dawn Field Institute/mock-repo.",
                "tags": ["metadata"],
                "source_path": "meta.yaml",
            },
        },
        "expected": {
            "must_fix": ["absolute_path"],
            "fixed_summary_must_not_contain": ["C:\\Users", "C:/Users"],
        },
        "weight": 1.5,
    },

    # ============================================================
    # Case 3: Generic tags — must filter or replace
    # ============================================================
    {
        "id": "validate_generic_tags",
        "description": "FDO with overly generic tags that add no value",
        "input": {
            "fdo_draft": {
                "id": "mock-repo-src-utils",
                "title": "Utility Functions",
                "domain": "tools",
                "summary": "Generic helper functions.",
                "tags": ["code", "file", "python", "utility", "hash-functions"],
                "source_path": "src/utils.py",
            },
        },
        "expected": {
            "removed_tags_should_include": ["code", "file"],
            "kept_tags_should_include": ["utility", "hash-functions"],
        },
        "weight": 1.0,
    },

    # ============================================================
    # Case 4: Over-analysis of simple content
    # ============================================================
    {
        "id": "validate_over_analysis",
        "description": "Short file with inflated summary",
        "input": {
            "fdo_draft": {
                "id": "mock-repo-src-init",
                "title": "Core Package Initialization",
                "domain": "tools",
                "summary": "This is the primary initialization module for the mock-repo core package. It establishes the foundational import structure, sets up the package namespace, configures the module discovery path, and provides the entry point for all downstream consumers of the package's functionality. The module serves as the critical bridge between the package's internal implementation details and its public-facing API surface.",
                "details": "The __init__.py file represents a crucial architectural decision...",
                "tags": ["initialization", "package-management", "module-system"],
                "source_path": "src/__init__.py",
            },
            "source_content": '"""Mock repo core package."""\n',
            "source_size": 30,
        },
        "expected": {
            "must_fix": ["over_analysis"],
            "fixed_summary_max_length": 100,
        },
        "weight": 1.5,
    },

    # ============================================================
    # Case 5: Acronym hallucination — wrong CIP expansion
    # ============================================================
    {
        "id": "validate_acronym_hallucination",
        "description": "FDO that incorrectly expands CIP as Cardano Improvement Proposal",
        "input": {
            "fdo_draft": {
                "id": "mock-repo-docs-architecture",
                "title": "Architecture Overview",
                "domain": "tools",
                "summary": "Architecture document for a repository following CIP (Cardano Improvement Proposal) schema v2.0.",
                "details": "Uses the Cardano Improvement Proposal framework for metadata management.",
                "tags": ["architecture", "CIP", "cardano"],
                "source_path": "docs/architecture.md",
            },
        },
        "expected": {
            "must_fix": ["acronym_hallucination"],
            "fixed_must_not_contain": [
                "Cardano Improvement Proposal",
                "cardano",
            ],
        },
        "weight": 2.0,  # Critical — this is the biggest known bug
    },

    # ============================================================
    # Case 6: Broken wikilinks
    # ============================================================
    {
        "id": "validate_broken_wikilinks",
        "description": "FDO with malformed wikilink syntax",
        "input": {
            "fdo_draft": {
                "id": "mock-repo-src-metrics",
                "title": "PAC Metrics Collector",
                "domain": "tools",
                "summary": "Metrics collection for field analysis.",
                "connections": "Related to [mock-repo-src-field-engine] and [[mock-repo-tests-field-engine]].",
                "tags": ["metrics", "PAC"],
                "source_path": "src/metrics.py",
            },
        },
        "expected": {
            "must_fix": ["wikilink_format"],
            "fixed_connections_must_contain": "[[mock-repo-src-field-engine]]",
            "fixed_connections_must_not_contain": "[mock-repo-src-field-engine]",
        },
        "weight": 0.8,
    },

    # ============================================================
    # Case 7: Multiple issues at once
    # ============================================================
    {
        "id": "validate_multiple_issues",
        "description": "FDO with several problems — abs path + generic tags + over-analysis",
        "input": {
            "fdo_draft": {
                "id": "mock-repo-package-json",
                "title": "Package Configuration File",
                "domain": "tools",
                "summary": "This is the comprehensive package.json configuration file located at C:\\Users\\peter\\repos\\mock-repo\\package.json. It serves as the central manifest for the mock-repo project, orchestrating dependency management, script execution, and project metadata dissemination across all development environments.",
                "tags": ["code", "file", "json", "package-management", "configuration"],
                "source_path": "package.json",
            },
            "source_content": '{"name": "mock-repo", "version": "1.0.0"}',
            "source_size": 40,
        },
        "expected": {
            "must_fix": ["absolute_path", "generic_tags", "over_analysis"],
            "min_fixes": 3,
        },
        "weight": 1.2,
    },
]
