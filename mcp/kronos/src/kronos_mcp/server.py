"""
Kronos MCP Server — knowledge vault + skills + task management for AI agents.

Tools:
  Vault:
    kronos_search         — Full-text search across all FDOs
    kronos_get            — Read a specific FDO by ID
    kronos_list           — List FDOs (optionally filtered by domain)
    kronos_graph          — Traverse the relationship graph around an FDO
    kronos_validate       — Run vault-wide validation checks
    kronos_create         — Create a new FDO
    kronos_update         — Update fields on an existing FDO
    kronos_deep_dive      — Gather source material paths for a concept
    kronos_navigate       — Read directory metadata (meta.yaml) for repo navigation

  Memory:
    kronos_memory_read    — Read GRIM's persistent working memory
    kronos_memory_update  — Update a section of working memory
    kronos_memory_sections — List memory sections with sizes

  Source Navigation:
    kronos_read_source    — Read file content from a repo source path
    kronos_search_source  — Grep across source files referenced by an FDO

  Tasks:
    kronos_task_create    — Create story or task in a feature FDO
    kronos_task_update    — Update story/task fields
    kronos_task_get       — Get story/task by ID
    kronos_task_list      — List stories with filters
    kronos_task_move      — Move story on the kanban board
    kronos_task_archive   — Archive closed stories
    kronos_board_view     — Get kanban board state
    kronos_backlog_view   — Get stories not on board
    kronos_calendar_view  — Get calendar for date range
    kronos_calendar_add   — Add personal calendar event
    kronos_calendar_update — Update/delete personal event
    kronos_calendar_sync  — Rebuild schedule from board

  Notes:
    kronos_note_append    — Append a note to the monthly rolling log
    kronos_notes_recent   — Get recent note entries (last N days)

  Skills:
    kronos_skills         — List all available GRIM skills
    kronos_skill_load     — Load a skill's full instruction protocol

  System:
    kronos_tool_groups    — List tool groups for access control
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from dotenv import load_dotenv
from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource

from .vault import VaultEngine, FDO, VALID_DOMAINS, VALID_STATUSES
from .search import SearchEngine

# Canonical domain enum for MCP tool schemas — derived from vault.py source of truth
_DOMAIN_ENUM = sorted(VALID_DOMAINS)
from .skills import SkillsEngine
from .tasks import TaskEngine
from .board import BoardEngine
from .calendar import CalendarEngine

load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────
# CRITICAL: MCP stdio servers communicate via stdin/stdout. If logging goes to
# stderr and the parent process doesn't drain it fast enough, the stderr pipe
# buffer fills up (~512KB on Windows), blocking any logging call, which blocks
# the asyncio event loop, which prevents stdout responses from being written.
#
# Fix: log to a file instead of stderr. Falls back to NullHandler if the log
# file can't be created (e.g. read-only filesystem).
_log_path = os.path.join(os.getenv("KRONOS_VAULT_PATH", "."), "..", ".kronos-mcp.log")
try:
    _log_path = os.path.abspath(_log_path)
    _handler = logging.FileHandler(_log_path, encoding="utf-8")
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"
    ))
    logging.root.addHandler(_handler)
    logging.root.setLevel(logging.INFO)
except Exception:
    logging.basicConfig(level=logging.WARNING)  # Fallback: minimal stderr logging

logger = logging.getLogger("kronos-mcp")

# Suppress MCP library's per-request INFO logs (they go to root → our file handler)
logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)

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

# ── Task management engines ──────────────────────────────────────────────────
task_engine = TaskEngine(vault_path)
board_engine = BoardEngine(vault_path, task_engine)
calendar_engine = CalendarEngine(vault_path, board_engine)

# ── Redis cache (optional) ────────────────────────────────────────────────────
from .cache import KronosCache, WRITE_TOOLS, MEMORY_WRITE_TOOLS, TASK_WRITE_TOOLS
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
                    "enum": _DOMAIN_ENUM,
                },
            },
        },
    ),
    Tool(
        name="kronos_graph",
        description=(
            "Traverse the knowledge graph around an FDO. Returns nodes and edges "
            "(related links, PAC parent/children) up to the specified depth. "
            "Use this to understand how concepts connect. "
            "Use scope to filter: 'tasks' for project/feature edges, "
            "'architecture' for design/ADR edges, 'knowledge' to exclude both."
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
                "scope": {
                    "type": "string",
                    "enum": ["all", "tasks", "architecture", "knowledge"],
                    "description": "Filter graph traversal scope (default: all)",
                    "default": "all",
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
                "domain": {"type": "string", "enum": _DOMAIN_ENUM},
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
                    "enum": _DOMAIN_ENUM,
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
    # ── Source navigation tools ──
    Tool(
        name="kronos_read_source",
        description=(
            "Read file content from a source path within the workspace. Use after "
            "kronos_deep_dive or kronos_navigate to inspect actual code, experiments, "
            "scripts, or documentation referenced in FDO source_paths. "
            "Supports line-range selection for large files via offset/max_lines."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository name (e.g., 'dawn-field-theory', 'fracton', 'reality-engine')",
                },
                "path": {
                    "type": "string",
                    "description": "Relative path within the repo (e.g., 'fracton/core/pac_regulation.py')",
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Maximum lines to return (default 200, max 500). Use offset for pagination.",
                    "default": 200,
                },
                "offset": {
                    "type": "integer",
                    "description": "Line offset to start reading from (0-based, default 0).",
                    "default": 0,
                },
            },
            "required": ["repo", "path"],
        },
    ),
    Tool(
        name="kronos_search_source",
        description=(
            "Search within source files referenced by an FDO's source_paths. "
            "Combines concept resolution with content grep — go from FDO ID to "
            "matching code lines in one call. Accepts an FDO ID or search query, "
            "then greps across its source files for the given pattern. "
            "Use to find function definitions, constants, or patterns within "
            "a concept's source material."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "FDO ID (exact) or search query to find the concept",
                },
                "pattern": {
                    "type": "string",
                    "description": "Text pattern to search for in source files (case-insensitive substring match)",
                },
                "depth": {
                    "type": "integer",
                    "description": "Hops of related FDOs to include (default 0 = just the root FDO, max 3)",
                    "default": 0,
                },
                "type_filter": {
                    "type": "string",
                    "description": "Filter source_paths by type before searching",
                    "enum": ["experiment", "script", "module", "doc", "config", "data"],
                },
                "max_matches": {
                    "type": "integer",
                    "description": "Maximum total file match groups to return (default 30, max 100)",
                    "default": 30,
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Lines of context before and after each match (default 2, max 5)",
                    "default": 2,
                },
            },
            "required": ["query", "pattern"],
        },
    ),

    # ── Memory tools ──
    Tool(
        name="kronos_memory_read",
        description=(
            "[memory:read] Read GRIM's persistent working memory (memory.md). "
            "Returns full content or a specific section. Memory is separate from "
            "vault FDOs — it stores session notes, objectives, preferences, and learnings."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": (
                        "Optional section name to read (e.g., 'Active Objectives', "
                        "'User Preferences'). Omit to read full memory."
                    ),
                },
            },
        },
    ),
    Tool(
        name="kronos_memory_update",
        description=(
            "[memory:write] Update GRIM's persistent working memory. Can update a "
            "specific section by name, or replace the entire file with full_content. "
            "Memory tools NEVER modify vault FDOs — they only touch memory.md."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": (
                        "Section name to update (e.g., 'Session Notes', 'Key Learnings'). "
                        "Mutually exclusive with full_content."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "New content for the section. Required when 'section' is provided.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["replace", "append"],
                    "description": "Whether to replace the section or append to it. Default: replace.",
                    "default": "replace",
                },
                "full_content": {
                    "type": "string",
                    "description": (
                        "Replace the entire memory.md file. Mutually exclusive with section/content. "
                        "Use sparingly — prefer section updates."
                    ),
                },
            },
        },
    ),
    Tool(
        name="kronos_memory_sections",
        description=(
            "[memory:read] List all sections in GRIM's working memory with their sizes. "
            "Use this to discover what sections exist before reading or updating."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),

    # ── System tools ──
    Tool(
        name="kronos_tool_groups",
        description=(
            "[system] List all tool groups and their members. Tool groups define "
            "access control boundaries — agents get tools based on their assigned groups."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),

    # ── Task management tools ──
    Tool(
        name="kronos_task_create",
        description=(
            "Create a new story or task. Stories live inside feat-* FDOs. "
            "Tasks are nested under stories. Provide feat_id for stories, "
            "or story_id for tasks."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["story", "task"],
                    "description": "Item type to create",
                },
                "feat_id": {
                    "type": "string",
                    "description": "Feature FDO ID (required for stories, e.g. 'feat-grim-taskman')",
                },
                "story_id": {
                    "type": "string",
                    "description": "Parent story ID (required for tasks)",
                },
                "title": {
                    "type": "string",
                    "description": "Title of the story or task",
                },
                "priority": {
                    "type": "string",
                    "enum": ["critical", "high", "medium", "low"],
                    "description": "Priority level (stories only, default: medium)",
                    "default": "medium",
                },
                "estimate_days": {
                    "type": "number",
                    "description": "Estimated days to complete (default: 1 for stories, 0.5 for tasks)",
                },
                "description": {
                    "type": "string",
                    "description": "Story description (stories only)",
                },
                "acceptance_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of acceptance criteria (stories only)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for the story",
                },
                "assignee": {
                    "type": "string",
                    "description": "Assignee (tasks only)",
                },
                "notes": {
                    "type": "string",
                    "description": "Notes (tasks only)",
                },
                "created_by": {
                    "type": "string",
                    "description": "Who created this item (e.g. 'human', 'agent:planning', 'agent:memory')",
                },
                "status": {
                    "type": "string",
                    "enum": ["draft", "new"],
                    "description": "Initial status — 'draft' for AI-created items pending approval, 'new' for human-created (default: new)",
                },
            },
            "required": ["type", "title"],
        },
    ),
    Tool(
        name="kronos_task_update",
        description=(
            "Update fields on a story or task. Pass the item ID and a dict of "
            "fields to update. Auto-logs status changes."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "Story or task ID to update",
                },
                "fields": {
                    "type": "object",
                    "description": (
                        "Fields to update. Stories: title, status, priority, estimate_days, "
                        "description, acceptance_criteria, tags. "
                        "Tasks: title, status, estimate_days, assignee, notes."
                    ),
                },
            },
            "required": ["item_id", "fields"],
        },
    ),
    Tool(
        name="kronos_task_get",
        description=(
            "Get full details of a story or task by ID. Returns all fields "
            "including tasks (for stories) and parent info."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "Story or task ID",
                },
            },
            "required": ["item_id"],
        },
    ),
    Tool(
        name="kronos_task_list",
        description=(
            "List stories with optional filters. Returns summary info for each "
            "story including task progress. Sorted by priority then creation date."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Filter by project (e.g. 'proj-grim')",
                },
                "feat_id": {
                    "type": "string",
                    "description": "Filter by feature (e.g. 'feat-grim-taskman')",
                },
                "status": {
                    "type": "string",
                    "enum": ["new", "active", "in_progress", "resolved", "closed"],
                    "description": "Filter by status",
                },
                "priority": {
                    "type": "string",
                    "enum": ["critical", "high", "medium", "low"],
                    "description": "Filter by priority",
                },
            },
        },
    ),
    Tool(
        name="kronos_task_move",
        description=(
            "Move a story on the kanban board. If not on the board yet, adds it. "
            "Auto-updates story status to match the column. "
            "Columns: new, active, in_progress, resolved, closed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "story_id": {
                    "type": "string",
                    "description": "Story ID to move",
                },
                "column": {
                    "type": "string",
                    "enum": ["new", "active", "in_progress", "resolved", "closed"],
                    "description": "Target board column",
                },
            },
            "required": ["story_id", "column"],
        },
    ),
    Tool(
        name="kronos_task_archive",
        description=(
            "Archive closed stories in feature FDOs. Moves closed stories from "
            "the active stories list to an archived_stories section."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "feat_id": {
                    "type": "string",
                    "description": "Feature to archive (omit to archive all closed stories across all features)",
                },
            },
        },
    ),
    Tool(
        name="kronos_board_view",
        description=(
            "Get the current kanban board state. Returns all columns with "
            "enriched story data (title, priority, estimate, task progress). "
            "Optionally filter by project."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Filter board to a specific project",
                },
            },
        },
    ),
    Tool(
        name="kronos_backlog_view",
        description=(
            "Get stories NOT currently on the board (the backlog). "
            "Filter by project, feature, or priority."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Filter by project",
                },
                "feat_id": {
                    "type": "string",
                    "description": "Filter by feature",
                },
                "priority": {
                    "type": "string",
                    "enum": ["critical", "high", "medium", "low"],
                    "description": "Filter by priority",
                },
            },
        },
    ),
    Tool(
        name="kronos_calendar_view",
        description=(
            "Get calendar entries for a date range. Merges work schedule "
            "(from board items + estimates) with personal events."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD)",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date (YYYY-MM-DD)",
                },
                "include_personal": {
                    "type": "boolean",
                    "description": "Include personal events (default: true)",
                    "default": True,
                },
            },
            "required": ["start_date", "end_date"],
        },
    ),
    Tool(
        name="kronos_calendar_add",
        description=(
            "Add a personal calendar event (non-project). "
            "For work items, use kronos_task_create + kronos_task_move instead."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Event title",
                },
                "date": {
                    "type": "string",
                    "description": "Event date (YYYY-MM-DD)",
                },
                "time": {
                    "type": "string",
                    "description": "Event time (HH:MM, optional)",
                },
                "duration_hours": {
                    "type": "number",
                    "description": "Duration in hours (optional)",
                },
                "recurring": {
                    "type": "boolean",
                    "description": "Is this a recurring event? (default: false)",
                    "default": False,
                },
                "notes": {
                    "type": "string",
                    "description": "Additional notes",
                },
            },
            "required": ["title", "date"],
        },
    ),
    Tool(
        name="kronos_calendar_update",
        description=(
            "Update or delete a personal calendar event. "
            "Pass action='update' with fields, or action='delete' to remove."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Personal event ID (e.g. 'personal-001')",
                },
                "action": {
                    "type": "string",
                    "enum": ["update", "delete"],
                    "description": "Whether to update fields or delete the event",
                },
                "fields": {
                    "type": "object",
                    "description": "Fields to update (ignored for delete). Supported: title, date, time, duration_hours, recurring, notes.",
                },
            },
            "required": ["event_id", "action"],
        },
    ),
    Tool(
        name="kronos_calendar_sync",
        description=(
            "Rebuild the work schedule from active board items + estimates. "
            "Sequences stories by priority, computes start/end dates. "
            "Call after moving items on the board or updating estimates."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date for scheduling (YYYY-MM-DD, default: today)",
                },
            },
        },
    ),
    # ── Notes tools ──────────────────────────────────────────────────────────
    Tool(
        name="kronos_note_append",
        description=(
            "Append a timestamped note entry to the current month's rolling note log. "
            "Notes are quick, specific captures (fixes, workarounds, gotchas). "
            "Creates the monthly file if it doesn't exist. Returns the entry anchor."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short title for the note entry"},
                "body": {"type": "string", "description": "Note content (markdown)"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Searchable tags for this entry",
                },
                "related": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Related FDO IDs (optional)",
                },
                "source_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths referenced (repo/path format, optional)",
                },
            },
            "required": ["title", "body", "tags"],
        },
    ),
    Tool(
        name="kronos_notes_recent",
        description=(
            "Get recent note entries from rolling logs. Returns entries from the "
            "last N days (default 30), optionally filtered by tags. Used by the "
            "memory node to surface recent knowledge."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "default": 30,
                    "description": "How many days back to search (default: 30)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by tags (OR logic)",
                },
                "max_entries": {
                    "type": "integer",
                    "default": 10,
                    "description": "Maximum entries to return (default: 10)",
                },
            },
        },
    ),
]


# ── Tool groups (access control boundaries) ─────────────────────────────────

TOOL_GROUPS = {
    "vault:read":   ["kronos_search", "kronos_get", "kronos_list", "kronos_graph",
                      "kronos_tags", "kronos_deep_dive", "kronos_validate",
                      "kronos_notes_recent"],
    "vault:write":  ["kronos_create", "kronos_update", "kronos_note_append"],
    "memory:read":  ["kronos_memory_read", "kronos_memory_sections"],
    "memory:write": ["kronos_memory_update"],
    "source:read":  ["kronos_navigate", "kronos_read_source", "kronos_search_source"],
    "system":       ["kronos_skills", "kronos_skill_load", "kronos_tool_groups"],
    "tasks:read":   ["kronos_task_list", "kronos_task_get", "kronos_board_view",
                      "kronos_backlog_view", "kronos_calendar_view"],
    "tasks:write":  ["kronos_task_create", "kronos_task_update", "kronos_task_move",
                      "kronos_task_archive", "kronos_calendar_add",
                      "kronos_calendar_update", "kronos_calendar_sync"],
}


# ── Tool handlers ────────────────────────────────────────────────────────────

def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False)


# ── Memory helpers (ported from core/memory_store.py — no GRIM dependency) ──

MEMORY_FILENAME = "memory.md"


def _memory_path() -> Path:
    """Full path to memory.md in the vault."""
    return Path(vault_path) / MEMORY_FILENAME


def _read_memory_file() -> str:
    """Read memory.md from vault. Returns empty string if missing."""
    p = _memory_path()
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        logger.exception("Failed to read memory.md")
        return ""


_memory_lock = threading.Lock()


def _write_memory_file(content: str) -> None:
    """Write content to memory.md in the vault. Thread-safe via _memory_lock."""
    from .fileutil import atomic_write
    p = _memory_path()
    with _memory_lock:
        try:
            atomic_write(p, content)
            logger.info("Updated memory.md (%d chars)", len(content))
        except Exception:
            logger.exception("Failed to write memory.md")
            raise


def _parse_memory_sections(content: str) -> dict[str, str]:
    """Parse markdown H2 sections into a dict. Strips HTML comments."""
    if not content.strip():
        return {}

    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    for line in content.split("\n"):
        match = re.match(r"^##\s+(.+)$", line)
        if match:
            if current_name is not None:
                sections[current_name] = _clean_memory_section("\n".join(current_lines))
            current_name = match.group(1).strip()
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)

    if current_name is not None:
        sections[current_name] = _clean_memory_section("\n".join(current_lines))

    return sections


def _update_memory_section(content: str, section_name: str, new_text: str) -> str:
    """Replace a specific H2 section's content, preserving the rest.

    If the section doesn't exist, appends it at the end.
    """
    pattern = re.compile(
        rf"(^##\s+{re.escape(section_name)}\s*\n)"
        rf"(.*?)"
        rf"(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )

    match = pattern.search(content)
    if match:
        replacement = f"{match.group(1)}{new_text.strip()}\n\n"
        return content[: match.start()] + replacement + content[match.end() :]

    return content.rstrip() + f"\n\n## {section_name}\n{new_text.strip()}\n"


def _append_to_memory_section(content: str, section_name: str, new_text: str) -> str:
    """Append text to an existing section, or create it if missing."""
    sections = _parse_memory_sections(content)
    if section_name in sections:
        existing = sections[section_name]
        combined = existing.rstrip() + "\n" + new_text.strip()
        return _update_memory_section(content, section_name, combined)
    return _update_memory_section(content, section_name, new_text)


def _clean_memory_section(text: str) -> str:
    """Strip HTML comments and leading/trailing whitespace."""
    cleaned = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return cleaned.strip()


# ── Memory handlers ──────────────────────────────────────────────────────────

def handle_memory_read(args: dict) -> str:
    """Read full memory or a specific section."""
    content = _read_memory_file()
    section = args.get("section")

    if not content:
        return _json({"content": "", "sections": []})

    sections = _parse_memory_sections(content)

    if section:
        if section in sections:
            return _json({"content": sections[section], "section": section})
        return _json({"error": f"Section '{section}' not found",
                       "available": list(sections.keys())})

    return _json({"content": content, "sections": list(sections.keys())})


def handle_memory_update(args: dict) -> str:
    """Update a section or replace full memory content.

    The entire read-modify-write cycle is under _memory_lock to prevent
    TOCTOU races (two concurrent updates both read the same content,
    then the second write silently overwrites the first's changes).
    """
    from .fileutil import atomic_write

    full_content = args.get("full_content")
    section = args.get("section")

    if full_content and section:
        return _json({"error": "Cannot specify both 'section' and 'full_content' — pick one"})

    if full_content:
        # Full file replacement
        if "## " not in full_content:
            return _json({"error": "full_content must contain at least one ## section header"})
        _write_memory_file(full_content)
        sections = _parse_memory_sections(full_content)
        return _json({"ok": True, "char_count": len(full_content),
                       "sections": list(sections.keys())})

    if not section:
        return _json({"error": "Provide either 'section' + 'content', or 'full_content'"})

    content_text = args.get("content", "")
    mode = args.get("mode", "replace")

    # Read-modify-write under a single lock to prevent TOCTOU races
    with _memory_lock:
        current = _read_memory_file()
        if not current:
            current = "# GRIM Working Memory\n"

        if mode == "append":
            updated = _append_to_memory_section(current, section, content_text)
        else:
            updated = _update_memory_section(current, section, content_text)

        try:
            p = _memory_path()
            atomic_write(p, updated)
            logger.info("Updated memory.md (%d chars)", len(updated))
        except Exception:
            logger.exception("Failed to write memory.md")
            raise

    return _json({"ok": True, "section": section, "char_count": len(content_text)})


def handle_memory_sections(args: dict) -> str:
    """List memory sections with sizes."""
    content = _read_memory_file()
    if not content:
        return _json({"sections": []})

    sections = _parse_memory_sections(content)
    result = [{"name": name, "char_count": len(text)} for name, text in sections.items()]
    return _json({"sections": result})


def handle_tool_groups(args: dict) -> str:
    """Return tool group definitions for access control."""
    return _json(TOOL_GROUPS)


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


# ── Source navigation helpers ────────────────────────────────────────────────

_SOURCE_EXTENSIONS = frozenset((
    ".py", ".md", ".yaml", ".yml", ".json", ".toml", ".txt",
    ".rst", ".cfg", ".ini", ".sh", ".bash", ".ps1", ".tex",
))


def _validate_workspace_path(repo: str, rel_path: str) -> tuple[Path | None, str | None]:
    """Resolve and validate a repo/path pair within the workspace.

    Returns (resolved_path, error_message). error_message is None on success.
    """
    workspace = Path(vault_path).parent
    target = (workspace / repo / rel_path).resolve()
    if not target.is_relative_to(workspace.resolve()):
        return None, "Path traversal blocked — path must be within workspace"
    return target, None


def _normalize_source_path(sp: Any) -> tuple[str, str, str]:
    """Normalize both structured and legacy flat-string source_paths.

    Returns (repo, path, type).
    """
    if isinstance(sp, dict):
        return sp.get("repo", ""), sp.get("path", ""), sp.get("type", "unknown")
    if isinstance(sp, str):
        parts = sp.strip().split("/", 1)
        if len(parts) == 2:
            return parts[0], parts[1], "unknown"
        return parts[0], "", "unknown"
    return "", "", "unknown"


# ── Source navigation handlers ───────────────────────────────────────────────

def handle_read_source(args: dict) -> str:
    """Read file content from a repo source path."""
    repo = args.get("repo", "").strip().strip("/\\")
    rel_path = args.get("path", "").strip().strip("/\\")
    max_lines = min(args.get("max_lines", 200), 500)
    offset = max(args.get("offset", 0), 0)

    if not repo or not rel_path:
        return _json({"error": "repo and path parameters required"})

    target, err = _validate_workspace_path(repo, rel_path)
    if err:
        return _json({"error": err})

    if not target.exists():
        return _json({"error": f"Not found: {repo}/{rel_path}"})

    if target.is_dir():
        return _json({
            "error": f"Path is a directory: {repo}/{rel_path}",
            "hint": "Use kronos_navigate for directories, or specify a file within it",
        })

    size = target.stat().st_size
    if size > 1_000_000:
        return _json({
            "error": f"File too large: {size:,} bytes",
            "hint": "This tool is for source code and docs, not binary/data files",
        })

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return _json({"error": f"Cannot read file: {e}"})

    lines = text.splitlines()
    total_lines = len(lines)
    selected = lines[offset : offset + max_lines]

    return _json({
        "repo": repo,
        "path": rel_path,
        "total_lines": total_lines,
        "offset": offset,
        "lines_returned": len(selected),
        "truncated": (offset + max_lines) < total_lines,
        "content": "\n".join(selected),
    })


def handle_search_source(args: dict) -> str:
    """Grep across source files referenced by an FDO's source_paths."""
    vault._ensure_index()
    query = args["query"]
    pattern = args["pattern"]
    depth = min(args.get("depth", 0), 3)
    type_filter = args.get("type_filter")
    max_matches = min(args.get("max_matches", 30), 100)
    context_lines = min(args.get("context_lines", 2), 5)

    # Resolve FDO (same logic as deep_dive)
    root_fdo = vault.get(query)
    if not root_fdo:
        results = vault.search(query, max_results=1)
        if results:
            root_fdo = results[0]
    if not root_fdo:
        return _json({
            "error": f"No FDO found for: {query}",
            "hint": "Use kronos_search to find concepts first",
        })

    # Collect source_paths from FDO graph
    workspace = Path(vault_path).parent
    visited: set[str] = set()
    all_paths: list[tuple[str, str, str, str]] = []  # (repo, path, type, from_fdo)

    def collect(fdo_id: str, d: int):
        if fdo_id in visited or d > depth:
            return
        visited.add(fdo_id)
        fdo = vault.get(fdo_id)
        if not fdo:
            return
        for sp in fdo.source_paths:
            repo, path, sp_type = _normalize_source_path(sp)
            if type_filter and sp_type != type_filter:
                continue
            if repo and path:
                all_paths.append((repo, path, sp_type, fdo_id))
        if d < depth:
            for rel_id in fdo.related:
                collect(rel_id, d + 1)

    collect(root_fdo.id, 0)

    if not all_paths:
        return _json({
            "root": root_fdo.id,
            "pattern": pattern,
            "error": "No source_paths found",
            "hint": "This FDO has no source_paths, or none match the type_filter",
        })

    # Compile search pattern
    try:
        pat = re.compile(re.escape(pattern), re.IGNORECASE)
    except re.error as e:
        return _json({"error": f"Invalid pattern: {e}"})

    matches: list[dict] = []
    files_searched = 0
    files_with_matches = 0

    for repo, rel_path, sp_type, from_fdo in all_paths:
        if len(matches) >= max_matches:
            break

        target, err = _validate_workspace_path(repo, rel_path)
        if err or target is None:
            continue

        # If directory, search files within it (one level)
        if target.is_dir():
            file_targets = [
                f for f in sorted(target.iterdir())
                if f.is_file()
                and f.stat().st_size < 500_000
                and f.suffix in _SOURCE_EXTENSIONS
            ]
        elif target.is_file() and target.stat().st_size < 500_000:
            file_targets = [target]
        else:
            continue

        for fpath in file_targets:
            if len(matches) >= max_matches:
                break
            files_searched += 1
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            lines = text.splitlines()
            file_hits: list[dict] = []
            for i, line in enumerate(lines):
                if pat.search(line):
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    file_hits.append({
                        "line": i + 1,
                        "context": "\n".join(lines[start:end]),
                    })
                    if len(file_hits) >= 20:  # cap per file
                        break

            if file_hits:
                files_with_matches += 1
                rel = str(fpath.relative_to(workspace))
                matches.append({
                    "file": rel.replace("\\", "/"),
                    "from_fdo": from_fdo,
                    "type": sp_type,
                    "hits": file_hits,
                })

    total_hits = sum(len(m["hits"]) for m in matches)

    return _json({
        "root": root_fdo.id,
        "root_title": root_fdo.title,
        "pattern": pattern,
        "depth": depth,
        "files_searched": files_searched,
        "files_with_matches": files_with_matches,
        "total_hits": total_hits,
        "truncated": len(matches) >= max_matches,
        "matches": matches,
    })


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
    scope = args.get("scope", "all")
    result = vault.graph_neighbors(args["id"], depth, scope=scope)
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


# ── Notes handlers ──────────────────────────────────────────────────────────

_notes_locks: dict[str, threading.Lock] = {}
_notes_locks_guard = threading.Lock()


def _get_notes_lock(month_str: str) -> threading.Lock:
    """Get or create a per-month lock for note append operations."""
    with _notes_locks_guard:
        if month_str not in _notes_locks:
            _notes_locks[month_str] = threading.Lock()
        return _notes_locks[month_str]


def handle_note_append(args: dict) -> str:
    """Append a timestamped note entry to the current month's rolling log."""
    from .fileutil import atomic_write

    title = args["title"]
    body = args["body"]
    tags = args.get("tags", [])
    related = args.get("related", [])
    source_paths = args.get("source_paths", [])

    now = datetime.now()
    month_str = now.strftime("%Y-%m")
    notes_dir = Path(vault.vault_path) / "notes"
    month_file = notes_dir / f"notes-{month_str}.md"
    anchor = f"note-{now.strftime('%Y%m%d-%H%M%S')}"

    # Build the entry block (can be done outside lock)
    entry_lines = [
        f"## {title}",
        f"<!-- anchor: {anchor} -->",
        f"**Date**: {now.strftime('%Y-%m-%d %H:%M')}",
        f"**Tags**: {', '.join(tags)}",
    ]
    if related:
        entry_lines.append(f"**Related**: {', '.join(f'[[{r}]]' for r in related)}")
    if source_paths:
        entry_lines.append(f"**Sources**: {', '.join(source_paths)}")
    entry_lines.append("")
    entry_lines.append(body)
    entry_lines.append("")
    entry_lines.append("---")
    entry_lines.append("")

    entry_text = "\n".join(entry_lines)

    # Per-month lock protects the read-modify-write cycle
    lock = _get_notes_lock(month_str)
    with lock:
        # Create monthly file with FDO frontmatter if it doesn't exist
        if not month_file.exists():
            notes_dir.mkdir(parents=True, exist_ok=True)
            month_name = now.strftime("%B %Y")
            today_str = now.strftime("%Y-%m-%d")
            header = (
                f"---\n"
                f"id: notes-{month_str}\n"
                f"title: \"Notes \\u2014 {month_name}\"\n"
                f"domain: notes\n"
                f"created: '{today_str}'\n"
                f"updated: '{today_str}'\n"
                f"status: developing\n"
                f"confidence: 0.9\n"
                f"confidence_basis: Rolling log of quick notes and fixes\n"
                f"tags: [notes, rolling-log, {month_str}]\n"
                f"related: []\n"
                f"source_repos: []\n"
                f"---\n\n"
                f"# Notes \\u2014 {month_name}\n\n"
            )
            atomic_write(month_file, header)

        # Read existing content and update the 'updated' date in frontmatter
        content = month_file.read_text(encoding="utf-8")
        today_str = now.strftime("%Y-%m-%d")
        content = re.sub(
            r"updated: '[^']*'",
            f"updated: '{today_str}'",
            content,
            count=1,
        )
        content += entry_text
        atomic_write(month_file, content)

    # Re-index so search picks up new content (outside lock)
    fdo = vault._parse_file(month_file)
    if fdo:
        vault.index[fdo.id] = fdo
        search_engine.index_fdo(fdo)

    return _json({
        "appended": anchor,
        "file": str(month_file),
        "month": month_str,
        "title": title,
    })


def _parse_note_entries(file_path: Path) -> list[dict]:
    """Parse individual note entries from a rolling log file."""
    if not file_path.exists():
        return []

    content = file_path.read_text(encoding="utf-8")

    # Strip YAML frontmatter
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3:].strip()

    # Split on ## headings (note entries)
    entries = []
    parts = re.split(r"^## ", content, flags=re.MULTILINE)

    for part in parts[1:]:  # skip preamble before first ##
        lines = part.strip().split("\n")
        if not lines:
            continue

        title = lines[0].strip()
        entry: dict[str, Any] = {"title": title}

        # Parse metadata lines
        body_lines: list[str] = []
        in_body = False

        for line in lines[1:]:
            stripped = line.strip()
            if stripped.startswith("<!-- anchor:"):
                match = re.search(r"anchor:\s*([\w-]+)", stripped)
                if match:
                    entry["anchor"] = match.group(1)
            elif stripped.startswith("**Date**:") and not in_body:
                entry["date"] = stripped.replace("**Date**:", "").strip()
            elif stripped.startswith("**Tags**:") and not in_body:
                tag_str = stripped.replace("**Tags**:", "").strip()
                entry["tags"] = [t.strip() for t in tag_str.split(",") if t.strip()]
            elif stripped.startswith("**Related**:") and not in_body:
                pass  # skip for now
            elif stripped.startswith("**Sources**:") and not in_body:
                pass  # skip for now
            elif stripped == "---":
                break  # entry separator
            else:
                if stripped or in_body:
                    in_body = True
                    body_lines.append(line)

        entry["body"] = "\n".join(body_lines).strip()
        entries.append(entry)

    return entries


def handle_notes_recent(args: dict) -> str:
    """Get recent note entries from rolling logs."""
    days = args.get("days", 30)
    filter_tags = args.get("tags", [])
    max_entries = args.get("max_entries", 10)

    notes_dir = Path(vault.vault_path) / "notes"
    if not notes_dir.exists():
        return _json({"entries": [], "total": 0})

    # Determine which monthly files to read (current + previous months)
    now = datetime.now()
    months_to_check: list[str] = []
    for i in range(min((days // 28) + 2, 6)):  # at most 6 months back
        month_offset = now.month - i
        year_offset = now.year
        while month_offset <= 0:
            month_offset += 12
            year_offset -= 1
        months_to_check.append(f"{year_offset:04d}-{month_offset:02d}")

    # Parse entries from all relevant monthly files
    all_entries: list[dict] = []
    cutoff = now.strftime("%Y-%m-%d")
    from datetime import timedelta
    cutoff_date = (now - timedelta(days=days)).strftime("%Y-%m-%d")

    for month_str in months_to_check:
        month_file = notes_dir / f"notes-{month_str}.md"
        entries = _parse_note_entries(month_file)
        for entry in entries:
            entry["month"] = month_str
            # Filter by date
            entry_date = entry.get("date", "")[:10]  # "2026-03-01 14:30" → "2026-03-01"
            if entry_date and entry_date >= cutoff_date:
                # Filter by tags (OR logic)
                if filter_tags:
                    entry_tags = set(entry.get("tags", []))
                    if not entry_tags.intersection(filter_tags):
                        continue
                all_entries.append(entry)

    # Sort by date descending (most recent first)
    all_entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    all_entries = all_entries[:max_entries]

    return _json({"entries": all_entries, "total": len(all_entries)})


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


# ── Task management handlers ─────────────────────────────────────────────────

def handle_task_create(args: dict) -> str:
    item_type = args.get("type") or "story"
    title = (args.get("title") or "").strip()
    if not title:
        return _json({"error": "title required"})

    if item_type == "story":
        feat_id = (args.get("feat_id") or "").strip()
        if not feat_id:
            return _json({"error": "feat_id required for stories"})

        # Pre-creation validation (non-blocking warnings)
        est = float(args.get("estimate_days") or 1.0)
        warnings = task_engine.validate_story_creation(
            feat_id=feat_id, title=title, estimate_days=est,
        )

        result = task_engine.create_story(
            feat_id=feat_id,
            title=title,
            priority=args.get("priority") or "medium",
            estimate_days=est,
            description=args.get("description") or "",
            acceptance_criteria=args.get("acceptance_criteria"),
            tags=args.get("tags"),
            created_by=args.get("created_by") or "human",
            status=args.get("status") or "new",
        )
        if warnings and not result.get("error"):
            result["warnings"] = warnings
        return _json(result)
    elif item_type == "task":
        story_id = (args.get("story_id") or "").strip()
        if not story_id:
            return _json({"error": "story_id required for tasks"})
        return _json(task_engine.create_task(
            story_id=story_id,
            title=title,
            estimate_days=float(args.get("estimate_days") or 0.5),
            assignee=args.get("assignee") or "",
            notes=args.get("notes") or "",
            created_by=args.get("created_by") or "human",
        ))
    else:
        return _json({"error": f"Invalid type: {item_type}", "valid": ["story", "task"]})


def handle_task_update(args: dict) -> str:
    item_id = (args.get("item_id") or "").strip()
    if not item_id:
        return _json({"error": "item_id required"})
    fields = args.get("fields") or {}
    if not fields:
        return _json({"error": "fields required"})
    return _json(task_engine.update_item(item_id, fields))


def handle_task_get(args: dict) -> str:
    item_id = (args.get("item_id") or "").strip()
    if not item_id:
        return _json({"error": "item_id required"})
    item = task_engine.get_item(item_id)
    if not item:
        return _json({"error": f"Item not found: {item_id}"})
    return _json(item)


def handle_task_list(args: dict) -> str:
    items = task_engine.list_items(
        project_id=args.get("project_id"),
        feat_id=args.get("feat_id"),
        status=args.get("status"),
        priority=args.get("priority"),
    )
    return _json({"stories": items, "count": len(items)})


def handle_task_move(args: dict) -> str:
    story_id = (args.get("story_id") or "").strip()
    if not story_id:
        return _json({"error": "story_id required"})
    column = (args.get("column") or "").strip()
    if not column:
        return _json({"error": "column required"})
    return _json(board_engine.move_story(story_id, column))


def handle_task_archive(args: dict) -> str:
    result = task_engine.archive_closed(feat_id=args.get("feat_id"))
    # Remove archived stories from the board using BoardEngine's lock-safe method
    if result.get("archived", 0) > 0:
        removed = board_engine.cleanup_archived()
        result["removed_from_board"] = removed
    return _json(result)


def handle_board_view(args: dict) -> str:
    return _json(board_engine.board_view(project_id=args.get("project_id")))


def handle_backlog_view(args: dict) -> str:
    return _json(board_engine.backlog_view(
        project_id=args.get("project_id"),
        feat_id=args.get("feat_id"),
        priority=args.get("priority"),
    ))


def handle_calendar_view(args: dict) -> str:
    start_date = (args.get("start_date") or "").strip()
    end_date = (args.get("end_date") or "").strip()
    if not start_date or not end_date:
        return _json({"error": "start_date and end_date required"})
    return _json(calendar_engine.calendar_view(
        start_date=start_date,
        end_date=end_date,
        include_personal=args.get("include_personal", True),
    ))


def handle_calendar_add(args: dict) -> str:
    title = (args.get("title") or "").strip()
    event_date = (args.get("date") or "").strip()
    if not title or not event_date:
        return _json({"error": "title and date required"})
    return _json(calendar_engine.add_personal(
        title=title,
        event_date=event_date,
        time=args.get("time"),
        duration_hours=args.get("duration_hours"),
        recurring=args.get("recurring", False),
        notes=args.get("notes", ""),
    ))


def handle_calendar_update(args: dict) -> str:
    event_id = (args.get("event_id") or "").strip()
    if not event_id:
        return _json({"error": "event_id required"})
    action = args.get("action") or "update"
    if action == "delete":
        return _json(calendar_engine.delete_personal(event_id))
    elif action == "update":
        fields = args.get("fields") or {}
        if not fields:
            return _json({"error": "fields required for update"})
        return _json(calendar_engine.update_personal(event_id, fields))
    else:
        return _json({"error": f"Invalid action: {action}", "valid": ["update", "delete"]})


def handle_calendar_sync(args: dict) -> str:
    return _json(calendar_engine.sync_schedule(start_date=args.get("start_date")))


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
    "kronos_read_source": handle_read_source,
    "kronos_search_source": handle_search_source,
    # Memory tools
    "kronos_memory_read": handle_memory_read,
    "kronos_memory_update": handle_memory_update,
    "kronos_memory_sections": handle_memory_sections,
    # System tools
    "kronos_tool_groups": handle_tool_groups,
    # Task management tools
    "kronos_task_create": handle_task_create,
    "kronos_task_update": handle_task_update,
    "kronos_task_get": handle_task_get,
    "kronos_task_list": handle_task_list,
    "kronos_task_move": handle_task_move,
    "kronos_task_archive": handle_task_archive,
    "kronos_board_view": handle_board_view,
    "kronos_backlog_view": handle_backlog_view,
    "kronos_calendar_view": handle_calendar_view,
    "kronos_calendar_add": handle_calendar_add,
    "kronos_calendar_update": handle_calendar_update,
    "kronos_calendar_sync": handle_calendar_sync,
    # Notes tools
    "kronos_note_append": handle_note_append,
    "kronos_notes_recent": handle_notes_recent,
}

# Per-tool timeout tiers (seconds). Fast reads get short timeouts,
# search/semantic tools get longer ones to avoid premature cancellation.
TOOL_TIMEOUTS: dict[str, float] = {
    # Fast reads (10s)
    "kronos_get": 10, "kronos_list": 10, "kronos_tags": 10,
    "kronos_memory_read": 10, "kronos_memory_sections": 10,
    "kronos_tool_groups": 10, "kronos_skills": 10, "kronos_skill_load": 10,
    "kronos_task_get": 10, "kronos_task_list": 10,
    "kronos_board_view": 10, "kronos_backlog_view": 10, "kronos_calendar_view": 10,
    # Writes (15s)
    "kronos_create": 15, "kronos_update": 15, "kronos_memory_update": 15,
    "kronos_task_create": 15, "kronos_task_update": 15, "kronos_task_move": 15,
    "kronos_task_archive": 15, "kronos_calendar_add": 15,
    "kronos_calendar_update": 15, "kronos_calendar_sync": 15, "kronos_note_append": 15,
    # Search / index-heavy (45s)
    "kronos_search": 45, "kronos_graph": 45, "kronos_validate": 45,
    "kronos_deep_dive": 45, "kronos_search_source": 30,
    # Other (15s)
    "kronos_navigate": 15, "kronos_read_source": 15, "kronos_notes_recent": 15,
}
DEFAULT_TIMEOUT: float = 30.0


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
        timeout = TOOL_TIMEOUTS.get(name, DEFAULT_TIMEOUT)
        result = await asyncio.wait_for(
            asyncio.to_thread(handler, arguments),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        timeout = TOOL_TIMEOUTS.get(name, DEFAULT_TIMEOUT)
        logger.error(f"Tool {name} timed out after {timeout}s")
        return [TextContent(type="text", text=_json({"error": f"{name} timed out after {timeout}s"}))]
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}", exc_info=True)
        return [TextContent(type="text", text=_json({"error": str(e)}))]

    # ── Cache write / invalidation ────────────────────────────────────────────
    if name in WRITE_TOOLS or name in MEMORY_WRITE_TOOLS or name in TASK_WRITE_TOOLS:
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

    # Set a larger thread pool to prevent starvation when slow tools
    # (semantic indexing, vault scan) occupy threads for 30-60s+.
    # Default is min(32, cpu+4) which can be as low as 8 on 4-core machines.
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=32))

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
