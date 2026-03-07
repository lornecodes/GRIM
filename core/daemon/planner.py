"""Goal decomposition — parse PLAN agent output and create draft stories.

A goal enters the daemon as a story with assignee=plan and tag=goal.
When the PLAN job completes, PlanParser validates the structured output,
PlanExecutor creates draft sub-stories with dependency chains, and
GoalTracker monitors child completion to auto-resolve the parent goal.

Expected PLAN agent output format (YAML):
    stories:
      - title: str (required)
        assignee: code|research|audit (required)
        description: str
        acceptance_criteria: [str]
        depends_on_index: [int]  # indices into this array
        priority: critical|high|medium|low
        estimate_days: float
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ── Valid values ──────────────────────────────────────────────────────────────

_VALID_ASSIGNEES = {"code", "research", "audit"}
_VALID_PRIORITIES = {"critical", "high", "medium", "low"}


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class PlannedStory:
    """A story parsed from PLAN agent output, before vault creation."""

    title: str
    assignee: str
    description: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    depends_on_index: list[int] = field(default_factory=list)
    priority: str = "medium"
    estimate_days: float = 1.0


@dataclass
class ParsedPlan:
    """Result of parsing PLAN agent output."""

    stories: list[PlannedStory]
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return len(self.stories) > 0 and len(self.errors) == 0


@dataclass
class ExecutedPlan:
    """Result of executing a plan — stories created in vault."""

    goal_story_id: str
    created_ids: list[str]
    dependency_map: dict[str, list[str]]  # {story_id: [dep_story_ids]}


# ── PlanParser ───────────────────────────────────────────────────────────────

class PlanParser:
    """Validates and parses PLAN agent output into PlannedStory objects."""

    def parse(self, result_text: str) -> ParsedPlan:
        """Parse PLAN agent output text.

        Looks for a YAML block containing a `stories` array.
        Returns ParsedPlan with stories and any validation errors.
        """
        errors: list[str] = []
        stories: list[PlannedStory] = []

        # Extract YAML — try fenced code block first, then raw text
        yaml_text = self._extract_yaml(result_text)
        if not yaml_text:
            return ParsedPlan(stories=[], errors=["No YAML content found in PLAN output"])

        # Parse YAML
        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError as e:
            return ParsedPlan(stories=[], errors=[f"Invalid YAML: {e}"])

        if not isinstance(data, dict):
            return ParsedPlan(stories=[], errors=["PLAN output must be a YAML mapping with 'stories' key"])

        raw_stories = data.get("stories", [])
        if not isinstance(raw_stories, list) or not raw_stories:
            return ParsedPlan(stories=[], errors=["No stories found in PLAN output"])

        # Validate each story
        for i, raw in enumerate(raw_stories):
            if not isinstance(raw, dict):
                errors.append(f"Story {i}: not a mapping")
                continue

            # Required fields
            title = raw.get("title", "")
            if not title or not isinstance(title, str):
                errors.append(f"Story {i}: missing or invalid title")
                continue

            assignee = raw.get("assignee", "")
            if assignee not in _VALID_ASSIGNEES:
                errors.append(f"Story {i}: invalid assignee '{assignee}' (must be code/research/audit)")
                continue

            # Optional fields with defaults
            priority = raw.get("priority", "medium")
            if priority not in _VALID_PRIORITIES:
                priority = "medium"

            estimate = raw.get("estimate_days", 1.0)
            try:
                estimate = float(estimate)
            except (TypeError, ValueError):
                estimate = 1.0

            depends_on_index = raw.get("depends_on_index", [])
            if not isinstance(depends_on_index, list):
                depends_on_index = []
            # Validate indices
            valid_indices = []
            for idx in depends_on_index:
                if isinstance(idx, int) and 0 <= idx < len(raw_stories) and idx != i:
                    valid_indices.append(idx)
                else:
                    errors.append(f"Story {i}: invalid depends_on_index {idx}")
            depends_on_index = valid_indices

            ac = raw.get("acceptance_criteria", [])
            if not isinstance(ac, list):
                ac = []

            stories.append(PlannedStory(
                title=title,
                assignee=assignee,
                description=raw.get("description", "") or "",
                acceptance_criteria=[str(c) for c in ac],
                depends_on_index=depends_on_index,
                priority=priority,
                estimate_days=estimate,
            ))

        if not stories:
            errors.append("No valid stories after validation")

        return ParsedPlan(stories=stories, errors=errors)

    @staticmethod
    def _extract_yaml(text: str) -> str:
        """Extract YAML from text — tries fenced block first, then raw."""
        # Try ```yaml ... ``` block
        m = re.search(r"```(?:yaml|yml)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()

        # Try raw YAML starting with "stories:"
        m = re.search(r"(stories:\s*\n.*)", text, re.DOTALL)
        if m:
            return m.group(1).strip()

        return ""


# ── PlanExecutor ─────────────────────────────────────────────────────────────

class PlanExecutor:
    """Creates draft stories in vault from a parsed plan."""

    def __init__(self, task_engine: Any):
        self._task_engine = task_engine

    def execute(
        self,
        plan: ParsedPlan,
        proj_id: str,
        goal_story_id: str,
    ) -> ExecutedPlan:
        """Create draft stories from a parsed plan.

        Stories are created sequentially so depends_on_index can be
        resolved to real story IDs. All stories get owner=grim,
        status=draft, created_by=agent:plan.
        """
        created_ids: list[str] = []
        dependency_map: dict[str, list[str]] = {}

        for i, planned in enumerate(plan.stories):
            # Resolve depends_on_index to real story IDs
            depends_on = []
            for dep_idx in planned.depends_on_index:
                if dep_idx < len(created_ids):
                    depends_on.append(created_ids[dep_idx])

            result = self._task_engine.create_story(
                proj_id=proj_id,
                title=planned.title,
                priority=planned.priority,
                estimate_days=planned.estimate_days,
                description=planned.description,
                acceptance_criteria=planned.acceptance_criteria,
                assignee=planned.assignee,
                tags=["goal-child", f"goal:{goal_story_id}"],
                created_by="agent:plan",
                status="draft",
                owner="grim",
                depends_on=depends_on,
            )

            if "error" in result:
                logger.warning("Failed to create story %d from plan: %s", i, result["error"])
                # Use a placeholder so indices stay aligned
                created_ids.append("")
                continue

            story_id = result["created"]
            created_ids.append(story_id)
            if depends_on:
                dependency_map[story_id] = depends_on

        # Filter out empty placeholders
        valid_ids = [sid for sid in created_ids if sid]

        return ExecutedPlan(
            goal_story_id=goal_story_id,
            created_ids=valid_ids,
            dependency_map=dependency_map,
        )

    def activate_plan(self, executed: ExecutedPlan) -> int:
        """Activate all draft stories from an executed plan.

        Sets status from 'draft' to 'active' on each child story.
        Returns count of activated stories.
        """
        activated = 0
        for story_id in executed.created_ids:
            result = self._task_engine.update_item(story_id, {"status": "active"})
            if "error" not in result:
                activated += 1
            else:
                logger.warning("Failed to activate %s: %s", story_id, result["error"])
        return activated

    def reject_plan(self, executed_ids: list[str]) -> int:
        """Delete draft stories from a rejected plan.

        Sets status to 'closed' (we don't physically delete, just close).
        Returns count of closed stories.
        """
        closed = 0
        for story_id in executed_ids:
            result = self._task_engine.update_item(story_id, {"status": "closed"})
            if "error" not in result:
                closed += 1
        return closed


# ── GoalTracker ──────────────────────────────────────────────────────────────

class GoalTracker:
    """Tracks goal completion by monitoring child story statuses."""

    def __init__(self, task_engine: Any):
        self._task_engine = task_engine

    def check_goal_complete(self, goal_story_id: str) -> tuple[bool, dict]:
        """Check if all children of a goal story are resolved/closed.

        Returns (complete, stats) where stats has counts.
        """
        # Find children by listing stories with goal tag
        all_items = self._task_engine.list_items()
        children = [
            item for item in all_items
            if f"goal:{goal_story_id}" in (item.get("tags") or [])
        ]

        if not children:
            return False, {"total": 0, "done": 0, "pending": 0}

        done_statuses = {"resolved", "closed"}
        done = sum(1 for c in children if c.get("status") in done_statuses)
        pending = len(children) - done

        return pending == 0, {
            "total": len(children),
            "done": done,
            "pending": pending,
        }

    def auto_resolve_goal(self, goal_story_id: str) -> bool:
        """Mark a goal as resolved if all children are done.

        Returns True if the goal was resolved.
        """
        complete, stats = self.check_goal_complete(goal_story_id)
        if not complete:
            return False

        result = self._task_engine.update_item(goal_story_id, {"status": "resolved"})
        if "error" in result:
            logger.warning("Failed to resolve goal %s: %s", goal_story_id, result["error"])
            return False

        logger.info("Goal %s auto-resolved: %d children complete", goal_story_id, stats["total"])
        return True
