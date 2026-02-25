"""
Actualization State — the data flowing through the LangGraph.

Every node reads from and writes to this TypedDict.
LangGraph manages state transitions automatically.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict


class ChunkMeta(TypedDict, total=False):
    """Metadata about a single chunk being processed."""
    path: str                    # Relative path within source
    source_type: str             # "file", "message", "section"
    language: str                # "python", "yaml", "markdown", etc.
    size_bytes: int
    hash: str                    # Content hash for change detection
    siblings: List[str]          # Other files in same directory
    parent_dir: str              # Parent directory path


class VaultMatch(TypedDict, total=False):
    """A matching FDO found in the vault."""
    id: str
    title: str
    domain: str
    status: str
    confidence: float
    summary: str
    tags: List[str]
    path: str                    # Relative to vault root
    match_score: float           # 0.0 - 2.0+


class FDODraft(TypedDict, total=False):
    """Draft FDO produced by the actualize node."""
    id: str
    title: str
    domain: str
    summary: str
    details: str
    connections: str
    open_questions: str
    references: str
    status: str
    confidence: float
    related: List[str]
    tags: List[str]
    pac_parent: Optional[str]
    pac_children: List[str]
    source_path: str
    source_repo: str


class ValidationResult(TypedDict, total=False):
    """Output from the validation node."""
    passed: bool
    errors: List[str]
    warnings: List[str]
    fixes_applied: List[str]


class CrossLinkPatch(TypedDict, total=False):
    """A backlink to patch into an existing FDO."""
    target_fdo_id: str
    target_fdo_path: str
    link_text: str


# =========================================================================
# Main Graph State
# =========================================================================

class ActualizationState(TypedDict, total=False):
    """
    Complete state for a single chunk flowing through the graph.

    LangGraph nodes receive this dict and return partial updates.
    Only changed keys need to be returned.
    """

    # --- Source info (set once by intake) ---
    source_type: str                    # "repo", "conversation", "document"
    source_id: str                      # e.g. repo name, conversation id
    source_path: str                    # Root path of the source
    domain: str                         # physics, ai-systems, tools, personal

    # --- Chunk queue (managed by intake + service loop) ---
    chunks: List[Dict[str, Any]]        # All chunks to process
    chunk_index: int                    # Current chunk being processed
    current_content: str                # Content of current chunk
    current_meta: ChunkMeta             # Metadata of current chunk

    # --- Extract output ---
    concepts: List[str]                 # Key concepts found in chunk
    entities: List[str]                 # Named entities (theories, tools, people)

    # --- Search output ---
    vault_matches: List[VaultMatch]     # Matching FDOs from vault
    vault_context: str                  # Formatted string for prompts

    # --- Judge output ---
    decision: Literal["new", "duplicate", "extend", "skip"]
    duplicate_of: Optional[str]         # FDO id if duplicate
    extend_target: Optional[str]        # FDO id if extending
    skip_reason: Optional[str]          # Why skipped

    # --- Actualize output ---
    fdo_draft: Optional[FDODraft]       # The generated FDO
    fdo_id: str                         # Computed FDO id

    # --- Validate output ---
    validation: ValidationResult
    retry_count: int                    # Times we've retried actualization

    # --- Crosslink output ---
    cross_links: List[CrossLinkPatch]   # Backlinks to patch

    # --- Accumulator (persists across chunks) ---
    fdos_created: List[str]             # IDs of FDOs created this run
    fdos_linked: List[str]              # IDs of existing FDOs we linked to
    fdos_skipped: List[str]             # Paths skipped
    errors: List[str]                   # Error messages
    api_calls: int                      # Total API calls made
    api_input_tokens: int               # Total input tokens
    api_output_tokens: int              # Total output tokens

    # --- Injected dependencies (set once by service, carried through graph) ---
    _client: Any                        # anthropic.Anthropic instance
    _model: str                         # Model name
    _vault_index: Any                   # VaultIndex instance
    _writer: Any                        # FDOWriter instance
    _crosslinker: Any                   # CrossLinker instance
    _vault_repo_dir: str                # Vault-relative dir for this source
