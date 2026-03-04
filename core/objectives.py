"""Persistent objectives — long-running goals that survive compression and sessions.

v0.0.6 objectives are stored as YAML in local/objectives/active.yaml (evolve node).
v0.10 objectives use the Pydantic Objective model from state.py (loop system).

Both coexist during migration. LegacyObjective handles YAML persistence and
the evolve node extraction. The state.Objective model drives the new loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from core.state import Objective as StateObjective
from core.state import ObjectiveStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Legacy objective (v0.0.6) — YAML persistence for evolve node
# ---------------------------------------------------------------------------

@dataclass
class Objective:
    """A persistent objective that survives compression and sessions.

    This is the v0.0.6 YAML-backed objective. For the new v0.10 loop-driving
    objective model, see core.state.Objective.
    """

    id: str  # short slug, e.g. "implement-caching"
    description: str  # what needs to be accomplished
    status: str = "active"  # "active" | "completed" | "stalled"
    created: str = ""  # ISO timestamp
    updated: str = ""  # ISO timestamp
    source_session: str = ""  # thread_id where first identified
    notes: list[str] = field(default_factory=list)  # progress notes

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status,
            "created": self.created,
            "updated": self.updated,
            "source_session": self.source_session,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Objective:
        return cls(
            id=d.get("id", ""),
            description=d.get("description", ""),
            status=d.get("status", "active"),
            created=d.get("created", ""),
            updated=d.get("updated", ""),
            source_session=d.get("source_session", ""),
            notes=d.get("notes", []),
        )

    def to_state_objective(self) -> StateObjective:
        """Convert to v0.10 state Objective for the loop system."""
        status_map = {
            "active": ObjectiveStatus.ACTIVE,
            "completed": ObjectiveStatus.COMPLETE,
            "stalled": ObjectiveStatus.BLOCKED,
        }
        return StateObjective(
            id=self.id,
            title=self.description,
            status=status_map.get(self.status, ObjectiveStatus.PENDING),
            context={"legacy": True, "notes": self.notes},
            created_at=self.created or datetime.now().isoformat(),
            updated_at=self.updated or datetime.now().isoformat(),
        )


def load_objectives(objectives_dir: Path) -> list[Objective]:
    """Load active objectives from local/objectives/active.yaml."""
    path = objectives_dir / "active.yaml"
    if not path.exists():
        return []

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return [Objective.from_dict(o) for o in raw.get("objectives", [])]
    except Exception:
        logger.warning("Failed to load objectives from %s", path)
        return []


def save_objectives(objectives: list[Objective], objectives_dir: Path) -> None:
    """Save objectives to local/objectives/active.yaml."""
    try:
        objectives_dir.mkdir(parents=True, exist_ok=True)
        path = objectives_dir / "active.yaml"
        data = {
            "updated": datetime.now().isoformat(),
            "objectives": [o.to_dict() for o in objectives],
        }
        path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        logger.info("Saved %d objectives to %s", len(objectives), path)
    except Exception:
        logger.exception("Failed to save objectives")


OBJECTIVE_EXTRACTION_PROMPT = """Analyze this conversation and the current objectives list.
Return an updated objectives list as JSON.

CURRENT OBJECTIVES:
{current_objectives}

RECENT CONVERSATION:
{conversation}

Rules:
- Add new objectives if the conversation reveals goals being worked toward
- Mark objectives "completed" if the conversation shows they were accomplished
- Mark objectives "stalled" if explicitly abandoned or deprioritized
- Keep IDs stable for existing objectives (do not rename them)
- Add brief progress notes when status changes
- Maximum {max_objectives} active objectives
- Use short, descriptive IDs like "implement-caching" or "fix-fracton-api"

Return ONLY valid JSON in this exact format:
{{"objectives": [{{"id": "...", "description": "...", "status": "active|completed|stalled", "notes": ["..."]}}]}}"""
