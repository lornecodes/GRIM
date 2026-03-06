"""Health monitoring for the management daemon."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.daemon.models import PipelineStatus
from core.daemon.pipeline import PipelineStore

logger = logging.getLogger(__name__)


@dataclass
class HealthMonitor:
    """Tracks daemon health metrics and detects stuck items."""

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    scan_count: int = 0
    dispatch_count: int = 0
    last_scan_at: datetime | None = None
    last_dispatch_at: datetime | None = None
    errors: list[str] = field(default_factory=list)
    _max_errors: int = 50

    def record_scan(self) -> None:
        """Record a completed scan cycle."""
        self.scan_count += 1
        self.last_scan_at = datetime.now(timezone.utc)

    def record_dispatch(self) -> None:
        """Record a successful dispatch."""
        self.dispatch_count += 1
        self.last_dispatch_at = datetime.now(timezone.utc)

    def record_error(self, message: str) -> None:
        """Record an error, keeping only the most recent."""
        self.errors.append(f"{datetime.now(timezone.utc).isoformat()}: {message}")
        if len(self.errors) > self._max_errors:
            self.errors = self.errors[-self._max_errors:]

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()

    async def status(self, store: PipelineStore) -> dict:
        """Return full health status including pipeline counts."""
        counts = await store.count_by_status()
        return {
            "running": True,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "started_at": self.started_at.isoformat(),
            "scan_count": self.scan_count,
            "dispatch_count": self.dispatch_count,
            "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
            "last_dispatch_at": self.last_dispatch_at.isoformat() if self.last_dispatch_at else None,
            "pipeline": counts,
            "recent_errors": self.errors[-5:],
        }

    async def stuck_items(
        self,
        store: PipelineStore,
        threshold_minutes: int = 30,
    ) -> list[dict]:
        """Find DISPATCHED items that haven't progressed for too long."""
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
        items = await store.list_items(status_filter=PipelineStatus.DISPATCHED)

        stuck = []
        for item in items:
            if item.updated_at < cutoff:
                stuck.append({
                    "id": item.id,
                    "story_id": item.story_id,
                    "job_id": item.job_id,
                    "minutes_stuck": round(
                        (datetime.now(timezone.utc) - item.updated_at).total_seconds() / 60, 1
                    ),
                })

        if stuck:
            logger.warning("Found %d stuck pipeline items (>%d min)", len(stuck), threshold_minutes)
        return stuck
