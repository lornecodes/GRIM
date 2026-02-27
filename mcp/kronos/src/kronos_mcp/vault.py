"""
Kronos Vault Engine — filesystem-native FDO reader/writer.

Works directly on markdown files with YAML frontmatter.
No Obsidian dependency — pure filesystem operations.
"""

from __future__ import annotations

import logging
import os
import re
import yaml
import glob
from pathlib import Path
from dataclasses import dataclass, field
from datetime import date
from typing import Any

logger = logging.getLogger("kronos-mcp.vault")


# ── FDO data model ──────────────────────────────────────────────────────────

REQUIRED_FIELDS = {"id", "title", "domain", "created", "updated", "status", "confidence", "related", "source_repos", "tags"}
VALID_DOMAINS = {"physics", "ai-systems", "tools", "personal", "modelling", "computing", "projects", "people", "interests", "notes", "media", "journal"}
VALID_STATUSES = {"seed", "developing", "stable", "archived", "validated"}


@dataclass
class FDO:
    """A Field Data Object — the atomic unit of knowledge in Kronos."""

    id: str
    title: str
    domain: str
    created: str
    updated: str
    status: str
    confidence: float
    related: list[str]
    source_repos: list[str]
    tags: list[str]
    body: str
    file_path: str

    # Optional extensions
    pac_parent: str | None = None
    pac_children: list[str] = field(default_factory=list)
    equations: list[str] = field(default_factory=list)
    falsifiable: bool | None = None
    confidence_basis: str | None = None
    superseded_by: str | None = None
    source_paths: list[dict[str, str]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    # Cached at parse time (not recomputed per access)
    _cached_summary: str | None = field(default=None, repr=False)

    @property
    def summary(self) -> str:
        """Extract the ## Summary section (cached after first access)."""
        if self._cached_summary is not None:
            return self._cached_summary
        m = re.search(r"## Summary\s*\n(.*?)(?=\n## |\Z)", self.body, re.DOTALL)
        self._cached_summary = m.group(1).strip() if m else ""
        return self._cached_summary

    @property
    def wikilinks(self) -> list[str]:
        """Extract all [[wikilinks]] from the body."""
        return re.findall(r"\[\[([^\]]+)\]\]", self.body)

    def frontmatter_dict(self) -> dict[str, Any]:
        """Serialize frontmatter back to dict."""
        d: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "domain": self.domain,
            "created": self.created,
            "updated": self.updated,
            "status": self.status,
            "confidence": self.confidence,
            "related": self.related,
            "source_repos": self.source_repos,
            "tags": self.tags,
        }
        if self.pac_parent:
            d["pac_parent"] = self.pac_parent
        if self.pac_children:
            d["pac_children"] = self.pac_children
        if self.equations:
            d["equations"] = self.equations
        if self.falsifiable is not None:
            d["falsifiable"] = self.falsifiable
        if self.confidence_basis:
            d["confidence_basis"] = self.confidence_basis
        if self.superseded_by:
            d["superseded_by"] = self.superseded_by
        if self.source_paths:
            d["source_paths"] = self.source_paths
        d.update(self.extra)
        return d

    def to_markdown(self) -> str:
        """Serialize back to full markdown with frontmatter."""
        fm = yaml.dump(self.frontmatter_dict(), default_flow_style=False, sort_keys=False, allow_unicode=True)
        return f"---\n{fm}---\n\n{self.body}"


# ── Vault engine ────────────────────────────────────────────────────────────

class VaultEngine:
    """Filesystem-native FDO vault reader/writer."""

    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path)
        if not self.vault_path.is_dir():
            raise FileNotFoundError(f"Vault not found: {vault_path}")
        self._index: dict[str, FDO] | None = None

    # ── Indexing ─────────────────────────────────────────────────────────

    def _build_index(self) -> dict[str, FDO]:
        """Scan all .md files and parse FDOs."""
        index: dict[str, FDO] = {}
        for md_path in self.vault_path.rglob("*.md"):
            fdo = self._parse_file(md_path)
            if fdo:
                # Pre-cache summary at parse time
                _ = fdo.summary
                index[fdo.id] = fdo
        return index

    @property
    def index(self) -> dict[str, FDO]:
        if self._index is None:
            self._index = self._build_index()
        return self._index

    @property
    def _index_ref(self) -> dict[str, FDO]:
        """Direct mutable reference to _index dict (for SearchEngine incremental updates)."""
        return self.index

    def _ensure_index(self):
        """Ensure the index is built (for SearchEngine initial build)."""
        _ = self.index

    def refresh(self):
        """Force full re-index. Prefer SearchEngine.ensure_indexed() for incremental."""
        self._index = None

    def _parse_file(self, path: Path) -> FDO | None:
        """Parse a markdown file into an FDO, or None if it lacks valid frontmatter."""
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read {path}: {e}")
            return None

        # Extract YAML frontmatter
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
        if not m:
            return None

        try:
            fm = yaml.safe_load(m.group(1))
        except yaml.YAMLError as e:
            logger.warning(f"Invalid YAML frontmatter in {path}: {e}")
            return None

        if not isinstance(fm, dict) or "id" not in fm:
            return None

        # Check required fields
        if not REQUIRED_FIELDS.issubset(fm.keys()):
            return None

        body = m.group(2).strip()

        return FDO(
            id=fm["id"],
            title=fm["title"],
            domain=fm["domain"],
            created=str(fm["created"]),
            updated=str(fm["updated"]),
            status=fm["status"],
            confidence=float(fm.get("confidence", 0.0)),
            related=[r.strip("[]").replace("[[", "").replace("]]", "") for r in (fm.get("related", []) or [])],
            source_repos=fm.get("source_repos", []) or [],
            tags=fm.get("tags", []) or [],
            body=body,
            file_path=str(path),
            pac_parent=fm.get("pac_parent"),
            pac_children=fm.get("pac_children", []) or [],
            equations=fm.get("equations", []) or [],
            falsifiable=fm.get("falsifiable"),
            confidence_basis=fm.get("confidence_basis"),
            superseded_by=fm.get("superseded_by"),
            source_paths=fm.get("source_paths", []) or [],
            extra={k: v for k, v in fm.items() if k not in {
                "id", "title", "domain", "created", "updated", "status",
                "confidence", "related", "source_repos", "tags", "pac_parent",
                "pac_children", "equations", "falsifiable", "confidence_basis",
                "superseded_by", "source_paths",
            }},
        )

    # ── Queries ──────────────────────────────────────────────────────────

    def get(self, fdo_id: str) -> FDO | None:
        """Get FDO by ID."""
        return self.index.get(fdo_id)

    def list_domain(self, domain: str) -> list[FDO]:
        """List all FDOs in a domain."""
        return [f for f in self.index.values() if f.domain == domain]

    def list_all(self) -> list[FDO]:
        """List all FDOs."""
        return list(self.index.values())

    def search(self, query: str, max_results: int = 20) -> list[FDO]:
        """Legacy full-text search. Prefer SearchEngine.search() for hybrid results."""
        query_lower = query.lower()
        scored: list[tuple[int, FDO]] = []

        for fdo in self.index.values():
            score = 0
            if query_lower in fdo.title.lower():
                score += 10
            if any(query_lower in t.lower() for t in fdo.tags):
                score += 5
            if query_lower in fdo.id.lower():
                score += 5
            if query_lower in fdo.summary.lower():
                score += 3
            if query_lower in fdo.body.lower():
                score += 1
            if score > 0:
                scored.append((score, fdo))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [fdo for _, fdo in scored[:max_results]]

    def graph_neighbors(self, fdo_id: str, depth: int = 1) -> dict[str, Any]:
        """Get the local graph around an FDO."""
        fdo = self.get(fdo_id)
        if not fdo:
            return {"error": f"FDO not found: {fdo_id}"}

        visited: set[str] = set()
        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        def walk(current_id: str, d: int):
            if current_id in visited or d > depth:
                return
            visited.add(current_id)
            current = self.get(current_id)
            if not current:
                nodes[current_id] = {"id": current_id, "found": False}
                return
            nodes[current_id] = {
                "id": current.id,
                "title": current.title,
                "domain": current.domain,
                "status": current.status,
                "confidence": current.confidence,
            }
            # Related links
            for rel_id in current.related:
                edges.append({"from": current_id, "to": rel_id, "type": "related"})
                walk(rel_id, d + 1)
            # PAC hierarchy
            if current.pac_parent:
                edges.append({"from": current_id, "to": current.pac_parent, "type": "pac_parent"})
                walk(current.pac_parent, d + 1)
            for child_id in current.pac_children:
                edges.append({"from": current_id, "to": child_id, "type": "pac_child"})
                walk(child_id, d + 1)

        walk(fdo_id, 0)

        # Deduplicate edges
        seen_edges: set[tuple] = set()
        unique_edges = []
        for e in edges:
            key = (e["from"], e["to"], e["type"])
            if key not in seen_edges:
                seen_edges.add(key)
                unique_edges.append(e)

        return {"center": fdo_id, "nodes": nodes, "edges": unique_edges}

    # ── Validation ───────────────────────────────────────────────────────

    def validate(self) -> dict[str, Any]:
        """Run comprehensive vault validation."""
        issues: list[str] = []
        all_ids = set(self.index.keys())

        for fdo in self.index.values():
            # Domain check
            if fdo.domain not in VALID_DOMAINS:
                issues.append(f"{fdo.id}: invalid domain '{fdo.domain}'")
            # Status check
            if fdo.status not in VALID_STATUSES:
                issues.append(f"{fdo.id}: invalid status '{fdo.status}'")
            # Confidence range
            if not 0.0 <= fdo.confidence <= 1.0:
                issues.append(f"{fdo.id}: confidence {fdo.confidence} out of range")
            # Related links exist
            for rel_id in fdo.related:
                if rel_id not in all_ids:
                    issues.append(f"{fdo.id}: related '{rel_id}' not found in vault")
            # Bidirectional check
            for rel_id in fdo.related:
                other = self.get(rel_id)
                if other and fdo.id not in other.related:
                    issues.append(f"{fdo.id} → {rel_id}: not bidirectional")
            # PAC parent exists
            if fdo.pac_parent and fdo.pac_parent not in all_ids:
                issues.append(f"{fdo.id}: pac_parent '{fdo.pac_parent}' not found")
            # PAC children exist
            for child_id in fdo.pac_children:
                if child_id not in all_ids:
                    issues.append(f"{fdo.id}: pac_child '{child_id}' not found")
            # Wikilinks resolve
            for link in fdo.wikilinks:
                if link not in all_ids:
                    issues.append(f"{fdo.id}: wikilink [[{link}]] not found")
            # Orphan check
            if not fdo.related and not fdo.pac_parent and not fdo.pac_children:
                issues.append(f"{fdo.id}: orphan (no related, no PAC links)")

        return {
            "total_fdos": len(self.index),
            "domains": {d: len(self.list_domain(d)) for d in VALID_DOMAINS if self.list_domain(d)},
            "issues_count": len(issues),
            "issues": issues,
            "valid": len(issues) == 0,
        }

    # ── Write operations ─────────────────────────────────────────────────

    def write_fdo(self, fdo: FDO) -> str:
        """Write an FDO to disk. Returns the file path."""
        domain_dir = self.vault_path / fdo.domain
        domain_dir.mkdir(parents=True, exist_ok=True)
        file_path = domain_dir / f"{fdo.id}.md"
        file_path.write_text(fdo.to_markdown(), encoding="utf-8")
        fdo.file_path = str(file_path)
        # Update in-memory index in-place — no full rebuild needed.
        # If index not yet built, leave it None (will be built on next access).
        if self._index is not None:
            self._index[fdo.id] = fdo
        return str(file_path)

    def update_field(self, fdo_id: str, field_name: str, value: Any) -> FDO | None:
        """Update a single field on an FDO and rewrite it."""
        fdo = self.get(fdo_id)
        if not fdo:
            return None
        if hasattr(fdo, field_name):
            setattr(fdo, field_name, value)
        else:
            fdo.extra[field_name] = value
        fdo.updated = str(date.today())
        self.write_fdo(fdo)
        return fdo
