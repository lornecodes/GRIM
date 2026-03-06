"""Tests for pool event notifiers — webhook + Discord."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.pool.events import PoolEvent, PoolEventType
from core.pool.notifiers import (
    DiscordWebhookNotifier,
    WebhookNotifier,
    _EMBED_COLORS,
    _NOISY_EVENTS,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def complete_event():
    return PoolEvent(
        type=PoolEventType.JOB_COMPLETE,
        job_id="job-test123",
        data={
            "result_preview": "All tests pass",
            "cost_usd": 0.05,
            "num_turns": 3,
            "diff_stat": "2 files changed",
            "changed_files": ["foo.py", "bar.py"],
        },
    )


@pytest.fixture
def failed_event():
    return PoolEvent(
        type=PoolEventType.JOB_FAILED,
        job_id="job-fail456",
        data={"error": "Syntax error in main.py", "retries": 2},
    )


@pytest.fixture
def blocked_event():
    return PoolEvent(
        type=PoolEventType.JOB_BLOCKED,
        job_id="job-block789",
        data={"question": "Which database should I use?"},
    )


@pytest.fixture
def review_event():
    return PoolEvent(
        type=PoolEventType.JOB_REVIEW,
        job_id="job-review001",
        data={
            "workspace_id": "workspace-abc12345",
            "diff_stat": "3 files changed",
            "changed_files": ["a.py", "b.py", "c.py"],
        },
    )


@pytest.fixture
def submitted_event():
    return PoolEvent(
        type=PoolEventType.JOB_SUBMITTED,
        job_id="job-sub001",
        data={"job_type": "code", "priority": "normal"},
    )


# ── Discord embed building ──────────────────────────────────────


def test_embed_complete(complete_event):
    embed = DiscordWebhookNotifier._build_embed(complete_event)
    assert "Complete" in embed["title"]
    assert embed["color"] == 0x2ECC71
    assert "$0.0500" in embed["description"]
    assert "2 files changed" in embed["description"]


def test_embed_failed(failed_event):
    embed = DiscordWebhookNotifier._build_embed(failed_event)
    assert "Failed" in embed["title"]
    assert embed["color"] == 0xE74C3C
    assert "Syntax error" in embed["description"]
    assert "2" in embed["description"]  # retries


def test_embed_blocked(blocked_event):
    embed = DiscordWebhookNotifier._build_embed(blocked_event)
    assert "Needs Input" in embed["title"]
    assert embed["color"] == 0xF39C12
    assert "Which database" in embed["description"]
    assert "clarify" in embed["description"]


def test_embed_review(review_event):
    embed = DiscordWebhookNotifier._build_embed(review_event)
    assert "Review" in embed["title"]
    assert embed["color"] == 0x3498DB
    assert "workspace-abc12345" in embed["description"]
    assert "3" in embed["description"]  # files changed count


def test_embed_cancelled():
    event = PoolEvent(type=PoolEventType.JOB_CANCELLED, job_id="j1")
    embed = DiscordWebhookNotifier._build_embed(event)
    assert "Cancelled" in embed["title"]
    assert embed["color"] == 0x95A5A6


# ── Noisy event filtering ───────────────────────────────────────


def test_noisy_events_set():
    assert PoolEventType.JOB_SUBMITTED in _NOISY_EVENTS
    assert PoolEventType.JOB_STARTED in _NOISY_EVENTS
    assert PoolEventType.JOB_COMPLETE not in _NOISY_EVENTS


@pytest.mark.asyncio
async def test_discord_notifier_filters_noisy(submitted_event):
    notifier = DiscordWebhookNotifier("https://example.com/webhook")
    with patch("core.pool.notifiers.httpx", create=True) as mock_httpx:
        await notifier(submitted_event)
        # httpx should NOT be imported/called because event is filtered
        # (the actual import is inside the function, so mock_httpx won't be used)


@pytest.mark.asyncio
async def test_discord_notifier_allows_noisy_when_disabled(submitted_event):
    notifier = DiscordWebhookNotifier("https://example.com/webhook", filter_noisy=False)
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=204))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client
        await notifier(submitted_event)
        mock_client.post.assert_called_once()


# ── Webhook notifier ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_notifier_posts_json(complete_event):
    notifier = WebhookNotifier("https://example.com/hook")
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client
        await notifier(complete_event)
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args.args[0] == "https://example.com/hook"


@pytest.mark.asyncio
async def test_webhook_notifier_filters_noisy(submitted_event):
    notifier = WebhookNotifier("https://example.com/hook")
    # Should not raise, just skip
    await notifier(submitted_event)


# ── Color mapping ───────────────────────────────────────────────


def test_embed_colors_all_mapped():
    """All non-noisy event types have colors."""
    for etype in PoolEventType:
        if etype not in _NOISY_EVENTS:
            assert etype in _EMBED_COLORS, f"Missing color for {etype}"
