"""GroundTruthLoader — read FDO facts directly from vault filesystem.

Provides domain accuracy verification without going through GRIM's MCP.
Reads FDO markdown files directly, extracts key facts, formulas, and
relationships for comparison against GRIM's responses.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class FDOFacts:
    """Extracted facts from a single FDO."""

    id: str
    title: str
    domain: str
    status: str = ""
    confidence: float = 0.0
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    key_facts: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)


class GroundTruthLoader:
    """Load FDO facts directly from the vault filesystem.

    No MCP dependency — pure filesystem reads for eval independence.

    Usage:
        loader = GroundTruthLoader(Path("kronos-vault"))
        facts = loader.load_fdo("pac-comprehensive")
        all_facts = loader.load_fdos(["pac-comprehensive", "sec-derivation"])
    """

    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    def load_fdo(self, fdo_id: str) -> FDOFacts | None:
        """Load a single FDO and extract facts."""
        # Search across domain directories
        for md_path in self.vault_path.rglob(f"{fdo_id}.md"):
            return self._parse_fdo(md_path, fdo_id)

        logger.warning("FDO not found: %s", fdo_id)
        return None

    def load_fdos(self, fdo_ids: list[str]) -> dict[str, FDOFacts]:
        """Load multiple FDOs and return a dict of facts."""
        result: dict[str, FDOFacts] = {}
        for fdo_id in fdo_ids:
            facts = self.load_fdo(fdo_id)
            if facts:
                result[fdo_id] = facts
        return result

    def _parse_fdo(self, path: Path, fdo_id: str) -> FDOFacts:
        """Parse an FDO markdown file into FDOFacts."""
        content = path.read_text(encoding="utf-8")

        # Split frontmatter and body
        frontmatter, body = self._split_frontmatter(content)

        # Extract from frontmatter
        title = frontmatter.get("title", fdo_id)
        domain = frontmatter.get("domain", "unknown")
        status = frontmatter.get("status", "")
        confidence = float(frontmatter.get("confidence", 0.0))
        tags = frontmatter.get("tags", [])
        related = frontmatter.get("related", [])

        # Extract summary section
        summary = self._extract_section(body, "Summary")

        # Extract key facts from body
        key_facts = self._extract_facts(body)

        return FDOFacts(
            id=fdo_id,
            title=title,
            domain=domain,
            status=status,
            confidence=confidence,
            tags=tags or [],
            summary=summary,
            key_facts=key_facts,
            related=related or [],
        )

    def _split_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        """Split YAML frontmatter from markdown body."""
        if not content.startswith("---"):
            return {}, content

        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}, content

        try:
            frontmatter = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            frontmatter = {}

        body = parts[2]
        return frontmatter, body

    def _extract_section(self, body: str, heading: str) -> str:
        """Extract content under a ## heading."""
        pattern = rf"##\s+{re.escape(heading)}\s*\n(.*?)(?=\n##|\Z)"
        match = re.search(pattern, body, re.DOTALL)
        if match:
            return match.group(1).strip()
        return ""

    def _extract_facts(self, body: str) -> list[str]:
        """Extract key facts from the body text.

        Looks for:
        - Bullet points in Summary and Details sections
        - Equations/formulas (lines with = signs)
        - Key-value patterns
        """
        facts: list[str] = []

        # Bullet points from Summary and Details
        for section in ("Summary", "Details"):
            text = self._extract_section(body, section)
            for line in text.split("\n"):
                line = line.strip()
                if line.startswith(("- ", "* ", "• ")):
                    fact = line.lstrip("-*• ").strip()
                    if len(fact) > 10:  # skip trivial bullets
                        facts.append(fact)

        # Equations (lines with mathematical symbols)
        for line in body.split("\n"):
            line = line.strip()
            if "=" in line and any(c in line for c in "αβγδΩπ∞∇") and len(line) > 5:
                facts.append(line)

        return facts
