"""Resource locking for parallel workspace execution.

Multiple CODE jobs in different worktrees run in parallel, but shared
operations (pytest, pip install, npm install, git push to main) need
serialization to avoid conflicts.

Usage::

    lock = ResourceLock()
    async with lock.acquire(ResourceScope.PYTEST):
        await run_pytest(...)
"""
from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from enum import Enum
from typing import AsyncIterator

logger = logging.getLogger(__name__)


class ResourceScope(str, Enum):
    """Scopes of shared resources that need serialization."""

    PYTEST = "pytest"
    PIP_INSTALL = "pip_install"
    NPM_INSTALL = "npm_install"
    GIT_MAIN = "git_main"


# Patterns to detect resource scope from bash commands
_SCOPE_PATTERNS: list[tuple[re.Pattern, ResourceScope]] = [
    (re.compile(r"\bpytest\b"), ResourceScope.PYTEST),
    (re.compile(r"\bpython\s+-m\s+pytest\b"), ResourceScope.PYTEST),
    (re.compile(r"\bpip\s+install\b"), ResourceScope.PIP_INSTALL),
    (re.compile(r"\bnpm\s+(ci|install)\b"), ResourceScope.NPM_INSTALL),
    (re.compile(r"\bgit\s+push\b.*\b(main|master)\b"), ResourceScope.GIT_MAIN),
    (re.compile(r"\bgit\s+merge\b.*\b(main|master)\b"), ResourceScope.GIT_MAIN),
]


def detect_resource_scope(command: str) -> ResourceScope | None:
    """Detect which shared resource scope a bash command would use.

    Returns the scope if the command matches a pattern, None otherwise.
    """
    for pattern, scope in _SCOPE_PATTERNS:
        if pattern.search(command):
            return scope
    return None


class ResourceLock:
    """Manages async locks for shared resource scopes.

    Each scope gets its own asyncio.Lock(). Acquiring a scope blocks
    other tasks from running commands in the same scope.
    """

    def __init__(self) -> None:
        self._locks: dict[ResourceScope, asyncio.Lock] = {
            scope: asyncio.Lock() for scope in ResourceScope
        }

    @asynccontextmanager
    async def acquire(self, scope: ResourceScope) -> AsyncIterator[None]:
        """Acquire a resource lock for the given scope."""
        lock = self._locks[scope]
        logger.debug("Acquiring lock: %s", scope.value)
        async with lock:
            logger.debug("Lock acquired: %s", scope.value)
            yield
        logger.debug("Lock released: %s", scope.value)

    def is_locked(self, scope: ResourceScope) -> bool:
        """Check if a resource scope is currently locked."""
        return self._locks[scope].locked()

    def status(self) -> dict[str, bool]:
        """Return lock states for all scopes."""
        return {scope.value: lock.locked() for scope, lock in self._locks.items()}
