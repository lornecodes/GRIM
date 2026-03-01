"""Staging tools — read/write the shared staging volume between GRIM and IronClaw.

These tools operate on /workspace/staging/ from GRIM's side (not via IronClaw bridge).
The staging volume is a Docker named volume mounted in both containers.

Directory structure per job:
    /workspace/staging/{job_id}/
        manifest.json       # metadata: task, timestamp, creator
        output/             # IronClaw writes here
        audit/              # audit verdicts
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

STAGING_BASE = Path(os.environ.get("STAGING_PATH", "/workspace/staging"))


def _validate_job_id(job_id: str) -> Path:
    """Validate job_id and return the job directory path."""
    # Prevent path traversal
    if ".." in job_id or "/" in job_id or "\\" in job_id:
        raise ValueError(f"Invalid job_id: {job_id}")
    job_dir = STAGING_BASE / job_id
    if not job_dir.exists():
        raise FileNotFoundError(f"Staging job '{job_id}' not found")
    return job_dir


def _validate_relative_path(path: str) -> str:
    """Validate a relative path within a staging job."""
    if ".." in path:
        raise ValueError(f"Path traversal not allowed: {path}")
    return path


# ---------------------------------------------------------------------------
# Read-only tools (for audit agent)
# ---------------------------------------------------------------------------


@tool
async def staging_list(job_id: str) -> str:
    """List all files in a staging job's output directory.

    Args:
        job_id: The staging job identifier.

    Returns:
        A formatted listing of files with sizes.
    """
    try:
        job_dir = _validate_job_id(job_id)
        output_dir = job_dir / "output"

        if not output_dir.exists():
            return f"No output directory for job '{job_id}'"

        files = []
        for f in sorted(output_dir.rglob("*")):
            if f.is_file():
                rel = f.relative_to(output_dir)
                size = f.stat().st_size
                files.append(f"{rel} ({size} bytes)")

        if not files:
            return f"Job '{job_id}' output directory is empty"

        header = f"Staging job '{job_id}' — {len(files)} file(s):\n"
        return header + "\n".join(f"  {f}" for f in files)

    except (ValueError, FileNotFoundError) as e:
        return f"[ERROR] {e}"
    except Exception as e:
        logger.exception("staging_list failed for job '%s'", job_id)
        return f"[ERROR] Failed to list staging: {e}"


@tool
async def staging_read(job_id: str, path: str) -> str:
    """Read a file from the staging area.

    Args:
        job_id: The staging job identifier.
        path: Relative path within the job's output/ directory.

    Returns:
        The file contents.
    """
    try:
        job_dir = _validate_job_id(job_id)
        path = _validate_relative_path(path)
        file_path = job_dir / "output" / path

        if not file_path.exists():
            return f"[ERROR] File not found: {path}"

        if not file_path.is_file():
            return f"[ERROR] Not a file: {path}"

        size = file_path.stat().st_size
        if size > 100_000:
            return f"[ERROR] File too large ({size} bytes). Max 100KB for staging read."

        content = file_path.read_text(encoding="utf-8", errors="replace")
        return f"[{path}] ({size} bytes)\n\n{content}"

    except (ValueError, FileNotFoundError) as e:
        return f"[ERROR] {e}"
    except Exception as e:
        logger.exception("staging_read failed for job '%s' path '%s'", job_id, path)
        return f"[ERROR] Failed to read staged file: {e}"


# ---------------------------------------------------------------------------
# Write tools (for integrate node / organize operations)
# ---------------------------------------------------------------------------


@tool
async def staging_accept(job_id: str, path: str, destination: str) -> str:
    """Accept a staged file — move it from staging to its final destination.

    Args:
        job_id: The staging job identifier.
        path: Relative path within the job's output/ directory.
        destination: Absolute path where the file should be placed.

    Returns:
        Confirmation message.
    """
    try:
        job_dir = _validate_job_id(job_id)
        path = _validate_relative_path(path)
        src = job_dir / "output" / path

        if not src.exists():
            return f"[ERROR] Source file not found: {path}"

        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))

        logger.info("Staging accept: %s → %s (job %s)", path, destination, job_id)
        return f"[ACCEPTED] {path} → {destination}"

    except (ValueError, FileNotFoundError) as e:
        return f"[ERROR] {e}"
    except Exception as e:
        logger.exception("staging_accept failed for job '%s'", job_id)
        return f"[ERROR] Failed to accept staged file: {e}"


@tool
async def staging_reject(job_id: str, path: str, reason: str) -> str:
    """Reject a staged file — log the reason and remove it from staging.

    Args:
        job_id: The staging job identifier.
        path: Relative path within the job's output/ directory.
        reason: Why the file was rejected.

    Returns:
        Confirmation message.
    """
    try:
        job_dir = _validate_job_id(job_id)
        path = _validate_relative_path(path)
        src = job_dir / "output" / path

        if not src.exists():
            return f"[ERROR] Source file not found: {path}"

        # Log rejection to audit directory
        audit_dir = job_dir / "audit"
        audit_dir.mkdir(exist_ok=True)
        rejection_log = audit_dir / "rejections.jsonl"
        with open(rejection_log, "a", encoding="utf-8") as f:
            json.dump({"path": path, "reason": reason}, f)
            f.write("\n")

        # Remove the rejected file
        src.unlink()

        logger.info("Staging reject: %s — %s (job %s)", path, reason, job_id)
        return f"[REJECTED] {path} — {reason}"

    except (ValueError, FileNotFoundError) as e:
        return f"[ERROR] {e}"
    except Exception as e:
        logger.exception("staging_reject failed for job '%s'", job_id)
        return f"[ERROR] Failed to reject staged file: {e}"


# ---------------------------------------------------------------------------
# Tool groups
# ---------------------------------------------------------------------------

STAGING_READ_TOOLS = [staging_list, staging_read]
STAGING_WRITE_TOOLS = [staging_accept, staging_reject]
STAGING_ALL_TOOLS = STAGING_READ_TOOLS + STAGING_WRITE_TOOLS
