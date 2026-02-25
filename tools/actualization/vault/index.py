"""
VaultIndex — Live searchable registry of all FDOs in the Kronos vault.

This is the knowledge graph's memory during ingestion.
Before actualizing any chunk, the graph searches this index
to find what already exists, preventing duplicates and creating links.

The index is updated live: when chunk #50 processes, it sees FDOs from #1-49.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None


def parse_yaml_frontmatter(text: str) -> Optional[Dict]:
    """Parse YAML frontmatter from markdown text."""
    fm_match = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    if not fm_match:
        return None

    raw = fm_match.group(1)
    if yaml:
        try:
            return yaml.safe_load(raw)
        except Exception:
            pass

    # Regex fallback
    result = {}
    for line in raw.split('\n'):
        m = re.match(r'^(\w[\w_-]*)\s*:\s*(.+)$', line.strip())
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith('[') and val.endswith(']'):
                items = [x.strip().strip('"').strip("'") for x in val[1:-1].split(',')]
                result[key] = [x for x in items if x]
            elif val.startswith('"') and val.endswith('"'):
                result[key] = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                result[key] = val[1:-1]
            else:
                try:
                    result[key] = float(val)
                except ValueError:
                    result[key] = val
    return result if result else None


class VaultIndex:
    """
    In-memory index of every FDO in the vault.

    Supports:
    - Exact tag matching
    - Substring matching
    - Fuzzy title matching (SequenceMatcher)
    - Summary keyword search
    - Live registration of new FDOs during ingestion
    """

    def __init__(self, vault_path: Path):
        self.vault = vault_path
        self.entries: Dict[str, Dict[str, Any]] = {}
        self._concept_index: Dict[str, List[str]] = {}  # term → [fdo_ids]
        self._built = False

    def build(self) -> int:
        """Scan all .md files in vault, parse frontmatter, build index."""
        count = 0
        for md_file in self.vault.rglob("*.md"):
            rel = str(md_file.relative_to(self.vault))
            if rel.startswith("templates") or rel.startswith("."):
                continue

            entry = self._parse_fdo(md_file)
            if entry:
                self.entries[entry["id"]] = entry
                self._index_entry(entry)
                count += 1

        self._built = True
        return count

    def _parse_fdo(self, path: Path) -> Optional[Dict[str, Any]]:
        """Extract frontmatter and summary from an FDO file."""
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None

        fm = parse_yaml_frontmatter(text)
        if not fm or not isinstance(fm, dict) or "id" not in fm:
            return None

        body = re.sub(r'^---.*?---\s*', '', text, count=1, flags=re.DOTALL).strip()
        summary = ""
        sum_match = re.search(r'## Summary\s*\n\s*(.*?)(?=\n##|\Z)', body, re.DOTALL)
        if sum_match:
            summary = sum_match.group(1).strip()[:300]

        tags = fm.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]

        related = fm.get("related", [])
        if isinstance(related, str):
            related = [r.strip() for r in related.split(",")]

        return {
            "id": fm["id"],
            "title": fm.get("title", ""),
            "domain": fm.get("domain", ""),
            "status": fm.get("status", ""),
            "confidence": fm.get("confidence", 0.0),
            "summary": summary,
            "tags": tags,
            "related": related,
            "concepts": fm.get("pac_children", []) or [],
            "source_repos": fm.get("source_repos", []) or [],
            "path": str(path.relative_to(self.vault)).replace("\\", "/"),
        }

    def _index_entry(self, entry: Dict[str, Any]):
        """Add an entry's terms to the concept index."""
        fid = entry["id"]
        for term in entry.get("tags", []) + entry.get("concepts", []):
            tl = term.lower().strip()
            if tl:
                self._concept_index.setdefault(tl, []).append(fid)
        # Index title words (4+ chars)
        for word in re.findall(r'\w{4,}', entry.get("title", "").lower()):
            self._concept_index.setdefault(word, []).append(fid)

    def search(self, concepts: List[str], limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for FDOs matching given concepts.
        Uses exact match, substring match, and fuzzy title matching.
        Returns results sorted by match score (highest first).
        """
        if not concepts:
            return []

        scores: Dict[str, float] = {}

        for concept in concepts:
            cl = concept.lower().strip()
            if not cl:
                continue

            # Exact tag/concept match (strongest signal)
            for term, fdo_ids in self._concept_index.items():
                if cl == term:
                    for fid in fdo_ids:
                        scores[fid] = scores.get(fid, 0) + 1.0
                elif cl in term or term in cl:
                    for fid in fdo_ids:
                        scores[fid] = scores.get(fid, 0) + 0.5

            # Fuzzy title + summary keyword match
            for fid, entry in self.entries.items():
                title = entry.get("title", "").lower()
                ratio = SequenceMatcher(None, cl, title).ratio()
                if ratio > 0.5:
                    scores[fid] = scores.get(fid, 0) + ratio * 0.8
                if cl in entry.get("summary", "").lower():
                    scores[fid] = scores.get(fid, 0) + 0.3

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
        results = []
        for fid, score in ranked:
            entry = self.entries[fid].copy()
            entry["match_score"] = round(score, 3)
            results.append(entry)
        return results

    def register(self, entry: Dict[str, Any]):
        """Register a newly created FDO so future chunks find it."""
        fid = entry["id"]
        self.entries[fid] = entry
        self._index_entry(entry)

    def has(self, fdo_id: str) -> bool:
        return fdo_id in self.entries

    def get(self, fdo_id: str) -> Optional[Dict[str, Any]]:
        return self.entries.get(fdo_id)

    def format_for_prompt(self, matches: List[Dict[str, Any]], max_entries: int = 8) -> str:
        """Format vault matches as context for Claude prompts."""
        if not matches:
            return "No existing vault entries match this content."

        lines = []
        for m in matches[:max_entries]:
            lines.append(
                f"- **[[{m['id']}]]** ({m['domain']}, {m['status']}, "
                f"score={m['match_score']}) — {m['title']}\n"
                f"  {m.get('summary', '')[:150]}"
            )
        return "\n".join(lines)

    @property
    def count(self) -> int:
        return len(self.entries)
