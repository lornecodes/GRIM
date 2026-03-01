"""Dispatch node — route to the appropriate doer agent.

This is the DOER COORDINATOR. It receives the routing decision from
the Router and delegates to the appropriate specialist agent, injecting
the matched skill protocol as the agent's system prompt instructions.

For IronClaw dispatches (Phase 4), the dispatch node also:
- Generates a staging job_id
- Creates the staging directory
- Writes a manifest.json with task metadata
- Scans for staged artifacts after execution
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.state import AgentResult, GrimState, StagingArtifact

logger = logging.getLogger(__name__)

STAGING_BASE = Path(os.environ.get("STAGING_PATH", "/workspace/staging"))


def _create_staging_job(task: str) -> tuple[str, Path]:
    """Create a staging directory for an IronClaw job.

    Returns:
        Tuple of (job_id, output_dir path).
    """
    job_id = uuid.uuid4().hex[:8]
    job_dir = STAGING_BASE / job_id
    output_dir = job_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write manifest
    manifest = {
        "job_id": job_id,
        "created": datetime.now(timezone.utc).isoformat(),
        "task": task[:500],
        "status": "in_progress",
    }
    manifest_path = job_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info("Staging job created: %s at %s", job_id, job_dir)
    return job_id, output_dir


def _scan_staging_artifacts(job_id: str) -> list[StagingArtifact]:
    """Scan the staging output directory for artifacts after execution."""
    output_dir = STAGING_BASE / job_id / "output"
    if not output_dir.exists():
        return []

    artifacts = []
    for f in output_dir.rglob("*"):
        if f.is_file():
            rel = str(f.relative_to(output_dir))
            size = f.stat().st_size

            # Classify artifact type
            if f.suffix in (".log", ".txt"):
                artifact_type = "log"
            elif f.suffix in (".json", ".yaml", ".yml", ".toml"):
                artifact_type = "script_output"
            else:
                artifact_type = "file"

            artifacts.append(
                StagingArtifact(
                    path=rel,
                    size_bytes=size,
                    artifact_type=artifact_type,
                    created_by="ironclaw",
                )
            )

    logger.info("Staging scan: %d artifacts for job %s", len(artifacts), job_id)
    return artifacts


def make_dispatch_node(agents: dict):
    """Create a dispatch node closure with available agents.

    Args:
        agents: Dict mapping delegation_type → agent callable.
                e.g. {"memory": memory_agent_fn, "code": coder_agent_fn}
    """

    async def dispatch_node(state: GrimState) -> dict:
        """Dispatch to the appropriate agent based on delegation_type."""
        delegation_type = state.get("delegation_type")
        if not delegation_type:
            logger.warning("Dispatch: no delegation_type set, falling back to companion")
            return {"agent_result": None}

        agent_fn = agents.get(delegation_type)
        if agent_fn is None:
            logger.warning(
                "Dispatch: no agent for delegation_type '%s' — available: %s",
                delegation_type,
                list(agents.keys()),
            )
            return {
                "agent_result": AgentResult(
                    agent=delegation_type,
                    success=False,
                    summary=f"No agent available for '{delegation_type}' — Phase 2 feature.",
                )
            }

        logger.info("Dispatch: delegating to '%s' agent", delegation_type)

        # For IronClaw dispatches: set up staging directory
        staging_update = {}
        if delegation_type == "ironclaw":
            messages = state.get("messages", [])
            task_text = ""
            if messages:
                last_msg = messages[-1]
                task_text = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

            job_id, output_dir = _create_staging_job(task_text)
            staging_update["staging_job_id"] = job_id
            staging_update["review_count"] = 0

        try:
            result = await agent_fn(state | staging_update)

            update = {"agent_result": result}
            update.update(staging_update)

            # After IronClaw execution: scan for staged artifacts
            if delegation_type == "ironclaw" and "staging_job_id" in staging_update:
                artifacts = _scan_staging_artifacts(staging_update["staging_job_id"])
                update["staging_artifacts"] = artifacts

            return update
        except Exception as exc:
            logger.exception("Dispatch: agent '%s' failed", delegation_type)
            return {
                "agent_result": AgentResult(
                    agent=delegation_type,
                    success=False,
                    summary=f"Agent '{delegation_type}' failed: {exc}",
                ),
                **staging_update,
            }

    return dispatch_node
