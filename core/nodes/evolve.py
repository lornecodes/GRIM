"""Evolve node — update field state and capture session learning.

Runs at end of session (or periodically). Computes field state drift
based on conversation topics and outcomes, saves snapshots.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import yaml

from core.config import GrimConfig
from core.state import FieldState, GrimState

logger = logging.getLogger(__name__)


def make_evolve_node(config: GrimConfig):
    """Create an evolve node closure with config."""

    async def evolve_node(state: GrimState) -> dict:
        """Evolve field state based on session activity."""
        field_state: FieldState | None = state.get("field_state")
        if field_state is None:
            return {}

        topics = state.get("session_topics", [])
        knowledge_context = state.get("knowledge_context", [])

        # Capture start state before modulation
        start_snapshot = field_state.snapshot()

        # Modulate based on knowledge confidence
        if knowledge_context:
            avg_confidence = sum(f.confidence for f in knowledge_context) / len(knowledge_context)
            field_state.modulate(confidence=avg_confidence)

        # Modulate toward more coherent state after productive sessions
        if len(topics) > 3:
            field_state.coherence = min(1.0, field_state.coherence + 0.02)
            field_state.valence = min(1.0, field_state.valence + 0.05)

        end_snapshot = field_state.snapshot()

        # Save evolution snapshot
        if config.evolution_dir:
            _save_snapshot(
                config.evolution_dir,
                start=start_snapshot,
                end=end_snapshot,
                topics=topics,
                session_start=state.get("session_start"),
            )

        logger.info(
            "Evolve: coherence %.2f→%.2f, valence %.2f→%.2f, uncertainty %.2f→%.2f",
            start_snapshot["coherence"], end_snapshot["coherence"],
            start_snapshot["valence"], end_snapshot["valence"],
            start_snapshot["uncertainty"], end_snapshot["uncertainty"],
        )

        return {"field_state": field_state}

    return evolve_node


def _save_snapshot(
    evolution_dir: Path,
    start: dict,
    end: dict,
    topics: list[str],
    session_start: datetime | None,
) -> None:
    """Save a field state evolution snapshot to disk."""
    try:
        evolution_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        filename = now.strftime("%Y-%m-%d_session_%H%M.yaml")

        snapshot = {
            "session_start": session_start.isoformat() if session_start else None,
            "session_end": now.isoformat(),
            "field_state_start": start,
            "field_state_end": end,
            "topics": topics,
        }

        (evolution_dir / filename).write_text(
            yaml.dump(snapshot, default_flow_style=False),
            encoding="utf-8",
        )
        logger.debug("Saved evolution snapshot: %s", filename)

    except Exception:
        logger.exception("Failed to save evolution snapshot")
