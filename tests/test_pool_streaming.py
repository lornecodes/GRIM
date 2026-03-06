"""Tests for pool streaming infrastructure (Phase 9).

Tests the on_message callback in AgentSlot, streaming event emission
in ExecutionPool, and event type classification.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from core.pool.events import (
    PoolEvent,
    PoolEventBus,
    PoolEventType,
    STREAMING_EVENT_TYPES,
    is_streaming_event,
)
from core.pool.models import Job, JobType, JobPriority, JobStatus, JobResult


# ── Event type tests ──


class TestStreamingEventTypes:
    """Test the new streaming event types and classification."""

    def test_agent_output_exists(self):
        assert PoolEventType.AGENT_OUTPUT == "agent_output"

    def test_agent_tool_result_exists(self):
        assert PoolEventType.AGENT_TOOL_RESULT == "agent_tool_result"

    def test_streaming_types_frozenset(self):
        assert PoolEventType.AGENT_OUTPUT in STREAMING_EVENT_TYPES
        assert PoolEventType.AGENT_TOOL_RESULT in STREAMING_EVENT_TYPES
        assert len(STREAMING_EVENT_TYPES) == 2

    def test_is_streaming_event_true(self):
        event = PoolEvent(
            type=PoolEventType.AGENT_OUTPUT,
            job_id="test-1",
            data={"type": "text", "text": "hello"},
        )
        assert is_streaming_event(event) is True

    def test_is_streaming_event_false_lifecycle(self):
        event = PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id="test-1",
        )
        assert is_streaming_event(event) is False

    def test_lifecycle_types_not_streaming(self):
        for etype in [
            PoolEventType.JOB_SUBMITTED,
            PoolEventType.JOB_STARTED,
            PoolEventType.JOB_COMPLETE,
            PoolEventType.JOB_FAILED,
            PoolEventType.JOB_BLOCKED,
            PoolEventType.JOB_CANCELLED,
            PoolEventType.JOB_REVIEW,
        ]:
            event = PoolEvent(type=etype, job_id="test-1")
            assert is_streaming_event(event) is False

    def test_event_to_dict_includes_data(self):
        event = PoolEvent(
            type=PoolEventType.AGENT_OUTPUT,
            job_id="job-123",
            data={"type": "text", "text": "hello world"},
        )
        d = event.to_dict()
        assert d["event_type"] == "agent_output"
        assert d["job_id"] == "job-123"
        assert d["text"] == "hello world"
        # Data 'type' field preserved (e.g. "text", "tool_use")
        assert d["type"] == "text"


# ── Event bus tests ──


class TestEventBusStreaming:
    """Test that event bus correctly distributes streaming events."""

    @pytest.mark.asyncio
    async def test_emit_streaming_event(self):
        bus = PoolEventBus()
        received = []
        bus.subscribe(lambda e: _append(received, e))

        event = PoolEvent(
            type=PoolEventType.AGENT_OUTPUT,
            job_id="job-1",
            data={"type": "text", "text": "hi"},
        )
        await bus.emit(event)

        assert len(received) == 1
        assert received[0].type == PoolEventType.AGENT_OUTPUT
        assert received[0].data["text"] == "hi"

    @pytest.mark.asyncio
    async def test_multiple_streaming_events(self):
        bus = PoolEventBus()
        received = []
        bus.subscribe(lambda e: _append(received, e))

        for i in range(5):
            await bus.emit(PoolEvent(
                type=PoolEventType.AGENT_OUTPUT,
                job_id="job-1",
                data={"type": "text", "text": f"msg-{i}"},
            ))

        assert len(received) == 5

    @pytest.mark.asyncio
    async def test_subscriber_error_doesnt_block(self):
        bus = PoolEventBus()
        good = []

        async def bad_sub(e):
            raise RuntimeError("fail")

        bus.subscribe(bad_sub)
        bus.subscribe(lambda e: _append(good, e))

        await bus.emit(PoolEvent(
            type=PoolEventType.AGENT_OUTPUT,
            job_id="job-1",
            data={"type": "text", "text": "hi"},
        ))

        assert len(good) == 1


# ── AgentSlot on_message callback tests ──


class TestSlotOnMessage:
    """Test that AgentSlot.execute() calls on_message for each SDK message.

    Since ClaudeSDKClient is lazily imported inside execute(), we test the
    callback mechanism indirectly through the _capture_message helper and
    verify the on_message parameter signature is accepted.
    """

    def test_capture_message_helper(self):
        """Verify _capture_message produces correct dict structure."""
        from core.pool.slot import _capture_message

        # Mock an AssistantMessage with text block
        mock_text = MagicMock()
        mock_text.text = "hello"

        mock_msg = MagicMock()
        mock_msg.content = [mock_text]

        # We need to set up isinstance checks — use spec approach
        # _capture_message uses isinstance which needs real types.
        # Just test that it handles unknown types gracefully.
        result = _capture_message(mock_msg)
        assert "role" in result

    def test_on_message_signature_accepted(self):
        """Verify AgentSlot.execute() accepts on_message parameter."""
        from core.pool.slot import AgentSlot
        import inspect
        sig = inspect.signature(AgentSlot.execute)
        assert "on_message" in sig.parameters
        param = sig.parameters["on_message"]
        assert param.default is None


# ── Pool _on_agent_message tests ──


class TestPoolOnAgentMessage:
    """Test ExecutionPool._on_agent_message emits correct events."""

    @pytest.mark.asyncio
    async def test_emits_agent_output_for_text(self):
        from core.pool.pool import ExecutionPool

        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_config = MagicMock()
        mock_config.workspace_root = None

        pool = ExecutionPool(mock_queue, mock_config)
        emitted = []
        pool.events.subscribe(lambda e: _append(emitted, e))

        await pool._on_agent_message("job-123", {
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
        })

        assert len(emitted) == 1
        assert emitted[0].type == PoolEventType.AGENT_OUTPUT
        assert emitted[0].job_id == "job-123"
        assert emitted[0].data["type"] == "text"
        assert emitted[0].data["text"] == "hello"
        assert emitted[0].data["block_type"] == "text"

    @pytest.mark.asyncio
    async def test_emits_agent_output_for_tool_use(self):
        from core.pool.pool import ExecutionPool

        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_config = MagicMock()
        mock_config.workspace_root = None

        pool = ExecutionPool(mock_queue, mock_config)
        emitted = []
        pool.events.subscribe(lambda e: _append(emitted, e))

        await pool._on_agent_message("job-456", {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "Read", "input": {"path": "/foo"}}],
        })

        assert len(emitted) == 1
        assert emitted[0].data["type"] == "tool_use"
        assert emitted[0].data["name"] == "Read"
        assert emitted[0].data["block_type"] == "tool_use"

    @pytest.mark.asyncio
    async def test_emits_tool_result_for_result_role(self):
        from core.pool.pool import ExecutionPool

        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_config = MagicMock()
        mock_config.workspace_root = None

        pool = ExecutionPool(mock_queue, mock_config)
        emitted = []
        pool.events.subscribe(lambda e: _append(emitted, e))

        await pool._on_agent_message("job-789", {
            "role": "result",
            "num_turns": 3,
            "cost_usd": 0.10,
        })

        assert len(emitted) == 1
        assert emitted[0].type == PoolEventType.AGENT_TOOL_RESULT
        assert emitted[0].data["block_type"] == "result"

    @pytest.mark.asyncio
    async def test_ignores_unknown_roles(self):
        from core.pool.pool import ExecutionPool

        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_config = MagicMock()
        mock_config.workspace_root = None

        pool = ExecutionPool(mock_queue, mock_config)
        emitted = []
        pool.events.subscribe(lambda e: _append(emitted, e))

        await pool._on_agent_message("job-000", {
            "role": "SomeOtherMessage",
        })

        assert len(emitted) == 0

    @pytest.mark.asyncio
    async def test_multiple_content_blocks(self):
        from core.pool.pool import ExecutionPool

        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_config = MagicMock()
        mock_config.workspace_root = None

        pool = ExecutionPool(mock_queue, mock_config)
        emitted = []
        pool.events.subscribe(lambda e: _append(emitted, e))

        await pool._on_agent_message("job-multi", {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                {"type": "text", "text": "second"},
            ],
        })

        assert len(emitted) == 3
        assert emitted[0].data["block_type"] == "text"
        assert emitted[1].data["block_type"] == "tool_use"
        assert emitted[2].data["block_type"] == "text"


# ── Helper ──

async def _append(lst, item):
    lst.append(item)
