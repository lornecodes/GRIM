"""ManagementEngine — the orchestration loop for the daemon.

Runs as an asyncio background task. Each cycle:
1. Scans vault for eligible stories via ProjectScanner
2. Advances BACKLOG items to READY
3. Dispatches READY items by submitting Jobs to ExecutionPool
4. Listens on PoolEventBus for job lifecycle events

Phase 3 adds intelligent event handling: auto-resolve blocked questions,
validate completed output against acceptance criteria, and enrich retry
instructions with feedback. LLM calls are surgical and optional.

Phase 4 adds PR lifecycle: create PRs for CODE jobs on REVIEW, approve/reject
endpoints, and PR comment polling. Non-CODE jobs skip PRs and advance directly
to MERGED.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from core.daemon.health import HealthMonitor
from core.daemon.models import PipelineStatus
from core.daemon.pipeline import PipelineStore
from core.daemon.scanner import ProjectScanner

if TYPE_CHECKING:
    from core.pool.events import PoolEvent, PoolEventBus
    from core.pool.models import Job
    from core.pool.queue import JobQueue

logger = logging.getLogger(__name__)


class ManagementEngine:
    """Project-level orchestration daemon.

    Watches vault for dispatchable stories, submits them as pool jobs,
    and reacts to job lifecycle events.

    Usage:
        engine = ManagementEngine(config, pool_queue, pool_events)
        await engine.start()
        # ... daemon runs until stopped ...
        await engine.stop()
    """

    def __init__(
        self,
        config: Any,
        pool_queue: JobQueue,
        pool_events: PoolEventBus,
        vault_path: Path | None = None,
        task_engine: Any | None = None,
        workspace_manager: Any | None = None,
    ) -> None:
        self._config = config
        self._pool_queue = pool_queue
        self._pool_events = pool_events

        # Pipeline store (SQLite)
        db_path = getattr(config, "daemon_db_path", Path("local/daemon.db"))
        self._store = PipelineStore(db_path)

        # Scanner
        _vault_path = vault_path or getattr(config, "vault_path", Path("../kronos-vault"))
        self._vault_path = _vault_path
        project_filter = getattr(config, "daemon_project_filter", []) or []
        self._scanner = ProjectScanner(_vault_path, project_filter or None)

        # Task engine for writing job_id back to vault
        self._task_engine = task_engine

        # Workspace manager for PR lifecycle (Phase 4)
        self._workspace_mgr = workspace_manager

        # Context builder for rich agent instructions
        workspace_root = getattr(config, "workspace_root", _vault_path.parent)
        self._context_builder = self._make_context_builder(_vault_path, workspace_root, self._store)

        # Config values
        self._poll_interval = getattr(config, "daemon_poll_interval", 30.0)
        self._max_concurrent = getattr(config, "daemon_max_concurrent_jobs", 1)
        self._auto_dispatch = getattr(config, "daemon_auto_dispatch", True)

        # Phase 3: Intelligence config
        self._auto_resolve = config.daemon_auto_resolve
        self._validate_output = config.daemon_validate_output
        self._max_daemon_retries = config.daemon_max_daemon_retries

        # Phase 5A: Ownership config
        self._default_owner = getattr(config, "daemon_default_owner", "grim")
        self._nudge_after_days = getattr(config, "daemon_nudge_after_days", 3)
        self._last_nudge_check: float = 0.0  # monotonic time of last nudge cycle

        # Phase 5C: Goal Decomposition
        self._auto_approve_threshold = getattr(config, "daemon_auto_approve_threshold", 3)

        # Phase 5E: Proactive Notifications
        self._daily_summary_hour = getattr(config, "daemon_daily_summary_hour", 14)
        self._stuck_threshold_hours = getattr(config, "daemon_stuck_threshold_hours", 2)
        self._notifier = self._make_notifier()
        self._last_notification_check: float = 0.0  # monotonic time
        self._last_daily_summary_date: str = ""  # ISO date of last daily summary

        # Phase 3: Intelligence module (optional — degrades gracefully)
        self._intelligence = self._make_intelligence(config)

        # Phase 4: PR lifecycle
        self._auto_pr = getattr(config, "daemon_auto_pr", True)
        self._pr_poll_interval = getattr(config, "daemon_pr_poll_interval", 300)
        self._github = self._make_github_client(config)

        # Runtime state
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None
        self._pr_poll_task: Optional[asyncio.Task] = None
        self._health = HealthMonitor()
        self._event_callback = self._handle_pool_event  # stable reference for sub/unsub

    @property
    def store(self) -> PipelineStore:
        """Access the pipeline store (for endpoints)."""
        return self._store

    @property
    def health(self) -> HealthMonitor:
        """Access the health monitor (for endpoints)."""
        return self._health

    def _ensure_task_engine(self) -> bool:
        """Lazy-init the TaskEngine. Returns True if available."""
        if self._task_engine is not None:
            return True
        try:
            from kronos_mcp.tasks import TaskEngine
            self._task_engine = TaskEngine(str(self._scanner._vault_path))
            return True
        except ImportError:
            return False

    def _make_notifier(self):
        """Create a DaemonNotifier, or None if imports fail."""
        try:
            from core.daemon.notifier import DaemonNotifier
            return DaemonNotifier(stuck_threshold_hours=self._stuck_threshold_hours)
        except ImportError:
            return None

    async def start(self) -> None:
        """Initialize store and start the main loop."""
        await self._store.initialize()
        self._pool_events.subscribe(self._event_callback)
        self._running = True
        self._loop_task = asyncio.create_task(self._main_loop())
        # Start PR comment polling if GitHub is available
        if self._github and self._auto_pr:
            self._pr_poll_task = asyncio.create_task(self._pr_poll_loop())
        logger.info("ManagementEngine started (poll=%ss, max_concurrent=%d)",
                     self._poll_interval, self._max_concurrent)

    async def stop(self) -> None:
        """Gracefully stop the engine."""
        self._running = False
        self._pool_events.unsubscribe(self._event_callback)
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        if self._pr_poll_task:
            self._pr_poll_task.cancel()
            try:
                await self._pr_poll_task
            except asyncio.CancelledError:
                pass
        logger.info("ManagementEngine stopped")

    # ── Main Loop ─────────────────────────────────────────────────

    async def _main_loop(self) -> None:
        """Periodic scan + dispatch cycle."""
        # Initial delay before first cycle (let startup complete)
        try:
            await asyncio.sleep(min(self._poll_interval, 5.0))
        except asyncio.CancelledError:
            return

        while self._running:
            try:
                await self._cycle()
            except Exception:
                logger.exception("Daemon cycle error")
                self._health.record_error("Cycle failed — see logs")

            try:
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break

    async def _cycle(self) -> None:
        """One full scan + dispatch cycle."""
        # Step 1: Sync vault → pipeline
        await self._scan_cycle()

        # Step 2: Advance BACKLOG → READY (if auto_dispatch enabled)
        if self._auto_dispatch:
            await self._promote_cycle()

        # Step 3: Dispatch READY → pool
        await self._dispatch_cycle()

        # Step 4: Nudge idle human-owned stories (rate-limited to once/hour)
        try:
            await self._nudge_cycle()
        except Exception:
            logger.exception("Nudge cycle error")

        # Step 5: Check goal completion (Phase 5C)
        try:
            await self._goal_tracking_cycle()
        except Exception:
            logger.exception("Goal tracking cycle error")

        # Step 6: Proactive notifications (rate-limited, Phase 5E)
        try:
            await self._notification_cycle()
        except Exception:
            logger.exception("Notification cycle error")

        self._health.record_scan()

    async def _scan_cycle(self) -> None:
        """Sync pipeline with vault stories."""
        try:
            result = await self._scanner.sync_pipeline(self._store)
            if result["added"] or result["removed"] or result["updated"]:
                logger.info("Scan sync: %s", result)
        except Exception:
            logger.exception("Scan cycle failed")
            self._health.record_error("Scan failed")

    async def _promote_cycle(self) -> None:
        """Advance BACKLOG items to READY.

        Human-owned stories stay in BACKLOG — visible but never auto-promoted.
        Stories with unsatisfied dependencies stay in BACKLOG.
        """
        from core.daemon.scanner import check_dependencies

        backlog = await self._store.list_items(status_filter=PipelineStatus.BACKLOG)

        # Batch-resolve dependency statuses for all stories that have deps
        dep_story_ids: set[str] = set()
        for item in backlog:
            if item.depends_on:
                try:
                    import json
                    dep_ids = json.loads(item.depends_on)
                    dep_story_ids.update(dep_ids)
                except (json.JSONDecodeError, TypeError):
                    pass

        story_statuses: dict[str, str] = {}
        if dep_story_ids:
            story_statuses = self._batch_get_story_statuses(list(dep_story_ids))

        for item in backlog:
            # Phase 5A: skip human-owned stories
            effective_owner = self._resolve_owner(item)
            if effective_owner == "human":
                continue

            # Phase 5C: skip already-decomposed goal stories
            # If a plan/goal story already has children, it was decomposed
            # externally (or in a previous run). Auto-resolve it instead of
            # re-dispatching to the PLAN agent.
            if item.assignee == "plan" and self._is_decomposed_goal(item):
                try:
                    await self._store.advance(item.id, PipelineStatus.READY)
                    await self._store.advance(item.id, PipelineStatus.DISPATCHED, job_id="pre-decomposed")
                    await self._store.advance(item.id, PipelineStatus.REVIEW)
                    await self._store.advance(item.id, PipelineStatus.MERGED)
                    logger.info("Goal %s already decomposed — auto-merged", item.story_id)
                except Exception:
                    logger.exception("Failed to auto-merge decomposed goal %s", item.story_id)
                continue

            # Phase 5B: check dependencies
            if item.depends_on:
                satisfied, blocking = check_dependencies(item.depends_on, story_statuses)
                if not satisfied:
                    # Update blocked_by field
                    import json
                    blocked_json = json.dumps(blocking)
                    if item.blocked_by != blocked_json:
                        await self._store.update_fields(item.id, blocked_by=blocked_json)
                    continue

                # Dependencies just became satisfied — clear blocked_by and emit event
                if item.blocked_by:
                    await self._store.update_fields(item.id, blocked_by="")
                    from core.pool.events import PoolEvent, PoolEventType
                    await self._pool_events.emit(PoolEvent(
                        type=PoolEventType.DAEMON_DEPENDENCY_SATISFIED,
                        job_id="",
                        data={
                            "story_id": item.story_id,
                            "project_id": item.project_id,
                        },
                    ))

            try:
                await self._store.advance(item.id, PipelineStatus.READY)
                logger.info("Promoted %s (%s) to READY", item.id, item.story_id)
            except Exception:
                logger.exception("Failed to promote %s", item.id)

    async def _dispatch_cycle(self) -> None:
        """Dispatch READY items as pool jobs, respecting concurrency limits."""
        # Count currently dispatched
        dispatched = await self._store.list_items(status_filter=PipelineStatus.DISPATCHED)
        available_slots = self._max_concurrent - len(dispatched)

        if available_slots <= 0:
            return

        for _ in range(available_slots):
            item = await self._store.next_ready()
            if item is None:
                break

            try:
                job_id = await self._submit_to_pool(item)
                await self._store.advance(
                    item.id,
                    PipelineStatus.DISPATCHED,
                    job_id=job_id,
                    attempts=item.attempts + 1,
                )
                # Write job_id back to vault story
                await self._update_vault_story(item.story_id, job_id)
                self._health.record_dispatch()
                logger.info("Dispatched %s → job %s", item.story_id, job_id)
            except Exception:
                logger.exception("Failed to dispatch %s", item.story_id)
                self._health.record_error(f"Dispatch failed: {item.story_id}")

    def _is_decomposed_goal(self, item: Any) -> bool:
        """Check if a plan/goal story already has children in the vault.

        Returns True if the story has the 'goal' tag and at least one other
        story in the same project references it (via goal:{id} tag or
        depends_on chain pointing to sibling stories).
        """
        story_data = self._get_story_details(item.story_id)
        if not story_data:
            return False

        tags = story_data.get("tags", [])
        if "goal" not in tags:
            return False

        # Check if any sibling stories reference this goal
        if not self._ensure_task_engine():
            return False

        try:
            all_stories = self._task_engine.list_items(project_id=item.project_id)
            for s in all_stories:
                s_tags = s.get("tags", []) or []
                if f"goal:{item.story_id}" in s_tags:
                    return True
                # Also check if story has created_by: agent:planning and
                # description mentions this goal
                s_desc = s.get("description", "") or ""
                if item.story_id in s_desc and s.get("id") != item.story_id:
                    return True
        except Exception:
            logger.debug("Could not check goal children for %s", item.story_id)

        return False

    def _resolve_owner(self, item: Any) -> str:
        """Resolve the effective owner of a pipeline item.

        Empty owner resolves to default: 'grim' if assignee is set, 'human' otherwise.
        """
        owner = getattr(item, "owner", "") or ""
        if owner:
            return owner
        # Default: grim if assignee set, human otherwise
        if getattr(item, "assignee", ""):
            return self._default_owner
        return "human"

    def _batch_get_story_statuses(self, story_ids: list[str]) -> dict[str, str]:
        """Get vault statuses for a batch of story IDs.

        Returns {story_id: status} for all found stories.
        """
        if not story_ids:
            return {}

        if not self._ensure_task_engine():
            return {}

        try:
            batch = self._task_engine.get_items_batch(story_ids)
            return {sid: data.get("status", "") for sid, data in batch.items()}
        except Exception:
            logger.warning("Failed to batch-get story statuses for dependency check")
            return {}

    async def _nudge_cycle(self) -> None:
        """Check for human-owned stories idle too long and emit nudge events.

        Runs at most once per hour to avoid spam.
        """
        import time
        now_mono = time.monotonic()
        if now_mono - self._last_nudge_check < 3600:  # once per hour
            return
        self._last_nudge_check = now_mono

        if self._nudge_after_days <= 0:
            return

        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._nudge_after_days)

        backlog = await self._store.list_items(status_filter=PipelineStatus.BACKLOG)
        for item in backlog:
            effective_owner = self._resolve_owner(item)
            if effective_owner != "human":
                continue
            if item.updated_at < cutoff:
                from core.pool.events import PoolEvent, PoolEventType
                await self._pool_events.emit(PoolEvent(
                    type=PoolEventType.DAEMON_NUDGE,
                    job_id="",
                    data={
                        "story_id": item.story_id,
                        "project_id": item.project_id,
                        "idle_days": (datetime.now(timezone.utc) - item.updated_at).days,
                        "title": item.story_id,  # pipeline doesn't store title
                    },
                ))
                logger.info("Nudging human owner for idle story %s (%d days)",
                           item.story_id, (datetime.now(timezone.utc) - item.updated_at).days)

    async def _notification_cycle(self) -> None:
        """Run proactive notifications — stuck detection + daily summary.

        Rate-limited to once per hour. Daily summary emits at the configured hour.
        """
        if self._notifier is None:
            return

        import time
        now_mono = time.monotonic()
        if now_mono - self._last_notification_check < 3600:  # once per hour
            return
        self._last_notification_check = now_mono

        # Stuck detection
        stuck_items = await self._notifier.detect_stuck(self._store)
        from core.pool.events import PoolEvent, PoolEventType
        for stuck in stuck_items:
            await self._pool_events.emit(PoolEvent(
                type=PoolEventType.DAEMON_STUCK_WARNING,
                job_id=stuck.job_id or "",
                data={
                    "story_id": stuck.story_id,
                    "project_id": stuck.project_id,
                    "hours_dispatched": round(stuck.hours_dispatched, 1),
                },
            ))
            logger.warning("Stuck warning: %s dispatched for %.1f hours",
                          stuck.story_id, stuck.hours_dispatched)

        # Daily summary — emit once per calendar day at the configured hour
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        if today != self._last_daily_summary_date and now.hour >= self._daily_summary_hour:
            self._last_daily_summary_date = today
            summary = await self._notifier.daily_summary(self._store)
            await self._pool_events.emit(PoolEvent(
                type=PoolEventType.DAEMON_DAILY_SUMMARY,
                job_id="",
                data={
                    "counts": summary.counts_by_status,
                    "completed_today": summary.completed_today,
                    "stuck_count": len(summary.stuck_items),
                    "stuck_items": summary.stuck_items[:5],
                    "human_idle_count": len(summary.human_idle),
                    "human_idle": summary.human_idle[:5],
                    "total_items": summary.total_items,
                    "formatted": self._notifier.format_daily_summary(summary),
                },
            ))
            logger.info("Daily summary emitted: %d total, %d completed today, %d stuck",
                       summary.total_items, summary.completed_today, len(summary.stuck_items))

            # Prune old data (once per day alongside daily summary)
            await self._prune_old_data()

    async def _prune_old_data(self) -> None:
        """Delete stale pipeline items and pool jobs older than 30 days."""
        try:
            pruned_pipeline = await self._store.prune_merged(days=30)
            if pruned_pipeline:
                logger.info("Pruned %d old merged pipeline items", pruned_pipeline)
        except Exception:
            logger.warning("Pipeline prune failed", exc_info=True)

        try:
            pruned_jobs = await self._pool_queue.prune_completed(days=30)
            if pruned_jobs:
                logger.info("Pruned %d old completed pool jobs", pruned_jobs)
        except Exception:
            logger.warning("Pool job prune failed", exc_info=True)

    async def daily_summary(self) -> dict:
        """Generate a daily summary on demand (for REST/Discord).

        Returns dict with summary data + formatted text.
        """
        if self._notifier is None:
            return {"error": "Notifier not available"}

        summary = await self._notifier.daily_summary(self._store)
        return {
            "counts": summary.counts_by_status,
            "completed_today": summary.completed_today,
            "stuck_count": len(summary.stuck_items),
            "stuck_items": summary.stuck_items,
            "human_idle_count": len(summary.human_idle),
            "human_idle": summary.human_idle,
            "total_items": summary.total_items,
            "formatted": self._notifier.format_daily_summary(summary),
        }

    async def _submit_to_pool(self, item: Any) -> str:
        """Build a Job from the pipeline item and submit to the pool queue."""
        from core.pool.models import Job, JobType, JobPriority

        # Map assignee to job type
        type_map = {
            "code": JobType.CODE,
            "research": JobType.RESEARCH,
            "audit": JobType.AUDIT,
            "plan": JobType.PLAN,
        }
        job_type = type_map.get(item.assignee, JobType.CODE)

        # Map priority int back to enum
        priority_map = {0: JobPriority.CRITICAL, 1: JobPriority.HIGH, 2: JobPriority.NORMAL, 3: JobPriority.LOW}
        priority = priority_map.get(item.priority, JobPriority.NORMAL)

        # Build instructions from story metadata
        instructions = self._build_instructions(item)

        job = Job(
            job_type=job_type,
            priority=priority,
            instructions=instructions,
            target_repo=self._infer_target_repo(item),
        )

        await self._pool_queue.submit(job)
        return job.id

    # Known project → repo mappings
    _PROJECT_REPO_MAP: dict[str, str] = {
        "proj-grim": "GRIM", "proj-charizard": "GRIM", "proj-mewtwo": "GRIM",
        "proj-dft": "dawn-field-theory", "proj-fracton": "fracton",
        "proj-reality-engine": "reality-engine",
    }

    def _infer_target_repo(self, item: Any) -> str | None:
        """Infer target repo from project ID."""
        return self._PROJECT_REPO_MAP.get(getattr(item, "project_id", None))

    def _build_instructions(self, item: Any) -> str:
        """Build agent instructions from pipeline item + vault story data."""
        story_data = self._get_story_details(item.story_id)

        # Try rich context builder first
        if self._context_builder and story_data:
            try:
                return self._context_builder.build(story_data, item.project_id)
            except Exception:
                logger.warning("ContextBuilder failed for %s, using fallback", item.story_id)

        # Fallback: minimal instructions
        parts = [f"Story: {item.story_id}"]
        if story_data:
            if story_data.get("title"):
                parts.append(f"Title: {story_data['title']}")
            if story_data.get("description"):
                parts.append(f"\n{story_data['description']}")
            ac = story_data.get("acceptance_criteria", [])
            if ac:
                parts.append("\nAcceptance Criteria:")
                for criterion in ac:
                    parts.append(f"- {criterion}")
        else:
            parts.append(f"Project: {item.project_id}")

        return "\n".join(parts)

    @staticmethod
    def _make_context_builder(vault_path: Path, workspace_root: Path, pipeline_store=None):
        """Create a ContextBuilder, or None if imports fail."""
        try:
            from core.daemon.context import ContextBuilder
            return ContextBuilder(vault_path, workspace_root, pipeline_store=pipeline_store)
        except ImportError:
            return None

    def _get_story_details(self, story_id: str) -> dict | None:
        """Fetch story details from vault via TaskEngine."""
        if not self._ensure_task_engine():
            return None

        try:
            batch = self._task_engine.get_items_batch([story_id])
            return batch.get(story_id)
        except Exception:
            logger.warning("Failed to get story details for %s", story_id)
            return None

    async def _update_vault_story(self, story_id: str, job_id: str) -> None:
        """Write job_id back to the vault story."""
        if not self._ensure_task_engine():
            return

        try:
            self._task_engine.update_item(story_id, {"job_id": job_id})
        except Exception:
            logger.warning("Failed to update vault story %s with job_id %s", story_id, job_id)

    # ── Intelligence Factory ──────────────────────────────────────

    @staticmethod
    def _make_intelligence(config: Any) -> dict | None:
        """Create intelligence components, or None if imports fail."""
        try:
            from core.daemon.intelligence import (
                ClarificationResolver,
                OutputValidator,
                RetryEnricher,
            )
            resolve_model = config.daemon_resolve_model
            validate_model = config.daemon_validate_model
            confidence_threshold = config.daemon_resolve_confidence_threshold

            return {
                "resolver": ClarificationResolver(
                    model=resolve_model,
                    confidence_threshold=confidence_threshold,
                ),
                "validator": OutputValidator(model=validate_model),
                "enricher": RetryEnricher(),
            }
        except ImportError:
            return None

    # ── GitHub Client Factory ─────────────────────────────────────

    @staticmethod
    def _make_github_client(config: Any):
        """Create a GitHubClient, or None if disabled or unavailable."""
        if not getattr(config, "daemon_auto_pr", True):
            return None
        try:
            from core.daemon.github import GitHubClient
            repo = getattr(config, "daemon_github_repo", "")
            return GitHubClient(default_repo=repo)
        except ImportError:
            return None

    # ── PR Lifecycle (Phase 4) ─────────────────────────────────

    async def _handle_review(self, item: Any, workspace_id: str | None) -> None:
        """Handle a REVIEW item: create PR for CODE jobs, auto-merge non-CODE."""
        from core.pool.events import PoolEvent, PoolEventType

        # Refetch item to get latest state (job_id, workspace_id, etc.)
        fresh = await self._store.get(item.id)
        if fresh:
            item = fresh

        # Determine job type
        job_type_str = "code"
        if item.job_id:
            try:
                job = await self._pool_queue.get(item.job_id)
                if job:
                    job_type_str = getattr(job, "job_type", "code")
                    if hasattr(job_type_str, "value"):
                        job_type_str = job_type_str.value
            except Exception:
                pass

        # Non-CODE jobs skip PR — advance directly to MERGED
        if job_type_str != "code" or not workspace_id:
            await self._store.advance(item.id, PipelineStatus.MERGED)
            self._update_vault_story_status(item.story_id, "resolved")
            logger.info("Job %s (type=%s) → MERGED directly (no PR)", item.job_id, job_type_str)
            return

        # CODE job with workspace → PR path
        if not self._github or not self._auto_pr:
            return  # stay in REVIEW for manual handling

        if not self._workspace_mgr:
            return

        ws = self._workspace_mgr.get(workspace_id)
        if not ws:
            return

        try:
            # Push branch
            await self._github.push_branch(ws.worktree_path, ws.branch_name)

            # Build PR body
            story_data = self._get_story_details(item.story_id)
            title = (story_data or {}).get("title", item.story_id)
            body = self._build_pr_body(item, story_data)

            # Create PR
            pr_number, pr_url = await self._github.create_pr(
                ws.worktree_path, ws.branch_name, title, body,
            )

            # Persist PR info
            await self._store.update_fields(
                item.id, pr_number=pr_number, pr_url=pr_url,
            )

            # Emit JOB_REVIEW event with PR link
            diff_stat = ""
            try:
                diff_stat = await self._workspace_mgr.get_branch_diff(workspace_id) or ""
            except Exception:
                pass

            await self._pool_events.emit(PoolEvent(
                type=PoolEventType.JOB_REVIEW,
                job_id=item.job_id or "",
                data={
                    "workspace_id": workspace_id,
                    "pr_number": pr_number,
                    "pr_url": pr_url,
                    "story_id": item.story_id,
                    "diff_stat": diff_stat,
                },
            ))

            logger.info("PR #%d created for %s: %s", pr_number, item.story_id, pr_url)

        except Exception:
            logger.warning("PR creation failed for %s — item stays in REVIEW", item.story_id)

    def _build_pr_body(self, item: Any, story_data: dict | None) -> str:
        """Build a PR description from story data."""
        parts = [f"Story: `{item.story_id}`", f"Project: `{item.project_id}`"]

        if story_data:
            if story_data.get("description"):
                parts.append(f"\n## Description\n{story_data['description']}")
            ac = story_data.get("acceptance_criteria", [])
            if ac:
                parts.append("\n## Acceptance Criteria")
                for criterion in ac:
                    parts.append(f"- [ ] {criterion}")

        parts.append("\n---\nGenerated by GRIM Management Daemon (Project Mewtwo)")
        return "\n".join(parts)

    def _update_vault_story_status(self, story_id: str, status: str) -> None:
        """Update story status in vault and sync board (best-effort).

        When a story moves to resolved/closed, also checks if any downstream
        stories (that depend on this one) can be auto-activated.
        """
        if not self._ensure_task_engine():
            return
        try:
            self._task_engine.update_item(story_id, {"status": status})
        except Exception:
            logger.warning("Failed to update vault story %s status to %s", story_id, status)

        # Sync board.yaml to reflect the status change
        self._sync_board_status(story_id, status)

        # Auto-activate downstream stories whose deps are now satisfied
        if status in ("resolved", "closed"):
            self._auto_activate_dependents(story_id)

    def _sync_board_status(self, story_id: str, status: str) -> None:
        """Move story to the matching board column (best-effort).

        Maps vault statuses to board columns. If the story isn't on the
        board yet, adds it to the appropriate column.
        """
        status_to_column = {
            "new": "new",
            "active": "active",
            "in_progress": "in_progress",
            "resolved": "resolved",
            "closed": "closed",
        }
        column = status_to_column.get(status)
        if not column:
            return

        if not self._ensure_task_engine():
            return

        try:
            from kronos_mcp.board import BoardEngine
            board = BoardEngine(str(self._vault_path), self._task_engine)
            board.move_story(story_id, column)
            logger.debug("Board sync: %s → %s", story_id, column)
        except Exception:
            logger.debug("Board sync failed for %s → %s", story_id, column)

    def _auto_activate_dependents(self, resolved_story_id: str) -> None:
        """Check all stories that depend on resolved_story_id.

        If all their dependencies are now resolved/closed, activate them
        (change status from 'new' to 'active') so the scanner picks them up.
        """
        if not self._ensure_task_engine():
            return

        try:
            all_stories = self._task_engine.list_items()
        except Exception:
            logger.debug("Could not list stories for auto-activate")
            return

        # Build status map for dependency checking
        status_map = {s.get("id", ""): s.get("status", "") for s in all_stories}

        satisfied_statuses = {"resolved", "closed"}

        for story in all_stories:
            story_id = story.get("id", "")
            story_status = story.get("status", "")
            deps = story.get("depends_on") or []

            # Only activate stories that are currently 'new'
            if story_status != "new":
                continue

            # Must depend on the just-resolved story
            if resolved_story_id not in deps:
                continue

            # Check if ALL dependencies are satisfied
            all_satisfied = all(
                status_map.get(dep_id, "") in satisfied_statuses
                for dep_id in deps
            )

            if all_satisfied:
                try:
                    self._task_engine.update_item(story_id, {"status": "active"})
                    self._sync_board_status(story_id, "active")
                    logger.info("Auto-activated %s (all deps satisfied)", story_id)
                except Exception:
                    logger.warning("Failed to auto-activate %s", story_id)

    def _persist_result_to_vault(self, story_id: str, job_id: str, result_text: str) -> None:
        """Save job result as a vault note for durable persistence.

        Results stored in pipeline DB are ephemeral (lost on DB wipe/restart).
        Vault notes survive indefinitely and can be referenced by downstream
        stories via the research context builder.
        """
        if not result_text:
            return

        try:
            from kronos_mcp.server import handle_note_append

            # Truncate to 3000 chars for vault note
            body = result_text[:3000]
            if len(result_text) > 3000:
                body += "\n\n*(truncated — full result in pool job)*"

            handle_note_append({
                "title": f"Job result: {story_id} ({job_id})",
                "body": body,
                "tags": ["daemon", "job-result", story_id],
                "related": [story_id],
            })
            logger.info("Persisted result to vault note: %s", story_id)
        except Exception:
            logger.debug("Could not persist result to vault note for %s", story_id)

    async def approve_item(self, item_id: str) -> Any:
        """Approve a REVIEW item: merge PR, merge workspace, advance to MERGED."""
        from core.daemon.models import InvalidTransition

        item = await self._store.get(item_id)
        if item is None:
            raise ValueError(f"Pipeline item not found: {item_id}")
        if item.status != PipelineStatus.REVIEW:
            raise InvalidTransition(item.status, PipelineStatus.MERGED)

        # Merge PR if exists
        if item.pr_number and self._github and self._workspace_mgr and item.workspace_id:
            ws = self._workspace_mgr.get(item.workspace_id)
            if ws:
                try:
                    await self._github.merge_pr(ws.repo_path, item.pr_number)
                except Exception:
                    logger.warning("PR merge failed for #%d, continuing with local merge", item.pr_number)

        # Merge workspace to base (local squash), then clean up worktree
        if item.workspace_id and self._workspace_mgr:
            try:
                await self._workspace_mgr.merge_to_base(item.workspace_id)
            except Exception:
                logger.warning("Workspace merge failed for %s", item.workspace_id)
            try:
                await self._workspace_mgr.destroy(item.workspace_id)
            except Exception:
                logger.warning("Workspace cleanup failed for %s", item.workspace_id)

        # Advance to MERGED
        updated = await self._store.advance(item.id, PipelineStatus.MERGED)

        # Update vault story → resolved
        self._update_vault_story_status(item.story_id, "resolved")

        # Emit DAEMON_APPROVED event
        from core.pool.events import PoolEvent, PoolEventType
        await self._pool_events.emit(PoolEvent(
            type=PoolEventType.DAEMON_APPROVED,
            job_id=item.job_id or "",
            data={
                "story_id": item.story_id,
                "pr_number": item.pr_number,
                "pr_url": item.pr_url,
            },
        ))

        logger.info("Approved %s → MERGED (%s)", item.id, item.story_id)
        return updated

    async def reject_item(self, item_id: str) -> Any:
        """Reject a REVIEW item: close PR, destroy workspace, advance to FAILED."""
        from core.daemon.models import InvalidTransition

        item = await self._store.get(item_id)
        if item is None:
            raise ValueError(f"Pipeline item not found: {item_id}")
        if item.status != PipelineStatus.REVIEW:
            raise InvalidTransition(item.status, PipelineStatus.FAILED)

        # Close PR if exists
        if item.pr_number and self._github and self._workspace_mgr and item.workspace_id:
            ws = self._workspace_mgr.get(item.workspace_id)
            if ws:
                try:
                    await self._github.close_pr(ws.repo_path, item.pr_number)
                except Exception:
                    logger.warning("PR close failed for #%d", item.pr_number)

        # Destroy workspace
        if item.workspace_id and self._workspace_mgr:
            try:
                await self._workspace_mgr.destroy(item.workspace_id)
            except Exception:
                logger.warning("Workspace destroy failed for %s", item.workspace_id)

        # Advance to FAILED
        updated = await self._store.advance(
            item.id, PipelineStatus.FAILED, error="Rejected by reviewer",
        )

        # Emit DAEMON_REJECTED event
        from core.pool.events import PoolEvent, PoolEventType
        await self._pool_events.emit(PoolEvent(
            type=PoolEventType.DAEMON_REJECTED,
            job_id=item.job_id or "",
            data={
                "story_id": item.story_id,
                "pr_number": item.pr_number,
                "reason": "Rejected by reviewer",
            },
        ))

        logger.info("Rejected %s → FAILED (%s)", item.id, item.story_id)
        return updated

    # ── PR Comment Polling ─────────────────────────────────────

    async def _pr_poll_loop(self) -> None:
        """Periodically check REVIEW items for new PR comments."""
        # Initial delay
        try:
            await asyncio.sleep(self._pr_poll_interval)
        except asyncio.CancelledError:
            return

        while self._running:
            try:
                await self._poll_pr_comments()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("PR comment poll error")

            try:
                await asyncio.sleep(self._pr_poll_interval)
            except asyncio.CancelledError:
                break

    async def _poll_pr_comments(self) -> None:
        """Check all REVIEW items with PRs for new comments."""
        if not self._github or not self._workspace_mgr:
            return

        items = await self._store.list_items(status_filter=PipelineStatus.REVIEW)
        for item in items:
            if not item.pr_number:
                continue

            ws = self._workspace_mgr.get(item.workspace_id) if item.workspace_id else None
            if not ws:
                continue

            try:
                comments = await self._github.list_pr_comments(ws.repo_path, item.pr_number)
                new_count = len(comments)

                if new_count > item.pr_comment_count:
                    from core.pool.events import PoolEvent, PoolEventType

                    new_comments = comments[item.pr_comment_count:]
                    for comment in new_comments:
                        await self._pool_events.emit(PoolEvent(
                            type=PoolEventType.DAEMON_ESCALATION,
                            job_id=item.job_id or "",
                            data={
                                "story_id": item.story_id,
                                "pr_number": item.pr_number,
                                "pr_url": item.pr_url,
                                "comment_author": comment["author"],
                                "comment_body": comment["body"],
                                "reason": "New PR comment",
                            },
                        ))

                    await self._store.update_fields(
                        item.id, pr_comment_count=new_count,
                    )
                    logger.info(
                        "PR #%d has %d new comments for %s",
                        item.pr_number, len(new_comments), item.story_id,
                    )

            except Exception:
                logger.warning("Failed to poll PR comments for %s", item.story_id)

    # ── Goal Decomposition (Phase 5C) ───────────────────────────

    async def _handle_plan_complete(self, item: Any, job_id: str, event_data: dict) -> None:
        """Handle a completed PLAN job — parse output, create draft stories."""
        from core.pool.events import PoolEvent, PoolEventType

        # Get job result text
        result_text = ""
        try:
            job = await self._pool_queue.get(job_id)
            if job:
                result_text = getattr(job, "result", "") or ""
        except Exception:
            logger.warning("Could not fetch PLAN job result for %s", job_id)

        if not result_text:
            await self._store.advance(item.id, PipelineStatus.FAILED, error="PLAN job returned no output")
            return

        # Parse the plan
        from core.daemon.planner import PlanParser, PlanExecutor
        parser = PlanParser()
        parsed = parser.parse(result_text)

        if not parsed.valid:
            error_msg = "; ".join(parsed.errors[:3])
            await self._store.advance(item.id, PipelineStatus.FAILED, error=f"Plan parse failed: {error_msg}")
            logger.warning("PLAN parse failed for %s: %s", item.story_id, error_msg)
            return

        # Ensure task engine is available
        if not self._ensure_task_engine():
            await self._store.advance(item.id, PipelineStatus.FAILED, error="TaskEngine unavailable")
            return

        # Execute plan — create draft stories
        executor = PlanExecutor(self._task_engine)
        executed = executor.execute(parsed, item.project_id, item.story_id)

        if not executed.created_ids:
            await self._store.advance(item.id, PipelineStatus.FAILED, error="Plan execution created no stories")
            return

        # Store plan metadata on the goal story
        self._task_engine.update_item(item.story_id, {
            "tags": list(set((self._get_story_details(item.story_id) or {}).get("tags", []) + ["goal"])),
        })

        # Auto-approve gate
        if (self._auto_approve_threshold > 0
                and len(executed.created_ids) <= self._auto_approve_threshold):
            # Auto-approve: activate all draft stories
            activated = executor.activate_plan(executed)
            await self._store.advance(item.id, PipelineStatus.MERGED)
            self._update_vault_story_status(item.story_id, "active")
            logger.info("PLAN %s auto-approved: %d stories activated", item.story_id, activated)
        else:
            # Require human approval — advance to REVIEW (manual)
            await self._store.advance(item.id, PipelineStatus.REVIEW)
            await self._pool_events.emit(PoolEvent(
                type=PoolEventType.DAEMON_PLAN_PROPOSED,
                job_id=job_id,
                data={
                    "story_id": item.story_id,
                    "project_id": item.project_id,
                    "story_count": len(executed.created_ids),
                    "created_ids": executed.created_ids,
                },
            ))
            logger.info("PLAN %s proposed: %d stories awaiting approval",
                        item.story_id, len(executed.created_ids))

    async def approve_goal(self, goal_story_id: str) -> dict:
        """Approve a proposed plan — activate all draft children."""
        if not self._ensure_task_engine():
            return {"error": "TaskEngine unavailable"}

        # Find the pipeline item for this goal
        item = await self._store.get_by_story(goal_story_id)
        if item is None:
            return {"error": f"Goal {goal_story_id} not found in pipeline"}

        if item.status != PipelineStatus.REVIEW:
            return {"error": f"Goal is not pending approval (status: {item.status.value})"}

        # Find draft children
        all_items = self._task_engine.list_items()
        children = [
            s for s in all_items
            if f"goal:{goal_story_id}" in (s.get("tags") or [])
            and s.get("status") == "draft"
        ]

        if not children:
            return {"error": "No draft children found for this goal"}

        from core.daemon.planner import ExecutedPlan, PlanExecutor
        executor = PlanExecutor(self._task_engine)
        child_ids = [c["id"] for c in children]
        executed = ExecutedPlan(
            goal_story_id=goal_story_id,
            created_ids=child_ids,
            dependency_map={},
        )
        activated = executor.activate_plan(executed)

        # Advance goal to MERGED
        await self._store.advance(item.id, PipelineStatus.MERGED)
        self._update_vault_story_status(goal_story_id, "active")

        logger.info("Goal %s approved: %d stories activated", goal_story_id, activated)
        return {"approved": goal_story_id, "activated": activated, "story_ids": child_ids}

    async def reject_goal(self, goal_story_id: str) -> dict:
        """Reject a proposed plan — close draft children, fail the goal."""
        if not self._ensure_task_engine():
            return {"error": "TaskEngine unavailable"}

        item = await self._store.get_by_story(goal_story_id)
        if item is None:
            return {"error": f"Goal {goal_story_id} not found in pipeline"}

        if item.status != PipelineStatus.REVIEW:
            return {"error": f"Goal is not pending approval (status: {item.status.value})"}

        # Find and close draft children
        all_items = self._task_engine.list_items()
        children = [
            s for s in all_items
            if f"goal:{goal_story_id}" in (s.get("tags") or [])
            and s.get("status") == "draft"
        ]

        from core.daemon.planner import PlanExecutor
        executor = PlanExecutor(self._task_engine)
        closed = executor.reject_plan([c["id"] for c in children])

        # Fail the goal
        await self._store.advance(item.id, PipelineStatus.FAILED, error="Plan rejected by reviewer")

        logger.info("Goal %s rejected: %d draft stories closed", goal_story_id, closed)
        return {"rejected": goal_story_id, "closed": closed}

    async def _goal_tracking_cycle(self) -> None:
        """Check active goals for completion — auto-resolve when all children done."""
        if not self._ensure_task_engine():
            return

        from core.daemon.planner import GoalTracker

        # Find MERGED goal stories (these have activated children running)
        merged = await self._store.list_items(status_filter=PipelineStatus.MERGED)
        tracker = GoalTracker(self._task_engine)

        for item in merged:
            # Check if this is a goal (has goal tag in vault)
            story_data = self._get_story_details(item.story_id)
            if not story_data:
                continue
            tags = story_data.get("tags", [])
            if "goal" not in tags:
                continue

            complete, stats = tracker.check_goal_complete(item.story_id)
            if complete and stats["total"] > 0:
                tracker.auto_resolve_goal(item.story_id)
                from core.pool.events import PoolEvent, PoolEventType
                await self._pool_events.emit(PoolEvent(
                    type=PoolEventType.DAEMON_GOAL_COMPLETE,
                    job_id="",
                    data={
                        "story_id": item.story_id,
                        "project_id": item.project_id,
                        "children_count": stats["total"],
                    },
                ))
                logger.info("Goal %s auto-resolved: %d children complete",
                           item.story_id, stats["total"])

    # ── Pool Event Handler ────────────────────────────────────────

    async def _handle_pool_event(self, event: PoolEvent) -> None:
        """React to pool job lifecycle events."""
        from core.pool.events import PoolEventType

        job_id = event.job_id
        if not job_id:
            return

        # Look up pipeline item by job_id
        item = await self._store.get_by_job(job_id)
        if item is None:
            return  # Not a daemon-managed job

        try:
            if event.type == PoolEventType.JOB_COMPLETE:
                await self._handle_complete(item, job_id, event.data)

            elif event.type == PoolEventType.JOB_FAILED:
                error = event.data.get("error", "Unknown error")
                await self._handle_failed(item, job_id, error)

            elif event.type == PoolEventType.JOB_BLOCKED:
                question = event.data.get("question", "")
                await self._store.advance(item.id, PipelineStatus.BLOCKED)
                logger.info("Job %s blocked → BLOCKED (%s)", job_id, item.story_id)
                # Try auto-resolution
                await self._handle_blocked(item, job_id, question)

            elif event.type == PoolEventType.JOB_REVIEW:
                # Fallback: if workspace_id wasn't in JOB_COMPLETE, pick it up here
                workspace_id = event.data.get("workspace_id")
                fresh = await self._store.get(item.id)
                if fresh and fresh.status == PipelineStatus.REVIEW and not fresh.workspace_id and workspace_id:
                    await self._store.update_fields(item.id, workspace_id=workspace_id)
                    logger.info("Job %s workspace_id updated via JOB_REVIEW: %s", job_id, workspace_id)

        except Exception:
            logger.exception("Failed to handle pool event for job %s", job_id)
            self._health.record_error(f"Event handler failed: {event.type} for {job_id}")

    # ── Intelligent Event Handlers ─────────────────────────────

    async def _handle_blocked(self, item: Any, job_id: str, question: str) -> None:
        """Try to auto-resolve a blocked question from ADR context."""
        from core.pool.events import PoolEvent, PoolEventType

        if not self._intelligence or not self._auto_resolve or not question:
            # Emit escalation for unresolvable
            if question:
                await self._pool_events.emit(PoolEvent(
                    type=PoolEventType.DAEMON_ESCALATION,
                    job_id=job_id,
                    data={
                        "question": question,
                        "story_id": item.story_id,
                        "reason": "Auto-resolve disabled or unavailable",
                    },
                ))
            return

        resolver = self._intelligence["resolver"]

        # Get ADR context
        boundaries = ""
        adr_context = ""
        if self._context_builder:
            try:
                adrs = self._context_builder._resolve_adrs(item.project_id)
                boundaries = self._context_builder._resolve_decision_boundaries(adrs)
                adr_context = self._context_builder._resolve_adr_context(adrs)
            except Exception:
                logger.warning("Failed to resolve ADR context for %s", item.story_id)

        resolution = await resolver.resolve(question, boundaries, adr_context)

        if resolution.answered and resolution.confidence >= resolver._confidence_threshold:
            # Auto-resolve: provide clarification to the pool
            await self._pool_queue.provide_clarification(job_id, resolution.answer)
            await self._store.advance(item.id, PipelineStatus.READY)
            await self._pool_events.emit(PoolEvent(
                type=PoolEventType.DAEMON_AUTO_RESOLVED,
                job_id=job_id,
                data={
                    "question": question,
                    "answer": resolution.answer,
                    "source": resolution.source,
                    "confidence": resolution.confidence,
                    "story_id": item.story_id,
                },
            ))
            logger.info("Auto-resolved blocked job %s (%s) via %s",
                        job_id, item.story_id, resolution.source)
        else:
            # Escalate to human
            await self._pool_events.emit(PoolEvent(
                type=PoolEventType.DAEMON_ESCALATION,
                job_id=job_id,
                data={
                    "question": question,
                    "story_id": item.story_id,
                    "reason": f"Could not resolve (source={resolution.source}, "
                              f"confidence={resolution.confidence:.2f})",
                },
            ))
            logger.info("Escalating blocked job %s (%s) — confidence too low",
                        job_id, item.story_id)

    async def _handle_complete(self, item: Any, job_id: str, event_data: dict) -> None:
        """Validate completed work and advance or retry.

        PLAN jobs are routed through the planner pipeline instead of normal review.
        """
        # Phase 5C: PLAN jobs → goal decomposition
        if item.assignee == "plan":
            await self._handle_plan_complete(item, job_id, event_data)
            return

        workspace_id = event_data.get("workspace_id")

        # Phase 5D: Fetch result and store summary early (before any early returns)
        result_text = ""
        try:
            job = await self._pool_queue.get(job_id)
            if job:
                result_text = getattr(job, "result", "") or ""
        except Exception:
            logger.warning("Could not fetch job result for %s", job_id)

        if result_text:
            truncated_summary = result_text[:2000]
            try:
                await self._store.update_fields(item.id, result_summary=truncated_summary)
            except Exception:
                logger.warning("Could not store result_summary for %s", item.story_id)

            # Phase 6: Persist result to vault note (durable, survives DB wipes)
            self._persist_result_to_vault(item.story_id, job_id, result_text)

        # Phase 5D: Research-specific handling
        if item.assignee == "research":
            await self._handle_research_complete(item, job_id, result_text)

        if not self._intelligence or not self._validate_output:
            # No validation — advance directly to REVIEW
            await self._store.advance(
                item.id, PipelineStatus.REVIEW,
                workspace_id=workspace_id,
            )
            logger.info("Job %s complete → REVIEW (%s)", job_id, item.story_id)
            await self._handle_review(item, workspace_id)
            return

        # Get acceptance criteria
        story_data = self._get_story_details(item.story_id)
        ac = (story_data or {}).get("acceptance_criteria", [])

        if not ac:
            # No criteria to validate against
            await self._store.advance(
                item.id, PipelineStatus.REVIEW,
                workspace_id=workspace_id,
            )
            logger.info("Job %s complete → REVIEW (%s) [no AC]", job_id, item.story_id)
            await self._handle_review(item, workspace_id)
            return

        diff_stat = event_data.get("diff_stat", "")
        changed_files = event_data.get("changed_files", [])

        # Determine if this is an execution story (needs run evidence)
        is_exec = False
        if story_data:
            try:
                from core.daemon.context import _is_execution_story
                is_exec = _is_execution_story(story_data)
            except ImportError:
                pass

        validator = self._intelligence["validator"]
        verdict = await validator.validate(
            ac, result_text, diff_stat, changed_files,
            is_execution_story=is_exec,
        )

        if verdict.outcome == "pass":
            await self._store.advance(
                item.id, PipelineStatus.REVIEW,
                workspace_id=workspace_id,
            )
            logger.info("Job %s validated PASS → REVIEW (%s)", job_id, item.story_id)
            await self._handle_review(item, workspace_id)

        elif verdict.outcome == "fail":
            if item.daemon_retries < self._max_daemon_retries:
                await self._handle_retry(
                    item, job_id, error="Validation failed",
                    validation_feedback=verdict.reasoning,
                    missing_criteria=verdict.missing_criteria,
                )
            else:
                await self._store.advance(
                    item.id, PipelineStatus.FAILED,
                    error=f"Validation failed after {item.daemon_retries} retries: {verdict.reasoning}",
                )
                logger.warning("Job %s validation FAIL → FAILED (%s): %s",
                              job_id, item.story_id, verdict.reasoning)

        elif verdict.outcome == "partial":
            # Advance to REVIEW but flag via escalation
            await self._store.advance(
                item.id, PipelineStatus.REVIEW,
                workspace_id=workspace_id,
            )
            from core.pool.events import PoolEvent, PoolEventType
            await self._pool_events.emit(PoolEvent(
                type=PoolEventType.DAEMON_ESCALATION,
                job_id=job_id,
                data={
                    "story_id": item.story_id,
                    "reason": f"Partial validation: {verdict.reasoning}",
                    "missing_criteria": verdict.missing_criteria,
                },
            ))
            logger.info("Job %s validated PARTIAL → REVIEW (%s): %s",
                        job_id, item.story_id, verdict.reasoning)
            await self._handle_review(item, workspace_id)

    async def _handle_research_complete(self, item: Any, job_id: str, result_text: str) -> None:
        """Handle research job completion — validate and emit event.

        Research stories get heuristic validation (no LLM) and emit
        DAEMON_RESEARCH_COMPLETE so dependent code stories know they're unblocked.
        """
        # Validate research output if intelligence is available
        if self._intelligence and self._validate_output:
            validator = self._intelligence["validator"]
            verdict = validator.validate_research(result_text)
            if verdict.outcome == "fail":
                logger.warning("Research validation failed for %s: %s",
                              item.story_id, verdict.reasoning)
                # Don't block the pipeline — just log the warning

        # Emit research complete event
        from core.pool.events import PoolEvent, PoolEventType
        await self._pool_events.emit(PoolEvent(
            type=PoolEventType.DAEMON_RESEARCH_COMPLETE,
            job_id=job_id,
            data={
                "story_id": item.story_id,
                "project_id": item.project_id,
                "has_result": bool(result_text),
            },
        ))
        logger.info("Research complete: %s (job=%s)", item.story_id, job_id)

    async def _handle_failed(self, item: Any, job_id: str, error: str) -> None:
        """Handle failed job — retry with feedback or escalate."""
        if self._intelligence and item.daemon_retries < self._max_daemon_retries:
            await self._handle_retry(item, job_id, error=error)
        else:
            await self._store.advance(
                item.id, PipelineStatus.FAILED,
                error=error,
            )
            if self._intelligence:
                from core.pool.events import PoolEvent, PoolEventType
                await self._pool_events.emit(PoolEvent(
                    type=PoolEventType.DAEMON_ESCALATION,
                    job_id=job_id,
                    data={
                        "story_id": item.story_id,
                        "reason": f"Job failed after {item.daemon_retries} daemon retries: {error}",
                    },
                ))
            logger.warning("Job %s failed → FAILED (%s): %s", job_id, item.story_id, error)

    async def _handle_retry(
        self,
        item: Any,
        old_job_id: str,
        error: str = "",
        validation_feedback: str = "",
        missing_criteria: list[str] | None = None,
    ) -> None:
        """Retry a job with enriched instructions."""
        enricher = self._intelligence["enricher"]

        # Build enriched instructions
        original_instructions = self._build_instructions(item)
        enriched = enricher.enrich_instructions(
            original_instructions,
            error=error,
            validation_feedback=validation_feedback,
            missing_criteria=missing_criteria,
            attempt=item.daemon_retries + 1,
        )

        # Create new job
        from core.pool.models import Job, JobType, JobPriority

        type_map = {
            "code": JobType.CODE,
            "research": JobType.RESEARCH,
            "audit": JobType.AUDIT,
            "plan": JobType.PLAN,
        }
        priority_map = {0: JobPriority.CRITICAL, 1: JobPriority.HIGH, 2: JobPriority.NORMAL, 3: JobPriority.LOW}

        job = Job(
            job_type=type_map.get(item.assignee, JobType.CODE),
            priority=priority_map.get(item.priority, JobPriority.NORMAL),
            instructions=enriched,
        )

        await self._pool_queue.submit(job)

        # Advance pipeline: DISPATCHED/FAILED → FAILED → READY → DISPATCHED
        # We need to go through valid transitions
        if item.status == PipelineStatus.DISPATCHED:
            await self._store.advance(item.id, PipelineStatus.FAILED, error=error)
        # FAILED → READY
        await self._store.advance(item.id, PipelineStatus.READY)
        # READY → DISPATCHED with new job_id
        await self._store.advance(
            item.id, PipelineStatus.DISPATCHED,
            job_id=job.id,
            attempts=item.attempts + 1,
            daemon_retries=item.daemon_retries + 1,
        )

        # Write job_id back to vault
        await self._update_vault_story(item.story_id, job.id)
        self._health.record_dispatch()
        logger.info("Retrying %s → new job %s (daemon retry %d)",
                    item.story_id, job.id, item.daemon_retries + 1)
