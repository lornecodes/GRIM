"""AI Bridge — transparent reverse proxy with token usage tracking.

Sits between Claude API consumers (GRIM, IronClaw, etc.) and CLIProxyAPI.
Forwards all requests, extracts token usage from response bodies, logs to SQLite.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from tracker import TokenTracker

logger = logging.getLogger(__name__)

UPSTREAM_URL = os.getenv("UPSTREAM_URL", "http://cliproxyapi:8317")
DB_PATH = Path(os.getenv("BRIDGE_DB_PATH", "/data/tokens.db"))

_tracker: TokenTracker | None = None
_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize tracker and HTTP client on startup."""
    global _tracker, _client

    _tracker = TokenTracker(DB_PATH)
    await _tracker.initialize()

    _client = httpx.AsyncClient(
        base_url=UPSTREAM_URL,
        timeout=httpx.Timeout(300.0, connect=10.0),  # LLM calls can be slow
    )

    logger.info("AI Bridge started — upstream: %s, db: %s", UPSTREAM_URL, DB_PATH)
    yield

    await _client.aclose()
    await _tracker.close()
    logger.info("AI Bridge shut down")


app = FastAPI(title="AI Bridge", lifespan=lifespan)


# ── Health check ────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check — also pings upstream."""
    upstream_ok = False
    try:
        resp = await _client.get("/v1/models")
        upstream_ok = resp.status_code == 200
    except Exception:
        pass
    return {"status": "ok", "upstream": upstream_ok}


# ── Usage API ───────────────────────────────────────────────

@app.get("/bridge/usage/summary")
async def usage_summary(days: int = 30):
    """Aggregate token usage by caller and model."""
    return JSONResponse(await _tracker.summary(days=days))


@app.get("/bridge/usage/by-day")
async def usage_by_day(days: int = 30, caller_id: str | None = None):
    """Daily token aggregates for charting."""
    return JSONResponse(await _tracker.by_day(days=days, caller_id=caller_id))


@app.get("/bridge/usage/recent")
async def usage_recent(limit: int = 50):
    """Last N raw usage records."""
    return JSONResponse(await _tracker.recent(limit=min(limit, 500)))


# ── Proxy ───────────────────────────────────────────────────

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy(request: Request, path: str):
    """Forward all requests to upstream CLIProxyAPI."""
    caller_id = request.headers.get("x-caller-id", "unknown")

    # Build upstream request
    url = f"/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = dict(request.headers)
    # Remove hop-by-hop headers that shouldn't be forwarded
    for h in ("host", "transfer-encoding"):
        headers.pop(h, None)

    body = await request.body()

    # Detect streaming and messages endpoint
    is_messages = path.rstrip("/").endswith("messages") and request.method == "POST"
    is_streaming = False
    if is_messages and body:
        try:
            payload = json.loads(body)
            is_streaming = payload.get("stream", False)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    try:
        if is_messages and is_streaming:
            return await _handle_streaming(url, headers, body, caller_id)
        elif is_messages:
            return await _handle_non_streaming(url, headers, body, caller_id)
        else:
            return await _handle_passthrough(request.method, url, headers, body)
    except httpx.ConnectError:
        return JSONResponse({"error": "upstream unavailable"}, status_code=502)
    except httpx.TimeoutException:
        return JSONResponse({"error": "upstream timeout"}, status_code=504)


async def _handle_non_streaming(url: str, headers: dict, body: bytes, caller_id: str) -> Response:
    """Forward non-streaming /v1/messages and extract usage."""
    resp = await _client.request("POST", url, headers=headers, content=body)

    # Extract usage from response body
    try:
        data = resp.json()
        usage = data.get("usage", {})
        model = data.get("model")
        if usage:
            await _tracker.record(
                caller_id=caller_id,
                model=model,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cache_read=usage.get("cache_read_input_tokens", 0),
                cache_create=usage.get("cache_creation_input_tokens", 0),
            )
    except Exception:
        logger.debug("Could not extract usage from non-streaming response", exc_info=True)

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )


async def _handle_streaming(url: str, headers: dict, body: bytes, caller_id: str) -> StreamingResponse:
    """Forward streaming /v1/messages, extract usage from SSE events."""

    # State for capturing usage across the stream
    usage_state = {
        "model": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read": 0,
        "cache_create": 0,
    }

    async def stream_with_tracking():
        """Stream chunks through while capturing usage events."""
        try:
            async with _client.stream("POST", url, headers=headers, content=body) as resp:
                async for line in resp.aiter_lines():
                    # Forward every line immediately
                    yield line + "\n"

                    # Parse SSE data lines for usage info
                    if not line.startswith("data: "):
                        continue

                    try:
                        event_data = json.loads(line[6:])
                        event_type = event_data.get("type", "")

                        if event_type == "message_start":
                            msg = event_data.get("message", {})
                            usage_state["model"] = msg.get("model")
                            usage = msg.get("usage", {})
                            usage_state["input_tokens"] = usage.get("input_tokens", 0)
                            usage_state["cache_read"] = usage.get("cache_read_input_tokens", 0)
                            usage_state["cache_create"] = usage.get("cache_creation_input_tokens", 0)

                        elif event_type == "message_delta":
                            usage = event_data.get("usage", {})
                            usage_state["output_tokens"] = usage.get("output_tokens", 0)

                    except (json.JSONDecodeError, KeyError):
                        pass

        except Exception:
            logger.warning("Stream error", exc_info=True)
            return

        # Stream complete — log usage
        if usage_state["input_tokens"] or usage_state["output_tokens"]:
            try:
                await _tracker.record(
                    caller_id=caller_id,
                    model=usage_state["model"],
                    input_tokens=usage_state["input_tokens"],
                    output_tokens=usage_state["output_tokens"],
                    cache_read=usage_state["cache_read"],
                    cache_create=usage_state["cache_create"],
                )
            except Exception:
                logger.debug("Failed to record streaming usage", exc_info=True)

    return StreamingResponse(
        stream_with_tracking(),
        media_type="text/event-stream",
    )


async def _handle_passthrough(method: str, url: str, headers: dict, body: bytes) -> Response:
    """Forward any non-messages request without modification."""
    resp = await _client.request(method, url, headers=headers, content=body)
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )
