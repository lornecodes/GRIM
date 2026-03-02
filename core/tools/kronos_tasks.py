"""Task management Kronos tools — board, stories, tasks, calendar.

Read tools are available to the companion (thinker) for answering
queries about the board, backlog, and calendar.
Write tools are available to the memory agent for creating/updating
stories, moving items on the board, and managing calendar events.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

from core.tools.kronos_read import _call_mcp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Read tools — companion + all agents
# ---------------------------------------------------------------------------


@tool
async def kronos_board_view(project_id: str = "") -> str:
    """View the kanban board with stories grouped by column.

    Returns stories in columns: new, active, in_progress, resolved, closed.
    Each story includes title, priority, estimate, task counts, and feature.

    Args:
        project_id: Optional project ID to filter (e.g., "proj-grim").

    Returns:
        JSON with board columns and story summaries.
    """
    kwargs: dict[str, Any] = {}
    if project_id:
        kwargs["project_id"] = project_id
    result = await _call_mcp("kronos_board_view", **kwargs)
    return json.dumps(result, indent=2)


@tool
async def kronos_backlog_view(project_id: str = "", feat_id: str = "", priority: str = "") -> str:
    """View stories NOT on the kanban board (the backlog).

    Args:
        project_id: Optional project filter.
        feat_id: Optional feature filter.
        priority: Optional priority filter (critical, high, medium, low).

    Returns:
        JSON with backlog stories and count.
    """
    kwargs: dict[str, Any] = {}
    if project_id:
        kwargs["project_id"] = project_id
    if feat_id:
        kwargs["feat_id"] = feat_id
    if priority:
        kwargs["priority"] = priority
    result = await _call_mcp("kronos_backlog_view", **kwargs)
    return json.dumps(result, indent=2)


@tool
async def kronos_task_get(item_id: str) -> str:
    """Get full details of a story or task by ID.

    Args:
        item_id: Story or task ID (e.g., "story-grim-ui-001", "task-grim-ui-001-002").

    Returns:
        JSON with all fields including nested tasks (for stories).
    """
    result = await _call_mcp("kronos_task_get", item_id=item_id)
    return json.dumps(result, indent=2)


@tool
async def kronos_task_list(
    project_id: str = "",
    feat_id: str = "",
    status: str = "",
    priority: str = "",
) -> str:
    """List stories with optional filters.

    Args:
        project_id: Filter by project (e.g., "proj-grim").
        feat_id: Filter by feature (e.g., "feat-grim-ui").
        status: Filter by status (draft, new, active, in_progress, resolved, closed).
        priority: Filter by priority (critical, high, medium, low).

    Returns:
        JSON with filtered stories and count.
    """
    kwargs: dict[str, Any] = {}
    if project_id:
        kwargs["project_id"] = project_id
    if feat_id:
        kwargs["feat_id"] = feat_id
    if status:
        kwargs["status"] = status
    if priority:
        kwargs["priority"] = priority
    result = await _call_mcp("kronos_task_list", **kwargs)
    return json.dumps(result, indent=2)


@tool
async def kronos_calendar_view(start_date: str, end_date: str) -> str:
    """Get calendar entries for a date range.

    Merges work schedule (from board items + estimates) with personal events.

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).

    Returns:
        JSON with calendar entries.
    """
    result = await _call_mcp(
        "kronos_calendar_view",
        start_date=start_date,
        end_date=end_date,
    )
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Write tools — memory agent only
# ---------------------------------------------------------------------------


@tool
async def kronos_task_create(
    type: str,
    title: str,
    feat_id: str = "",
    story_id: str = "",
    priority: str = "medium",
    estimate_days: float = 1.0,
    description: str = "",
    acceptance_criteria: str = "",
    tags: str = "",
    notes: str = "",
    assignee: str = "",
    created_by: str = "",
    status: str = "",
) -> str:
    """Create a new story or task.

    Stories live inside feat-* FDOs. Tasks are nested under stories.
    AI-created items should use status="draft" and created_by="agent:<name>".

    Args:
        type: "story" or "task".
        title: Title of the story or task.
        feat_id: Feature FDO ID (required for stories, e.g., "feat-grim-ui").
        story_id: Parent story ID (required for tasks).
        priority: Priority level for stories (critical, high, medium, low).
        estimate_days: Estimated days to complete.
        description: Story description.
        acceptance_criteria: JSON array string of acceptance criteria (stories only).
        tags: JSON array string of tags (stories only).
        notes: Task notes.
        assignee: Task assignee.
        created_by: Who created this (e.g. "human", "agent:planning").
        status: Initial status — "draft" for AI-created, "new" for human-created.

    Returns:
        JSON with creation result (may include warnings).
    """
    kwargs: dict[str, Any] = {"type": type, "title": title}
    if feat_id:
        kwargs["feat_id"] = feat_id
    if story_id:
        kwargs["story_id"] = story_id
    if priority and type == "story":
        kwargs["priority"] = priority
    if estimate_days:
        kwargs["estimate_days"] = estimate_days
    if description:
        kwargs["description"] = description
    if acceptance_criteria:
        try:
            kwargs["acceptance_criteria"] = json.loads(acceptance_criteria)
        except (json.JSONDecodeError, TypeError):
            kwargs["acceptance_criteria"] = [acceptance_criteria]
    if tags:
        try:
            kwargs["tags"] = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            kwargs["tags"] = [tags]
    if notes:
        kwargs["notes"] = notes
    if assignee:
        kwargs["assignee"] = assignee
    if created_by:
        kwargs["created_by"] = created_by
    if status:
        kwargs["status"] = status
    result = await _call_mcp("kronos_task_create", **kwargs)
    return json.dumps(result, indent=2)


@tool
async def kronos_task_update(item_id: str, fields: str) -> str:
    """Update fields on a story or task.

    Args:
        item_id: Story or task ID to update.
        fields: JSON string of fields to update. Stories: title, status, priority,
                estimate_days, description. Tasks: title, status, estimate_days, notes, assignee.

    Returns:
        JSON with update result.
    """
    try:
        parsed_fields = json.loads(fields)
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"error": "fields must be a valid JSON string"})
    result = await _call_mcp("kronos_task_update", item_id=item_id, fields=parsed_fields)
    return json.dumps(result, indent=2)


@tool
async def kronos_task_move(story_id: str, column: str) -> str:
    """Move a story on the kanban board. Auto-updates story status to match the column.

    Args:
        story_id: Story ID to move.
        column: Target column (new, active, in_progress, resolved, closed).

    Returns:
        JSON with move result.
    """
    result = await _call_mcp("kronos_task_move", story_id=story_id, column=column)
    return json.dumps(result, indent=2)


@tool
async def kronos_task_archive(feat_id: str = "") -> str:
    """Archive closed stories. Moves them from active to archived in feature FDOs.

    Args:
        feat_id: Optional feature to archive (omit to archive all closed stories).

    Returns:
        JSON with archive result.
    """
    kwargs: dict[str, Any] = {}
    if feat_id:
        kwargs["feat_id"] = feat_id
    result = await _call_mcp("kronos_task_archive", **kwargs)
    return json.dumps(result, indent=2)


@tool
async def kronos_calendar_add(
    title: str,
    date: str,
    time: str = "",
    duration_hours: float = 0,
    notes: str = "",
) -> str:
    """Add a personal calendar event.

    Args:
        title: Event title.
        date: Event date (YYYY-MM-DD).
        time: Optional event time (HH:MM).
        duration_hours: Optional duration in hours.
        notes: Optional notes.

    Returns:
        JSON with creation result.
    """
    kwargs: dict[str, Any] = {"title": title, "date": date}
    if time:
        kwargs["time"] = time
    if duration_hours:
        kwargs["duration_hours"] = duration_hours
    if notes:
        kwargs["notes"] = notes
    result = await _call_mcp("kronos_calendar_add", **kwargs)
    return json.dumps(result, indent=2)


@tool
async def kronos_calendar_update(event_id: str, action: str = "update", fields: str = "{}") -> str:
    """Update or delete a personal calendar event.

    Args:
        event_id: Personal event ID (e.g., "personal-001").
        action: "update" or "delete".
        fields: JSON string of fields to update (title, date, time, duration_hours, notes).

    Returns:
        JSON with update result.
    """
    kwargs: dict[str, Any] = {"event_id": event_id, "action": action}
    if action == "update":
        try:
            parsed = json.loads(fields)
            kwargs.update(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    result = await _call_mcp("kronos_calendar_update", **kwargs)
    return json.dumps(result, indent=2)


@tool
async def kronos_calendar_sync(start_date: str = "") -> str:
    """Rebuild the work schedule from active board items + estimates.

    Sequences stories by priority, computes start/end dates.
    Call after moving items on the board or updating estimates.

    Args:
        start_date: Start date for scheduling (YYYY-MM-DD, default: today).

    Returns:
        JSON with sync result.
    """
    kwargs: dict[str, Any] = {}
    if start_date:
        kwargs["start_date"] = start_date
    result = await _call_mcp("kronos_calendar_sync", **kwargs)
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Tool collections
# ---------------------------------------------------------------------------

TASK_READ_TOOLS = [
    kronos_board_view,
    kronos_backlog_view,
    kronos_task_get,
    kronos_task_list,
    kronos_calendar_view,
]

TASK_WRITE_TOOLS = [
    kronos_task_create,
    kronos_task_update,
    kronos_task_move,
    kronos_task_archive,
    kronos_calendar_add,
    kronos_calendar_update,
    kronos_calendar_sync,
]

TASK_ALL_TOOLS = TASK_READ_TOOLS + TASK_WRITE_TOOLS

# Register with tool registry
from core.tools.registry import tool_registry
tool_registry.register_group("tasks_read", TASK_READ_TOOLS)
tool_registry.register_group("tasks_write", TASK_WRITE_TOOLS)
tool_registry.register_group("tasks", TASK_ALL_TOOLS)
