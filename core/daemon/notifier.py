"""DaemonNotifier — proactive notifications for the management daemon.

Generates summaries and alerts without being asked:
- Daily summary: pipeline counts, completed today, stuck items
- Stuck detection: items DISPATCHED > N hours with no progress
- All output is data — the engine emits events, Discord formats them.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DailySummary:
    """Snapshot of daemon pipeline state for daily reporting."""

    counts_by_status: dict[str, int] = field(default_factory=dict)
    completed_today: int = 0
    stuck_items: list[dict[str, Any]] = field(default_factory=list)
    human_idle: list[dict[str, Any]] = field(default_factory=list)
    total_items: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class StuckItem:
    """A pipeline item that has been dispatched too long."""

    item_id: str
    story_id: str
    project_id: str
    hours_dispatched: float
    job_id: str | None = None


class DaemonNotifier:
    """Generates proactive notifications from pipeline state.

    Stateless — queries the pipeline store each time. The engine
    manages timing and event emission.
    """

    def __init__(self, stuck_threshold_hours: int = 2) -> None:
        self._stuck_threshold_hours = stuck_threshold_hours

    async def daily_summary(self, store) -> DailySummary:
        """Build a daily summary from current pipeline state.

        Args:
            store: PipelineStore instance

        Returns:
            DailySummary with counts, completions, stuck items, and idle items
        """
        from core.daemon.models import PipelineStatus

        counts = await store.count_by_status()
        total = sum(counts.values())

        # Completed today: items that moved to MERGED in last 24 hours
        now = datetime.now(timezone.utc)
        cutoff_24h = now - timedelta(hours=24)
        merged = await store.list_items(status_filter=PipelineStatus.MERGED)
        completed_today = sum(1 for item in merged if item.updated_at >= cutoff_24h)

        # Stuck items
        stuck = await self.detect_stuck(store)
        stuck_dicts = [
            {
                "item_id": s.item_id,
                "story_id": s.story_id,
                "project_id": s.project_id,
                "hours": round(s.hours_dispatched, 1),
                "job_id": s.job_id,
            }
            for s in stuck
        ]

        # Human idle (BACKLOG items with no recent updates)
        backlog = await store.list_items(status_filter=PipelineStatus.BACKLOG)
        idle_cutoff = now - timedelta(days=3)
        human_idle = []
        for item in backlog:
            if getattr(item, "owner", "") == "human" and item.updated_at < idle_cutoff:
                human_idle.append({
                    "story_id": item.story_id,
                    "idle_days": (now - item.updated_at).days,
                })

        return DailySummary(
            counts_by_status=counts,
            completed_today=completed_today,
            stuck_items=stuck_dicts,
            human_idle=human_idle,
            total_items=total,
        )

    async def detect_stuck(self, store) -> list[StuckItem]:
        """Find items stuck in DISPATCHED state beyond the threshold.

        Args:
            store: PipelineStore instance

        Returns:
            List of StuckItem entries for items dispatched too long
        """
        from core.daemon.models import PipelineStatus

        if self._stuck_threshold_hours <= 0:
            return []

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=self._stuck_threshold_hours)

        dispatched = await store.list_items(status_filter=PipelineStatus.DISPATCHED)
        stuck = []

        for item in dispatched:
            if item.updated_at < cutoff:
                hours = (now - item.updated_at).total_seconds() / 3600
                stuck.append(StuckItem(
                    item_id=item.id,
                    story_id=item.story_id,
                    project_id=item.project_id,
                    hours_dispatched=hours,
                    job_id=item.job_id,
                ))

        return stuck

    def format_daily_summary(self, summary: DailySummary) -> str:
        """Format a daily summary as a Discord-ready string."""
        parts = ["**Daily Daemon Summary**"]

        # Status counts
        if summary.counts_by_status:
            status_line = "  ".join(
                f"{status}: **{count}**"
                for status, count in sorted(summary.counts_by_status.items())
                if count > 0
            )
            parts.append(status_line)
        else:
            parts.append("Pipeline is empty.")

        # Completed today
        if summary.completed_today > 0:
            parts.append(f"Completed today: **{summary.completed_today}** stories merged")

        # Stuck items
        if summary.stuck_items:
            parts.append(f"\n**Stuck** ({len(summary.stuck_items)} items):")
            for s in summary.stuck_items[:5]:  # cap at 5
                parts.append(f"  `{s['story_id']}` — dispatched {s['hours']}h ago")
        else:
            parts.append("No stuck items.")

        # Human idle
        if summary.human_idle:
            parts.append(f"\n**Idle Human Stories** ({len(summary.human_idle)}):")
            for h in summary.human_idle[:5]:
                parts.append(f"  `{h['story_id']}` — {h['idle_days']} days")

        return "\n".join(parts)
