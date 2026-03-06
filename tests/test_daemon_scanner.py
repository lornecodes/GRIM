"""Unit tests for the daemon project scanner.

Uses a temp vault with real FDO files to test scanning and pipeline sync.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.daemon.models import PipelineStatus
from core.daemon.pipeline import PipelineStore
from core.daemon.scanner import ProjectScanner, ScannedStory


# ── Fixtures ─────────────────────────────────────────────────────


def _make_project_fdo(vault_path: Path, proj_id: str, stories: list[dict]) -> None:
    """Write a minimal proj-* FDO file in the vault."""
    projects_dir = vault_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    fm = {
        "id": proj_id,
        "title": f"Project {proj_id}",
        "domain": "projects",
        "status": "developing",
        "confidence": 0.7,
        "tags": ["epic"],
        "stories": stories,
    }
    body = f"# {proj_id}\n\n## Summary\nTest project."
    fm_yaml = yaml.dump(fm, default_flow_style=False, sort_keys=False)
    fdo_path = projects_dir / f"{proj_id}.md"
    fdo_path.write_text(f"---\n{fm_yaml}---\n\n{body}", encoding="utf-8")


@pytest.fixture
def vault(tmp_path) -> Path:
    """Create a temp vault with test project FDOs."""
    vault_path = tmp_path / "vault"

    _make_project_fdo(vault_path, "proj-alpha", [
        {
            "id": "story-alpha-001",
            "title": "Active code story",
            "status": "active",
            "priority": "high",
            "assignee": "code",
            "description": "Implement feature X",
            "acceptance_criteria": ["Tests pass", "Docs updated"],
            "estimate_days": 2.0,
            "tags": ["feature"],
        },
        {
            "id": "story-alpha-002",
            "title": "In-progress research",
            "status": "in_progress",
            "priority": "medium",
            "assignee": "research",
            "description": "Research topic Y",
            "acceptance_criteria": ["FDO created"],
        },
        {
            "id": "story-alpha-003",
            "title": "New story (no assignee)",
            "status": "new",
            "priority": "low",
            "assignee": "",
            "description": "Unassigned",
        },
        {
            "id": "story-alpha-004",
            "title": "Resolved story",
            "status": "resolved",
            "priority": "medium",
            "assignee": "code",
            "description": "Already done",
        },
    ])

    _make_project_fdo(vault_path, "proj-beta", [
        {
            "id": "story-beta-001",
            "title": "Active audit",
            "status": "active",
            "priority": "critical",
            "assignee": "audit",
            "description": "Security audit",
        },
    ])

    return vault_path


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    return tmp_path / "test_scanner.db"


@pytest.fixture
async def store(tmp_db) -> PipelineStore:
    s = PipelineStore(tmp_db)
    await s.initialize()
    return s


# ── ScannedStory tests ───────────────────────────────────────────


class TestScannedStory:
    """Test ScannedStory data class."""

    def test_from_dict(self):
        data = {
            "id": "story-001",
            "title": "Test",
            "status": "active",
            "priority": "high",
            "assignee": "code",
            "description": "Do it",
            "acceptance_criteria": ["Done"],
            "estimate_days": 2.0,
            "tags": ["feature"],
        }
        s = ScannedStory(data, "proj-x")
        assert s.id == "story-001"
        assert s.project_id == "proj-x"
        assert s.priority_int == 1  # high
        assert s.is_eligible

    def test_not_eligible_wrong_status(self):
        s = ScannedStory({"id": "s", "status": "new", "assignee": "code"}, "p")
        assert not s.is_eligible

    def test_not_eligible_no_assignee(self):
        s = ScannedStory({"id": "s", "status": "active", "assignee": ""}, "p")
        assert not s.is_eligible

    def test_eligible_in_progress(self):
        s = ScannedStory({"id": "s", "status": "in_progress", "assignee": "research"}, "p")
        assert s.is_eligible

    def test_priority_int_defaults(self):
        s = ScannedStory({"id": "s", "priority": "unknown"}, "p")
        assert s.priority_int == 2  # default medium


# ── Scanner scan() tests ─────────────────────────────────────────


class TestProjectScannerScan:
    """Test vault scanning."""

    def test_scan_finds_eligible(self, vault):
        scanner = ProjectScanner(vault)
        stories = scanner.scan()
        ids = {s.id for s in stories}
        # Should find: alpha-001 (active+code), alpha-002 (in_progress+research), beta-001 (active+audit)
        assert "story-alpha-001" in ids
        assert "story-alpha-002" in ids
        assert "story-beta-001" in ids

    def test_scan_excludes_ineligible(self, vault):
        scanner = ProjectScanner(vault)
        stories = scanner.scan()
        ids = {s.id for s in stories}
        # Should NOT find: alpha-003 (no assignee), alpha-004 (resolved)
        assert "story-alpha-003" not in ids
        assert "story-alpha-004" not in ids

    def test_scan_count(self, vault):
        scanner = ProjectScanner(vault)
        assert len(scanner.scan()) == 3

    def test_scan_with_project_filter(self, vault):
        scanner = ProjectScanner(vault, project_filter=["proj-alpha"])
        stories = scanner.scan()
        assert all(s.project_id == "proj-alpha" for s in stories)
        assert len(stories) == 2

    def test_scan_with_empty_filter(self, vault):
        scanner = ProjectScanner(vault, project_filter=[])
        stories = scanner.scan()
        # Empty filter = no filter (scans all)
        assert len(stories) == 3

    def test_scan_nonexistent_vault(self, tmp_path):
        scanner = ProjectScanner(tmp_path / "nonexistent")
        stories = scanner.scan()
        assert stories == []

    def test_scan_story_fields(self, vault):
        scanner = ProjectScanner(vault)
        stories = {s.id: s for s in scanner.scan()}
        alpha1 = stories["story-alpha-001"]
        assert alpha1.title == "Active code story"
        assert alpha1.assignee == "code"
        assert alpha1.priority == "high"
        assert alpha1.description == "Implement feature X"
        assert alpha1.acceptance_criteria == ["Tests pass", "Docs updated"]


# ── Scanner sync_pipeline() tests ────────────────────────────────


class TestProjectScannerSync:
    """Test vault-to-pipeline synchronization."""

    @pytest.mark.asyncio
    async def test_sync_adds_new(self, vault, store):
        scanner = ProjectScanner(vault)
        result = await scanner.sync_pipeline(store)
        assert result["added"] == 3
        assert result["removed"] == 0

        items = await store.list_items()
        assert len(items) == 3

    @pytest.mark.asyncio
    async def test_sync_idempotent(self, vault, store):
        scanner = ProjectScanner(vault)
        await scanner.sync_pipeline(store)
        result = await scanner.sync_pipeline(store)
        assert result["added"] == 0
        assert result["removed"] == 0

    @pytest.mark.asyncio
    async def test_sync_removes_stale_backlog(self, vault, store):
        scanner = ProjectScanner(vault)
        await scanner.sync_pipeline(store)

        # Add a story to pipeline that doesn't exist in vault
        await store.add("story-ghost-001", "proj-ghost")
        items_before = await store.list_items()
        assert len(items_before) == 4

        result = await scanner.sync_pipeline(store)
        assert result["removed"] == 1

        items_after = await store.list_items()
        assert len(items_after) == 3

    @pytest.mark.asyncio
    async def test_sync_keeps_dispatched(self, vault, store):
        """Dispatched items should NOT be removed even if story changes in vault."""
        scanner = ProjectScanner(vault)
        await scanner.sync_pipeline(store)

        # Advance an item to DISPATCHED
        items = await store.list_items()
        item = next(i for i in items if i.story_id == "story-alpha-001")
        await store.advance(item.id, PipelineStatus.READY)
        await store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-x")

        # Now remove the story from vault by rewriting the project
        _make_project_fdo(vault, "proj-alpha", [
            {
                "id": "story-alpha-002",
                "title": "In-progress research",
                "status": "in_progress",
                "priority": "medium",
                "assignee": "research",
            },
        ])

        result = await scanner.sync_pipeline(store)
        # The dispatched item should NOT be removed
        dispatched = await store.get(item.id)
        assert dispatched is not None
        assert dispatched.status == PipelineStatus.DISPATCHED

    @pytest.mark.asyncio
    async def test_sync_updates_priority(self, vault, store):
        scanner = ProjectScanner(vault)
        await scanner.sync_pipeline(store)

        # Change priority in vault
        _make_project_fdo(vault, "proj-alpha", [
            {
                "id": "story-alpha-001",
                "title": "Active code story",
                "status": "active",
                "priority": "critical",  # was high
                "assignee": "code",
            },
            {
                "id": "story-alpha-002",
                "title": "In-progress research",
                "status": "in_progress",
                "priority": "medium",
                "assignee": "research",
            },
        ])

        result = await scanner.sync_pipeline(store)
        assert result["updated"] == 1

        item = await store.get_by_story("story-alpha-001")
        assert item is not None
        assert item.priority == 0  # critical

    @pytest.mark.asyncio
    async def test_sync_with_filter(self, vault, store):
        scanner = ProjectScanner(vault, project_filter=["proj-beta"])
        result = await scanner.sync_pipeline(store)
        assert result["added"] == 1

        items = await store.list_items()
        assert len(items) == 1
        assert items[0].story_id == "story-beta-001"

    @pytest.mark.asyncio
    async def test_sync_sets_correct_priority(self, vault, store):
        scanner = ProjectScanner(vault)
        await scanner.sync_pipeline(store)

        item = await store.get_by_story("story-beta-001")
        assert item is not None
        assert item.priority == 0  # critical
        assert item.assignee == "audit"
