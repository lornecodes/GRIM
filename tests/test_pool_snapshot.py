"""Tests for workspace snapshot/restore functionality."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.pool.workspace import Workspace, WorkspaceManager


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def ws_mgr(tmp_path):
    return WorkspaceManager(base_dir=tmp_path / "worktrees")


@pytest.fixture
def populated_workspace(tmp_path):
    """Create a workspace with actual files for snapshotting."""
    ws_dir = tmp_path / "worktrees" / "workspace-snap123"
    ws_dir.mkdir(parents=True)
    (ws_dir / "main.py").write_text("print('hello')")
    (ws_dir / "tests").mkdir()
    (ws_dir / "tests" / "test_main.py").write_text("def test_hello(): pass")
    ws = Workspace(
        id="workspace-snap123",
        job_id="job-snap12345678",
        repo_path=tmp_path / "repo",
        worktree_path=ws_dir,
        branch_name="grim/workspace-snap123",
    )
    return ws


@pytest.fixture
def snapshot_dir(tmp_path):
    return tmp_path / "snapshots"


# ── Snapshot ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_creates_archive(ws_mgr, populated_workspace, snapshot_dir):
    ws_mgr._workspaces[populated_workspace.id] = populated_workspace
    path = await ws_mgr.snapshot(populated_workspace.id, snapshot_dir)
    assert path is not None
    assert path.exists()
    assert path.suffix == ".gz"
    assert path.name.startswith("workspace-snap123_")


@pytest.mark.asyncio
async def test_snapshot_creates_metadata(ws_mgr, populated_workspace, snapshot_dir):
    ws_mgr._workspaces[populated_workspace.id] = populated_workspace
    path = await ws_mgr.snapshot(populated_workspace.id, snapshot_dir)
    meta_path = path.with_suffix("").with_suffix(".meta.json")
    assert meta_path.exists()
    with open(meta_path) as f:
        meta = json.load(f)
    assert meta["id"] == "workspace-snap123"
    assert meta["job_id"] == "job-snap12345678"
    assert "snapshot_timestamp" in meta


@pytest.mark.asyncio
async def test_snapshot_unknown_workspace(ws_mgr, snapshot_dir):
    result = await ws_mgr.snapshot("nonexistent", snapshot_dir)
    assert result is None


# ── Restore ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restore_from_snapshot(ws_mgr, populated_workspace, snapshot_dir, tmp_path):
    ws_mgr._workspaces[populated_workspace.id] = populated_workspace
    archive_path = await ws_mgr.snapshot(populated_workspace.id, snapshot_dir)

    # Create a fresh manager for restore
    restore_mgr = WorkspaceManager(base_dir=tmp_path / "restored")
    ws = await restore_mgr.restore_snapshot(
        archive_path, "job-restored123", tmp_path / "repo",
    )
    assert ws is not None
    assert ws.job_id == "job-restored123"
    assert ws.status == "active"
    # Check files were extracted
    assert (ws.worktree_path / "main.py").exists()


@pytest.mark.asyncio
async def test_restore_nonexistent_snapshot(ws_mgr, tmp_path):
    ws = await ws_mgr.restore_snapshot(
        tmp_path / "nonexistent.tar.gz", "job-x", tmp_path / "repo",
    )
    assert ws is None


@pytest.mark.asyncio
async def test_restore_adds_to_workspaces(ws_mgr, populated_workspace, snapshot_dir, tmp_path):
    ws_mgr._workspaces[populated_workspace.id] = populated_workspace
    archive_path = await ws_mgr.snapshot(populated_workspace.id, snapshot_dir)

    restore_mgr = WorkspaceManager(base_dir=tmp_path / "restored")
    ws = await restore_mgr.restore_snapshot(
        archive_path, "job-rest123", tmp_path / "repo",
    )
    assert ws is not None
    assert restore_mgr.get(ws.id) is not None


# ── List snapshots ───────────────────────────────────────────────


def test_list_snapshots_empty(ws_mgr, tmp_path):
    result = ws_mgr.list_snapshots(tmp_path / "nonexistent")
    assert result == []


@pytest.mark.asyncio
async def test_list_snapshots_returns_metadata(ws_mgr, populated_workspace, snapshot_dir):
    ws_mgr._workspaces[populated_workspace.id] = populated_workspace
    await ws_mgr.snapshot(populated_workspace.id, snapshot_dir)
    snapshots = ws_mgr.list_snapshots(snapshot_dir)
    assert len(snapshots) == 1
    assert snapshots[0]["id"] == "workspace-snap123"
    assert snapshots[0]["archive_exists"] is True


@pytest.mark.asyncio
async def test_list_multiple_snapshots(ws_mgr, populated_workspace, snapshot_dir):
    import asyncio
    ws_mgr._workspaces[populated_workspace.id] = populated_workspace
    await ws_mgr.snapshot(populated_workspace.id, snapshot_dir)
    await asyncio.sleep(1.1)  # ensure different timestamp
    await ws_mgr.snapshot(populated_workspace.id, snapshot_dir)
    snapshots = ws_mgr.list_snapshots(snapshot_dir)
    assert len(snapshots) == 2


# ── REST endpoint smoke tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_endpoints_503_when_pool_disabled():
    from httpx import ASGITransport, AsyncClient
    from server.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/pool/workspaces/ws-1/snapshot")
        assert resp.status_code == 503

        resp = await client.get("/api/pool/snapshots")
        assert resp.status_code == 503

        resp = await client.post("/api/pool/workspaces/restore", json={
            "snapshot_path": "/tmp/snap.tar.gz", "job_id": "j1",
        })
        assert resp.status_code == 503
