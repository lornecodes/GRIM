"""
Git repository source adapter.

Discovers files in a git repo, respects .gitignore patterns,
computes content hashes for sync detection, and yields Chunk objects
for the actualization graph.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set

from .base import Chunk, Source, SyncManifest


# =========================================================================
# Constants
# =========================================================================

# Always skip these regardless of .gitignore
ALWAYS_SKIP_DIRS: Set[str] = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".egg-info", ".eggs", "*.egg-info",
}

# Skip files by extension
SKIP_EXTENSIONS: Set[str] = {
    ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".db", ".sqlite", ".sqlite3",
    ".lock",
}

# Skip files by name
SKIP_NAMES: Set[str] = {
    ".DS_Store", "Thumbs.db", ".gitattributes",
}

# Max file size to read (256 KB — larger files get truncated)
MAX_FILE_SIZE: int = 256 * 1024

# Max content to send to Claude (chars)
MAX_CONTENT_CHARS: int = 50_000


# =========================================================================
# RepoSource
# =========================================================================

class RepoSource:
    """
    Adapts a local git repository into actualization chunks.

    Uses `git ls-files` when available for accurate .gitignore
    compliance, falls back to manual walk if not a git repo.
    """

    def __init__(self, repo_path: str | Path, source_id: Optional[str] = None):
        self.repo_path = Path(repo_path).resolve()
        if not self.repo_path.is_dir():
            raise ValueError(f"Not a directory: {self.repo_path}")
        self._source_id = source_id or self.repo_path.name
        self._is_git = (self.repo_path / ".git").is_dir()

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def source_type(self) -> str:
        return "repo"

    # -----------------------------------------------------------------
    # File discovery
    # -----------------------------------------------------------------

    def _git_ls_files(self) -> List[str]:
        """Use git to list tracked files (respects .gitignore)."""
        try:
            result = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return [
                    line.strip()
                    for line in result.stdout.splitlines()
                    if line.strip()
                ]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return []

    def _manual_walk(self) -> List[str]:
        """Walk the filesystem, applying skip rules."""
        files: List[str] = []
        for root, dirs, filenames in os.walk(self.repo_path):
            # Prune skipped directories
            dirs[:] = [
                d for d in dirs
                if d not in ALWAYS_SKIP_DIRS
                and not d.endswith(".egg-info")
            ]
            rel_root = Path(root).relative_to(self.repo_path)
            for fname in filenames:
                files.append(str(rel_root / fname).replace("\\", "/"))
        return files

    def _should_skip_file(self, rel_path: str) -> bool:
        """Check if a file should be skipped based on name/extension."""
        name = Path(rel_path).name
        ext = Path(rel_path).suffix.lower()

        if name in SKIP_NAMES:
            return True
        if ext in SKIP_EXTENSIONS:
            return True

        # Check directory components
        parts = Path(rel_path).parts
        for part in parts[:-1]:  # All dirs except filename
            if part in ALWAYS_SKIP_DIRS or part.endswith(".egg-info"):
                return True

        return False

    def _read_file(self, rel_path: str) -> str:
        """Read file content with size guard and encoding fallback."""
        abs_path = self.repo_path / rel_path
        try:
            size = abs_path.stat().st_size
            if size > MAX_FILE_SIZE:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(MAX_FILE_SIZE)
                return content + f"\n\n[... truncated at {MAX_FILE_SIZE // 1024}KB, total {size // 1024}KB ...]"
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return ""

    def _detect_language(self, rel_path: str) -> str:
        """Map file extension to language hint."""
        ext_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".rs": "rust", ".go": "go", ".java": "java", ".c": "c",
            ".cpp": "cpp", ".h": "c", ".hpp": "cpp", ".cs": "csharp",
            ".rb": "ruby", ".php": "php", ".swift": "swift",
            ".kt": "kotlin", ".scala": "scala", ".r": "r",
            ".md": "markdown", ".rst": "restructuredtext",
            ".yaml": "yaml", ".yml": "yaml", ".json": "json",
            ".toml": "toml", ".ini": "ini", ".cfg": "ini",
            ".xml": "xml", ".html": "html", ".css": "css",
            ".sh": "bash", ".bash": "bash", ".zsh": "zsh",
            ".ps1": "powershell", ".bat": "batch",
            ".sql": "sql", ".graphql": "graphql",
            ".dockerfile": "dockerfile", ".tf": "terraform",
        }
        ext = Path(rel_path).suffix.lower()
        name = Path(rel_path).name.lower()

        # Special filenames
        if name == "dockerfile":
            return "dockerfile"
        if name == "makefile":
            return "makefile"
        if name in ("readme", "license", "contributing", "changelog"):
            return "text"

        return ext_map.get(ext, ext.lstrip(".") if ext else "text")

    def discover(self) -> Iterator[Chunk]:
        """Yield file-level chunks for all discoverable files."""
        if self._is_git:
            paths = self._git_ls_files()
        else:
            paths = self._manual_walk()

        # Sort for deterministic ordering
        paths.sort()

        for rel_path in paths:
            # Normalize path separators
            rel_path = rel_path.replace("\\", "/")

            if self._should_skip_file(rel_path):
                continue

            content = self._read_file(rel_path)
            if not content and not rel_path.endswith(("__init__.py", ".gitkeep")):
                # Skip truly empty files (but keep __init__.py which are often empty)
                pass

            # Truncate very long content for Claude
            display_content = content
            if len(display_content) > MAX_CONTENT_CHARS:
                display_content = display_content[:MAX_CONTENT_CHARS] + \
                    "\n\n[... content truncated for analysis ...]"

            parent = str(Path(rel_path).parent).replace("\\", "/")
            if parent == ".":
                parent = ""

            yield Chunk(
                path=rel_path,
                source_id=self._source_id,
                content=display_content,
                language=self._detect_language(rel_path),
                size=len(content.encode("utf-8", errors="replace")),
                is_directory=False,
                parent_path=parent,
                repo_root=self._source_id,
            )

    def discover_directories(self) -> Iterator[Chunk]:
        """
        Yield directory-level chunks for PAC parent nodes.
        Should be called AFTER file ingestion so we know what exists.
        """
        # Collect all directories that contain files
        dir_children: Dict[str, List[str]] = defaultdict(list)

        if self._is_git:
            paths = self._git_ls_files()
        else:
            paths = self._manual_walk()

        for rel_path in paths:
            rel_path = rel_path.replace("\\", "/")
            if self._should_skip_file(rel_path):
                continue
            parent = str(Path(rel_path).parent).replace("\\", "/")
            if parent == ".":
                parent = ""
            dir_children[parent].append(rel_path)

        # Also collect subdirectories
        all_dirs: Set[str] = set()
        for path in dir_children:
            if path:
                all_dirs.add(path)
                # Add ancestor dirs
                parts = Path(path).parts
                for i in range(1, len(parts)):
                    ancestor = "/".join(parts[:i])
                    all_dirs.add(ancestor)

        # Sort deepest first → build parents after children
        sorted_dirs = sorted(all_dirs, key=lambda d: d.count("/"), reverse=True)

        for dir_path in sorted_dirs:
            # Collect file children and subdir children
            files_in_dir = dir_children.get(dir_path, [])
            subdirs = [d for d in all_dirs if str(Path(d).parent).replace("\\", "/") == dir_path]

            children = sorted(files_in_dir + subdirs)

            parent = str(Path(dir_path).parent).replace("\\", "/")
            if parent == ".":
                parent = ""

            # Build a content summary listing children
            lines = [f"# Directory: {dir_path}", ""]
            if subdirs:
                lines.append("## Subdirectories")
                for sd in sorted(subdirs):
                    lines.append(f"- {Path(sd).name}/")
                lines.append("")
            if files_in_dir:
                lines.append("## Files")
                for fp in sorted(files_in_dir):
                    lines.append(f"- {Path(fp).name}")
                lines.append("")

            yield Chunk(
                path=dir_path,
                source_id=self._source_id,
                content="\n".join(lines),
                language="directory",
                size=0,
                is_directory=True,
                children=children,
                parent_path=parent,
                repo_root=self._source_id,
            )

        # Finally yield root directory
        root_files = dir_children.get("", [])
        root_subdirs = [d for d in all_dirs if "/" not in d and d]

        lines = [f"# Repository: {self._source_id}", ""]
        if root_subdirs:
            lines.append("## Directories")
            for sd in sorted(root_subdirs):
                lines.append(f"- {sd}/")
            lines.append("")
        if root_files:
            lines.append("## Root Files")
            for fp in sorted(root_files):
                lines.append(f"- {Path(fp).name}")
            lines.append("")

        yield Chunk(
            path="",  # Root
            source_id=self._source_id,
            content="\n".join(lines),
            language="directory",
            size=0,
            is_directory=True,
            children=sorted(root_files + root_subdirs),
            parent_path="",
            repo_root=self._source_id,
        )

    # -----------------------------------------------------------------
    # Sync manifest
    # -----------------------------------------------------------------

    def load_manifest(self, manifest_dir: Path) -> SyncManifest:
        manifest_path = manifest_dir / f"{self._source_id}.json"
        if manifest_path.exists():
            data = json.loads(manifest_path.read_text())
            return SyncManifest(
                source_id=self._source_id,
                files=data.get("files", {}),
                last_sync=data.get("last_sync", ""),
            )
        return SyncManifest(source_id=self._source_id)

    def save_manifest(self, manifest: SyncManifest, manifest_dir: Path) -> None:
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{self._source_id}.json"
        manifest_path.write_text(json.dumps({
            "source_id": manifest.source_id,
            "files": manifest.files,
            "last_sync": datetime.now(timezone.utc).isoformat(),
        }, indent=2))
