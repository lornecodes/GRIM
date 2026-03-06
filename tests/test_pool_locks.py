"""Tests for resource locking — parallel workspace serialization."""
from __future__ import annotations

import asyncio

import pytest

from core.pool.locks import ResourceLock, ResourceScope, detect_resource_scope


# ── detect_resource_scope ────────────────────────────────────────


def test_detect_pytest():
    assert detect_resource_scope("pytest tests/ -v") == ResourceScope.PYTEST


def test_detect_python_m_pytest():
    assert detect_resource_scope("python -m pytest tests/") == ResourceScope.PYTEST


def test_detect_pip_install():
    assert detect_resource_scope("pip install -e .") == ResourceScope.PIP_INSTALL


def test_detect_npm_install():
    assert detect_resource_scope("npm install") == ResourceScope.NPM_INSTALL


def test_detect_npm_ci():
    assert detect_resource_scope("npm ci") == ResourceScope.NPM_INSTALL


def test_detect_git_push_main():
    assert detect_resource_scope("git push origin main") == ResourceScope.GIT_MAIN


def test_detect_git_merge_main():
    assert detect_resource_scope("git merge main") == ResourceScope.GIT_MAIN


def test_detect_git_push_master():
    assert detect_resource_scope("git push origin master") == ResourceScope.GIT_MAIN


def test_detect_unrelated_command():
    assert detect_resource_scope("ls -la") is None


def test_detect_echo_command():
    assert detect_resource_scope("echo hello") is None


def test_detect_git_status():
    assert detect_resource_scope("git status") is None


# ── ResourceLock acquire/release ─────────────────────────────────


@pytest.mark.asyncio
async def test_lock_acquire_release():
    lock = ResourceLock()
    assert not lock.is_locked(ResourceScope.PYTEST)
    async with lock.acquire(ResourceScope.PYTEST):
        assert lock.is_locked(ResourceScope.PYTEST)
    assert not lock.is_locked(ResourceScope.PYTEST)


@pytest.mark.asyncio
async def test_lock_status_all_unlocked():
    lock = ResourceLock()
    status = lock.status()
    assert all(v is False for v in status.values())
    assert "pytest" in status
    assert "pip_install" in status
    assert "npm_install" in status
    assert "git_main" in status


@pytest.mark.asyncio
async def test_lock_independent_scopes():
    """Different scopes don't block each other."""
    lock = ResourceLock()
    async with lock.acquire(ResourceScope.PYTEST):
        assert lock.is_locked(ResourceScope.PYTEST)
        assert not lock.is_locked(ResourceScope.PIP_INSTALL)


# ── Concurrent lock contention ───────────────────────────────────


@pytest.mark.asyncio
async def test_lock_contention():
    """Two tasks trying same scope are serialized."""
    lock = ResourceLock()
    order = []

    async def task(name: str, delay: float):
        async with lock.acquire(ResourceScope.PYTEST):
            order.append(f"{name}_start")
            await asyncio.sleep(delay)
            order.append(f"{name}_end")

    # Launch two tasks
    t1 = asyncio.create_task(task("A", 0.05))
    await asyncio.sleep(0.01)  # ensure A starts first
    t2 = asyncio.create_task(task("B", 0.01))
    await asyncio.gather(t1, t2)

    # A should complete before B starts
    assert order == ["A_start", "A_end", "B_start", "B_end"]


@pytest.mark.asyncio
async def test_no_deadlock_different_scopes():
    """Two tasks on different scopes run concurrently — no deadlock."""
    lock = ResourceLock()
    results = []

    async def task_a():
        async with lock.acquire(ResourceScope.PYTEST):
            await asyncio.sleep(0.02)
            results.append("A")

    async def task_b():
        async with lock.acquire(ResourceScope.PIP_INSTALL):
            await asyncio.sleep(0.02)
            results.append("B")

    await asyncio.gather(task_a(), task_b())
    # Both should complete (order may vary due to concurrency)
    assert set(results) == {"A", "B"}


# ── Pool integration ────────────────────────────────────────────


def test_pool_status_includes_locks():
    from unittest.mock import MagicMock
    from core.pool.pool import ExecutionPool
    from core.pool.queue import JobQueue

    config = MagicMock()
    config.workspace_root = "/tmp/ws"
    config.pool_num_slots = 1
    config.pool_max_turns_per_job = 5
    config.pool_poll_interval = 1.0
    config.pool_job_timeout_secs = 60
    config.kronos_mcp_command = ""
    config.vault_path = None
    config.skills_path = None
    config.repos_manifest = "repos.yaml"

    queue = MagicMock(spec=JobQueue)
    pool = ExecutionPool(queue, config)
    status = pool.status
    assert "resource_locks" in status
    assert isinstance(status["resource_locks"], dict)
    assert "pytest" in status["resource_locks"]


# ── Import ───────────────────────────────────────────────────────


def test_import_from_pool_package():
    from core.pool import ResourceLock, ResourceScope
    assert ResourceLock is not None
    assert ResourceScope is not None


def test_resource_scope_values():
    assert ResourceScope.PYTEST.value == "pytest"
    assert ResourceScope.PIP_INSTALL.value == "pip_install"
    assert ResourceScope.NPM_INSTALL.value == "npm_install"
    assert ResourceScope.GIT_MAIN.value == "git_main"
