"""Pool event notifiers — push events to external destinations.

Notifiers subscribe to the PoolEventBus and forward events to
webhooks, Discord, or other services.
"""
from __future__ import annotations

import logging
from typing import Any

from core.pool.events import PoolEvent, PoolEventType

logger = logging.getLogger(__name__)

# Event types that are too noisy for external notifications by default
_NOISY_EVENTS = {
    PoolEventType.JOB_SUBMITTED,
    PoolEventType.JOB_STARTED,
    PoolEventType.AGENT_OUTPUT,
    PoolEventType.AGENT_TOOL_RESULT,
}

# Discord embed colors by event type
_EMBED_COLORS = {
    PoolEventType.JOB_COMPLETE: 0x2ECC71,   # Green
    PoolEventType.JOB_FAILED: 0xE74C3C,     # Red
    PoolEventType.JOB_BLOCKED: 0xF39C12,    # Yellow
    PoolEventType.JOB_REVIEW: 0x3498DB,     # Blue
    PoolEventType.JOB_CANCELLED: 0x95A5A6,  # Grey
    # Daemon intelligence events (Project Mewtwo Phase 3)
    PoolEventType.DAEMON_ESCALATION: 0xE67E22,      # Orange
    PoolEventType.DAEMON_AUTO_RESOLVED: 0x27AE60,   # Dark green
}


class WebhookNotifier:
    """Posts pool events as JSON to an HTTP endpoint."""

    def __init__(
        self,
        url: str,
        filter_noisy: bool = True,
    ) -> None:
        self._url = url
        self._filter_noisy = filter_noisy

    async def __call__(self, event: PoolEvent) -> None:
        if self._filter_noisy and event.type in _NOISY_EVENTS:
            return

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(self._url, json=event.to_dict())
        except Exception:
            logger.exception("Webhook notification failed for %s", event.job_id)


class DiscordWebhookNotifier:
    """Formats pool events as Discord embeds and posts to a webhook URL."""

    def __init__(
        self,
        webhook_url: str,
        filter_noisy: bool = True,
    ) -> None:
        self._webhook_url = webhook_url
        self._filter_noisy = filter_noisy

    async def __call__(self, event: PoolEvent) -> None:
        if self._filter_noisy and event.type in _NOISY_EVENTS:
            return

        payload = {"embeds": [self._build_embed(event)]}

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self._webhook_url, json=payload)
                if resp.status_code >= 400:
                    logger.warning(
                        "Discord webhook returned %d for event %s",
                        resp.status_code, event.type.value,
                    )
        except Exception:
            logger.exception("Discord webhook failed for %s", event.job_id)

    @staticmethod
    def _build_embed(event: PoolEvent) -> dict[str, Any]:
        """Build a Discord embed object from a pool event."""
        color = _EMBED_COLORS.get(event.type, 0x7F8C8D)
        job_id = event.job_id

        if event.type == PoolEventType.JOB_COMPLETE:
            preview = event.data.get("result_preview", "")[:300]
            cost = event.data.get("cost_usd", 0)
            turns = event.data.get("num_turns", 0)
            diff_stat = event.data.get("diff_stat", "")
            description = f"**Cost:** ${cost:.4f} | **Turns:** {turns}"
            if diff_stat:
                description += f"\n```\n{diff_stat}\n```"
            if preview:
                description += f"\n{preview[:200]}"
            return {
                "title": f"Job Complete: {job_id}",
                "description": description,
                "color": color,
                "timestamp": event.timestamp.isoformat(),
            }

        elif event.type == PoolEventType.JOB_FAILED:
            error = event.data.get("error", "Unknown error")
            retries = event.data.get("retries", 0)
            description = f"**Error:** {error}"
            if retries:
                description += f"\n**Retries exhausted:** {retries}"
            return {
                "title": f"Job Failed: {job_id}",
                "description": description,
                "color": color,
                "timestamp": event.timestamp.isoformat(),
            }

        elif event.type == PoolEventType.JOB_BLOCKED:
            question = event.data.get("question", "")
            return {
                "title": f"Job Needs Input: {job_id}",
                "description": f"{question}\n\n*Reply: `@GRIM clarify {job_id} your answer`*",
                "color": color,
                "timestamp": event.timestamp.isoformat(),
            }

        elif event.type == PoolEventType.JOB_REVIEW:
            ws_id = event.data.get("workspace_id", "")
            changed = event.data.get("changed_files", [])
            diff_stat = event.data.get("diff_stat", "")
            description = f"**Workspace:** {ws_id}"
            if changed:
                description += f"\n**Files changed:** {len(changed)}"
            if diff_stat:
                description += f"\n```\n{diff_stat}\n```"
            return {
                "title": f"Job Ready for Review: {job_id}",
                "description": description,
                "color": color,
                "timestamp": event.timestamp.isoformat(),
            }

        elif event.type == PoolEventType.JOB_CANCELLED:
            return {
                "title": f"Job Cancelled: {job_id}",
                "description": "Job was cancelled.",
                "color": color,
                "timestamp": event.timestamp.isoformat(),
            }

        elif event.type == PoolEventType.DAEMON_ESCALATION:
            question = event.data.get("question", "")
            reason = event.data.get("reason", "")
            story_id = event.data.get("story_id", "")
            parts = []
            if story_id:
                parts.append(f"**Story:** {story_id}")
            if question:
                parts.append(f"**Question:** {question}")
            if reason:
                parts.append(reason)
            return {
                "title": f"Daemon Escalation: {job_id}",
                "description": "\n".join(parts) or "Needs human attention",
                "color": color,
                "timestamp": event.timestamp.isoformat(),
            }

        elif event.type == PoolEventType.DAEMON_AUTO_RESOLVED:
            question = event.data.get("question", "")
            answer = event.data.get("answer", "")
            source = event.data.get("source", "unknown")
            confidence = event.data.get("confidence", 0.0)
            story_id = event.data.get("story_id", "")
            description = (
                f"**Story:** {story_id}\n"
                f"**Question:** {question}\n"
                f"**Answer:** {answer}\n"
                f"**Source:** {source} (confidence: {confidence:.0%})"
            )
            return {
                "title": f"Daemon Resolved: {job_id}",
                "description": description,
                "color": color,
                "timestamp": event.timestamp.isoformat(),
            }

        else:
            return {
                "title": f"{event.type.value}: {job_id}",
                "description": str(event.data),
                "color": color,
                "timestamp": event.timestamp.isoformat(),
            }
