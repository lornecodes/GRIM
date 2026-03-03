"""
Kronos Search Engine — 3-tier hybrid search for the knowledge vault.

Architecture:
    FileWatcher  → mtime-based incremental re-indexing
    BM25Index    → keyword/term-frequency search
    SemanticIndex → sentence-transformer cosine similarity
    GraphIndex   → adjacency expansion from FDO `related` links
    FusionRanker → Reciprocal Rank Fusion across all channels
    SearchEngine → orchestrates the above

Zero infrastructure — all in-process, filesystem-native.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from .vault import FDO, VaultEngine

logger = logging.getLogger("kronos-mcp.search")


# ── Data types ───────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    """A scored search result from any channel."""
    fdo_id: str
    score: float
    channel: str  # "keyword", "semantic", "graph"
    snippet: str = ""


@dataclass
class FusedResult:
    """A search result after RRF fusion across channels."""
    fdo_id: str
    rrf_score: float
    channel_scores: dict[str, float] = field(default_factory=dict)
    channel_ranks: dict[str, int] = field(default_factory=dict)


# ── FileWatcher ──────────────────────────────────────────────────────────────

class FileWatcher:
    """Track file modification times to detect changes without full re-scan."""

    def __init__(self):
        self._mtimes: dict[str, float] = {}  # path → last known mtime

    def check_changes(self, paths: list[str]) -> tuple[list[str], list[str], list[str]]:
        """Compare current mtimes against cached.

        Returns (added, modified, removed) path lists.
        """
        current: dict[str, float] = {}
        for p in paths:
            try:
                current[p] = os.path.getmtime(p)
            except OSError:
                continue

        current_set = set(current.keys())
        cached_set = set(self._mtimes.keys())

        added = list(current_set - cached_set)
        removed = list(cached_set - current_set)
        modified = [
            p for p in current_set & cached_set
            if current[p] != self._mtimes[p]
        ]

        self._mtimes = current
        return added, modified, removed

    def is_fresh(self, paths: list[str]) -> bool:
        """Quick check: have any files changed?"""
        for p in paths:
            try:
                mtime = os.path.getmtime(p)
            except OSError:
                if p in self._mtimes:
                    return False
                continue
            cached = self._mtimes.get(p)
            if cached is None or mtime != cached:
                return False
        return True

    @property
    def tracked_count(self) -> int:
        return len(self._mtimes)


# ── BM25 Keyword Index ──────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer, lowercase."""
    return re.findall(r"[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?", text.lower())


class BM25Index:
    """In-memory BM25 (Okapi) keyword index over FDO text fields.

    Indexes: id, title, tags, summary, body with configurable field weights.
    """

    FIELD_WEIGHTS = {
        "id": 3.0,
        "title": 5.0,
        "tags": 4.0,
        "summary": 3.0,
        "body": 1.0,
    }

    # BM25 parameters
    K1 = 1.5
    B = 0.75

    def __init__(self):
        self._docs: dict[str, dict[str, list[str]]] = {}  # fdo_id → {field: tokens}
        self._doc_lengths: dict[str, float] = {}  # fdo_id → weighted token count
        self._avg_dl: float = 0.0
        self._df: Counter = Counter()  # term → doc frequency
        self._n_docs: int = 0
        self._dirty = True

    def add(self, fdo_id: str, fields: dict[str, str]):
        """Add or update a document in the index."""
        tokenized: dict[str, list[str]] = {}
        weighted_length = 0.0

        for field_name, text in fields.items():
            tokens = _tokenize(text)
            tokenized[field_name] = tokens
            weight = self.FIELD_WEIGHTS.get(field_name, 1.0)
            weighted_length += len(tokens) * weight

        self._docs[fdo_id] = tokenized
        self._doc_lengths[fdo_id] = weighted_length
        self._dirty = True

    def remove(self, fdo_id: str):
        """Remove a document from the index."""
        self._docs.pop(fdo_id, None)
        self._doc_lengths.pop(fdo_id, None)
        self._dirty = True

    def _rebuild_stats(self):
        """Rebuild IDF stats (call after batch add/remove)."""
        self._df.clear()
        self._n_docs = len(self._docs)

        for fdo_id, fields in self._docs.items():
            seen_terms: set[str] = set()
            for tokens in fields.values():
                seen_terms.update(tokens)
            for term in seen_terms:
                self._df[term] += 1

        total_length = sum(self._doc_lengths.values())
        self._avg_dl = total_length / self._n_docs if self._n_docs else 1.0
        self._dirty = False

    def search(self, query: str, max_results: int = 20) -> list[SearchResult]:
        """BM25 search across all indexed documents."""
        if self._dirty:
            self._rebuild_stats()

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores: dict[str, float] = {}

        for token in query_tokens:
            df = self._df.get(token, 0)
            if df == 0:
                continue

            # IDF: log((N - df + 0.5) / (df + 0.5) + 1)
            idf = math.log((self._n_docs - df + 0.5) / (df + 0.5) + 1.0)

            for fdo_id, fields in self._docs.items():
                # Weighted term frequency across fields
                tf = 0.0
                for field_name, tokens in fields.items():
                    count = tokens.count(token)
                    if count > 0:
                        weight = self.FIELD_WEIGHTS.get(field_name, 1.0)
                        tf += count * weight

                if tf == 0:
                    continue

                dl = self._doc_lengths[fdo_id]
                # BM25: idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl/avgdl))
                numerator = tf * (self.K1 + 1.0)
                denominator = tf + self.K1 * (1.0 - self.B + self.B * dl / self._avg_dl)
                score = idf * numerator / denominator

                scores[fdo_id] = scores.get(fdo_id, 0.0) + score

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [
            SearchResult(fdo_id=fid, score=s, channel="keyword")
            for fid, s in ranked[:max_results]
        ]


# ── Semantic Index ───────────────────────────────────────────────────────────

class SemanticIndex:
    """Embedding-based semantic search using sentence-transformers.

    Embeddings are cached to disk to avoid re-computing on restart.
    Model loads lazily on first search — keyword search works immediately.
    """

    DEFAULT_MODEL = "all-mpnet-base-v2"
    CACHE_FILE = ".kronos_cache/embeddings.json"

    def __init__(self, vault_path: str, model_name: str | None = None):
        self._vault_path = Path(vault_path)
        self._model_name = model_name or os.getenv("KRONOS_EMBED_MODEL", self.DEFAULT_MODEL)
        self._model = None  # Lazy loaded
        self._embeddings: dict[str, list[float]] = {}  # fdo_id → embedding vector
        self._content_hashes: dict[str, str] = {}  # fdo_id → hash of content that was embedded
        self._dim: int = 0
        self._available: bool | None = None  # None = not checked yet
        self._load_cache()

    @property
    def available(self) -> bool:
        """Check if sentence-transformers is importable (cached check)."""
        if self._available is None:
            try:
                import sentence_transformers  # noqa: F401
                self._available = True
            except Exception:
                self._available = False
                logger.warning(
                    "sentence-transformers not available — semantic search disabled. "
                    "Install with: pip install sentence-transformers"
                )
        return self._available

    # Timeout for model loading (seconds).  Covers both local load and
    # any fallback network download from HuggingFace.
    MODEL_LOAD_TIMEOUT = 60

    def _load_model(self):
        """Load the sentence-transformer model (lazy, first search only).

        Uses local_files_only=True to avoid network calls to HuggingFace
        when the model is already cached. Falls back to network download
        only if the local cache is missing.

        Raises TimeoutError if loading exceeds MODEL_LOAD_TIMEOUT seconds.
        """
        if self._model is not None:
            return
        if not self.available:
            return

        import concurrent.futures

        logger.info(f"Loading embedding model: {self._model_name}")
        t0 = time.time()

        def _do_load():
            from sentence_transformers import SentenceTransformer
            try:
                # Try local-only first (no network, fast)
                return SentenceTransformer(
                    self._model_name, local_files_only=True
                )
            except OSError:
                # Model not cached — download once
                logger.info(f"Model not cached locally, downloading: {self._model_name}")
                return SentenceTransformer(self._model_name)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_do_load)
            try:
                self._model = future.result(timeout=self.MODEL_LOAD_TIMEOUT)
            except concurrent.futures.TimeoutError:
                logger.error(
                    f"Model load timed out after {self.MODEL_LOAD_TIMEOUT}s — "
                    f"semantic search disabled for this session"
                )
                self._available = False
                return

        self._dim = self._model.get_sentence_embedding_dimension()
        logger.info(f"Model loaded in {time.time() - t0:.1f}s — dim={self._dim}")

    def _content_hash(self, text: str) -> str:
        """Fast hash to detect content changes."""
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]

    def _cache_path(self) -> Path:
        return self._vault_path / self.CACHE_FILE

    def _load_cache(self):
        """Load embeddings from disk cache."""
        cache = self._cache_path()
        if not cache.exists():
            return
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            self._embeddings = data.get("embeddings", {})
            self._content_hashes = data.get("content_hashes", {})
            self._dim = data.get("dim", 0)
            model = data.get("model", "")
            if model != self._model_name:
                # Model changed — invalidate all embeddings
                logger.info(f"Embedding model changed ({model} → {self._model_name}), clearing cache")
                self._embeddings.clear()
                self._content_hashes.clear()
        except Exception as e:
            logger.warning(f"Failed to load embedding cache: {e}")

    def _save_cache(self):
        """Persist embeddings to disk."""
        cache = self._cache_path()
        cache.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = {
                "model": self._model_name,
                "dim": self._dim,
                "embeddings": self._embeddings,
                "content_hashes": self._content_hashes,
            }
            cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save embedding cache: {e}")

    def update(self, fdo_id: str, text: str):
        """Update embedding for one FDO if content changed. Returns True if re-embedded."""
        content_hash = self._content_hash(text)
        if self._content_hashes.get(fdo_id) == content_hash and fdo_id in self._embeddings:
            return False  # No change

        if not self.available:
            return False

        self._load_model()
        if self._model is None:
            return False

        embedding = self._model.encode(text, show_progress_bar=False, normalize_embeddings=True)
        self._embeddings[fdo_id] = embedding.tolist()
        self._content_hashes[fdo_id] = content_hash
        return True

    def update_batch(self, items: dict[str, str]):
        """Batch update embeddings for multiple FDOs. Only re-embeds changed content."""
        to_embed: dict[str, str] = {}
        for fdo_id, text in items.items():
            content_hash = self._content_hash(text)
            if self._content_hashes.get(fdo_id) != content_hash or fdo_id not in self._embeddings:
                to_embed[fdo_id] = text
                self._content_hashes[fdo_id] = content_hash

        if not to_embed:
            return

        if not self.available:
            return

        self._load_model()
        if self._model is None:
            return

        ids = list(to_embed.keys())
        texts = list(to_embed.values())

        logger.info(f"Embedding {len(texts)} FDOs (batch)...")
        t0 = time.time()
        embeddings = self._model.encode(
            texts, show_progress_bar=False, normalize_embeddings=True, batch_size=32
        )
        for i, fdo_id in enumerate(ids):
            self._embeddings[fdo_id] = embeddings[i].tolist()

        logger.info(f"Embedded {len(texts)} FDOs in {time.time() - t0:.1f}s")
        self._save_cache()

    def remove(self, fdo_id: str):
        """Remove an FDO from the index."""
        self._embeddings.pop(fdo_id, None)
        self._content_hashes.pop(fdo_id, None)

    def search(self, query: str, max_results: int = 20) -> list[SearchResult]:
        """Cosine similarity search against all stored embeddings."""
        if not self.available or not self._embeddings:
            return []

        self._load_model()
        if self._model is None:
            return []

        import numpy as np

        # Encode query
        query_vec = self._model.encode(
            query, show_progress_bar=False, normalize_embeddings=True
        )

        # Vectorized cosine similarity (normalized → dot product)
        ids = list(self._embeddings.keys())
        matrix = np.array([self._embeddings[fid] for fid in ids], dtype=np.float32)
        sims = matrix @ query_vec.astype(np.float32)

        # Sort descending
        top_idx = np.argsort(sims)[::-1][:max_results]
        return [
            SearchResult(fdo_id=ids[i], score=float(sims[i]), channel="semantic")
            for i in top_idx
            if sims[i] > 0.0
        ]

    def flush(self):
        """Force save cache to disk."""
        self._save_cache()

    @property
    def indexed_count(self) -> int:
        return len(self._embeddings)


# ── Graph Index ──────────────────────────────────────────────────────────────

class GraphIndex:
    """In-memory adjacency graph built from FDO `related` and `pac_*` fields.

    Supports neighbor expansion: given initial hits, expand through graph
    to find related FDOs that keyword/semantic might miss.
    """

    def __init__(self):
        self._adjacency: dict[str, set[str]] = {}  # fdo_id → set of neighbor ids

    def build(self, fdos: dict[str, Any]):
        """Build adjacency from FDO index. Accepts {id: FDO} dict."""
        self._adjacency.clear()
        for fdo_id, fdo in fdos.items():
            neighbors: set[str] = set()
            # Related links
            for rel in (fdo.related or []):
                # Strip wikilink markers if present
                clean = rel.strip("[]").strip()
                if clean:
                    neighbors.add(clean)
            # PAC hierarchy
            if fdo.pac_parent:
                neighbors.add(fdo.pac_parent)
            for child in (fdo.pac_children or []):
                neighbors.add(child)
            # Body wikilinks
            for link in fdo.wikilinks:
                neighbors.add(link)
            self._adjacency[fdo_id] = neighbors

    def add(self, fdo_id: str, related: list[str], pac_parent: str | None, pac_children: list[str], wikilinks: list[str]):
        """Add/update a single node."""
        neighbors: set[str] = set()
        for rel in (related or []):
            clean = rel.strip("[]").strip()
            if clean:
                neighbors.add(clean)
        if pac_parent:
            neighbors.add(pac_parent)
        for child in (pac_children or []):
            neighbors.add(child)
        for link in (wikilinks or []):
            neighbors.add(link)
        self._adjacency[fdo_id] = neighbors

    def remove(self, fdo_id: str):
        """Remove a node."""
        self._adjacency.pop(fdo_id, None)

    def expand(
        self,
        seed_ids: list[str],
        max_depth: int = 1,
        max_expand: int = 20,
        decay: float = 0.5,
    ) -> list[SearchResult]:
        """Expand from seed FDOs through the graph.

        Returns neighbors scored by proximity (decay per hop).
        """
        visited: set[str] = set(seed_ids)
        results: dict[str, float] = {}
        frontier = [(fid, 1.0) for fid in seed_ids]

        for depth in range(max_depth):
            next_frontier: list[tuple[str, float]] = []
            for fid, parent_score in frontier:
                neighbors = self._adjacency.get(fid, set())
                for neighbor in neighbors:
                    if neighbor in visited:
                        continue
                    visited.add(neighbor)
                    score = parent_score * decay
                    results[neighbor] = max(results.get(neighbor, 0.0), score)
                    next_frontier.append((neighbor, score))
            frontier = next_frontier

        ranked = sorted(results.items(), key=lambda x: x[1], reverse=True)
        return [
            SearchResult(fdo_id=fid, score=s, channel="graph")
            for fid, s in ranked[:max_expand]
        ]


# ── Fusion Ranker ────────────────────────────────────────────────────────────

class FusionRanker:
    """Reciprocal Rank Fusion (RRF) — combines ranked lists from multiple channels.

    RRF score for document d:
        score(d) = Σ  1 / (k + rank_r(d))
                   r∈rankers

    k=60 is the standard constant (from original RRF paper).
    """

    K = 60

    def fuse(
        self,
        channel_results: dict[str, list[SearchResult]],
        channel_weights: dict[str, float] | None = None,
        max_results: int = 20,
    ) -> list[FusedResult]:
        """Fuse results from multiple channels using weighted RRF."""
        weights = channel_weights or {}
        all_ids: set[str] = set()
        rankings: dict[str, dict[str, int]] = {}     # channel → {fdo_id → rank}
        raw_scores: dict[str, dict[str, float]] = {}  # channel → {fdo_id → score}

        for channel, results in channel_results.items():
            rankings[channel] = {}
            raw_scores[channel] = {}
            for rank, result in enumerate(results):
                rankings[channel][result.fdo_id] = rank
                raw_scores[channel][result.fdo_id] = result.score
                all_ids.add(result.fdo_id)

        fused: dict[str, FusedResult] = {}
        for fdo_id in all_ids:
            rrf_score = 0.0
            channel_scores: dict[str, float] = {}
            channel_ranks: dict[str, int] = {}

            for channel in channel_results:
                if fdo_id in rankings[channel]:
                    rank = rankings[channel][fdo_id]
                    weight = weights.get(channel, 1.0)
                    rrf_score += weight / (self.K + rank)
                    channel_scores[channel] = raw_scores[channel][fdo_id]
                    channel_ranks[channel] = rank

            fused[fdo_id] = FusedResult(
                fdo_id=fdo_id,
                rrf_score=rrf_score,
                channel_scores=channel_scores,
                channel_ranks=channel_ranks,
            )

        ranked = sorted(fused.values(), key=lambda x: x.rrf_score, reverse=True)
        return ranked[:max_results]


# ── Search Engine (orchestrator) ─────────────────────────────────────────────

class SearchEngine:
    """Orchestrates 3-tier hybrid search over the Kronos vault.

    Channels:
        keyword  — BM25 over title, tags, summary, body
        semantic — sentence-transformer cosine similarity (optional)
        graph    — expand neighbors of top keyword+semantic hits

    Fusion: Reciprocal Rank Fusion (RRF)
    """

    CHANNEL_WEIGHTS = {
        "tag_exact": 2.0,   # Exact tag matches dominate
        "keyword": 1.0,
        "semantic": 1.2,   # Slight boost for semantic — catches conceptual matches
        "graph": 0.6,      # Graph is supplementary, not primary
    }

    def __init__(self, vault: "VaultEngine"):
        self._vault = vault
        self._watcher = FileWatcher()
        self._bm25 = BM25Index()
        self._semantic = SemanticIndex(str(vault.vault_path))
        self._graph = GraphIndex()
        self._ranker = FusionRanker()
        self._initialized = False
        self._semantic_indexed = False  # Semantic builds lazily on first semantic search
        self._semantic_loading = False  # True while background thread is building
        import threading
        self._semantic_lock = threading.Lock()
        self._index_lock = threading.Lock()  # Serialises concurrent _full_index() calls

    @property
    def watcher(self) -> FileWatcher:
        return self._watcher

    def invalidate(self):
        """Force full re-index on next search. Use index_fdo() after writes instead."""
        self._initialized = False
        self._semantic_indexed = False
        self._watcher._mtimes.clear()
        # Do NOT call vault.refresh() — write_fdo already updated the vault index

    def index_fdo(self, fdo: "FDO"):
        """Incrementally index a new or updated FDO — no full rebuild needed.

        Called by handle_create/handle_update instead of invalidate().
        Keeps _initialized=True so subsequent reads stay fast.
        Thread-safe: all operations are atomic dict assignments (GIL-protected).
        """
        if not self._initialized:
            # Index not built yet — will be built on first search, which picks
            # up the new FDO from vault._ensure_index() anyway.
            return

        # Update BM25 (add/replace the FDO's entry)
        self._bm25.add(fdo.id, self._fdo_text_fields(fdo))

        # Update graph
        self._graph.add(
            fdo.id, fdo.related, fdo.pac_parent, fdo.pac_children, fdo.wikilinks
        )

        # Update file watcher so is_fresh() returns True for this file
        if fdo.file_path:
            try:
                mtime = os.path.getmtime(fdo.file_path)
                self._watcher._mtimes[fdo.file_path] = mtime
            except OSError:
                pass

        # Update semantic embedding if semantic index is already built
        if self._semantic_indexed and self._semantic.available:
            text = self._fdo_embed_text(fdo)
            self._semantic.update(fdo.id, text)
            # Persist updated cache in background to avoid blocking handler
            import threading
            threading.Thread(
                target=self._semantic.flush,
                daemon=True,
                name=f"embed-flush-{fdo.id}",
            ).start()

    def _ensure_indexed(self):
        """Build or incrementally update BM25 + graph indices (NOT semantic).

        Thread-safe: only one thread runs _full_index() at a time.
        All others skip the full build if it completes while they wait.
        """
        # Fast path — no lock needed for the hot case
        if self._initialized:
            all_paths = [str(p) for p in self._vault.vault_path.rglob("*.md")]
            if self._watcher.is_fresh(all_paths):
                return  # Nothing changed
            # Fall through to do incremental update under lock
        else:
            all_paths = [str(p) for p in self._vault.vault_path.rglob("*.md")]

        with self._index_lock:
            # Double-check after acquiring lock: another thread may have finished
            if self._initialized and self._watcher.is_fresh(all_paths):
                return

            if not self._initialized:
                # Full build of BM25 + graph (fast, ~400ms for 115 FDOs)
                self._full_index(all_paths)
                self._initialized = True
                return

        # Incremental update (outside lock — safe since only adds/removes)
        if self._watcher.is_fresh(all_paths):
            return  # Nothing changed

        added, modified, removed = self._watcher.check_changes(all_paths)
        if not added and not modified and not removed:
            return

        logger.info(f"Incremental index: +{len(added)} ~{len(modified)} -{len(removed)}")

        # Remove deleted
        for path in removed:
            fdo_id = Path(path).stem
            self._bm25.remove(fdo_id)
            self._semantic.remove(fdo_id)
            self._graph.remove(fdo_id)

        # Re-parse added + modified
        to_embed: dict[str, str] = {}
        for path in added + modified:
            fdo = self._vault._parse_file(Path(path))
            if fdo is None:
                continue
            # Update vault index entry
            self._vault._index_ref[fdo.id] = fdo
            # Update BM25
            self._bm25.add(fdo.id, self._fdo_text_fields(fdo))
            # Queue for semantic (if semantic is active)
            if self._semantic_indexed:
                to_embed[fdo.id] = self._fdo_embed_text(fdo)
            # Update graph
            self._graph.add(fdo.id, fdo.related, fdo.pac_parent, fdo.pac_children, fdo.wikilinks)

        # Batch embed changed (only if semantic already initialized)
        if to_embed:
            self._semantic.update_batch(to_embed)

    def _ensure_semantic(self, blocking: bool = True):
        """Lazily build semantic index on first semantic search request.

        The lock is held only briefly to check/set flags. The heavy model load
        and embedding happens OUTSIDE the lock so other search requests can
        proceed with keyword-only results instead of blocking for 60s+.

        Args:
            blocking: If False, return immediately if another thread is loading.
                      The background pre-load uses blocking=True.
                      Search requests use blocking=False to avoid hanging.
        """
        if self._semantic_indexed:
            return True
        if not self._semantic.available:
            return False

        # Brief lock to check/set loading flag
        acquired = self._semantic_lock.acquire(blocking=blocking)
        if not acquired:
            logger.warning("Semantic index still loading — skipping semantic for this request")
            return False
        try:
            if self._semantic_indexed:
                return True
            if self._semantic_loading:
                # Another thread is building — skip semantic for this request
                logger.warning("Semantic index still loading — skipping semantic for this request")
                return False
            self._semantic_loading = True
        finally:
            self._semantic_lock.release()

        # Heavy work outside lock — only one thread gets here due to flag
        try:
            logger.info("Building semantic index (first semantic search)...")
            t0 = time.time()
            to_embed: dict[str, str] = {}
            for fdo in self._vault.index.values():
                to_embed[fdo.id] = self._fdo_embed_text(fdo)
            self._semantic.update_batch(to_embed)

            with self._semantic_lock:
                self._semantic_indexed = True
                self._semantic_loading = False
            logger.info(f"Semantic index built in {time.time() - t0:.1f}s — {self._semantic.indexed_count} embeddings")
            return True
        except Exception as e:
            with self._semantic_lock:
                self._semantic_loading = False
            logger.warning(f"Semantic index build failed: {e}")
            return False

    def _full_index(self, all_paths: list[str]):
        """Full index build: BM25 + graph (fast). Semantic deferred."""
        logger.info(f"Building full index over {len(all_paths)} files...")
        t0 = time.time()

        # Force vault to parse all files (populates its own index)
        self._vault._ensure_index()

        # Track mtimes
        self._watcher.check_changes(all_paths)

        # Build BM25
        for fdo in self._vault.index.values():
            self._bm25.add(fdo.id, self._fdo_text_fields(fdo))

        # Build graph
        self._graph.build(self._vault.index)

        # Index meta.yaml entries from workspace repos
        self._meta_entries: dict[str, dict[str, Any]] = {}
        try:
            meta_entries = self._scan_repo_metadata()
            for entry in meta_entries:
                self._meta_entries[entry["meta_id"]] = entry
                self._index_meta_entry(entry)
        except Exception as e:
            logger.warning(f"meta.yaml scanning failed (search still works): {e}")

        elapsed = time.time() - t0
        logger.info(
            f"Index built in {elapsed:.1f}s — "
            f"bm25={len(self._bm25._docs)} (incl {len(self._meta_entries)} meta), "
            f"graph={len(self._graph._adjacency)}"
        )

    @staticmethod
    def _fdo_text_fields(fdo: "FDO") -> dict[str, str]:
        """Extract text fields for BM25 indexing."""
        return {
            "id": fdo.id.replace("-", " "),
            "title": fdo.title,
            "tags": " ".join(fdo.tags),
            "summary": fdo.summary,
            "body": fdo.body,
        }

    @staticmethod
    def _fdo_embed_text(fdo: "FDO") -> str:
        """Build the text to embed — title + summary is usually enough.

        We embed title + tags + summary (not full body) for:
        1. Better signal-to-noise (body has tables, code, etc.)
        2. Faster embedding
        3. Summary captures the core meaning
        """
        parts = [fdo.title]
        if fdo.tags:
            parts.append(" ".join(fdo.tags))
        summary = fdo.summary
        if summary:
            parts.append(summary)
        return " | ".join(parts)

    # Directories to skip when scanning for meta.yaml
    _META_SKIP = {
        "__pycache__", ".venv", "venv", "node_modules", ".git", ".tox",
        ".mypy_cache", ".pytest_cache", "dist", "build", ".egg-info",
        ".claude", ".obsidian", ".kronos_cache", "legacy", "external_repos",
    }

    def _scan_repo_metadata(self) -> list[dict[str, Any]]:
        """Scan workspace repos for meta.yaml files worth indexing.

        Returns dicts with keys: meta_id, repo, path, description, semantic_scope,
        semantic_tags, status, key_results.

        Only scans depth 0-3 to avoid auto-generated __pycache__ clutter.
        """
        workspace = self._vault.vault_path.parent
        repos_yaml = workspace / "repos.yaml"
        if not repos_yaml.exists():
            return []

        try:
            manifest = yaml.safe_load(repos_yaml.read_text(encoding="utf-8")) or {}
        except Exception:
            return []

        repo_names = [
            r["path"] for r in manifest.get("repos", [])
            if r.get("tier") in ("core", "support")
        ]

        entries: list[dict[str, Any]] = []
        for repo_name in repo_names:
            repo_root = workspace / repo_name
            if not repo_root.is_dir():
                continue
            self._walk_meta_yaml(repo_root, repo_name, "", 0, 3, entries)

        logger.info(f"Scanned {len(entries)} meta.yaml entries from {len(repo_names)} repos")
        return entries

    def _walk_meta_yaml(
        self, directory: Path, repo: str, rel_path: str,
        depth: int, max_depth: int, entries: list[dict[str, Any]],
    ):
        """Recursively walk directories for meta.yaml, respecting skip list and max depth."""
        meta_file = directory / "meta.yaml"
        if meta_file.is_file():
            try:
                meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) or {}
            except Exception:
                meta = {}
            desc = meta.get("description", "")
            if desc:  # Only index if there's something meaningful
                meta_id = f"meta::{repo}/{rel_path}" if rel_path else f"meta::{repo}"
                entries.append({
                    "meta_id": meta_id,
                    "repo": repo,
                    "path": rel_path,
                    "description": desc,
                    "semantic_scope": meta.get("semantic_scope", []),
                    "semantic_tags": meta.get("semantic_tags", []),
                    "status": meta.get("status", ""),
                    "key_results": meta.get("key_results", ""),
                })

        if depth >= max_depth:
            return

        try:
            for child in sorted(directory.iterdir()):
                if not child.is_dir():
                    continue
                name = child.name
                if name.startswith(".") and name not in (".spec",):
                    continue
                if name in self._META_SKIP:
                    continue
                child_rel = f"{rel_path}/{name}" if rel_path else name
                self._walk_meta_yaml(child, repo, child_rel, depth + 1, max_depth, entries)
        except PermissionError:
            pass

    def _index_meta_entry(self, entry: dict[str, Any]):
        """Index a single meta.yaml entry into BM25."""
        meta_id = entry["meta_id"]
        tags_text = " ".join(entry.get("semantic_tags", []) + entry.get("semantic_scope", []))
        fields = {
            "id": entry["repo"] + " " + entry["path"].replace("/", " "),
            "title": entry.get("description", "")[:200],
            "tags": tags_text,
            "summary": entry.get("description", ""),
            "body": entry.get("key_results", ""),
        }
        self._bm25.add(meta_id, fields)

    def search(
        self,
        query: str,
        max_results: int = 10,
        channels: list[str] | None = None,
        graph_depth: int = 1,
    ) -> list[FusedResult]:
        """Run hybrid search across all (or specified) channels.

        Args:
            query: Search query text
            max_results: Max results to return
            channels: Which channels to use (default: all available)
            graph_depth: How many hops for graph expansion

        Returns:
            Fused results sorted by RRF score
        """
        self._ensure_indexed()

        if channels is None:
            # Default: fast channels only (no semantic model load)
            channels = ["tag_exact", "keyword", "graph"]

        # Expand retrieval window for fusion (more candidates = better fusion)
        retrieve_k = max_results * 3

        channel_results: dict[str, list[SearchResult]] = {}

        # 0. Exact tag matching — highest priority channel
        if "tag_exact" in channels:
            tag_results = self._tag_exact_search(query, max_results=retrieve_k)
            if tag_results:
                channel_results["tag_exact"] = tag_results

        # 1. Keyword search (BM25)
        if "keyword" in channels:
            kw_results = self._bm25.search(query, max_results=retrieve_k)
            channel_results["keyword"] = kw_results

        # 2. Semantic search (lazy build on first use, non-blocking)
        if "semantic" in channels:
            ready = self._ensure_semantic(blocking=False)
            if ready and self._semantic_indexed:
                sem_results = self._semantic.search(query, max_results=retrieve_k)
                channel_results["semantic"] = sem_results
            elif self._semantic_loading:
                logger.info("Semantic index still loading — returning results without semantic channel")

        # 3. Graph expansion (from top hits across other channels)
        if "graph" in channels and channel_results:
            seed_ids: list[str] = []
            seen: set[str] = set()
            for ch_results in channel_results.values():
                for r in ch_results[:5]:  # Top 5 from each channel
                    if r.fdo_id not in seen:
                        seed_ids.append(r.fdo_id)
                        seen.add(r.fdo_id)
            if seed_ids:
                graph_results = self._graph.expand(
                    seed_ids, max_depth=graph_depth, max_expand=retrieve_k
                )
                channel_results["graph"] = graph_results

        if not channel_results:
            return []

        # 4. Fuse with RRF
        fused = self._ranker.fuse(
            channel_results,
            channel_weights=self.CHANNEL_WEIGHTS,
            max_results=max_results,
        )

        return fused

    def _tag_exact_search(self, query: str, max_results: int = 20) -> list[SearchResult]:
        """Find FDOs where query terms exactly match tags, titles, or IDs.

        Scoring: exact tag match > partial tag match > title contains > id contains.
        This ensures known vocabulary always surfaces results.
        """
        query_lower = query.lower()
        query_terms = set(_tokenize(query))
        scores: dict[str, float] = {}

        for fdo in self._vault.index.values():
            score = 0.0
            fdo_tags_lower = [t.lower() for t in fdo.tags]

            # Exact full-query match against a tag (e.g. query="PAC conservation", tag="PAC conservation")
            if query_lower in fdo_tags_lower:
                score += 20.0

            # Exact full-query match against title
            if query_lower == fdo.title.lower():
                score += 15.0

            # Exact full-query match against ID
            if query_lower == fdo.id.lower() or query_lower == fdo.id.replace("-", " ").lower():
                score += 15.0

            # Individual query terms match tags (partial)
            for term in query_terms:
                for tag in fdo_tags_lower:
                    tag_terms = set(_tokenize(tag))
                    if term in tag_terms:
                        score += 3.0

            # Query substring appears in a tag
            for tag in fdo_tags_lower:
                if query_lower in tag or tag in query_lower:
                    score += 5.0

            # Title contains query
            if query_lower in fdo.title.lower():
                score += 4.0

            if score > 0:
                scores[fdo.id] = score

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [
            SearchResult(fdo_id=fid, score=s, channel="tag_exact")
            for fid, s in ranked[:max_results]
        ]

    def get_meta(self, meta_id: str) -> dict[str, Any] | None:
        """Look up a meta.yaml entry by its ID (e.g. 'meta::dawn-field-theory/foundational')."""
        return getattr(self, '_meta_entries', {}).get(meta_id)

    def stats(self) -> dict[str, Any]:
        """Return index statistics."""
        meta_count = len(getattr(self, '_meta_entries', {}))
        return {
            "bm25_docs": len(self._bm25._docs),
            "meta_entries": meta_count,
            "semantic_indexed": self._semantic.indexed_count,
            "semantic_available": self._semantic.available,
            "semantic_model": self._semantic._model_name,
            "graph_nodes": len(self._graph._adjacency),
            "file_watcher_tracked": self._watcher.tracked_count,
            "initialized": self._initialized,
        }
