"""ManagementEngine — the orchestration loop for the daemon.

Runs as an asyncio background task. Each cycle:
1. Scans vault for eligible stories via ProjectScanner
2. Advances BACKLOG items to READY
3. Dispatches READY items by submitting Jobs to ExecutionPool
4. Listens on PoolEventBus for job lifecycle events

Phase 3 adds intelligent event handling: auto-resolve blocked questions,
validate completed output against acceptance criteria, and enrich retry
instructions with feedback. LLM calls are surgical and optional.
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
    ) -> None:
        self._config = config
        self._pool_queue = pool_queue
        self._pool_events = pool_events

        # Pipeline store (SQLite)
        db_path = getattr(config, "daemon_db_path", Path("local/daemon.db"))
        self._store = PipelineStore(db_path)

        # Scanner
        _vault_path = vault_path or getattr(config, "vault_path", Path("../kronos-vault"))
        project_filter = getattr(config, "daemon_project_filter", []) or []
        self._scanner = ProjectScanner(_vault_path, project_filter or None)

        # Task engine for writing job_id back to vault
        self._task_engine = task_engine

        # Context builder for rich agent instructions
        workspace_root = getattr(config, "workspace_root", _vault_path.parent)
        self._context_builder = self._make_context_builder(_vault_path, workspace_root)

        # Config values
        self._poll_interval = getattr(config, "daemon_poll_interval", 30.0)
        self._max_concurrent = getattr(config, "daemon_max_concurrent_jobs", 1)
        self._auto_dispatch = getattr(config, "daemon_auto_dispatch", True)

        # Phase 3: Intelligence config
        self._auto_resolve = config.daemon_auto_resolve
        self._validate_output = config.daemon_validate_output
        self._max_daemon_retries = config.daemon_max_daemon_retries

        # Phase 3: Intelligence module (optional — degrades gracefully)
        self._intelligence = self._make_intelligence(config)

        # Runtime state
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None
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

    async def start(self) -> None:
        """Initialize store and start the main loop."""
        await self._store.initialize()
        self._pool_events.subscribe(self._event_callback)
        self._running = True
        self._loop_task = asyncio.create_task(self._main_loop())
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
        """Advance BACKLOG items to READY."""
        backlog = await self._store.list_items(status_filter=PipelineStatus.BACKLOG)
        for item in backlog:
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
        )

        await self._pool_queue.submit(job)
        return job.id

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
    def _make_context_builder(vault_path: Path, workspace_root: Path):
        """Create a ContextBuilder, or None if imports fail."""
        try:
            from core.daemon.context import ContextBuilder
            return ContextBuilder(vault_path, workspace_root)
        except ImportError:
            return None

    def _get_story_details(self, story_id: str) -> dict | None:
        """Fetch story details from vault via TaskEngine."""
        if self._task_engine is None:
            try:
                from kronos_mcp.tasks import TaskEngine
                self._task_engine = TaskEngine(str(self._scanner._vault_path))
            except ImportError:
                return None

        try:
            batch = self._task_engine.get_items_batch([story_id])
            return batch.get(story_id)
        except Exception:
            logger.warning("Failed to get story details for %s", story_id)
            return None

    async def _update_vault_story(self, story_id: str, job_id: str) -> None:
        """Write job_id back to the vault story."""
        if self._task_engine is None:
            try:
                from kronos_mcp.tasks import TaskEngine
                self._task_engine = TaskEngine(str(self._scanner._vault_path))
            except ImportError:
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
        """Validate completed work and advance or retry."""
        workspace_id = event_data.get("workspace_id")

        if not self._intelligence or not self._validate_output:
            # No validation — advance directly to REVIEW
            await self._store.advance(
                item.id, PipelineStatus.REVIEW,
                workspace_id=workspace_id,
            )
            logger.info("Job %s complete → REVIEW (%s)", job_id, item.story_id)
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
            return

        # Get job result from pool
        result_text = ""
        try:
            job = await self._pool_queue.get(job_id)
            if job:
                result_text = getattr(job, "result", "") or ""
        except Exception:
            logger.warning("Could not fetch job result for %s", job_id)

        diff_stat = event_data.get("diff_stat", "")
        changed_files = event_data.get("changed_files", [])

        validator = self._intelligence["validator"]
        verdict = await validator.validate(ac, result_text, diff_stat, changed_files)

        if verdict.outcome == "pass":
            await self._store.advance(
                item.id, PipelineStatus.REVIEW,
                workspace_id=workspace_id,
            )
            logger.info("Job %s validated PASS → REVIEW (%s)", job_id, item.story_id)

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
