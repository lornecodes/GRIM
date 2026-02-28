"""
Kronos MCP Server — knowledge vault + skills for AI agents.

Tools:
  Vault:
    kronos_search       — Full-text search across all FDOs
    kronos_get          — Read a specific FDO by ID
    kronos_list         — List FDOs (optionally filtered by domain)
    kronos_graph        — Traverse the relationship graph around an FDO
    kronos_validate     — Run vault-wide validation checks
    kronos_create       — Create a new FDO
    kronos_update       — Update fields on an existing FDO
    kronos_deep_dive    — Gather source material paths for a concept
    kronos_navigate     — Read directory metadata (meta.yaml) for repo navigation

  Skills:
    kronos_skills       — List all available GRIM skills
    kronos_skill_load   — Load a skill's full instruction protocol
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from dotenv import load_dotenv
from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource

from .vault import VaultEngine, FDO, VALID_DOMAINS, VALID_STATUSES
from .search import SearchEngine
from .skills import SkillsEngine

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kronos-mcp")

# ── Configuration ────────────────────────────────────────────────────────────

vault_path = os.getenv("KRONOS_VAULT_PATH", "")
skills_path = os.getenv("KRONOS_SKILLS_PATH", "")

if not vault_path:
    raise ValueError(
        "KRONOS_VAULT_PATH environment variable required. "
        "Set it to the absolute path of your kronos-vault directory."
    )

vault = VaultEngine(vault_path)
search_engine = SearchEngine(vault)
skills_engine = SkillsEngine(skills_path) if skills_path else None

# ── Redis cache (optional) ────────────────────────────────────────────────────
from .cache import KronosCache, WRITE_TOOLS
cache = KronosCache.from_env()

# Pre-load semantic index in background thread so first search is fast.
# Timeout prevents the preload from hanging the server startup indefinitely
# (e.g. if the sentence-transformer model needs to download from HuggingFace).
import threading

PRELOAD_TIMEOUT = 90  # seconds — generous, but not infinite

def _preload_semantic():
    try:
        t0 = time.time()
        search_engine._ensure_indexed()  # BM25 + graph first
        logger.info(f"BM25 + graph indexed in {time.time() - t0:.1f}s")
        search_engine._ensure_semantic(blocking=True)  # Then semantic model + embeddings
        logger.info(f"Semantic pre-load complete in {time.time() - t0:.1f}s — all channels ready")
    except Exception as e:
        logger.warning(f"Semantic pre-load failed (search still works without it): {e}")

threading.Thread(target=_preload_semantic, daemon=True, name="semantic-preload").start()

app = Server("kronos-mcp")


# ── Tool definitions ─────────────────────────────────────────────────────────

TOOLS: list[Tool] = [
    # ── Vault tools ──
    Tool(
        name="kronos_search",
        description=(
            "Search the Kronos knowledge vault using hybrid search (exact tag matching + "
            "BM25 keyword + graph expansion with Reciprocal Rank Fusion). "
            "Returns FDOs ranked by combined relevance. Use kronos_tags first to "
            "discover available tags and vocabulary, then search with those terms. "
            "Semantic search is enabled by default for natural language understanding. "
            "Use semantic=false to disable it for faster tag/keyword-only queries."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — tag, concept name, or natural language",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 10)",
                    "default": 10,
                },
                "semantic": {
                    "type": "boolean",
                    "description": "Semantic (embedding) search channel for conceptual matches. Enabled by default. Set false for fast tag/keyword-only queries.",
                    "default": True,
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="kronos_get",
        description=(
            "Get a specific FDO (Field Data Object) by its ID. Returns full content "
            "including frontmatter, summary, details, connections, and open questions."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "The FDO ID (kebab-case, matches filename without .md)",
                },
            },
            "required": ["id"],
        },
    ),
    Tool(
        name="kronos_list",
        description=(
            "List FDOs in the vault. Optionally filter by domain. "
            "Returns ID, title, domain, status, and confidence for each."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Filter by domain",
                    "enum": ["physics", "ai-systems", "tools", "personal", "modelling", "computing"],
                },
            },
        },
    ),
    Tool(
        name="kronos_graph",
        description=(
            "Traverse the knowledge graph around an FDO. Returns nodes and edges "
            "(related links, PAC parent/children) up to the specified depth. "
            "Use this to understand how concepts connect."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "The center FDO ID to traverse from",
                },
                "depth": {
                    "type": "integer",
                    "description": "How many hops to traverse (default: 1, max: 3)",
                    "default": 1,
                },
            },
            "required": ["id"],
        },
    ),
    Tool(
        name="kronos_validate",
        description=(
            "Run comprehensive validation on the entire vault. Checks schema compliance, "
            "bidirectional links, wikilink resolution, orphan detection, PAC consistency. "
            "Use after creating or updating FDOs."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="kronos_create",
        description=(
            "Create a new FDO in the vault. Validates schema before writing. "
            "The FDO will be written to the appropriate domain directory. "
            "Use the deep-ingest or vault-sync skill protocol for guidance on quality."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Unique kebab-case ID"},
                "title": {"type": "string", "description": "Human-readable title"},
                "domain": {"type": "string", "enum": ["physics", "ai-systems", "tools", "personal", "modelling", "computing"]},
                "status": {"type": "string", "enum": ["seed", "developing", "stable"], "default": "seed"},
                "confidence": {"type": "number", "description": "0.0 to 1.0"},
                "related": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "IDs of related FDOs",
                },
                "source_repos": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Repository names this comes from",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Searchable tags",
                },
                "body": {
                    "type": "string",
                    "description": "Markdown body (# Title, ## Summary, ## Details, ## Connections, etc.)",
                },
                "confidence_basis": {"type": "string", "description": "Why this confidence level"},
                "pac_parent": {"type": "string", "description": "Parent FDO ID"},
                "source_paths": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "repo": {"type": "string", "description": "Repository name (e.g., 'dawn-field-theory')"},
                            "path": {"type": "string", "description": "Relative path within the repo"},
                            "type": {"type": "string", "enum": ["experiment", "script", "module", "doc", "config", "data"]},
                        },
                        "required": ["repo", "path", "type"],
                    },
                    "description": "Links to source material in code repos",
                },
            },
            "required": ["id", "title", "domain", "confidence", "body"],
        },
    ),
    Tool(
        name="kronos_update",
        description=(
            "Update fields on an existing FDO. Automatically bumps the 'updated' date. "
            "For body updates, pass the full new body text."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "FDO ID to update"},
                "fields": {
                    "type": "object",
                    "description": (
                        "Dict of field names to new values. Supported: title, status, "
                        "confidence, related, tags, body, confidence_basis, pac_parent, etc."
                    ),
                },
            },
            "required": ["id", "fields"],
        },
    ),
    # ── Skill tools ──
    Tool(
        name="kronos_skills",
        description=(
            "List all available GRIM skills. Skills are instruction protocols that tell "
            "you how to perform complex tasks (deep ingestion, vault sync, etc.) with "
            "quality gates and checkpoints. Load a skill with kronos_skill_load before "
            "starting a multi-step task."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="kronos_tags",
        description=(
            "List all tags in the Kronos vault with FDO counts, grouped by domain. "
            "Use this BEFORE searching to discover the right vocabulary. "
            "Returns: tag hierarchy by domain, flat tag list with counts, and top tags."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Filter to a specific domain (optional)",
                    "enum": ["physics", "ai-systems", "tools", "personal", "modelling", "computing"],
                },
            },
        },
    ),
    Tool(
        name="kronos_deep_dive",
        description=(
            "Gather all source material paths for a concept. Takes an FDO ID or search "
            "query and returns structured source_paths from the FDO and optionally its "
            "related FDOs (up to `depth` hops). Returns paths grouped by repo, filtered "
            "by type. Use this to find the actual code, experiments, and docs behind a concept."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "FDO ID (exact) or search query to find the concept",
                },
                "depth": {
                    "type": "integer",
                    "description": "How many hops of related FDOs to include (default: 1, max: 3)",
                    "default": 1,
                },
                "type_filter": {
                    "type": "string",
                    "description": "Filter source_paths by type (experiment, script, module, doc, config, data). Omit for all types.",
                    "enum": ["experiment", "script", "module", "doc", "config", "data"],
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="kronos_skill_load",
        description=(
            "Load the full instruction protocol for a GRIM skill. Returns the complete "
            "step-by-step protocol including phases, quality gates, checkpoints, and "
            "appendices. Follow the protocol to perform the task correctly."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name (e.g., 'deep-ingest', 'vault-sync')",
                },
            },
            "required": ["name"],
        },
    ),
    # ── Navigation tools ──
    Tool(
        name="kronos_navigate",
        description=(
            "Read directory metadata (meta.yaml) from any repo path. Returns description, "
            "semantic scope, files, and child directories. Use this to understand what a "
            "directory contains before diving into its files. Falls back to a basic file "
            "listing if no meta.yaml exists."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path from workspace root "
                        "(e.g., 'dawn-field-theory/foundational/experiments/milestone1')"
                    ),
                },
            },
            "required": ["path"],
        },
    ),
]


# ── Tool handlers ────────────────────────────────────────────────────────────

def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False)


def _fdo_summary(fdo: FDO) -> dict:
    return {
        "id": fdo.id,
        "title": fdo.title,
        "domain": fdo.domain,
        "status": fdo.status,
        "confidence": fdo.confidence,
        "tags": fdo.tags,
    }


def _fdo_full(fdo: FDO) -> dict:
    d = {
        "id": fdo.id,
        "title": fdo.title,
        "domain": fdo.domain,
        "created": fdo.created,
        "updated": fdo.updated,
        "status": fdo.status,
        "confidence": fdo.confidence,
        "confidence_basis": fdo.confidence_basis,
        "related": fdo.related,
        "source_repos": fdo.source_repos,
        "tags": fdo.tags,
        "pac_parent": fdo.pac_parent,
        "pac_children": fdo.pac_children,
        "equations": fdo.equations,
        "falsifiable": fdo.falsifiable,
        "source_paths": fdo.source_paths,
        "summary": fdo.summary,
        "body": fdo.body,
    }
    # Include extra frontmatter fields (type, role, etc.)
    if fdo.extra:
        d.update(fdo.extra)
    return d


def handle_search(args: dict) -> str:
    query = args["query"]
    max_results = args.get("max_results", 10)
    use_semantic = args.get("semantic", True)
    channels = ["tag_exact", "keyword", "graph"]
    if use_semantic:
        channels.append("semantic")
    ranked = search_engine.search(query, max_results=max_results, channels=channels)
    if not ranked:
        return _json({"query": query, "count": 0, "results": [], "directories": []})
    results = []
    directories = []
    for fused in ranked:
        if fused.fdo_id.startswith("meta::"):
            meta = search_engine.get_meta(fused.fdo_id)
            if meta:
                directories.append({
                    "type": "directory",
                    "repo": meta["repo"],
                    "path": meta["path"],
                    "description": meta["description"],
                    "semantic_scope": meta.get("semantic_scope", []),
                    "status": meta.get("status", ""),
                    "score": round(fused.rrf_score, 4),
                })
        else:
            fdo = vault.get(fused.fdo_id)
            if fdo:
                entry = _fdo_summary(fdo)
                entry["score"] = round(fused.rrf_score, 4)
                entry["channels"] = {k: round(v, 4) for k, v in fused.channel_scores.items()}
                results.append(entry)
    return _json({
        "query": query,
        "count": len(results),
        "directories_count": len(directories),
        "semantic_enabled": use_semantic,
        "results": results,
        "directories": directories,
    })


def handle_get(args: dict) -> str:
    # Read-only: only need the vault FDO dict, not BM25/graph indices.
    vault._ensure_index()
    fdo = vault.get(args["id"])
    if not fdo:
        return _json({"error": f"FDO not found: {args['id']}", "hint": "Use kronos_search to find FDOs"})
    return _json(_fdo_full(fdo))


def handle_list(args: dict) -> str:
    # Read-only: only need the vault FDO dict, not BM25/graph indices.
    vault._ensure_index()
    domain = args.get("domain")
    fdos = vault.list_domain(domain) if domain else vault.list_all()
    fdos.sort(key=lambda f: (f.domain, f.id))
    return _json({
        "count": len(fdos),
        "domain_filter": domain,
        "fdos": [_fdo_summary(f) for f in fdos],
    })


def handle_graph(args: dict) -> str:
    # Read-only: only need the vault FDO dict, not BM25/graph indices.
    vault._ensure_index()
    depth = min(args.get("depth", 1), 3)
    result = vault.graph_neighbors(args["id"], depth)
    return _json(result)


def handle_validate(args: dict) -> str:
    # Read-only: only need the vault FDO dict, not BM25/graph indices.
    vault._ensure_index()
    result = vault.validate()
    return _json(result)


def handle_create(args: dict) -> str:
    # Read-only check: only need vault dict to verify ID doesn't exist.
    vault._ensure_index()

    fdo_id = args["id"]
    if vault.get(fdo_id):
        return _json({"error": f"FDO already exists: {fdo_id}", "hint": "Use kronos_update to modify existing FDOs"})

    domain = args["domain"]
    if domain not in VALID_DOMAINS:
        return _json({"error": f"Invalid domain: {domain}", "valid": list(VALID_DOMAINS)})

    today = str(date.today())
    fdo = FDO(
        id=fdo_id,
        title=args["title"],
        domain=domain,
        created=today,
        updated=today,
        status=args.get("status", "seed"),
        confidence=float(args.get("confidence", 0.3)),
        related=args.get("related", []),
        source_repos=args.get("source_repos", []),
        tags=args.get("tags", []),
        body=args["body"],
        file_path="",
        pac_parent=args.get("pac_parent"),
        confidence_basis=args.get("confidence_basis"),
        source_paths=args.get("source_paths", []),
    )

    path = vault.write_fdo(fdo)
    # Incremental index update — no full rebuild, keeps _initialized=True.
    search_engine.index_fdo(fdo)
    return _json({"created": fdo_id, "path": path, "domain": domain})


def handle_update(args: dict) -> str:
    # Read-only check: only need vault dict to fetch the FDO.
    vault._ensure_index()

    fdo_id = args["id"]
    fdo = vault.get(fdo_id)
    if not fdo:
        return _json({"error": f"FDO not found: {fdo_id}"})

    fields = args.get("fields", {})
    for field_name, value in fields.items():
        if hasattr(fdo, field_name) and field_name not in ("id", "created", "file_path"):
            setattr(fdo, field_name, value)
        elif field_name not in ("id", "created", "file_path"):
            fdo.extra[field_name] = value

    fdo.updated = str(date.today())
    path = vault.write_fdo(fdo)
    # Incremental index update — no full rebuild, keeps _initialized=True.
    search_engine.index_fdo(fdo)
    return _json({"updated": fdo_id, "path": path, "fields_changed": list(fields.keys())})


def handle_deep_dive(args: dict) -> str:
    vault._ensure_index()
    query = args["query"]
    depth = min(args.get("depth", 1), 3)
    type_filter = args.get("type_filter")

    # Resolve query to an FDO — try exact ID first, then search
    root_fdo = vault.get(query)
    if not root_fdo:
        results = vault.search(query, max_results=1)
        if results:
            root_fdo = results[0]
    if not root_fdo:
        return _json({"error": f"No FDO found for: {query}", "hint": "Use kronos_search to find concepts"})

    # Walk related FDOs up to depth
    visited: set[str] = set()
    fdo_sources: list[dict] = []

    def collect(fdo_id: str, d: int):
        if fdo_id in visited or d > depth:
            return
        visited.add(fdo_id)
        fdo = vault.get(fdo_id)
        if not fdo:
            return
        paths = fdo.source_paths
        if type_filter:
            paths = [p for p in paths if p.get("type") == type_filter]
        if paths:
            fdo_sources.append({
                "fdo_id": fdo.id,
                "fdo_title": fdo.title,
                "hop": d,
                "source_paths": paths,
            })
        if d < depth:
            for rel_id in fdo.related:
                collect(rel_id, d + 1)

    collect(root_fdo.id, 0)

    # Group all paths by repo, enriching directory entries with meta.yaml
    workspace = Path(vault_path).parent
    by_repo: dict[str, list[dict]] = {}
    for entry in fdo_sources:
        for sp in entry["source_paths"]:
            repo = sp.get("repo", "unknown")
            if repo not in by_repo:
                by_repo[repo] = []
            path_entry: dict[str, Any] = {
                "path": sp["path"],
                "type": sp["type"],
                "from_fdo": entry["fdo_id"],
            }
            # Enrich directory source_paths with meta.yaml context
            if sp.get("type") in ("experiment", "module"):
                meta_path = workspace / repo / sp["path"] / "meta.yaml"
                if meta_path.is_file():
                    try:
                        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
                        path_entry["meta"] = {
                            k: meta[k] for k in ("description", "status", "semantic_scope")
                            if k in meta
                        }
                    except Exception:
                        pass
            by_repo[repo].append(path_entry)

    return _json({
        "root": root_fdo.id,
        "root_title": root_fdo.title,
        "depth": depth,
        "type_filter": type_filter,
        "fdos_traversed": len(visited),
        "fdos_with_sources": len(fdo_sources),
        "sources_by_fdo": fdo_sources,
        "sources_by_repo": by_repo,
    })


def handle_skills(args: dict) -> str:
    if not skills_engine:
        return _json({"error": "Skills path not configured. Set KRONOS_SKILLS_PATH environment variable."})
    skills_engine.refresh()
    return _json({"skills": skills_engine.list_skills()})


def handle_skill_load(args: dict) -> str:
    if not skills_engine:
        return _json({"error": "Skills path not configured. Set KRONOS_SKILLS_PATH environment variable."})
    skills_engine.refresh()
    name = args["name"]
    skill = skills_engine.get_skill(name)
    if not skill:
        available = [s["name"] for s in skills_engine.list_skills()]
        return _json({"error": f"Skill not found: {name}", "available": available})
    return _json({
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "type": skill.skill_type,
        "phases": skill.phases,
        "permissions": skill.permissions,
        "quality_gates": skill.quality_gates,
        "protocol": skill.protocol,
    })


def handle_tags(args: dict) -> str:
    # Read-only: tags come from FDO frontmatter, not the BM25/graph indices.
    vault._ensure_index()
    domain_filter = args.get("domain")

    # Collect all tags with their FDOs and domains
    tag_fdo_map: dict[str, list[dict]] = {}  # tag → [{id, domain}]
    domain_tags: dict[str, dict[str, int]] = {}  # domain → {tag → count}

    for fdo in vault.index.values():
        if domain_filter and fdo.domain != domain_filter:
            continue
        for tag in fdo.tags:
            tag_lower = tag.lower()
            if tag_lower not in tag_fdo_map:
                tag_fdo_map[tag_lower] = []
            tag_fdo_map[tag_lower].append({"id": fdo.id, "domain": fdo.domain})

            if fdo.domain not in domain_tags:
                domain_tags[fdo.domain] = {}
            domain_tags[fdo.domain][tag_lower] = domain_tags[fdo.domain].get(tag_lower, 0) + 1

    # Flat tag list sorted by count
    tag_counts = {tag: len(fdos) for tag, fdos in tag_fdo_map.items()}
    sorted_tags = sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))

    # Domain hierarchy: domain → sorted tags
    hierarchy = {}
    for domain in sorted(domain_tags.keys()):
        tags = domain_tags[domain]
        hierarchy[domain] = sorted(tags.items(), key=lambda x: (-x[1], x[0]))

    return _json({
        "total_tags": len(tag_counts),
        "total_fdos": sum(1 for fdo in vault.index.values()
                         if not domain_filter or fdo.domain == domain_filter),
        "domain_filter": domain_filter,
        "top_tags": [{"tag": t, "count": c} for t, c in sorted_tags[:30]],
        "by_domain": {
            domain: [{"tag": t, "count": c} for t, c in tags]
            for domain, tags in hierarchy.items()
        },
    })


_NAVIGATE_SKIP = {
    "__pycache__", ".venv", "venv", "node_modules", ".git",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".egg-info",
}


def handle_navigate(args: dict) -> str:
    """Read meta.yaml from a directory, or fall back to a file listing."""
    rel_path = args["path"].strip().strip("/\\")

    # Workspace root = vault parent (kronos-vault sits inside the workspace)
    workspace = Path(vault_path).parent
    target = workspace / rel_path

    if not target.exists():
        return _json({"error": f"Path not found: {rel_path}"})
    if not target.is_dir():
        return _json({"error": f"Not a directory: {rel_path}", "hint": "kronos_navigate works on directories"})

    result: dict[str, Any] = {"path": rel_path}

    meta_file = target / "meta.yaml"
    if meta_file.exists():
        try:
            meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}
        except Exception as e:
            meta = {}
            result["meta_parse_error"] = str(e)

        result["has_meta"] = True
        for key in ("description", "semantic_scope", "semantic_tags", "status",
                     "key_results", "files", "child_directories", "schema_version"):
            if key in meta:
                result[key] = meta[key]
    else:
        result["has_meta"] = False

    # Always include a directory listing (filtered)
    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        dirs = []
        files = []
        for entry in entries:
            name = entry.name
            if name.startswith(".") and name not in (".spec",):
                continue
            if name in _NAVIGATE_SKIP:
                continue
            if entry.is_dir():
                has_child_meta = (entry / "meta.yaml").exists()
                dirs.append({"name": name, "has_meta": has_child_meta})
            else:
                files.append(name)
        result["listing"] = {"directories": dirs, "files": files}
    except PermissionError:
        result["listing"] = {"error": "Permission denied"}

    return _json(result)


HANDLERS = {
    "kronos_search": handle_search,
    "kronos_get": handle_get,
    "kronos_list": handle_list,
    "kronos_graph": handle_graph,
    "kronos_validate": handle_validate,
    "kronos_create": handle_create,
    "kronos_update": handle_update,
    "kronos_tags": handle_tags,
    "kronos_deep_dive": handle_deep_dive,
    "kronos_skills": handle_skills,
    "kronos_skill_load": handle_skill_load,
    "kronos_navigate": handle_navigate,
}


# ── MCP wiring ───────────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(
    name: str, arguments: Any
) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
    if not isinstance(arguments, dict):
        arguments = {}

    handler = HANDLERS.get(name)
    if not handler:
        raise ValueError(f"Unknown tool: {name}")

    # ── Cache read (read-only tools only) ─────────────────────────────────────
    cached = cache.get(name, arguments)
    if cached is not None:
        return [TextContent(type="text", text=cached)]

    # ── Execute handler ───────────────────────────────────────────────────────
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(handler, arguments),
            timeout=30.0,  # 30s hard timeout — prevents indefinite hangs
        )
    except asyncio.TimeoutError:
        logger.error(f"Tool {name} timed out after 30s")
        return [TextContent(type="text", text=_json({"error": f"{name} timed out after 30s"}))]
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}", exc_info=True)
        return [TextContent(type="text", text=_json({"error": str(e)}))]

    # ── Cache write / invalidation ────────────────────────────────────────────
    if name in WRITE_TOOLS:
        cache.invalidate_for_write(name, arguments)
    else:
        cache.set(name, arguments, result)

    return [TextContent(type="text", text=result)]


async def main():
    from mcp.server.stdio import stdio_server
    import anyio
    from io import TextIOWrapper
    import sys

    logger.info(f"Kronos MCP starting — vault: {vault_path}")
    if skills_engine:
        logger.info(f"Skills path: {skills_path}")

    # On Windows, TextIOWrapper with default newline=None translates \n → \r\n.
    # MCP protocol requires \n-only line endings (newline-delimited JSON).
    # Pass explicit stdout with newline="" to suppress the translation.
    fixed_stdout = anyio.wrap_file(
        TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="")
    )

    async with stdio_server(stdout=fixed_stdout) as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )
