"""Codebase Agent — spatial awareness of workspace repos (read-only).

Phase 3 of v0.0.6: the Codebase Agent has rich read access to all repos
in the workspace via MCP source navigation tools, file reading tools, and
git read tools. It understands directory structures via meta.yaml, can
search source files linked to FDOs, and track repo changes over time.

Trust boundary: ALL tools are read-only. Code changes go through the execution pool.
"""
from __future__ import annotations

import logging

import yaml

from core.agents.base import BaseAgent
from core.config import GrimConfig
from core.tools.kronos_read import COMPANION_TOOLS
from core.tools.kronos_source import SOURCE_ALL_TOOLS
from core.tools.workspace import FILE_READ_TOOLS, GIT_READ_TOOLS

logger = logging.getLogger(__name__)


class CodebaseAgent(BaseAgent):
    """Agent for codebase navigation, deep indexing, and spatial awareness."""

    agent_name = "codebase"
    agent_display_name = "Codebase"
    agent_role = "spatial_awareness"
    agent_description = "Read-only repo navigation — code structure, meta.yaml, source tracing, git history"
    agent_color = "#06b6d4"

    protocol_priority = ["repo-navigate", "deep-ingest", "fdo-source-validate"]
    default_protocol = (
        "You are a codebase agent with deep spatial awareness of the workspace.\n"
        "You have read-only access to all repos — you CANNOT write files or run code.\n\n"
        "Source navigation tools (MCP):\n"
        "- kronos_navigate: traverse directory structures via meta.yaml metadata\n"
        "- kronos_read_source: read file contents with pagination\n"
        "- kronos_search_source: grep across FDO-linked source files\n"
        "- kronos_deep_dive: gather all source paths for a concept\n\n"
        "Repo-aware git tools:\n"
        "- git_log_repo: git log for a specific repo\n"
        "- git_diff_repo: git diff for a specific repo\n\n"
        "Indexing tools:\n"
        "- deep_index_repo: build comprehensive understanding of a repo\n"
        "- repo_changes_since: detect changes since a date\n\n"
        "Workspace tools:\n"
        "- read_file, list_directory, search_files, grep_workspace: low-level file ops\n"
        "- git_status, git_diff, git_log: standard git reads\n\n"
        "Vault tools:\n"
        "- kronos_search, kronos_get, kronos_list: knowledge graph queries\n\n"
        "Approach:\n"
        "1. Start with kronos_navigate for structural overview (meta.yaml)\n"
        "2. Use kronos_search to find relevant FDOs for context\n"
        "3. Use kronos_read_source or read_file for specific file contents\n"
        "4. Use kronos_search_source to find patterns across FDO source files\n"
        "5. Use git tools for change history and recent activity\n\n"
        "Always execute the task — do not say you can't do something "
        "if you have a tool that can do it."
    )

    def __init__(self, config: GrimConfig) -> None:
        tools = (
            list(SOURCE_ALL_TOOLS)
            + list(COMPANION_TOOLS)
            + list(FILE_READ_TOOLS)
            + list(GIT_READ_TOOLS)
        )
        super().__init__(config=config, tools=tools)
        self._config = config

    def build_context(self, state: dict) -> dict:
        """Codebase agent gets repo manifest + FDO context."""
        context = {}

        # Inject repo manifest so agent knows which repos are available
        repos_info = self._load_repos_manifest()
        if repos_info:
            context["workspace_repos"] = repos_info

        # Include knowledge context if available
        knowledge_context = state.get("knowledge_context", [])
        if knowledge_context:
            fdo_details = []
            for fdo in knowledge_context[:8]:
                detail = f"{fdo.id} ({fdo.domain}, {fdo.status}): {fdo.summary[:150]}"
                if fdo.related:
                    detail += f" → related: {', '.join(fdo.related[:3])}"
                fdo_details.append(detail)
            context["relevant_knowledge"] = "\n".join(fdo_details)

        return context

    def _load_repos_manifest(self) -> str:
        """Load repos.yaml and return a summary string for the agent."""
        manifest_path = self._config.workspace_root / self._config.repos_manifest
        if not manifest_path.exists():
            return ""

        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            repos = raw.get("repos", [])
            lines = ["Available workspace repos:"]
            for r in repos:
                name = r.get("name", "?")
                desc = r.get("description", "")
                tier = r.get("tier", "")
                path = r.get("path", name)
                lines.append(f"  - {name} ({tier}): {desc} [path: {path}]")
            return "\n".join(lines)
        except Exception as e:
            logger.warning("Failed to load repos manifest: %s", e)
            return ""


def make_codebase_agent(config: GrimConfig):
    """Create a Codebase Agent callable for the dispatch node."""
    return CodebaseAgent.make_callable(config)


# Discovery attributes for AgentRegistry
__agent_name__ = "codebase"
__make_agent__ = make_codebase_agent
__agent_class__ = CodebaseAgent
