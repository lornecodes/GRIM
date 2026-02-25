"""
Base source protocol for actualization service.

Any source that can be ingested (repos, conversations, documents)
implements this interface.  The service iterates over chunks from the
source and feeds each into the LangGraph pipeline.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Protocol, runtime_checkable


@dataclass
class Chunk:
    """
    One unit of knowledge to be actualized.

    For a repo source this is a single file.
    For a conversation source this might be a message or turn.
    For a document source this might be a section or page.
    """

    # Identity
    path: str                          # Relative path within source (e.g. "src/main.py")
    source_id: str                     # Which source this comes from
    content: str                       # Raw text content
    content_hash: str = ""             # SHA-256 for dedup / change detection

    # Metadata the graph can use
    language: str = ""                 # File extension or language hint
    size: int = 0                      # Byte count of raw content
    is_directory: bool = False         # True → directory summary, not file content
    children: list[str] = field(default_factory=list)  # Child paths (for directories)

    # Parent context
    parent_path: str = ""              # Parent directory path
    repo_root: str = ""               # Top-level source root name

    def __post_init__(self):
        if not self.content_hash and self.content:
            self.content_hash = hashlib.sha256(self.content.encode()).hexdigest()[:16]
        if not self.size and self.content:
            self.size = len(self.content.encode())

    def to_meta(self) -> Dict[str, Any]:
        """Convert to ChunkMeta dict for graph state."""
        return {
            "path": self.path,
            "source_id": self.source_id,
            "content": self.content,
            "content_hash": self.content_hash,
            "language": self.language,
            "size": self.size,
            "is_directory": self.is_directory,
            "children": self.children,
            "parent_path": self.parent_path,
        }


@dataclass
class SyncManifest:
    """Tracks what has been ingested and when."""
    source_id: str
    files: Dict[str, str] = field(default_factory=dict)  # path → content_hash
    last_sync: str = ""

    def is_changed(self, path: str, content_hash: str) -> bool:
        return self.files.get(path) != content_hash

    def record(self, path: str, content_hash: str):
        self.files[path] = content_hash


@runtime_checkable
class Source(Protocol):
    """Protocol for knowledge sources."""

    @property
    def source_id(self) -> str:
        """Unique identifier for this source (e.g. repo name)."""
        ...

    @property
    def source_type(self) -> str:
        """Type of source: 'repo', 'conversation', 'document'."""
        ...

    def discover(self) -> Iterator[Chunk]:
        """
        Yield all chunks from this source.

        For a repo: iterate files respecting .gitignore.
        For a conversation: iterate turns/messages.
        """
        ...

    def discover_directories(self) -> Iterator[Chunk]:
        """
        Yield directory-level chunks (for PAC parent nodes).
        Called AFTER file-level ingestion completes.
        """
        ...

    def load_manifest(self, manifest_dir: Path) -> SyncManifest:
        """Load sync manifest from disk."""
        ...

    def save_manifest(self, manifest: SyncManifest, manifest_dir: Path) -> None:
        """Save sync manifest to disk."""
        ...
