"""
Concurrency and stress tests for Kronos MCP engines.

Tests: deadlocks, race conditions, atomic writes, lock contention,
file corruption under concurrent access.

Uses temporary directories — no real vault, no side-effects.

Run:
    cd GRIM && python -m pytest tests/test_concurrency.py -v
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import tempfile
import threading
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from datetime import date
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

# ── Bootstrap — load engine modules directly ─────────────────────────────────
grim_root = Path(__file__).resolve().parent.parent
_mcp_src = grim_root / "mcp" / "kronos" / "src" / "kronos_mcp"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load fileutil first (dependency of others)
_fileutil_mod = _load_module("kronos_mcp.fileutil", _mcp_src / "fileutil.py")
atomic_write = _fileutil_mod.atomic_write

_tasks_mod = _load_module("kronos_mcp.tasks", _mcp_src / "tasks.py")
_board_mod = _load_module("kronos_mcp.board", _mcp_src / "board.py")
_calendar_mod = _load_module("kronos_mcp.calendar", _mcp_src / "calendar.py")

TaskEngine = _tasks_mod.TaskEngine
BoardEngine = _board_mod.BoardEngine
COLUMNS = _board_mod.COLUMNS
CalendarEngine = _calendar_mod.CalendarEngine

# ── Deadlock detection helper ────────────────────────────────────────────────

DEADLOCK_TIMEOUT = 10  # seconds


def run_concurrent_no_deadlock(fns: list, timeout: float = DEADLOCK_TIMEOUT):
    """Run callables concurrently, fail if any doesn't complete in timeout."""
    with ThreadPoolExecutor(max_workers=len(fns)) as pool:
        futures = [pool.submit(fn) for fn in fns]
        for f in as_completed(futures, timeout=timeout):
            f.result()  # Propagate exceptions


# ── Fixtures ─────────────────────────────────────────────────────────────────

MINIMAL_FEATURE_FDO = """\
---
id: feat-test-conc
title: "Concurrency Test Feature"
domain: ai-systems
created: "2026-03-01"
updated: "2026-03-01"
status: developing
confidence: 0.5
related:
  - proj-test
tags: [test]
stories: []
---

# Concurrency Test Feature

## Summary
Feature for concurrency tests.
"""


def make_temp_vault(features: dict[str, str] | None = None) -> str:
    """Create a temp vault with optional feature FDOs. Returns vault path."""
    vault_dir = tempfile.mkdtemp(prefix="kronos_conc_")
    # Create required subdirectories
    for sub in ["ai-systems", "projects", "calendar", "notes"]:
        os.makedirs(os.path.join(vault_dir, sub), exist_ok=True)

    if features:
        for fdo_id, content in features.items():
            domain = "ai-systems"
            p = Path(vault_dir) / domain / f"{fdo_id}.md"
            p.write_text(content, encoding="utf-8")

    return vault_dir


def make_engines(vault_dir: str):
    """Create TaskEngine + BoardEngine + CalendarEngine for the temp vault."""
    te = TaskEngine(vault_dir)
    be = BoardEngine(vault_dir, te)
    ce = CalendarEngine(vault_dir, be)
    return te, be, ce


def add_story(te: TaskEngine, feat_id: str, title: str, status: str = "new") -> str:
    """Create a story and return its ID."""
    result = te.create_story(
        feat_id=feat_id,
        title=title,
        status=status,
    )
    return result["created"]


# ══════════════════════════════════════════════════════════════════════════════
# Test Classes
# ══════════════════════════════════════════════════════════════════════════════


class TestAtomicWrite(TestCase):
    """Tests for the atomic_write utility."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="atomic_")
        self.path = Path(self.tmpdir) / "test.yaml"

    def test_basic_write_and_read(self):
        atomic_write(self.path, "hello world")
        assert self.path.read_text() == "hello world"

    def test_overwrite_existing(self):
        self.path.write_text("old content")
        atomic_write(self.path, "new content")
        assert self.path.read_text() == "new content"

    def test_creates_parent_dirs(self):
        nested = Path(self.tmpdir) / "a" / "b" / "c" / "file.txt"
        atomic_write(nested, "deep content")
        assert nested.read_text() == "deep content"

    def test_unicode_content(self):
        content = "Poincar\u00e9 \u2014 m\u00f6bius \u2192 \u03b1\u03b2\u03b3"
        atomic_write(self.path, content)
        assert self.path.read_text(encoding="utf-8") == content

    def test_empty_content(self):
        atomic_write(self.path, "")
        assert self.path.read_text() == ""

    def test_large_content(self):
        content = "x" * (1024 * 1024)  # 1MB
        atomic_write(self.path, content)
        assert len(self.path.read_text()) == 1024 * 1024

    def test_no_leftover_tmp_files(self):
        atomic_write(self.path, "content")
        tmp_files = [f for f in os.listdir(self.tmpdir) if f.endswith(".tmp")]
        assert tmp_files == [], f"Leftover tmp files: {tmp_files}"

    def test_original_intact_on_failure(self):
        """If os.replace fails, original file should be intact."""
        self.path.write_text("original")
        original_replace = os.replace
        def failing_replace(*args, **kwargs):
            raise OSError("mock fail")
        _fileutil_mod.os.replace = failing_replace
        try:
            try:
                atomic_write(self.path, "new content")
            except OSError:
                pass
            assert self.path.read_text() == "original"
        finally:
            _fileutil_mod.os.replace = original_replace

    def test_concurrent_writes_same_file(self):
        """10 threads writing to the same file. Final content should be one valid value.

        atomic_write() has built-in retry for Windows PermissionError.
        """
        results = []
        barrier = threading.Barrier(10)

        def writer(i):
            barrier.wait()
            atomic_write(self.path, f"thread-{i}")
            results.append(i)

        run_concurrent_no_deadlock([lambda i=i: writer(i) for i in range(10)])
        content = self.path.read_text()
        assert content.startswith("thread-"), f"Corrupt content: {content!r}"

    def test_concurrent_writes_different_files(self):
        """10 threads each writing to different files."""
        def writer(i):
            p = Path(self.tmpdir) / f"file-{i}.txt"
            atomic_write(p, f"content-{i}")

        run_concurrent_no_deadlock([lambda i=i: writer(i) for i in range(10)])
        for i in range(10):
            p = Path(self.tmpdir) / f"file-{i}.txt"
            assert p.read_text() == f"content-{i}"


class TestBoardTaskDeadlock(TestCase):
    """Tests that board + task operations don't deadlock under concurrency."""

    def setUp(self):
        self.vault_dir = make_temp_vault({"feat-test-conc": MINIMAL_FEATURE_FDO})
        self.te, self.be, _ = make_engines(self.vault_dir)

    def _add_stories(self, n: int) -> list[str]:
        ids = []
        for i in range(n):
            sid = add_story(self.te, "feat-test-conc", f"Story {i}")
            ids.append(sid)
        return ids

    def test_move_story_no_deadlock(self):
        """move_story + task update from different threads should not deadlock."""
        sids = self._add_stories(3)
        for sid in sids:
            self.be.add_to_board(sid, "new")

        def move_op(sid, col):
            self.be.move_story(sid, col)

        def update_op(sid):
            self.te.update_item(sid, {"priority": "high"})

        fns = [
            lambda: move_op(sids[0], "active"),
            lambda: update_op(sids[0]),
            lambda: move_op(sids[1], "in_progress"),
            lambda: update_op(sids[1]),
        ]
        run_concurrent_no_deadlock(fns)

    def test_concurrent_board_moves_same_story(self):
        """5 threads moving the same story to different columns."""
        sids = self._add_stories(1)
        self.be.add_to_board(sids[0], "new")
        cols = ["active", "in_progress", "resolved", "new", "active"]

        def mover(col):
            self.be.move_story(sids[0], col)

        run_concurrent_no_deadlock([lambda c=c: mover(c) for c in cols])
        # Story should be in exactly one column
        board = self.be._load_board()
        found = sum(1 for ids in board["columns"].values() if sids[0] in ids)
        assert found == 1, f"Story in {found} columns"

    def test_concurrent_board_moves_different_stories(self):
        """5 stories, each moved by a different thread."""
        sids = self._add_stories(5)
        for sid in sids:
            self.be.add_to_board(sid, "new")

        def mover(sid):
            self.be.move_story(sid, "active")

        run_concurrent_no_deadlock([lambda s=s: mover(s) for s in sids])
        board = self.be._load_board()
        for sid in sids:
            assert sid in board["columns"]["active"]

    def test_bidirectional_lock_order(self):
        """Thread A: board->task, Thread B: task->board. Should not deadlock."""
        sids = self._add_stories(2)
        self.be.add_to_board(sids[0], "new")
        self.be.add_to_board(sids[1], "new")

        def board_then_task():
            self.be.move_story(sids[0], "active")
            self.te.update_item(sids[0], {"priority": "high"})

        def task_then_board():
            self.te.update_item(sids[1], {"priority": "critical"})
            self.be.move_story(sids[1], "active")

        run_concurrent_no_deadlock([board_then_task, task_then_board])

    def test_add_to_board_no_deadlock(self):
        """Concurrent add_to_board calls don't deadlock."""
        sids = self._add_stories(5)

        def adder(sid):
            self.be.add_to_board(sid, "new")

        run_concurrent_no_deadlock([lambda s=s: adder(s) for s in sids])
        board = self.be._load_board()
        for sid in sids:
            assert sid in board["columns"]["new"]

    def test_board_move_while_task_create(self):
        """Thread A moves story, Thread B creates task in same feature."""
        sids = self._add_stories(1)
        self.be.add_to_board(sids[0], "new")

        def mover():
            self.be.move_story(sids[0], "active")

        def creator():
            self.te.create_task(
                story_id=sids[0],
                title="New task",
            )

        run_concurrent_no_deadlock([mover, creator])

    def test_rapid_add_remove_cycle(self):
        """Add story, move through columns, rapid cycles across threads."""
        sids = self._add_stories(3)

        def cycle(sid):
            for col in ["new", "active", "in_progress", "resolved"]:
                self.be.move_story(sid, col)

        run_concurrent_no_deadlock([lambda s=s: cycle(s) for s in sids])

    def test_board_view_during_moves(self):
        """Read board view while moves are happening — no crash."""
        sids = self._add_stories(3)
        for sid in sids:
            self.be.add_to_board(sid, "new")

        errors = []

        def mover():
            for _ in range(5):
                for sid in sids:
                    self.be.move_story(sid, "active")
                    self.be.move_story(sid, "new")

        def reader():
            for _ in range(10):
                try:
                    view = self.be.board_view()
                    assert "columns" in view
                except Exception as e:
                    errors.append(e)

        run_concurrent_no_deadlock([mover, reader])
        assert not errors, f"Reader errors: {errors}"

    def test_board_save_atomicity(self):
        """board.yaml should never be corrupted after concurrent moves."""
        sids = self._add_stories(5)
        for sid in sids:
            self.be.add_to_board(sid, "new")

        def mover(i):
            cols = ["active", "in_progress", "resolved", "new"]
            for _ in range(3):
                self.be.move_story(sids[i], cols[i % len(cols)])

        run_concurrent_no_deadlock([lambda i=i: mover(i) for i in range(5)])
        # Verify board.yaml is valid YAML
        content = self.be.board_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        assert "columns" in data
        for col in COLUMNS:
            assert isinstance(data["columns"].get(col, []), list)

    def test_draft_guard_concurrent(self):
        """Multiple threads trying to move draft stories."""
        # Create stories with draft status
        sids = []
        for i in range(3):
            sid = add_story(self.te, "feat-test-conc", f"Draft {i}", status="draft")
            sids.append(sid)

        results = []

        def mover(sid):
            r = self.be.move_story(sid, "active")
            results.append(r)

        run_concurrent_no_deadlock([lambda s=s: mover(s) for s in sids])
        # All should have error about draft
        for r in results:
            assert "error" in r

    def test_cleanup_archived_no_deadlock(self):
        """cleanup_archived + concurrent task operations don't deadlock."""
        sids = self._add_stories(3)
        for sid in sids:
            self.be.add_to_board(sid, "new")

        def cleaner():
            self.be.cleanup_archived()

        def updater():
            for sid in sids:
                self.te.update_item(sid, {"priority": "medium"})

        run_concurrent_no_deadlock([cleaner, updater, cleaner])

    def test_move_nonexistent_story_concurrent(self):
        """Multiple threads moving non-existent stories should not crash."""
        def mover(i):
            self.be.move_story(f"story-nonexistent-{i}", "active")

        run_concurrent_no_deadlock([lambda i=i: mover(i) for i in range(5)])

    def test_remove_and_add_concurrent(self):
        """Remove from board while another thread adds."""
        sids = self._add_stories(2)
        self.be.add_to_board(sids[0], "new")

        def remover():
            self.be.remove_from_board(sids[0])

        def adder():
            self.be.add_to_board(sids[1], "new")

        run_concurrent_no_deadlock([remover, adder])

    def test_backlog_view_during_moves(self):
        """Backlog view while board is being modified."""
        sids = self._add_stories(5)
        for sid in sids[:3]:
            self.be.add_to_board(sid, "new")

        def mover():
            for sid in sids[:3]:
                self.be.move_story(sid, "active")

        def reader():
            view = self.be.backlog_view()
            assert "backlog" in view

        run_concurrent_no_deadlock([mover, reader])


_vault_mod = _load_module("kronos_mcp.vault", _mcp_src / "vault.py")
VaultEngine = _vault_mod.VaultEngine
FDO = _vault_mod.FDO


class TestVaultWriteRaces(TestCase):
    """Tests for vault FDO write races."""

    def setUp(self):
        self.vault_dir = make_temp_vault()
        self.VaultEngine = VaultEngine
        self.FDO = FDO
        self.vault = self.VaultEngine(self.vault_dir)

    def _make_fdo(self, fdo_id: str, body: str = "Test body") -> "FDO":
        return self.FDO(
            id=fdo_id,
            title=f"Test {fdo_id}",
            domain="ai-systems",
            created="2026-03-01",
            updated="2026-03-01",
            status="seed",
            confidence=0.5,
            related=[],
            source_repos=[],
            tags=["test"],
            body=body,
            file_path="",
        )

    def test_concurrent_write_same_fdo(self):
        """5 threads writing to the same FDO — file should be valid."""
        fdo = self._make_fdo("test-race-1")
        self.vault.write_fdo(fdo)

        def writer(i):
            f = self._make_fdo("test-race-1", body=f"Body from thread {i}")
            self.vault.write_fdo(f)

        run_concurrent_no_deadlock([lambda i=i: writer(i) for i in range(5)])
        # File should be valid markdown with frontmatter
        p = Path(self.vault_dir) / "ai-systems" / "test-race-1.md"
        content = p.read_text(encoding="utf-8")
        assert content.startswith("---\n")
        assert "Body from thread" in content

    def test_concurrent_write_different_fdos(self):
        """5 threads each writing different FDOs."""
        def writer(i):
            fdo = self._make_fdo(f"test-diff-{i}")
            self.vault.write_fdo(fdo)

        run_concurrent_no_deadlock([lambda i=i: writer(i) for i in range(5)])
        for i in range(5):
            p = Path(self.vault_dir) / "ai-systems" / f"test-diff-{i}.md"
            assert p.exists(), f"Missing: {p}"

    def test_read_during_write(self):
        """Read FDO while another thread is writing it."""
        fdo = self._make_fdo("test-rw-1")
        self.vault.write_fdo(fdo)

        non_perm_errors = []

        def writer():
            for i in range(10):
                f = self._make_fdo("test-rw-1", body=f"Update {i}")
                self.vault.write_fdo(f)

        def reader():
            for _ in range(10):
                try:
                    self.vault.get("test-rw-1")
                except PermissionError:
                    pass  # Windows: read during replace
                except Exception as e:
                    non_perm_errors.append(e)

        run_concurrent_no_deadlock([writer, reader])
        assert not non_perm_errors, f"Non-permission errors: {non_perm_errors}"

    def test_write_fdo_lock_isolation(self):
        """Per-FDO lock should NOT block writes to different FDOs."""
        # Create two FDOs
        fdo1 = self._make_fdo("test-iso-1")
        fdo2 = self._make_fdo("test-iso-2")
        self.vault.write_fdo(fdo1)
        self.vault.write_fdo(fdo2)

        times = {}

        def writer(fdo_id, i):
            start = time.time()
            f = self._make_fdo(fdo_id, body=f"Thread {i}")
            self.vault.write_fdo(f)
            times[f"{fdo_id}-{i}"] = time.time() - start

        run_concurrent_no_deadlock([
            lambda: writer("test-iso-1", 0),
            lambda: writer("test-iso-2", 1),
        ])
        # Both should complete quickly (no cross-FDO blocking)
        for key, t in times.items():
            assert t < 5.0, f"{key} took {t}s"

    def test_rapid_create_update_cycle(self):
        """Create FDO, then update 20 times from 3 threads."""
        fdo = self._make_fdo("test-rapid-1")
        self.vault.write_fdo(fdo)

        def updater(thread_id):
            for i in range(20):
                f = self._make_fdo("test-rapid-1", body=f"T{thread_id}-{i}")
                self.vault.write_fdo(f)

        run_concurrent_no_deadlock([lambda t=t: updater(t) for t in range(3)])
        p = Path(self.vault_dir) / "ai-systems" / "test-rapid-1.md"
        content = p.read_text(encoding="utf-8")
        assert content.startswith("---\n")

    def test_file_not_corrupted_after_concurrent_ops(self):
        """Verify file is valid YAML frontmatter after many concurrent writes."""
        fdo = self._make_fdo("test-corrupt-1")
        self.vault.write_fdo(fdo)

        def writer(i):
            f = self._make_fdo("test-corrupt-1", body=f"Version {i}\nMultiline body.")
            self.vault.write_fdo(f)

        run_concurrent_no_deadlock([lambda i=i: writer(i) for i in range(10)])
        p = Path(self.vault_dir) / "ai-systems" / "test-corrupt-1.md"
        content = p.read_text(encoding="utf-8")
        # Parse frontmatter
        match = re.match(r"^---\n(.*?)---\n", content, re.DOTALL)
        assert match, "No valid frontmatter"
        fm = yaml.safe_load(match.group(1))
        assert fm["id"] == "test-corrupt-1"

    def test_concurrent_write_creates_domain_dir(self):
        """Multiple threads creating FDOs in a domain that doesn't exist yet."""
        def writer(i):
            fdo = self.FDO(
                id=f"test-newdom-{i}",
                title=f"Test {i}",
                domain="computing",
                created="2026-03-01",
                updated="2026-03-01",
                status="seed",
                confidence=0.5,
                related=[],
                source_repos=[],
                tags=[],
                body="body",
                file_path="",
            )
            self.vault.write_fdo(fdo)

        os.makedirs(os.path.join(self.vault_dir, "computing"), exist_ok=True)
        run_concurrent_no_deadlock([lambda i=i: writer(i) for i in range(5)])
        for i in range(5):
            p = Path(self.vault_dir) / "computing" / f"test-newdom-{i}.md"
            assert p.exists()

    def test_update_field_concurrent(self):
        """Concurrent update_field on the same FDO."""
        fdo = self._make_fdo("test-uf-1")
        self.vault.write_fdo(fdo)
        # Force index build
        _ = self.vault.index

        def updater(i):
            self.vault.update_field("test-uf-1", "confidence", 0.1 * i)

        run_concurrent_no_deadlock([lambda i=i: updater(i) for i in range(5)])
        p = Path(self.vault_dir) / "ai-systems" / "test-uf-1.md"
        content = p.read_text(encoding="utf-8")
        assert content.startswith("---\n")


class TestCalendarConcurrency(TestCase):
    """Tests for calendar engine concurrency."""

    def setUp(self):
        self.vault_dir = make_temp_vault({"feat-test-conc": MINIMAL_FEATURE_FDO})
        self.te, self.be, self.ce = make_engines(self.vault_dir)

    def test_concurrent_add_personal_events(self):
        """5 threads each adding a personal event."""
        results = []

        def adder(i):
            r = self.ce.add_personal(f"Event {i}", f"2026-03-{10+i:02d}")
            results.append(r)

        run_concurrent_no_deadlock([lambda i=i: adder(i) for i in range(5)])
        assert len(results) == 5
        # All should have unique IDs
        ids = [r["created"] for r in results]
        assert len(set(ids)) == 5, f"Duplicate IDs: {ids}"

    def test_concurrent_sync_schedule(self):
        """3 threads syncing simultaneously."""
        # Add some stories to board first
        sids = []
        for i in range(3):
            sid = add_story(self.te, "feat-test-conc", f"Story {i}")
            sids.append(sid)
            self.be.add_to_board(sid, "active")

        def syncer():
            self.ce.sync_schedule()

        run_concurrent_no_deadlock([syncer, syncer, syncer])
        # Schedule should be valid
        data = self.ce._load_yaml(self.ce.schedule_path)
        assert "entries" in data

    def test_add_event_during_sync(self):
        """Add personal event while sync is running."""
        sids = []
        for i in range(2):
            sid = add_story(self.te, "feat-test-conc", f"Story {i}")
            sids.append(sid)
            self.be.add_to_board(sid, "active")

        def syncer():
            self.ce.sync_schedule()

        def adder():
            self.ce.add_personal("Personal event", "2026-03-15")

        run_concurrent_no_deadlock([syncer, adder])

    def test_calendar_view_during_sync(self):
        """Read calendar while sync writes."""
        errors = []

        def syncer():
            self.ce.sync_schedule()

        def reader():
            try:
                self.ce.calendar_view("2026-03-01", "2026-03-31")
            except Exception as e:
                errors.append(e)

        run_concurrent_no_deadlock([syncer, reader])
        assert not errors

    def test_concurrent_update_personal(self):
        """3 threads updating different events."""
        for i in range(3):
            self.ce.add_personal(f"Event {i}", f"2026-03-{10+i:02d}")

        def updater(i):
            eid = f"personal-{i+1:03d}"
            self.ce.update_personal(eid, {"title": f"Updated {i}"})

        run_concurrent_no_deadlock([lambda i=i: updater(i) for i in range(3)])

    def test_concurrent_delete_personal(self):
        """3 threads deleting different events."""
        for i in range(3):
            self.ce.add_personal(f"Event {i}", f"2026-03-{10+i:02d}")

        def deleter(i):
            eid = f"personal-{i+1:03d}"
            self.ce.delete_personal(eid)

        run_concurrent_no_deadlock([lambda i=i: deleter(i) for i in range(3)])
        data = self.ce._load_yaml(self.ce.personal_path)
        assert len(data.get("entries", [])) == 0

    def test_sequential_id_generation_concurrent(self):
        """Verify no duplicate personal-NNN IDs when adding concurrently."""
        results = []

        def adder(i):
            r = self.ce.add_personal(f"Event {i}", f"2026-04-{i+1:02d}")
            results.append(r["created"])

        run_concurrent_no_deadlock([lambda i=i: adder(i) for i in range(10)])
        assert len(set(results)) == 10, f"Duplicate IDs among {results}"

    def test_personal_yaml_not_corrupted(self):
        """personal.yaml is valid YAML after concurrent adds."""
        def adder(i):
            self.ce.add_personal(f"Event {i}", f"2026-03-{i+1:02d}")

        run_concurrent_no_deadlock([lambda i=i: adder(i) for i in range(5)])
        content = self.ce.personal_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        assert isinstance(data.get("entries"), list)

    def test_schedule_yaml_not_corrupted(self):
        """schedule.yaml is valid YAML after concurrent syncs."""
        sids = []
        for i in range(2):
            sid = add_story(self.te, "feat-test-conc", f"Story {i}")
            sids.append(sid)
            self.be.add_to_board(sid, "active")

        def syncer():
            self.ce.sync_schedule()

        run_concurrent_no_deadlock([syncer for _ in range(3)])
        content = self.ce.schedule_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        assert isinstance(data.get("entries"), list)

    def test_update_and_delete_same_event(self):
        """Race between update and delete on same event."""
        self.ce.add_personal("Target", "2026-03-10")

        def updater():
            self.ce.update_personal("personal-001", {"title": "Updated"})

        def deleter():
            self.ce.delete_personal("personal-001")

        # Both should complete without crash (one may fail gracefully)
        run_concurrent_no_deadlock([updater, deleter])


class TestMemoryWriteConcurrency(TestCase):
    """Tests for memory file write concurrency."""

    def setUp(self):
        self.vault_dir = make_temp_vault()
        self.memory_path = Path(self.vault_dir) / "memory.md"
        self.memory_path.write_text("## Section A\nContent A\n\n## Section B\nContent B\n")

    def test_concurrent_atomic_writes(self):
        """Multiple threads writing memory.md with atomic_write."""
        def writer(i):
            atomic_write(self.memory_path, f"## Thread {i}\nContent from {i}\n")

        run_concurrent_no_deadlock([lambda i=i: writer(i) for i in range(5)])
        content = self.memory_path.read_text()
        assert content.startswith("## Thread")

    def test_read_during_write(self):
        """Read memory while write is happening — should not crash or return garbage."""
        non_perm_errors = []

        def writer():
            for i in range(10):
                atomic_write(self.memory_path, f"Update {i}\n")

        def reader():
            for _ in range(10):
                try:
                    self.memory_path.read_text()
                except PermissionError:
                    pass  # Windows: read during replace
                except Exception as e:
                    non_perm_errors.append(e)

        run_concurrent_no_deadlock([writer, reader])
        assert not non_perm_errors, f"Non-permission errors: {non_perm_errors}"

    def test_memory_file_not_corrupted(self):
        """File content is always valid (not a mix of two writes)."""
        contents = set()

        def writer(i):
            # Write a distinguishable pattern
            content = f"MARKER-{i}\n" + "x" * 100 + "\n"
            atomic_write(self.memory_path, content)

        run_concurrent_no_deadlock([lambda i=i: writer(i) for i in range(5)])
        content = self.memory_path.read_text()
        # Content should start with exactly one MARKER
        assert content.count("MARKER-") == 1

    def test_large_content_concurrent(self):
        """Large memory sections written concurrently."""
        def writer(i):
            content = f"## Big Section {i}\n" + "data\n" * 1000
            atomic_write(self.memory_path, content)

        run_concurrent_no_deadlock([lambda i=i: writer(i) for i in range(3)])
        content = self.memory_path.read_text()
        assert "## Big Section" in content

    def test_empty_then_write_concurrent(self):
        """Start with empty file, concurrent writes."""
        self.memory_path.write_text("")

        def writer(i):
            atomic_write(self.memory_path, f"Content {i}\n")

        run_concurrent_no_deadlock([lambda i=i: writer(i) for i in range(5)])
        content = self.memory_path.read_text()
        assert content.startswith("Content")


class TestNotesConcurrency(TestCase):
    """Tests for notes append concurrency.

    These test the file-level operations; the actual handle_note_append
    handler is tested via MCP handler tests.
    """

    def setUp(self):
        self.notes_dir = Path(tempfile.mkdtemp(prefix="notes_"))

    def test_concurrent_append_same_file(self):
        """5 threads appending to the same file using atomic_write."""
        target = self.notes_dir / "notes.md"
        target.write_text("# Header\n\n")
        lock = threading.Lock()

        def appender(i):
            with lock:
                content = target.read_text()
                content += f"## Note {i}\nBody {i}\n\n"
                atomic_write(target, content)

        run_concurrent_no_deadlock([lambda i=i: appender(i) for i in range(5)])
        content = target.read_text()
        # All 5 notes should be present (lock serializes)
        for i in range(5):
            assert f"## Note {i}" in content

    def test_concurrent_append_different_files(self):
        """Threads appending to different month files."""
        def appender(month):
            target = self.notes_dir / f"notes-2026-{month:02d}.md"
            atomic_write(target, f"# Notes for month {month}\n")

        run_concurrent_no_deadlock([lambda m=m: appender(m) for m in range(1, 6)])
        for m in range(1, 6):
            p = self.notes_dir / f"notes-2026-{m:02d}.md"
            assert p.exists()

    def test_file_creation_race(self):
        """2 threads both try to create the same notes file."""
        target = self.notes_dir / "notes-race.md"
        lock = threading.Lock()

        def creator(i):
            with lock:
                if not target.exists():
                    atomic_write(target, f"Created by {i}\n")
                content = target.read_text()
                content += f"Entry {i}\n"
                atomic_write(target, content)

        run_concurrent_no_deadlock([lambda i=i: creator(i) for i in range(2)])
        assert target.exists()
        content = target.read_text()
        assert "Created by" in content

    def test_notes_file_not_corrupted(self):
        """File is valid after concurrent appends."""
        target = self.notes_dir / "notes-valid.md"
        target.write_text("---\nid: notes-test\n---\n\n# Notes\n\n")
        lock = threading.Lock()

        def appender(i):
            with lock:
                content = target.read_text()
                content += f"## Entry {i}\nBody\n\n---\n\n"
                atomic_write(target, content)

        run_concurrent_no_deadlock([lambda i=i: appender(i) for i in range(5)])
        content = target.read_text()
        assert content.startswith("---\n")
        # Should have frontmatter
        match = re.match(r"^---\n(.*?)---\n", content, re.DOTALL)
        assert match
        fm = yaml.safe_load(match.group(1))
        assert fm["id"] == "notes-test"


class TestSearchConcurrency(TestCase):
    """Tests for search engine concurrency."""

    def setUp(self):
        self.vault_dir = make_temp_vault()
        # Create some FDO files for indexing
        for i in range(5):
            content = f"""---
id: test-search-{i}
title: "Search Test {i}"
domain: ai-systems
created: "2026-03-01"
updated: "2026-03-01"
status: seed
confidence: 0.5
related: []
source_repos: []
tags: [search, test]
---

# Search Test {i}

## Summary
Content for search test {i}. Keywords: entropy, recursion, PAC.
"""
            p = Path(self.vault_dir) / "ai-systems" / f"test-search-{i}.md"
            p.write_text(content, encoding="utf-8")

    def _make_search_engine(self):
        """Create a search engine with semantic disabled for speed."""
        if "kronos_mcp.search" not in sys.modules:
            _load_module("kronos_mcp.search", _mcp_src / "search.py")
        _search_mod = sys.modules["kronos_mcp.search"]
        vault = VaultEngine(self.vault_dir)
        se = _search_mod.SearchEngine(vault)
        return se

    def test_concurrent_search_requests(self):
        """10 threads all searching simultaneously."""
        se = self._make_search_engine()
        results = []

        def searcher(i):
            r = se.search(f"test search {i}", channels=["tag_exact", "keyword", "graph"])
            results.append(r)

        run_concurrent_no_deadlock([lambda i=i: searcher(i) for i in range(10)])
        assert len(results) == 10

    def test_search_during_index_build(self):
        """Search while index is being built — should not crash."""
        se = self._make_search_engine()
        errors = []

        def searcher():
            try:
                se.search("entropy", channels=["tag_exact", "keyword", "graph"])
            except Exception as e:
                errors.append(e)

        # Run multiple searches — first will trigger index build
        run_concurrent_no_deadlock([searcher for _ in range(5)])
        assert not errors

    def test_semantic_loading_flag(self):
        """Verify _semantic_loading flag prevents double-build."""
        se = self._make_search_engine()
        # Manually set loading flag
        se._semantic_loading = True
        result = se._ensure_semantic(blocking=False)
        assert result is False  # Should skip, not block

    def test_concurrent_index_invalidate(self):
        """Invalidate + immediate search from another thread."""
        se = self._make_search_engine()
        se.search("test", channels=["tag_exact", "keyword", "graph"])  # Build index

        def invalidator():
            se.invalidate()

        def searcher():
            se.search("recursion", channels=["tag_exact", "keyword", "graph"])

        # Should not crash
        run_concurrent_no_deadlock([invalidator, searcher, searcher])

    def test_tag_search_concurrent(self):
        """Concurrent tag searches."""
        se = self._make_search_engine()
        results = []

        def searcher(tag):
            r = se.search(tag, channels=["tag_exact", "keyword", "graph"])
            results.append(r)

        run_concurrent_no_deadlock([
            lambda: searcher("search"),
            lambda: searcher("test"),
            lambda: searcher("entropy"),
        ])
        assert len(results) == 3


class TestRedisResilience(TestCase):
    """Tests for Redis cache resilience."""

    def setUp(self):
        # Load cache module
        self._cache_mod = _load_module(
            "kronos_mcp.cache_test",
            _mcp_src / "cache.py",
        )
        self.KronosCache = self._cache_mod.KronosCache

    def test_disabled_cache_fallthrough(self):
        """All ops are no-ops when redis is None."""
        cache = self.KronosCache(redis_client=None)
        assert cache.get("kronos_get", {"id": "x"}) is None
        cache.set("kronos_get", {"id": "x"}, "result")  # Should not raise
        cache.invalidate_for_write("kronos_update", {"id": "x"})
        assert cache.health_check() is False

    def test_cache_get_exception_returns_none(self):
        """Exception in get() returns None, doesn't propagate."""
        mock_redis = MagicMock()
        mock_redis.get.side_effect = ConnectionError("lost connection")
        cache = self.KronosCache(redis_client=mock_redis)
        result = cache.get("kronos_get", {"id": "x"})
        assert result is None

    def test_cache_set_exception_silent(self):
        """Exception in set() doesn't propagate."""
        mock_redis = MagicMock()
        mock_redis.setex.side_effect = ConnectionError("lost connection")
        cache = self.KronosCache(redis_client=mock_redis)
        # Should not raise
        cache.set("kronos_get", {"id": "x"}, "result")

    def test_health_check_connected(self):
        """health_check returns True when ping succeeds."""
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        cache = self.KronosCache(redis_client=mock_redis)
        assert cache.health_check() is True

    def test_health_check_disconnected(self):
        """health_check returns False when ping fails."""
        mock_redis = MagicMock()
        mock_redis.ping.side_effect = ConnectionError("refused")
        cache = self.KronosCache(redis_client=mock_redis)
        assert cache.health_check() is False

    def test_redis_disconnect_mid_session(self):
        """Redis works initially, then disconnects — cache degrades gracefully."""
        mock_redis = MagicMock()
        call_count = [0]

        def intermittent_get(key):
            call_count[0] += 1
            if call_count[0] > 2:
                raise ConnectionError("disconnected")
            return None

        mock_redis.get.side_effect = intermittent_get
        cache = self.KronosCache(redis_client=mock_redis)

        # First calls work
        cache.get("kronos_get", {"id": "1"})
        cache.get("kronos_get", {"id": "2"})
        # Third call fails but returns None gracefully
        result = cache.get("kronos_get", {"id": "3"})
        assert result is None

    def test_invalidation_exception_logged(self):
        """Invalidation failure doesn't crash."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.side_effect = ConnectionError("gone")
        cache = self.KronosCache(redis_client=mock_redis)
        # Should not raise
        cache.invalidate_for_write("kronos_update", {"id": "x"})

    def test_flush_all_on_failure(self):
        """flush_all handles errors gracefully."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.side_effect = ConnectionError("gone")
        cache = self.KronosCache(redis_client=mock_redis)
        result = cache.flush_all()
        assert result == 0


class TestTimeoutTiers(TestCase):
    """Tests for per-tool timeout configuration."""

    def setUp(self):
        # Load server module to get HANDLERS and TOOL_TIMEOUTS
        # We can't import server.py directly, so read the constants
        self._server_path = _mcp_src / "server.py"

    def test_timeout_map_exists(self):
        """TOOL_TIMEOUTS dict is defined in server.py."""
        content = self._server_path.read_text(encoding="utf-8")
        assert "TOOL_TIMEOUTS" in content

    def test_fast_reads_have_short_timeout(self):
        """Read tools should have 10s timeout."""
        content = self._server_path.read_text(encoding="utf-8")
        for tool in ["kronos_get", "kronos_list", "kronos_tags", "kronos_task_get"]:
            assert f'"{tool}": 10' in content, f"{tool} not at 10s"

    def test_write_tools_have_medium_timeout(self):
        """Write tools should have 15s timeout."""
        content = self._server_path.read_text(encoding="utf-8")
        for tool in ["kronos_create", "kronos_update", "kronos_task_move"]:
            assert f'"{tool}": 15' in content, f"{tool} not at 15s"

    def test_search_tools_have_long_timeout(self):
        """Search tools should have 45s timeout."""
        content = self._server_path.read_text(encoding="utf-8")
        for tool in ["kronos_search", "kronos_graph", "kronos_validate"]:
            assert f'"{tool}": 45' in content, f"{tool} not at 45s"

    def test_default_timeout_defined(self):
        """DEFAULT_TIMEOUT should be defined."""
        content = self._server_path.read_text(encoding="utf-8")
        assert "DEFAULT_TIMEOUT" in content

    def test_all_handlers_covered(self):
        """Every handler in HANDLERS should have an entry in TOOL_TIMEOUTS."""
        content = self._server_path.read_text(encoding="utf-8")
        # Extract handler names from HANDLERS dict
        handlers = re.findall(r'"(kronos_\w+)":\s*handle_', content)
        # Extract timeout entries
        timeouts = re.findall(r'"(kronos_\w+)":\s*\d+', content)
        missing = set(handlers) - set(timeouts)
        assert not missing, f"Handlers without timeout config: {missing}"

    def test_timeout_values_positive(self):
        """All timeout values should be positive."""
        content = self._server_path.read_text(encoding="utf-8")
        # Find the TOOL_TIMEOUTS block
        match = re.search(r"TOOL_TIMEOUTS.*?=\s*\{(.*?)\}", content, re.DOTALL)
        assert match
        values = re.findall(r":\s*(\d+)", match.group(1))
        for v in values:
            assert int(v) > 0, f"Non-positive timeout: {v}"

    def test_dispatch_uses_per_tool_timeout(self):
        """call_tool() uses TOOL_TIMEOUTS.get() not hardcoded 30s."""
        content = self._server_path.read_text(encoding="utf-8")
        assert "TOOL_TIMEOUTS.get(name, DEFAULT_TIMEOUT)" in content
        # Should NOT have hardcoded 30.0 in the dispatch section
        # (may still exist in comments, that's ok)
        dispatch_section = content[content.index("async def call_tool"):]
        assert "timeout=30.0" not in dispatch_section
