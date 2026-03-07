"""Discord command handler for the Management Daemon.

Parses daemon-specific commands from Discord messages and routes them
to the GRIM server REST API. Formats daemon events for Discord display.

Commands:
    @GRIM status              — Pipeline summary
    @GRIM backlog [mine|grim|all] — Filtered backlog view
    @GRIM own <story-id> [grim|human] — Transfer story ownership
    @GRIM deps <story-id>    — Show dependency tree for a story
    @GRIM goal <proj-id> "description" — Create a goal story
    @GRIM show plan <story-id> — Show generated sub-stories
    @GRIM approve plan <story-id> — Approve and activate a plan
    @GRIM reject plan <story-id> — Reject and close a plan
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Command Patterns ──────────────────────────────────────────────────────────

# @GRIM status
STATUS_PATTERN = re.compile(r"\bstatus\b", re.IGNORECASE)

# @GRIM backlog [mine|grim|all]
BACKLOG_PATTERN = re.compile(
    r"\bbacklog(?:\s+(mine|grim|all))?\b", re.IGNORECASE,
)

# @GRIM own <story-id> [grim|human]
OWN_PATTERN = re.compile(
    r"\bown\s+(story-[\w-]+)\s+(grim|human)\b", re.IGNORECASE,
)

# @GRIM deps <story-id>
DEPS_PATTERN = re.compile(
    r"\bdeps\s+(story-[\w-]+)\b", re.IGNORECASE,
)

# @GRIM goal <proj-id> "description"
GOAL_PATTERN = re.compile(
    r'\bgoal\s+(proj-[\w-]+)\s+"([^"]+)"', re.IGNORECASE,
)

# @GRIM approve plan <story-id>
APPROVE_PLAN_PATTERN = re.compile(
    r"\bapprove\s+plan\s+(story-[\w-]+)\b", re.IGNORECASE,
)

# @GRIM reject plan <story-id>
REJECT_PLAN_PATTERN = re.compile(
    r"\breject\s+plan\s+(story-[\w-]+)\b", re.IGNORECASE,
)

# @GRIM show plan <story-id>
SHOW_PLAN_PATTERN = re.compile(
    r"\bshow\s+plan\s+(story-[\w-]+)\b", re.IGNORECASE,
)

# @GRIM daily
DAILY_PATTERN = re.compile(r"\bdaily\b", re.IGNORECASE)


class DaemonCommandHandler:
    """Handles daemon commands via GRIM server REST API."""

    def __init__(self, server_url: str):
        self._server_url = server_url.rstrip("/")

    async def _request(
        self, method: str, path: str, **kwargs: Any,
    ) -> dict | list | None:
        """Make an HTTP request to the GRIM server. Returns parsed JSON or None on error."""
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await getattr(client, method)(
                f"{self._server_url}{path}", **kwargs,
            )
            if resp.status_code == 404:
                return None  # caller interprets as "not running" or "not found"
            return resp.json()

    async def try_handle(self, content: str) -> str | None:
        """Try to parse and handle a daemon command.

        Returns a formatted response string, or None if the content
        is not a daemon command.
        """
        # Check patterns in order of specificity
        m = APPROVE_PLAN_PATTERN.search(content)
        if m:
            return await self._handle_approve_plan(m.group(1))

        m = REJECT_PLAN_PATTERN.search(content)
        if m:
            return await self._handle_reject_plan(m.group(1))

        m = SHOW_PLAN_PATTERN.search(content)
        if m:
            return await self._handle_show_plan(m.group(1))

        m = GOAL_PATTERN.search(content)
        if m:
            return await self._handle_goal(m.group(1), m.group(2))

        m = OWN_PATTERN.search(content)
        if m:
            return await self._handle_own(m.group(1), m.group(2).lower())

        m = DEPS_PATTERN.search(content)
        if m:
            return await self._handle_deps(m.group(1))

        m = BACKLOG_PATTERN.search(content)
        if m:
            scope = (m.group(1) or "all").lower()
            return await self._handle_backlog(scope)

        m = DAILY_PATTERN.search(content)
        if m:
            return await self._handle_daily()

        m = STATUS_PATTERN.search(content)
        if m:
            return await self._handle_status()

        return None

    async def _handle_status(self) -> str:
        """Fetch daemon pipeline summary."""
        try:
            data = await self._request("get", "/api/daemon/status")
            if data is None:
                return "Daemon is not running."
        except Exception as e:
            return f"Could not reach daemon: {e}"

        # Format summary
        counts = data.get("pipeline_counts", {})
        if not counts:
            return "Pipeline is empty. Nothing tracked."

        parts = ["**Daemon Pipeline**"]
        for status, count in sorted(counts.items()):
            if count > 0:
                parts.append(f"  {status}: {count}")

        uptime = data.get("uptime_seconds", 0)
        if uptime:
            hours = uptime // 3600
            mins = (uptime % 3600) // 60
            parts.append(f"\nUptime: {hours}h {mins}m")

        errors = data.get("recent_errors", [])
        if errors:
            parts.append(f"\nRecent errors: {len(errors)}")

        return "\n".join(parts)

    async def _handle_backlog(self, scope: str) -> str:
        """Fetch filtered backlog."""
        owner_filter = None
        if scope == "mine":
            owner_filter = "human"
        elif scope == "grim":
            owner_filter = "grim"

        try:
            params: dict[str, str] = {"status": "backlog"}
            if owner_filter:
                params["owner"] = owner_filter
            items = await self._request("get", "/api/daemon/pipeline", params=params)
            if items is None:
                return "Daemon is not running."
        except Exception as e:
            return f"Could not reach daemon: {e}"

        if not items:
            label = {"mine": "your", "grim": "GRIM's", "all": "the"}[scope]
            return f"Nothing in {label} backlog."

        parts = [f"**{'Your' if scope == 'mine' else 'GRIM' if scope == 'grim' else 'Full'} Backlog** ({len(items)} items)"]
        for item in items[:10]:  # cap at 10 to stay under Discord limits
            owner_tag = f" [{item.get('owner', '?')}]" if scope == "all" else ""
            parts.append(
                f"  `{item['story_id']}`{owner_tag} — {item.get('project_id', '?')} "
                f"(p{item.get('priority', '?')})"
            )
        if len(items) > 10:
            parts.append(f"  ... and {len(items) - 10} more")

        return "\n".join(parts)

    async def _handle_own(self, story_id: str, owner: str) -> str:
        """Transfer story ownership via pipeline."""
        try:
            items = await self._request("get", "/api/daemon/pipeline")
            if items is None:
                return "Daemon is not running."

            # Find matching pipeline item
            item_id = None
            for item in items:
                if item.get("story_id") == story_id:
                    item_id = item["id"]
                    break

            if not item_id:
                return f"Story `{story_id}` not found in daemon pipeline."

            result = await self._request(
                "patch", f"/api/daemon/pipeline/{item_id}/owner",
                json={"owner": owner},
            )
            if result is not None:
                return f"Ownership of `{story_id}` transferred to **{owner}**."
            return "Failed to update ownership."
        except Exception as e:
            return f"Could not reach daemon: {e}"

    async def _handle_deps(self, story_id: str) -> str:
        """Show dependency tree for a story."""
        try:
            data = await self._request("get", f"/api/daemon/pipeline/{story_id}/dependencies")
            if data is None:
                return f"Story `{story_id}` not found."
        except Exception as e:
            return f"Could not reach daemon: {e}"

        parts = [f"**Dependencies for `{story_id}`**"]

        deps = data.get("depends_on", [])
        if deps:
            parts.append("Depends on:")
            for dep in deps:
                status_icon = "done" if dep.get("satisfied") else "pending"
                parts.append(f"  `{dep['story_id']}` — {dep.get('status', '?')} ({status_icon})")
        else:
            parts.append("No dependencies.")

        blocked_by = data.get("blocked_by", [])
        if blocked_by:
            parts.append(f"\nBlocked by: {', '.join(f'`{b}`' for b in blocked_by)}")

        dependents = data.get("dependents", [])
        if dependents:
            parts.append(f"\nBlocks: {', '.join(f'`{d}`' for d in dependents)}")

        return "\n".join(parts)

    async def _handle_goal(self, proj_id: str, description: str) -> str:
        """Create a goal story (assignee: plan, tag: goal)."""
        try:
            data = await self._request(
                "post", "/api/daemon/goals",
                json={"proj_id": proj_id, "description": description},
            )
            if data is None:
                return "Daemon not running."
        except Exception as e:
            return f"Could not reach daemon: {e}"

        if "error" in data:
            return f"Failed: {data['error']}"

        story_id = data.get("story_id", "?")
        return f"Goal created: `{story_id}` in **{proj_id}**. PLAN agent will decompose it."

    async def _handle_show_plan(self, story_id: str) -> str:
        """Show generated sub-stories for a goal."""
        try:
            data = await self._request("get", f"/api/daemon/goals/{story_id}/plan")
            if data is None:
                return f"Goal `{story_id}` not found."
        except Exception as e:
            return f"Could not reach daemon: {e}"

        children = data.get("children", [])
        if not children:
            return f"No sub-stories found for `{story_id}`."

        parts = [f"**Plan for `{story_id}`** ({len(children)} stories)"]
        for child in children[:15]:
            status_tag = f" [{child.get('status', '?')}]"
            deps = child.get("depends_on", [])
            dep_tag = f" → {', '.join(deps)}" if deps else ""
            parts.append(f"  `{child['id']}` {child.get('assignee', '?')}{status_tag} — {child.get('title', '?')}{dep_tag}")
        if len(children) > 15:
            parts.append(f"  ... and {len(children) - 15} more")

        return "\n".join(parts)

    async def _handle_approve_plan(self, story_id: str) -> str:
        """Approve and activate a proposed plan."""
        try:
            data = await self._request("post", f"/api/daemon/goals/{story_id}/approve")
            if data is None:
                return "Daemon not running."
        except Exception as e:
            return f"Could not reach daemon: {e}"

        if "error" in data:
            return f"Failed: {data['error']}"

        activated = data.get("activated", 0)
        return f"Plan approved for `{story_id}`. {activated} stories activated."

    async def _handle_reject_plan(self, story_id: str) -> str:
        """Reject a proposed plan."""
        try:
            data = await self._request("post", f"/api/daemon/goals/{story_id}/reject")
            if data is None:
                return "Daemon not running."
        except Exception as e:
            return f"Could not reach daemon: {e}"

        if "error" in data:
            return f"Failed: {data['error']}"

        closed = data.get("closed", 0)
        return f"Plan rejected for `{story_id}`. {closed} draft stories closed."

    async def _handle_daily(self) -> str:
        """Fetch on-demand daily summary."""
        try:
            data = await self._request("get", "/api/daemon/daily")
            if data is None:
                return "Daemon is not running."
        except Exception as e:
            return f"Could not reach daemon: {e}"

        formatted = data.get("formatted", "")
        if formatted:
            return formatted
        return "No daily summary available."


# ── Daemon Event Formatting ──────────────────────────────────────────────────

def format_daemon_event(event: dict) -> str | None:
    """Format a daemon event for Discord display.

    Returns formatted string or None if event type is not display-worthy.
    """
    event_type = event.get("event_type", event.get("type", ""))
    data = event.get("data", event)  # data might be nested or flat

    if event_type == "daemon_nudge":
        story_id = data.get("story_id", "?")
        idle_days = data.get("idle_days", "?")
        return (
            f"**Nudge** — `{story_id}` has been sitting in your backlog "
            f"for {idle_days} days. Need a hand with it?"
        )

    if event_type == "daemon_escalation":
        story_id = data.get("story_id", "?")
        question = data.get("question", "")
        reason = data.get("reason", "")
        job_id = event.get("job_id", data.get("job_id", ""))
        parts = [f"**Daemon Escalation** `{story_id}`"]
        if question:
            parts.append(f"Question: {question}")
        if reason:
            parts.append(f"Reason: {reason}")
        if job_id:
            parts.append(f"\n*Reply: `@GRIM clarify {job_id} your answer`*")
        return "\n".join(parts)

    if event_type == "daemon_auto_resolved":
        story_id = data.get("story_id", "?")
        source = data.get("source", "?")
        confidence = data.get("confidence", 0)
        return (
            f"**Auto-Resolved** — `{story_id}` question answered via {source} "
            f"(confidence: {confidence:.0%})"
        )

    if event_type == "daemon_approved":
        story_id = data.get("story_id", "?")
        pr_url = data.get("pr_url", "")
        return f"**Approved** — `{story_id}` merged.{' ' + pr_url if pr_url else ''}"

    if event_type == "daemon_rejected":
        story_id = data.get("story_id", "?")
        reason = data.get("reason", "")
        return f"**Rejected** — `{story_id}`.{' ' + reason if reason else ''}"

    if event_type == "daemon_dependency_satisfied":
        story_id = data.get("story_id", "?")
        return f"**Dependencies Met** — `{story_id}` unblocked and promoting to READY."

    if event_type == "daemon_dependency_blocked":
        story_id = data.get("story_id", "?")
        blocking = data.get("blocking", [])
        blockers = ", ".join(f"`{b}`" for b in blocking) if blocking else "unknown"
        return f"**Blocked** — `{story_id}` waiting on: {blockers}"

    if event_type == "daemon_plan_proposed":
        story_id = data.get("story_id", "?")
        count = data.get("story_count", "?")
        return (
            f"**Plan Ready** — `{story_id}` decomposed into {count} stories.\n"
            f"*Reply: `@GRIM show plan {story_id}` to review, "
            f"`@GRIM approve plan {story_id}` to proceed.*"
        )

    if event_type == "daemon_goal_complete":
        story_id = data.get("story_id", "?")
        count = data.get("children_count", "?")
        return f"**Goal Complete** — `{story_id}` finished. All {count} sub-stories resolved."

    if event_type == "daemon_research_complete":
        story_id = data.get("story_id", "?")
        has_result = data.get("has_result", False)
        suffix = " Results captured." if has_result else " No results captured."
        return f"**Research Done** — `{story_id}` complete.{suffix} Dependent stories unblocked."

    if event_type == "daemon_stuck_warning":
        story_id = data.get("story_id", "?")
        hours = data.get("hours_dispatched", "?")
        return f"**Stuck** — `{story_id}` has been running for {hours} hours. Checking in."

    if event_type == "daemon_daily_summary":
        formatted = data.get("formatted", "")
        if formatted:
            return formatted
        return "**Daily Summary** — no data available."

    # Pool events routed to daemon channel
    if event_type == "job_review":
        story_id = data.get("story_id", "?")
        pr_url = data.get("pr_url", "")
        pr_number = data.get("pr_number", "")
        diff_stat = data.get("diff_stat", "")
        parts = [f"**PR Created** — `{story_id}` PR #{pr_number}"]
        if pr_url:
            parts.append(pr_url)
        if diff_stat:
            parts.append(f"```\n{diff_stat[:500]}\n```")
        return "\n".join(parts)

    if event_type == "job_complete":
        story_id = data.get("story_id", "")
        cost = data.get("cost_usd")
        turns = data.get("num_turns")
        parts = [f"**Job Complete** — `{story_id}`"]
        if turns:
            parts[0] += f" ({turns} turns, ${cost:.2f})" if cost else f" ({turns} turns)"
        return parts[0]

    if event_type == "job_failed":
        story_id = data.get("story_id", "")
        error = data.get("error", "unknown")
        return f"**Job Failed** — `{story_id}`: {error[:200]}"

    return None


# ── Daemon Event Type Detection ───────────────────────────────────────────────

DAEMON_EVENT_TYPES = frozenset({
    "daemon_nudge",
    "daemon_escalation",
    "daemon_auto_resolved",
    "daemon_approved",
    "daemon_rejected",
    "daemon_dependency_satisfied",
    "daemon_dependency_blocked",
    "daemon_plan_proposed",
    "daemon_goal_complete",
    "daemon_research_complete",
    "daemon_stuck_warning",
    "daemon_daily_summary",
    # Pool events that should also route to daemon channel
    "job_review",         # PR created — Peter wants Discord notification
    "job_complete",       # Job finished (any type)
    "job_failed",         # Job failed
})


def is_daemon_event(event: dict) -> bool:
    """Check if an event is a daemon event that should route to the daemon channel.

    Note: _broadcast_pool_event wraps events with ``type: "pool_event"`` so the
    original event type lives in ``event_type`` (from PoolEvent.to_dict()).
    We check ``event_type`` first to avoid the wrapper shadowing the real type.
    """
    event_type = event.get("event_type", event.get("type", ""))
    return event_type in DAEMON_EVENT_TYPES
