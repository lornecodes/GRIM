"""Atomic file write utility for Kronos YAML/markdown persistence.

Crash-safe writes: if the process dies mid-write, the original file is intact.
os.replace() is atomic on both Linux (POSIX rename) and Windows (NTFS).

On Windows, os.replace() can raise PermissionError if another process/thread
has the target file open. We retry with exponential backoff.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"
_MAX_RETRIES = 5 if _IS_WINDOWS else 0
_RETRY_BASE_DELAY = 0.01  # 10ms


def atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write content atomically via temp file + os.replace().

    The temp file is created in the SAME directory as the target — required
    for os.replace() to be atomic (same filesystem).

    On Windows, retries PermissionError up to 5 times with backoff since
    os.replace can fail if another thread has the file open.

    Args:
        path: Target file path.
        content: String content to write.
        encoding: File encoding (default: utf-8).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.stem}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)

        last_err: BaseException | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                os.replace(tmp_path, str(path))
                return
            except PermissionError as e:
                last_err = e
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_BASE_DELAY * (2 ** attempt))
                else:
                    raise
            except BaseException:
                raise
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
