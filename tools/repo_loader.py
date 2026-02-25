#!/usr/bin/env python3
"""
GRIM Repo Loader — PAC-Aware Knowledge Graph Builder

Takes a repository and actualizes it into the Kronos vault as FDO notes.
Follows PAC (Potential-Actualization Conservation): the repo is potential,
the vault notes are the actualized knowledge. f(Parent) = Σ f(Children).

Per-file flow (true PAC):
    1. PARTIAL LOAD  — read file, quick-extract key concepts (lightweight)
    2. SEARCH VAULT  — find existing FDOs that match those concepts
    3. ACTUALIZE     — Claude gets file + vault matches → produces FDO
    4. CROSS-LINK    — patch existing FDOs to reference the new one back
    5. INDEX UPDATE  — register new FDO so future files find it

This prevents duplication: SEC in dawn-field-theory and SEC in fracton
both link to the same canonical concept node.

Usage:
    python repo_loader.py scan <repo_path> [--domain physics|ai-systems|tools|personal]
    python repo_loader.py sync <repo_path>
    python repo_loader.py status [<repo_path>]
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv

# Load GRIM .env
GRIM_ROOT = Path(__file__).parent.parent
load_dotenv(GRIM_ROOT / ".env")

try:
    import yaml
except ImportError:
    yaml = None

try:
    from anthropic import Anthropic
except ImportError:
    print("ERROR: anthropic package not installed. Run: pip install anthropic")
    sys.exit(1)


# =============================================================================
# Configuration
# =============================================================================

VAULT_PATH = Path(os.getenv(
    "KRONOS_VAULT_PATH",
    str(GRIM_ROOT.parent / "kronos-vault")
)).resolve()

SYNC_DIR = VAULT_PATH / ".sync"

DEFAULT_PATTERNS = {
    "*.md", "*.py", "*.yaml", "*.yml", "*.json", "*.toml",
    "*.rs", "*.ts", "*.js", "*.spec.md", "*.txt",
}

SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".egg-info", "htmlcov",
    ".pytest_cache", ".venv", "venv", ".mypy_cache", ".tox",
    "target", "dist", "build", ".obsidian", ".sync", "_site",
    "cache", ".cache", ".changelog",
}

SKIP_FILES = {
    "package-lock.json", "yarn.lock", "Cargo.lock", "poetry.lock",
    "*.pyc", "*.pyo", "*.so", "*.dll", "*.exe", "*.bin",
}

MAX_FILE_CHARS = 12_000

DOMAIN_HINTS = {
    "dawn-field-theory": "physics",
    "foundational": "physics",
    "experiments": "physics",
    "dawn-models": "ai-systems",
    "GAIA": "ai-systems",
    "grimm": "ai-systems",
    "GRIM": "ai-systems",
    "fracton": "tools",
    "cip": "tools",
    "infrastructure": "tools",
    "internal": "personal",
}


# =============================================================================
# Utilities
# =============================================================================

def slugify(text: str) -> str:
    """Convert text to kebab-case slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except Exception:
        return ""


def git_head(repo: Path) -> Optional[str]:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"],
                           cwd=repo, capture_output=True, text=True)
        return r.stdout.strip()[:12] if r.returncode == 0 else None
    except Exception:
        return None


def git_diff_files(repo: Path, base: str) -> Tuple[Set[str], Set[str], Set[str]]:
    added, modified, deleted = set(), set(), set()
    try:
        r = subprocess.run(["git", "diff", "--name-status", base, "HEAD"],
                           cwd=repo, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        if r.returncode != 0:
            return added, modified, deleted
        for line in r.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) < 2:
                continue
            s, fp = parts[0], parts[-1]
            if s.startswith('A'):
                added.add(fp)
            elif s.startswith('M'):
                modified.add(fp)
            elif s.startswith('D'):
                deleted.add(fp)
            elif s.startswith('R'):
                if len(parts) > 2:
                    deleted.add(parts[1])
                added.add(parts[-1])
    except Exception:
        pass
    return added, modified, deleted


def should_include(path: Path, repo: Path) -> bool:
    rel = path.relative_to(repo)
    for part in rel.parts[:-1]:
        if part in SKIP_DIRS:
            return False
    name = path.name
    if name.startswith('.') and name != '.env.example':
        return False
    for pattern in SKIP_FILES:
        if pattern.startswith('*'):
            if name.endswith(pattern[1:]):
                return False
        elif name == pattern:
            return False
    suffix = path.suffix.lower()
    if not suffix:
        return name in {"README", "LICENSE", "Makefile", "Dockerfile", "CITATION.cff"}
    return any(f"*{suffix}" in DEFAULT_PATTERNS or path.match(p) for p in DEFAULT_PATTERNS)


def infer_domain(repo_name: str, rel_path: str) -> str:
    check = f"{repo_name}/{rel_path}".lower()
    for hint, domain in DOMAIN_HINTS.items():
        if hint.lower() in check:
            return domain
    return "tools"


def estimate_confidence(file_path: Path, content: str) -> float:
    name = file_path.name.lower()
    suffix = file_path.suffix.lower()
    if "preprint" in str(file_path):
        return 0.7
    if ".spec.md" in name or "SPEC" in file_path.name:
        return 0.6
    if name in ("readme.md", "architecture.md", "design.md"):
        return 0.6
    if name.startswith("test_") or name.startswith("exp_"):
        return 0.5
    if suffix in (".yaml", ".yml", ".toml", ".json"):
        return 0.4
    if suffix in (".py", ".rs", ".ts", ".js"):
        return 0.4
    return 0.3


def parse_yaml_frontmatter(text: str) -> Optional[Dict]:
    """Parse YAML frontmatter from markdown text."""
    fm_match = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    if not fm_match:
        return None

    raw = fm_match.group(1)

    # Try pyyaml first, fall back to regex parsing
    if yaml:
        try:
            return yaml.safe_load(raw)
        except Exception:
            pass

    # Regex fallback for basic YAML
    result = {}
    for line in raw.split('\n'):
        m = re.match(r'^(\w[\w_-]*)\s*:\s*(.+)$', line.strip())
        if m:
            key, val = m.group(1), m.group(2).strip()
            # Handle lists like [a, b, c]
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


# =============================================================================
# Vault Index — Live Searchable Knowledge Registry
# =============================================================================

class VaultIndex:
    """
    In-memory index of every FDO in the vault.
    Used during ingestion so each new file knows what already exists.

    This is the key to PAC-aware loading:
    before actualizing a file, we search this index for concept matches,
    then pass those matches to Claude so it links instead of duplicates.
    """

    def __init__(self, vault_path: Path):
        self.vault = vault_path
        self.entries: Dict[str, Dict[str, Any]] = {}  # id → entry
        self._concept_index: Dict[str, List[str]] = {}  # term → [fdo_ids]

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
                # Index every tag and concept
                for term in entry.get("tags", []) + entry.get("concepts", []):
                    term_lower = term.lower().strip()
                    if term_lower:
                        self._concept_index.setdefault(term_lower, []).append(entry["id"])
                # Index title words (4+ chars)
                for word in re.findall(r'\w{4,}', entry.get("title", "").lower()):
                    self._concept_index.setdefault(word, []).append(entry["id"])
                count += 1
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

        # Extract summary from body
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

    def search(self, concepts: List[str], limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for FDOs matching given concepts.
        Uses exact match, substring match, and fuzzy title matching.
        """
        if not concepts:
            return []

        scores: Dict[str, float] = {}

        for concept in concepts:
            cl = concept.lower().strip()

            # Exact tag/concept match (strongest)
            for term, fdo_ids in self._concept_index.items():
                if cl == term:
                    for fid in fdo_ids:
                        scores[fid] = scores.get(fid, 0) + 1.0
                elif cl in term or term in cl:
                    for fid in fdo_ids:
                        scores[fid] = scores.get(fid, 0) + 0.5

            # Fuzzy title match + summary keyword match
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

    def register(self, fdo_id: str, entry: Dict[str, Any]):
        """Register a newly created FDO so future files can find it."""
        self.entries[fdo_id] = entry
        for term in entry.get("tags", []) + entry.get("concepts", []):
            tl = term.lower().strip()
            if tl:
                self._concept_index.setdefault(tl, []).append(fdo_id)
        for word in re.findall(r'\w{4,}', entry.get("title", "").lower()):
            self._concept_index.setdefault(word, []).append(fdo_id)

    def format_for_prompt(self, matches: List[Dict[str, Any]], max_entries: int = 8) -> str:
        """Format vault matches as context for Claude."""
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


# =============================================================================
# FDO Builder
# =============================================================================

class FDO:
    """Field Data Object — the atomic unit of knowledge in Kronos."""

    def __init__(
        self,
        id: str,
        title: str,
        domain: str,
        summary: str = "",
        details: str = "",
        connections: str = "",
        open_questions: str = "",
        references: str = "",
        status: str = "seed",
        confidence: float = 0.3,
        related: List[str] = None,
        source_repos: List[str] = None,
        tags: List[str] = None,
        pac_parent: str = None,
        pac_children: List[str] = None,
        source_path: str = None,
    ):
        self.id = id
        self.title = title
        self.domain = domain
        self.summary = summary
        self.details = details
        self.connections = connections
        self.open_questions = open_questions
        self.references = references
        self.status = status
        self.confidence = confidence
        self.related = related or []
        self.source_repos = source_repos or []
        self.tags = tags or []
        self.pac_parent = pac_parent
        self.pac_children = pac_children or []
        self.source_path = source_path

    def to_markdown(self) -> str:
        now = datetime.now().strftime("%Y-%m-%d")
        fm_lines = [
            "---",
            f"id: {self.id}",
            f'title: "{self.title}"',
            f"domain: {self.domain}",
            f"created: {now}",
            f"updated: {now}",
            f"status: {self.status}",
            f"confidence: {self.confidence}",
            f"related: [{', '.join(self.related)}]",
            f"source_repos: [{', '.join(self.source_repos)}]",
            f"tags: [{', '.join(self.tags)}]",
        ]
        if self.pac_parent:
            fm_lines.append(f"pac_parent: {self.pac_parent}")
        if self.pac_children:
            fm_lines.append(f"pac_children: [{', '.join(self.pac_children)}]")
        if self.source_path:
            fm_lines.append(f'source_path: "{self.source_path}"')
        fm_lines.append("---")

        body = f"# {self.title}\n\n"
        if self.summary:
            body += f"## Summary\n\n{self.summary}\n\n"
        if self.details:
            body += f"## Details\n\n{self.details}\n\n"
        if self.connections:
            body += f"## Connections\n\n{self.connections}\n\n"
        if self.open_questions:
            body += f"## Open Questions\n\n{self.open_questions}\n\n"
        if self.references:
            body += f"## References\n\n{self.references}\n\n"

        return "\n".join(fm_lines) + "\n\n" + body


# =============================================================================
# Claude — The Actualization Engine
# =============================================================================

class ActualizationEngine:
    """
    Three-phase per-file actualization:
      1. extract_concepts() — lightweight, ~200 output tokens
      2. actualize_file()   — full analysis with vault context
      3. cross_link()       — determine who should link back
    """

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.calls = 0

    def _call(self, prompt: str, max_tokens: int = 2000, temp: float = 0.3) -> Optional[str]:
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=max_tokens, temperature=temp,
                messages=[{"role": "user", "content": prompt}],
            )
            self.total_input_tokens += resp.usage.input_tokens
            self.total_output_tokens += resp.usage.output_tokens
            self.calls += 1
            return resp.content[0].text.strip()
        except KeyboardInterrupt:
            print("\n  INTERRUPTED")
            return None
        except Exception as e:
            print(f"\n  ERROR: Claude call failed: {e}")
            return None

    def _parse_json(self, text: str) -> Optional[Dict]:
        if not text:
            return None
        cleaned = text
        if cleaned.startswith("```"):
            cleaned = re.sub(r'^```\w*\n?', '', cleaned)
            cleaned = re.sub(r'\n?```$', '', cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None

    # ----- Phase 1: Lightweight concept extraction -----

    def extract_concepts(self, content: str, file_path: str) -> List[str]:
        """
        Quick extraction of key concepts from a file.
        Cheap call (~200 output tokens) used to search the vault
        before full actualization.
        """
        preview = content[:4000] if len(content) > 4000 else content

        prompt = f"""Extract the 5-10 most important concepts, theories, algorithms, or named entities from this file.
These will be used to search an existing knowledge base for connections.

FILE: {file_path}

<content>
{preview}
</content>

Return ONLY a JSON array of concept strings, e.g.:
["symbolic entropy collapse", "PAC conservation", "golden ratio", "cellular automata"]

Be specific — use the actual names/terms from the content, not generic words."""

        text = self._call(prompt, max_tokens=300, temp=0.1)
        result = self._parse_json(text) if text else None
        if isinstance(result, list):
            return [str(c) for c in result[:10]]

        # Fallback: headings and bold text
        concepts = re.findall(r'(?:^#+\s+(.+)$|\*\*(.+?)\*\*)', content[:3000], re.MULTILINE)
        return [c[0] or c[1] for c in concepts[:8]]

    # ----- Phase 2: Full actualization with vault context -----

    def actualize_file(
        self,
        content: str,
        file_path: str,
        repo_name: str,
        domain: str,
        parent_id: str,
        vault_context: str,
        sibling_files: List[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Full actualization of a file → FDO knowledge.
        Receives vault_context: existing FDOs matching this file's concepts.
        Claude links to existing nodes instead of creating duplicates.
        """
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + "\n\n... [truncated]"

        siblings_ctx = ""
        if sibling_files:
            siblings_ctx = f"\nOther files in this directory: {', '.join(sibling_files[:15])}"

        prompt = f"""Analyze this file and produce a structured knowledge summary.

FILE: {file_path}
DOMAIN: {domain}
REPOSITORY: {repo_name}{siblings_ctx}

<file_content>
{content}
</file_content>

EXISTING KNOWLEDGE BASE ENTRIES (link to these, don't duplicate them):
{vault_context}

Produce a JSON response:
{{
    "title": "Human-readable title for this knowledge unit",
    "summary": "1-2 paragraph overview of what this file contains and why it matters",
    "details": "Key technical details, algorithms, concepts (markdown, 3-8 paragraphs)",
    "key_concepts": ["list", "of", "main", "concepts"],
    "connections": "How this relates to other knowledge. Use [[existing-id]] wikilinks for concepts that ALREADY EXIST in the vault (listed above). Describe new connections too.",
    "existing_links": ["ids-from-vault-matches-that-are-relevant"],
    "open_questions": "Unresolved issues or areas for exploration",
    "tags": ["specific", "searchable", "tags"],
    "is_duplicate_of": null,
    "confidence_note": "How well-established this content is"
}}

CRITICAL RULES:
- If this file covers a concept that ALREADY EXISTS in the vault, set "is_duplicate_of" to that FDO's id instead of null. Don't create redundant nodes.
- Use [[existing-id]] wikilinks to reference vault entries listed above.
- For genuinely new concepts, use [[kebab-case-slug]] wikilinks (they'll be resolved later).
- Tags should be specific (not "code" or "file").
- Preserve equations, constants, thresholds.
- Focus on WHAT and WHY, not line-by-line walkthrough.

Return ONLY the JSON object."""

        text = self._call(prompt, max_tokens=2000, temp=0.3)
        result = self._parse_json(text)

        if result is None:
            return {
                "title": Path(file_path).stem.replace("_", " ").replace("-", " ").title(),
                "summary": f"Auto-ingested from {file_path}. Parsing failed.",
                "details": content[:500],
                "key_concepts": [],
                "connections": "",
                "existing_links": [],
                "open_questions": "",
                "tags": [],
                "is_duplicate_of": None,
                "confidence_note": "Needs manual review",
            }
        return result

    # ----- Phase 3: Cross-link determination -----

    def suggest_cross_links(
        self,
        new_fdo_id: str,
        new_summary: str,
        matching_fdos: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        """
        For each vault match with score >= 0.7, suggest a backlink patch.
        Returns list of {fdo_id, fdo_path, link_text}.
        """
        patches = []
        for match in matching_fdos:
            if match.get("match_score", 0) >= 0.7:
                patches.append({
                    "fdo_id": match["id"],
                    "fdo_path": match["path"],
                    "link_text": f"- [[{new_fdo_id}]] — {new_summary[:80]}",
                })
        return patches

    # ----- Directory synthesis -----

    def actualize_directory(
        self,
        dir_path: str,
        repo_name: str,
        domain: str,
        child_summaries: List[Dict[str, str]],
        vault_context: str = "",
    ) -> Dict[str, Any]:
        """PAC parent synthesis: f(Parent) = Σ f(Children)."""
        children_desc = "\n".join(
            f"- **{c['name']}**: {c['summary'][:200]}"
            for c in child_summaries[:20]
        )

        prompt = f"""You are building a knowledge hierarchy. This directory contains:

DIRECTORY: {dir_path}
REPOSITORY: {repo_name}
DOMAIN: {domain}

CHILDREN:
{children_desc}

EXISTING VAULT CONTEXT:
{vault_context or "N/A"}

PAC principle: the parent's information = sum of children's, at higher abstraction.

Return a JSON object:
{{
    "title": "Title for this knowledge cluster",
    "summary": "2-3 paragraph overview synthesizing what this directory represents",
    "details": "How children relate to each other, overall architecture/narrative",
    "connections": "Broader connections (use [[wikilinks]] to existing vault entries)",
    "tags": ["relevant", "tags"]
}}

Return ONLY the JSON object."""

        text = self._call(prompt, max_tokens=1500, temp=0.3)
        result = self._parse_json(text)
        if result is None:
            return {
                "title": Path(dir_path).name.replace("_", " ").replace("-", " ").title(),
                "summary": f"Directory containing {len(child_summaries)} items.",
                "details": children_desc,
                "connections": "",
                "tags": [],
            }
        return result

    def cost_estimate(self) -> str:
        input_cost = (self.total_input_tokens / 1_000_000) * 3.0
        output_cost = (self.total_output_tokens / 1_000_000) * 15.0
        total = input_cost + output_cost
        return (
            f"{self.calls} calls | "
            f"{self.total_input_tokens:,} in / {self.total_output_tokens:,} out | "
            f"~${total:.3f}"
        )


# =============================================================================
# Manifest — Sync State Tracking
# =============================================================================

class SyncManifest:
    """Tracks what has been ingested. Stored in .sync/{repo}.json"""

    def __init__(self, repo_name: str):
        self.repo_name = repo_name
        self.path = SYNC_DIR / f"{slugify(repo_name)}.json"
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            with open(self.path, 'r') as f:
                return json.load(f)
        return {
            "repo_name": self.repo_name,
            "repo_path": None,
            "git_commit": None,
            "last_sync": None,
            "domain": None,
            "fdo_count": 0,
            "files": {},
            "directories": {},
            "pac_tree": {},
            "cross_links": {},
        }

    def save(self):
        SYNC_DIR.mkdir(parents=True, exist_ok=True)
        self.data["last_sync"] = datetime.now().isoformat()
        with open(self.path, 'w') as f:
            json.dump(self.data, f, indent=2)

    @property
    def git_commit(self) -> Optional[str]:
        return self.data.get("git_commit")

    @git_commit.setter
    def git_commit(self, val: str):
        self.data["git_commit"] = val

    @property
    def repo_path(self) -> Optional[str]:
        return self.data.get("repo_path")

    @repo_path.setter
    def repo_path(self, val: str):
        self.data["repo_path"] = val

    @property
    def domain(self) -> Optional[str]:
        return self.data.get("domain")

    @domain.setter
    def domain(self, val: str):
        self.data["domain"] = val

    def is_tracked(self, rel_path: str) -> bool:
        return rel_path in self.data["files"]

    def file_changed(self, rel_path: str, current_hash: str) -> bool:
        entry = self.data["files"].get(rel_path)
        return not entry or entry.get("hash") != current_hash

    def track_file(self, rel_path: str, fdo_path: str, content_hash: str, fdo_id: str = None):
        self.data["files"][rel_path] = {
            "fdo_path": fdo_path, "fdo_id": fdo_id,
            "hash": content_hash, "timestamp": datetime.now().isoformat(),
        }

    def track_directory(self, dir_path: str, fdo_path: str):
        self.data["directories"][dir_path] = {"fdo_path": fdo_path}

    def track_pac(self, fdo_id: str, parent: Optional[str], children: List[str]):
        self.data["pac_tree"][fdo_id] = {"parent": parent, "children": children}

    def track_cross_link(self, from_id: str, to_id: str):
        links = self.data["cross_links"].setdefault(from_id, [])
        if to_id not in links:
            links.append(to_id)

    def remove_file(self, rel_path: str) -> Optional[str]:
        entry = self.data["files"].pop(rel_path, None)
        return entry.get("fdo_path") if entry else None

    def update_count(self):
        self.data["fdo_count"] = len(self.data["files"]) + len(self.data["directories"])


# =============================================================================
# Cross-Link Patcher
# =============================================================================

class CrossLinker:
    """Patches existing FDOs to add backlinks when a new related FDO is created."""

    def __init__(self, vault_path: Path):
        self.vault = vault_path
        self.patches_applied = 0

    def patch(self, fdo_path: str, link_text: str) -> bool:
        """Add a backlink to an existing FDO's Connections section."""
        full_path = self.vault / fdo_path
        if not full_path.exists():
            return False

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception:
            return False

        # Skip if link already exists
        if link_text.split("]]")[0] in content:
            return False

        # Find Connections section and append
        conn_match = re.search(r'(## Connections\s*\n)', content)
        if conn_match:
            insert_at = conn_match.end()
            next_section = re.search(r'\n## ', content[insert_at:])
            if next_section:
                insert_at = insert_at + next_section.start()
            else:
                insert_at = len(content)

            new_content = (
                content[:insert_at].rstrip() + "\n" + link_text + "\n\n" +
                content[insert_at:].lstrip("\n")
            )
        else:
            oq_match = re.search(r'\n## Open Questions', content)
            insert_at = oq_match.start() if oq_match else len(content)
            new_content = (
                content[:insert_at].rstrip() +
                f"\n\n## Connections\n\n{link_text}\n\n" +
                content[insert_at:].lstrip("\n")
            )

        # Update the "updated" date
        today = datetime.now().strftime("%Y-%m-%d")
        new_content = re.sub(r'updated: \d{4}-\d{2}-\d{2}', f'updated: {today}', new_content)

        full_path.write_text(new_content, encoding="utf-8")
        self.patches_applied += 1
        return True


# =============================================================================
# Repo Loader — The Main Engine
# =============================================================================

class RepoLoader:
    """
    Loads a repository into the Kronos vault as PAC-structured FDOs.

    Per-file flow (true PAC actualization):
        1. Read file content
        2. EXTRACT  — quick concept extraction (~200 tokens)
        3. SEARCH   — query vault index for matching FDOs
        4. ACTUALIZE — Claude produces FDO with vault context
        5. CROSS-LINK — patch existing FDOs to link back
        6. INDEX     — register new FDO for future files

    File #50 knows about FDOs from files #1-49.
    SEC in dawn-field-theory links to SEC in fracton.
    """

    def __init__(self, vault_path: Path = None):
        self.vault = vault_path or VAULT_PATH
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set in environment or .env")
        self.engine = ActualizationEngine(api_key=api_key)
        self.index = VaultIndex(self.vault)
        self.linker = CrossLinker(self.vault)

    def scan(
        self,
        repo_path: str,
        domain: str = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Full scan of a repository into the vault."""
        repo = Path(repo_path).resolve()
        if not repo.exists():
            return {"error": f"Repository not found: {repo_path}"}

        repo_name = repo.name
        if domain is None:
            domain = infer_domain(repo_name, "")

        print(f"\n{'='*60}")
        print(f"  KRONOS REPO LOADER — PAC Actualization")
        print(f"{'='*60}")
        print(f"  Repo:   {repo}")
        print(f"  Name:   {repo_name}")
        print(f"  Domain: {domain}")
        print(f"  Vault:  {self.vault}")
        print(f"  Commit: {git_head(repo) or 'not a git repo'}")
        print(f"{'='*60}\n")

        manifest = SyncManifest(repo_name)
        manifest.repo_path = str(repo)
        manifest.domain = domain

        # Phase 1: Build vault index
        print("[1/5] Building vault index...")
        index_count = self.index.build()
        print(f"       {index_count} existing FDOs indexed\n")

        # Phase 2: Discover files
        print("[2/5] Discovering files...")
        files = self._discover_files(repo)
        print(f"       {len(files)} files to process\n")

        if not files:
            return {"error": "No files found matching patterns"}

        # Phase 3: Build PAC hierarchy
        print("[3/5] Building PAC hierarchy...")
        tree = self._build_hierarchy(files, repo)
        dir_count = sum(1 for v in tree.values() if v["type"] == "directory")
        print(f"       {dir_count} directories, {len(files)} files\n")

        # Phase 4: Per-file actualization
        print("[4/5] Actualizing files (extract → search → actualize → link)...")
        vault_dir = self.vault / "repos" / slugify(repo_name)
        vault_dir.mkdir(parents=True, exist_ok=True)

        fdos_created = 0
        fdos_skipped_dup = 0
        cross_links_made = 0
        child_summaries_by_dir = {}

        for i, file_path in enumerate(files):
            rel = file_path.relative_to(repo)
            rel_str = str(rel).replace("\\", "/")
            dir_rel = str(rel.parent).replace("\\", "/")

            h = file_hash(file_path)
            if not force and manifest.is_tracked(rel_str) and not manifest.file_changed(rel_str, h):
                child_summaries_by_dir.setdefault(dir_rel, []).append({
                    "name": file_path.name, "summary": "(unchanged)",
                })
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                print(f"  SKIP: {rel} ({e})")
                continue

            progress = f"[{i+1}/{len(files)}]"
            print(f"  {progress} {rel_str}")

            # --- Step A: Extract concepts ---
            print(f"         extract...", end=" ", flush=True)
            concepts = self.engine.extract_concepts(content, rel_str)
            print(f"{len(concepts)} concepts", end=" ", flush=True)

            # --- Step B: Search vault ---
            print(f"→ search...", end=" ", flush=True)
            matches = self.index.search(concepts, limit=8)
            vault_context = self.index.format_for_prompt(matches)
            hit_count = len([m for m in matches if m.get("match_score", 0) > 0.3])
            print(f"{hit_count} hits", end=" ", flush=True)

            # --- Step C: Actualize ---
            print(f"→ actualize...", end=" ", flush=True)

            siblings = [f.name for f in file_path.parent.iterdir()
                       if f.is_file() and f != file_path and should_include(f, repo)]

            fdo_id = slugify(f"{repo_name}-{rel.stem}")
            parent_id = (slugify(f"{repo_name}-{rel.parent.name}")
                        if rel.parent.name != repo_name else slugify(repo_name))

            result = self.engine.actualize_file(
                content=content, file_path=rel_str, repo_name=repo_name,
                domain=domain, parent_id=parent_id,
                vault_context=vault_context, sibling_files=siblings,
            )

            if result is None:
                print("FAILED")
                continue

            # --- Duplicate check ---
            dup_of = result.get("is_duplicate_of")
            if dup_of and dup_of in self.index.entries:
                print(f"→ DUPLICATE of [[{dup_of}]], linking")
                fdos_skipped_dup += 1
                manifest.track_file(rel_str, self.index.entries[dup_of]["path"], h, fdo_id=dup_of)
                existing_entry = self.index.entries[dup_of]
                self.linker.patch(
                    existing_entry["path"],
                    f"- Also in: `{rel_str}` ({repo_name})"
                )
                child_summaries_by_dir.setdefault(dir_rel, []).append({
                    "name": file_path.name,
                    "summary": f"(duplicate of {dup_of})",
                })
                continue

            # --- Step D: Write FDO ---
            confidence = estimate_confidence(file_path, content)
            existing_links = result.get("existing_links", [])
            related = list(set(
                [slugify(c) for c in result.get("key_concepts", [])[:5]] +
                [eid for eid in existing_links if eid in self.index.entries]
            ))

            fdo = FDO(
                id=fdo_id, title=result.get("title", rel.stem), domain=domain,
                summary=result.get("summary", ""),
                details=result.get("details", ""),
                connections=result.get("connections", ""),
                open_questions=result.get("open_questions", ""),
                references=f"- Source: `{rel_str}` in [{repo_name}]",
                status="seed", confidence=confidence,
                related=related, source_repos=[repo_name],
                tags=result.get("tags", []),
                pac_parent=parent_id, pac_children=[],
                source_path=rel_str,
            )

            fdo_rel_dir = vault_dir / rel.parent
            fdo_rel_dir.mkdir(parents=True, exist_ok=True)
            fdo_file = fdo_rel_dir / f"{slugify(rel.stem)}.md"
            fdo_file.write_text(fdo.to_markdown(), encoding="utf-8")

            fdo_vault_rel = str(fdo_file.relative_to(self.vault)).replace("\\", "/")
            manifest.track_file(rel_str, fdo_vault_rel, h, fdo_id=fdo_id)
            manifest.track_pac(fdo_id, parent_id, [])

            # --- Step E: Cross-link ---
            patches = self.engine.suggest_cross_links(
                fdo_id, result.get("summary", "")[:100], matches,
            )
            for patch in patches:
                if self.linker.patch(patch["fdo_path"], patch["link_text"]):
                    manifest.track_cross_link(patch["fdo_id"], fdo_id)
                    cross_links_made += 1

            # --- Step F: Register in live index ---
            self.index.register(fdo_id, {
                "id": fdo_id, "title": result.get("title", ""),
                "domain": domain, "status": "seed", "confidence": confidence,
                "summary": result.get("summary", "")[:300],
                "tags": result.get("tags", []),
                "concepts": result.get("key_concepts", []),
                "related": related, "source_repos": [repo_name],
                "path": fdo_vault_rel,
            })

            fdos_created += 1
            links_note = f" (+{len(patches)} links)" if patches else ""
            print(f"→ {fdo_id}{links_note}")

            child_summaries_by_dir.setdefault(dir_rel, []).append({
                "name": file_path.name,
                "summary": result.get("summary", "")[:200],
            })

        # Save manifest after Phase 4 so file FDOs survive if Phase 5 crashes
        manifest.git_commit = git_head(repo)
        manifest.update_count()
        manifest.save()

        # Phase 5: Directory index FDOs (PAC parents)
        print(f"\n[5/5] Building PAC parent nodes...")
        dirs_created = 0

        sorted_dirs = sorted(child_summaries_by_dir.keys(),
                           key=lambda d: d.count('/'), reverse=True)

        for dir_rel in sorted_dirs:
            children = child_summaries_by_dir[dir_rel]
            if not children:
                continue

            dir_name = Path(dir_rel).name if dir_rel != "." else repo_name
            dir_id = slugify(f"{repo_name}-{dir_name}")
            parent_name = Path(dir_rel).parent.name if dir_rel != "." else None
            parent_id = (slugify(f"{repo_name}-{parent_name}")
                        if parent_name and parent_name != "." else None)

            print(f"  {dir_rel or '(root)'}...", end=" ", flush=True)

            dir_concepts = [dir_name.replace("-", " "), repo_name]
            dir_matches = self.index.search(dir_concepts, limit=5)
            dir_vault_ctx = self.index.format_for_prompt(dir_matches)

            result = self.engine.actualize_directory(
                dir_path=dir_rel, repo_name=repo_name, domain=domain,
                child_summaries=children, vault_context=dir_vault_ctx,
            )

            child_ids = [slugify(f"{repo_name}-{Path(c['name']).stem}") for c in children]

            fdo = FDO(
                id=dir_id,
                title=result.get("title", dir_name.replace("-", " ").title()),
                domain=domain, summary=result.get("summary", ""),
                details=result.get("details", ""),
                connections=result.get("connections", ""),
                status="seed", confidence=0.4, related=[],
                source_repos=[repo_name],
                tags=result.get("tags", []) + ["pac-parent", "index"],
                pac_parent=parent_id, pac_children=child_ids,
            )

            fdo_dir = vault_dir / dir_rel if dir_rel != "." else vault_dir
            fdo_dir.mkdir(parents=True, exist_ok=True)
            fdo_file = fdo_dir / "_index.md"
            fdo_file.write_text(fdo.to_markdown(), encoding="utf-8")

            fdo_vault_rel = str(fdo_file.relative_to(self.vault)).replace("\\", "/")
            manifest.track_directory(dir_rel, fdo_vault_rel)
            manifest.track_pac(dir_id, parent_id, child_ids)
            dirs_created += 1

            self.index.register(dir_id, {
                "id": dir_id, "title": result.get("title", dir_name),
                "domain": domain, "status": "seed", "confidence": 0.4,
                "summary": result.get("summary", "")[:300],
                "tags": result.get("tags", []), "concepts": [],
                "related": [], "source_repos": [repo_name],
                "path": fdo_vault_rel,
            })

            # Roll up
            parent_dir = str(Path(dir_rel).parent).replace("\\", "/")
            if parent_dir != dir_rel and parent_dir != ".":
                child_summaries_by_dir.setdefault(parent_dir, []).append({
                    "name": dir_name,
                    "summary": result.get("summary", "")[:200],
                })

            print(f"→ {dir_id} ({len(children)} children)")

        # Root index
        self._build_root_index(repo_name, domain, vault_dir, manifest, child_summaries_by_dir)

        manifest.git_commit = git_head(repo)
        manifest.update_count()
        manifest.save()

        print(f"\n{'='*60}")
        print(f"  ACTUALIZATION COMPLETE")
        print(f"{'='*60}")
        print(f"  FDOs created:     {fdos_created} files + {dirs_created} dirs")
        print(f"  Duplicates found: {fdos_skipped_dup} (linked, not duplicated)")
        print(f"  Cross-links:      {cross_links_made} backlinks patched")
        print(f"  Vault total:      {len(self.index.entries)} FDOs")
        print(f"  Output:           {vault_dir}")
        print(f"  API usage:        {self.engine.cost_estimate()}")
        print(f"  Git commit:       {manifest.git_commit or 'N/A'}")
        print(f"{'='*60}\n")

        return {
            "success": True, "repo": repo_name,
            "fdos_created": fdos_created, "fdos_skipped_dup": fdos_skipped_dup,
            "cross_links": cross_links_made, "dirs_created": dirs_created,
            "vault_dir": str(vault_dir),
            "api_usage": self.engine.cost_estimate(),
        }

    def sync(self, repo_path: str) -> Dict[str, Any]:
        """Incremental sync — same extract→search→actualize→link flow, only changed files."""
        repo = Path(repo_path).resolve()
        repo_name = repo.name
        manifest = SyncManifest(repo_name)

        if not manifest.git_commit:
            print(f"No previous sync for '{repo_name}'. Running full scan...")
            return self.scan(repo_path, domain=manifest.domain or infer_domain(repo_name, ""))

        current_commit = git_head(repo)
        if manifest.git_commit == current_commit:
            print(f"'{repo_name}' already in sync at {current_commit}")
            return {"success": True, "message": "Already in sync", "commit": current_commit}

        print(f"\n{'='*60}")
        print(f"  KRONOS SYNC — Incremental PAC Actualization")
        print(f"{'='*60}")
        print(f"  Repo: {repo}")
        print(f"  From: {manifest.git_commit} → {current_commit}")
        print(f"{'='*60}\n")

        print("  Building vault index...", end=" ")
        index_count = self.index.build()
        print(f"{index_count} FDOs\n")

        added, modified, deleted = git_diff_files(repo, manifest.git_commit)
        added = {f for f in added if should_include(repo / f, repo)}
        modified = {f for f in modified if should_include(repo / f, repo)}
        deleted = {f for f in deleted if manifest.is_tracked(f)}

        print(f"  Changes: +{len(added)} ~{len(modified)} -{len(deleted)}\n")

        if not added and not modified and not deleted:
            manifest.git_commit = current_commit
            manifest.save()
            return {"success": True, "message": "No relevant changes"}

        domain = manifest.domain or infer_domain(repo_name, "")
        vault_dir = self.vault / "repos" / slugify(repo_name)

        for rel_path in deleted:
            fdo_path = manifest.remove_file(rel_path)
            if fdo_path:
                full_fdo = self.vault / fdo_path
                if full_fdo.exists():
                    full_fdo.unlink()
                    print(f"  DEL: {rel_path}")

        files_processed = 0
        cross_links = 0

        for rel_path in sorted(added | modified):
            file_path = repo / rel_path
            if not file_path.exists():
                continue

            rel = Path(rel_path)
            h = file_hash(file_path)
            if not manifest.file_changed(rel_path, h):
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            action = "NEW" if rel_path in added else "UPD"
            print(f"  {action}: {rel_path}")

            print(f"       extract...", end=" ", flush=True)
            concepts = self.engine.extract_concepts(content, rel_path)
            print(f"{len(concepts)}", end=" ", flush=True)

            print(f"→ search...", end=" ", flush=True)
            matches = self.index.search(concepts, limit=8)
            vault_context = self.index.format_for_prompt(matches)
            print(f"{len(matches)}", end=" ", flush=True)

            print(f"→ actualize...", end=" ", flush=True)
            fdo_id = slugify(f"{repo_name}-{rel.stem}")
            parent_id = slugify(f"{repo_name}-{rel.parent.name}")

            result = self.engine.actualize_file(
                content=content, file_path=rel_path, repo_name=repo_name,
                domain=domain, parent_id=parent_id, vault_context=vault_context,
            )

            if result is None:
                print("FAILED")
                continue

            dup_of = result.get("is_duplicate_of")
            if dup_of and dup_of in self.index.entries:
                print(f"→ DUP of [[{dup_of}]]")
                manifest.track_file(rel_path, self.index.entries[dup_of]["path"], h, fdo_id=dup_of)
                continue

            confidence = estimate_confidence(file_path, content)
            existing_links = result.get("existing_links", [])
            related = list(set(
                [slugify(c) for c in result.get("key_concepts", [])[:5]] +
                [eid for eid in existing_links if eid in self.index.entries]
            ))

            fdo = FDO(
                id=fdo_id, title=result.get("title", rel.stem), domain=domain,
                summary=result.get("summary", ""),
                details=result.get("details", ""),
                connections=result.get("connections", ""),
                open_questions=result.get("open_questions", ""),
                references=f"- Source: `{rel_path}` in [{repo_name}]",
                status="seed", confidence=confidence,
                related=related, source_repos=[repo_name],
                tags=result.get("tags", []),
                pac_parent=parent_id, source_path=rel_path,
            )

            fdo_dir = vault_dir / rel.parent
            fdo_dir.mkdir(parents=True, exist_ok=True)
            fdo_file = fdo_dir / f"{slugify(rel.stem)}.md"
            fdo_file.write_text(fdo.to_markdown(), encoding="utf-8")

            fdo_vault_rel = str(fdo_file.relative_to(self.vault)).replace("\\", "/")
            manifest.track_file(rel_path, fdo_vault_rel, h, fdo_id=fdo_id)

            patches = self.engine.suggest_cross_links(
                fdo_id, result.get("summary", "")[:100], matches
            )
            for patch in patches:
                if self.linker.patch(patch["fdo_path"], patch["link_text"]):
                    manifest.track_cross_link(patch["fdo_id"], fdo_id)
                    cross_links += 1

            self.index.register(fdo_id, {
                "id": fdo_id, "title": result.get("title", ""),
                "domain": domain, "status": "seed", "confidence": confidence,
                "summary": result.get("summary", "")[:300],
                "tags": result.get("tags", []),
                "concepts": result.get("key_concepts", []),
                "related": related, "source_repos": [repo_name],
                "path": fdo_vault_rel,
            })

            files_processed += 1
            links_note = f" (+{len(patches)} links)" if patches else ""
            print(f"→ {fdo_id}{links_note}")

        manifest.git_commit = current_commit
        manifest.update_count()
        manifest.save()

        print(f"\n{'='*60}")
        print(f"  SYNC COMPLETE")
        print(f"{'='*60}")
        print(f"  Processed:    {files_processed}")
        print(f"  Deleted:      {len(deleted)}")
        print(f"  Cross-links:  {cross_links}")
        print(f"  Commit:       {current_commit}")
        print(f"  API usage:    {self.engine.cost_estimate()}")
        print(f"{'='*60}\n")

        return {
            "success": True, "files_processed": files_processed,
            "files_deleted": len(deleted), "cross_links": cross_links,
            "commit": current_commit,
        }

    def status(self, repo_path: str = None) -> None:
        if repo_path:
            repo = Path(repo_path).resolve()
            self._print_status(SyncManifest(repo.name), repo)
        else:
            if not SYNC_DIR.exists():
                print("No repos tracked yet. Run: repo_loader.py scan <path>")
                return
            for f in sorted(SYNC_DIR.glob("*.json")):
                with open(f) as fh:
                    data = json.load(fh)
                m = SyncManifest(data.get("repo_name", f.stem))
                self._print_status(m, Path(data.get("repo_path", "")))
                print()

    def _print_status(self, manifest: SyncManifest, repo: Path):
        d = manifest.data
        name = d.get("repo_name", "unknown")
        current = git_head(repo) if repo.exists() else None
        indexed = d.get("git_commit")
        in_sync = current == indexed if current and indexed else False

        icon = "✓" if in_sync else "✗"
        print(f"  {icon} {name}")
        print(f"    Path:        {d.get('repo_path', 'unknown')}")
        print(f"    Domain:      {d.get('domain', 'unknown')}")
        print(f"    Commit:      {indexed or 'not synced'} → {current or 'unknown'}")
        print(f"    FDOs:        {d.get('fdo_count', 0)}")
        print(f"    Files:       {len(d.get('files', {}))}")
        print(f"    Cross-links: {sum(len(v) for v in d.get('cross_links', {}).values())}")
        print(f"    Last sync:   {d.get('last_sync', 'never')}")
        if not in_sync and indexed and repo.exists():
            a, m, dl = git_diff_files(repo, indexed)
            if a or m or dl:
                print(f"    Pending:     +{len(a)} ~{len(m)} -{len(dl)}")

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _discover_files(self, repo: Path) -> List[Path]:
        return sorted(fp for fp in repo.rglob("*") if fp.is_file() and should_include(fp, repo))

    def _build_hierarchy(self, files: List[Path], repo: Path) -> Dict[str, Dict]:
        tree = {}
        for f in files:
            rel = f.relative_to(repo)
            for i in range(len(rel.parts) - 1):
                dir_rel = "/".join(rel.parts[:i+1])
                if dir_rel not in tree:
                    parent = "/".join(rel.parts[:i]) if i > 0 else None
                    tree[dir_rel] = {"type": "directory", "parent": parent,
                                     "children": [], "files": []}
                if i == len(rel.parts) - 2:
                    tree[dir_rel]["files"].append(str(rel))
        for path, info in tree.items():
            parent = info.get("parent")
            if parent and parent in tree and path not in tree[parent]["children"]:
                tree[parent]["children"].append(path)
        return tree

    def _build_root_index(self, repo_name, domain, vault_dir, manifest, child_summaries):
        root_children = child_summaries.get(".", [])
        for key, summaries in child_summaries.items():
            if key.count("/") == 0 and key != ".":
                root_children.append({"name": key, "summary": f"{len(summaries)} items"})

        child_ids = [slugify(f"{repo_name}-{c['name'].split('.')[0]}") for c in root_children[:30]]
        nav = "\n".join(f"- [[{cid}]]" for cid in child_ids[:20])
        cl_count = sum(len(v) for v in manifest.data.get("cross_links", {}).values())

        fdo = FDO(
            id=slugify(repo_name),
            title=f"{repo_name} — Repository Knowledge Base",
            domain=domain,
            summary=f"PAC root node for {repo_name}. All FDOs trace lineage here.",
            details=f"## Contents\n\n{nav}\n\n## Sync Info\n\n"
                    f"- **Commit**: {git_head(Path(manifest.repo_path)) if manifest.repo_path else 'N/A'}\n"
                    f"- **Files**: {len(manifest.data.get('files', {}))}\n"
                    f"- **Cross-links**: {cl_count}\n"
                    f"- **Last sync**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
            connections=f"Source: `{manifest.repo_path}`\n\nEach directory index summarizes its children (PAC conservation).",
            status="developing", confidence=0.5,
            source_repos=[repo_name],
            tags=["pac-root", "repo-index", repo_name],
            pac_parent=None, pac_children=child_ids,
        )

        index_file = vault_dir / "_index.md"
        index_file.write_text(fdo.to_markdown(), encoding="utf-8")
        manifest.track_directory(".", str(index_file.relative_to(self.vault)).replace("\\", "/"))


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="GRIM Repo Loader — PAC-Aware Knowledge Graph Builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Per-file flow:
  1. EXTRACT   — quick concept extraction (~200 tokens)
  2. SEARCH    — find matching FDOs already in vault
  3. ACTUALIZE — Claude produces FDO with vault context
  4. CROSS-LINK — patch existing FDOs with backlinks
  5. INDEX     — register new FDO for future files

Examples:
  python repo_loader.py scan ../dawn-field-theory --domain physics
  python repo_loader.py sync ../dawn-field-theory
  python repo_loader.py status
        """,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="Full scan of a repository")
    scan_p.add_argument("repo", help="Path to repository")
    scan_p.add_argument("--domain", choices=["physics", "ai-systems", "tools", "personal"])
    scan_p.add_argument("--force", action="store_true", help="Overwrite existing FDOs")

    sync_p = sub.add_parser("sync", help="Incremental sync from git changes")
    sync_p.add_argument("repo", help="Path to repository")

    status_p = sub.add_parser("status", help="Show sync status")
    status_p.add_argument("repo", nargs="?")

    args = parser.parse_args()
    loader = RepoLoader()

    if args.command == "scan":
        loader.scan(args.repo, domain=args.domain, force=args.force)
    elif args.command == "sync":
        loader.sync(args.repo)
    elif args.command == "status":
        loader.status(args.repo)


if __name__ == "__main__":
    main()
