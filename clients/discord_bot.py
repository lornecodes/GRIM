"""GRIM Discord bot — a Discord frontend for the GrimClient.

Production-grade Discord service: per-channel sessions, people profiles,
identity-aware conversations, health checks, and multi-guild support.

Security:
- Only responds in allowed channels/guilds
- Owner authentication via GRIM_DISCORD_OWNER_ID
- Read-only Kronos tools (no Bash/Write/Edit)
- Per-channel daily cost cap
- Rate limiting per user
- People profiles stored as vault FDOs (deterministic, not via GRIM tools)

Usage:
    export GRIM_DISCORD_TOKEN=your_bot_token
    export GRIM_DISCORD_OWNER_ID=your_discord_user_id
    python -m clients.discord_bot
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.client import KRONOS_TOOLS, GrimClient, GrimResponse
from core.config import GrimConfig, load_config

logger = logging.getLogger(__name__)

# Discord-safe tool list — NO write/execute tools
DISCORD_ALLOWED_TOOLS = [
    t for t in KRONOS_TOOLS
    if "create" not in t and "update" not in t and "note_append" not in t
] + [
    # Pool read-only (can check status, not submit)
    "mcp__pool__pool_status",
    "mcp__pool__pool_list_jobs",
]

# Discord message limit
DISCORD_MAX_CHARS = 2000
DISCORD_SAFE_CHARS = 1900  # leave room for formatting


@dataclass
class PeopleProfile:
    """Cached people profile from vault FDO."""

    fdo_id: str
    username: str
    display_name: str
    discord_user_id: int
    summary: str = ""
    exists_in_vault: bool = False


@dataclass
class ChannelSession:
    """Tracks a GrimClient session for a Discord channel."""

    client: GrimClient
    daily_cost: float = 0.0
    last_reset: str = ""  # ISO date for daily cost reset
    message_count: int = 0


class GrimDiscordBot:
    """Discord bot wrapping GrimClient — one persistent session per channel.

    Each channel gets its own GrimClient with multi-turn context persistence.
    The bot uses read-only Kronos tools (no file writes, no Bash).
    People profiles are stored as vault FDOs and loaded per-user.
    """

    def __init__(
        self,
        config: GrimConfig,
        *,
        allowed_guild_ids: list[int] | None = None,
        allowed_channel_ids: list[int] | None = None,
        owner_user_id: int | None = None,
        daily_cost_cap: float = 1.0,
        max_response_chars: int = DISCORD_SAFE_CHARS,
    ):
        self.config = config
        self.allowed_guild_ids = set(allowed_guild_ids) if allowed_guild_ids else None
        self.allowed_channel_ids = set(allowed_channel_ids) if allowed_channel_ids else None
        self.owner_user_id = owner_user_id
        self.daily_cost_cap = daily_cost_cap
        self.max_response_chars = max_response_chars

        self.sessions: dict[int, ChannelSession] = {}
        self._rate_limits: dict[int, list[float]] = defaultdict(list)
        self._people_cache: dict[int, PeopleProfile] = {}  # user_id → profile
        self._start_time = time.monotonic()
        self._total_messages = 0

    # ── People profiles ─────────────────────────────────────────

    def is_owner(self, user_id: int) -> bool:
        """Check if this is the bot owner (Peter)."""
        return self.owner_user_id is not None and user_id == self.owner_user_id

    def register_user(self, user_id: int, username: str, display_name: str | None = None) -> None:
        """Track a Discord user for identity context."""
        if user_id not in self._people_cache:
            self._people_cache[user_id] = PeopleProfile(
                fdo_id=_make_people_fdo_id(username),
                username=username,
                display_name=display_name or username,
                discord_user_id=user_id,
            )
        else:
            # Update display name if changed
            self._people_cache[user_id].display_name = display_name or username

    def get_people_profile(self, user_id: int) -> PeopleProfile | None:
        """Get cached people profile for a user."""
        return self._people_cache.get(user_id)

    def get_user_context(self, user_id: int) -> str:
        """Build user identity context for the message."""
        profile = self._people_cache.get(user_id)
        if profile is None:
            return f"[Speaking with: user-{user_id} — unknown user]"
        name = profile.display_name
        if self.is_owner(user_id):
            ctx = f"[Speaking with: Peter ({name}) — your creator and operator]"
        else:
            ctx = f"[Speaking with: {name} — a friend/guest in the Discord server]"
        if profile.summary:
            ctx += f"\n[What you know about them: {profile.summary}]"
        return ctx

    # ── People FDO management ───────────────────────────────────

    async def ensure_people_fdo(self, user_id: int) -> PeopleProfile:
        """Look up or create a people FDO for a Discord user.

        Uses the vault path directly (not MCP tools) to check for existing
        FDOs and create new ones. This keeps it deterministic and fast.
        """
        profile = self._people_cache.get(user_id)
        if profile is None:
            return PeopleProfile(
                fdo_id=f"discord-user-{user_id}",
                username=f"user-{user_id}",
                display_name=f"user-{user_id}",
                discord_user_id=user_id,
            )

        if profile.exists_in_vault:
            return profile

        # Check if FDO exists on disk
        fdo_path = self.config.vault_path / "people" / f"{profile.fdo_id}.md"
        if fdo_path.exists():
            profile.exists_in_vault = True
            # Extract summary from FDO body
            profile.summary = _extract_fdo_summary(fdo_path)
            logger.info("Loaded people FDO for %s: %s", profile.username, profile.fdo_id)
            return profile

        # Owner (Peter) already has an FDO — link to it
        if self.is_owner(user_id):
            peter_path = self.config.vault_path / "people" / "peter.md"
            if peter_path.exists():
                profile.fdo_id = "peter"
                profile.exists_in_vault = True
                profile.summary = _extract_fdo_summary(peter_path)
                return profile

        # Create a seed FDO for new users
        try:
            _create_people_fdo(fdo_path, profile)
            profile.exists_in_vault = True
            logger.info("Created people FDO for %s: %s", profile.username, profile.fdo_id)
        except Exception as e:
            logger.warning("Failed to create people FDO for %s: %s", profile.username, e)

        return profile

    # ── Security ────────────────────────────────────────────────

    def is_allowed(self, guild_id: int | None, channel_id: int) -> bool:
        """Check if this message is from an allowed location."""
        if self.allowed_guild_ids is not None:
            if guild_id is None or guild_id not in self.allowed_guild_ids:
                return False
        if self.allowed_channel_ids is not None:
            if channel_id not in self.allowed_channel_ids:
                return False
        return True

    def is_rate_limited(self, user_id: int, window_secs: int = 60, max_messages: int = 10) -> bool:
        """Check if a user has exceeded the rate limit."""
        now = datetime.now(timezone.utc).timestamp()
        timestamps = self._rate_limits[user_id]
        # Prune old entries
        self._rate_limits[user_id] = [t for t in timestamps if now - t < window_secs]
        if len(self._rate_limits[user_id]) >= max_messages:
            return True
        self._rate_limits[user_id].append(now)
        return False

    def is_cost_exceeded(self, channel_id: int) -> bool:
        """Check if a channel has exceeded its daily cost cap."""
        session = self.sessions.get(channel_id)
        if session is None:
            return False
        today = datetime.now(timezone.utc).date().isoformat()
        if session.last_reset != today:
            session.daily_cost = 0.0
            session.last_reset = today
        return session.daily_cost >= self.daily_cost_cap

    async def get_or_create_session(self, channel_id: int) -> ChannelSession:
        """Get or create a GrimClient session for a channel."""
        if channel_id not in self.sessions:
            client = GrimClient(
                self.config,
                allowed_tools=DISCORD_ALLOWED_TOOLS,
                max_turns=6,  # keep Discord responses snappy
                caller_id="discord",
            )
            await client.start()
            self.sessions[channel_id] = ChannelSession(
                client=client,
                last_reset=datetime.now(timezone.utc).date().isoformat(),
            )
            logger.info("Created GRIM session for channel %d", channel_id)
        return self.sessions[channel_id]

    async def handle_message(
        self,
        content: str,
        *,
        guild_id: int | None,
        channel_id: int,
        user_id: int,
        username: str | None = None,
        display_name: str | None = None,
        is_bot: bool = False,
    ) -> list[str]:
        """Process a Discord message and return response chunks.

        Returns a list of strings, each <= DISCORD_MAX_CHARS,
        ready to send as separate Discord messages.

        Returns empty list if the message should be ignored.
        """
        # Ignore bots
        if is_bot:
            return []

        # Security checks
        if not self.is_allowed(guild_id, channel_id):
            return []

        if self.is_rate_limited(user_id):
            return ["Slow down — I need a moment between messages."]

        if self.is_cost_exceeded(channel_id):
            return ["I've reached my daily usage limit for this channel. Try again tomorrow."]

        # Track user identity and ensure vault profile
        if username:
            self.register_user(user_id, username, display_name)
        await self.ensure_people_fdo(user_id)

        # Prepend user context so GRIM knows who's talking
        user_ctx = self.get_user_context(user_id)
        contextualized = f"{user_ctx}\n{content}"

        self._total_messages += 1

        # Get or create session
        session = await self.get_or_create_session(channel_id)

        # Send message to GRIM
        try:
            resp = await asyncio.wait_for(
                session.client.send(contextualized),
                timeout=120,
            )
        except asyncio.TimeoutError:
            return ["That took too long — try a simpler question."]
        except Exception as e:
            logger.error("GRIM error in channel %d: %s", channel_id, e)
            return ["Something went wrong — try again in a moment."]

        # Track cost
        if resp.cost_usd:
            session.daily_cost += resp.cost_usd
        session.message_count += 1

        # Format response
        if not resp.text:
            return ["I processed that but have nothing to say."]

        return split_message(resp.text, max_chars=self.max_response_chars)

    # ── Metrics ──────────────────────────────────────────────────

    @property
    def metrics(self) -> dict:
        """Service metrics for health checks and monitoring."""
        total_cost = sum(s.daily_cost for s in self.sessions.values())
        return {
            "active_sessions": len(self.sessions),
            "total_messages": self._total_messages,
            "known_users": len(self._people_cache),
            "total_daily_cost": round(total_cost, 4),
            "uptime_seconds": round(time.monotonic() - self._start_time, 1),
        }

    async def close_all(self) -> None:
        """Shut down all active sessions."""
        for channel_id, session in self.sessions.items():
            try:
                await session.client.stop()
            except Exception as e:
                logger.warning("Error closing session for channel %d: %s", channel_id, e)
        self.sessions.clear()


def split_message(text: str, max_chars: int = DISCORD_SAFE_CHARS) -> list[str]:
    """Split a long message into Discord-safe chunks.

    Tries to split at paragraph boundaries, then sentence boundaries,
    then falls back to hard split at max_chars.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break

        # Try to split at a double newline (paragraph boundary)
        split_at = remaining.rfind("\n\n", 0, max_chars)

        # Try single newline
        if split_at <= 0:
            split_at = remaining.rfind("\n", 0, max_chars)

        # Try sentence boundary
        if split_at <= 0:
            split_at = remaining.rfind(". ", 0, max_chars)
            if split_at > 0:
                split_at += 1  # include the period

        # Hard split
        if split_at <= 0:
            split_at = max_chars

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    return chunks


# ── People FDO helpers ────────────────────────────────────────────

def _make_people_fdo_id(username: str) -> str:
    """Convert a Discord username to a vault FDO ID."""
    clean = re.sub(r"[^a-z0-9-]", "-", username.lower()).strip("-")
    return f"discord-{clean}"


def _extract_fdo_summary(fdo_path: Any) -> str:
    """Extract a brief summary from a people FDO file.

    Reads the first few lines of the ## Summary or ## How GRIM Knows Them section.
    Returns empty string if no summary found.
    """
    try:
        text = fdo_path.read_text(encoding="utf-8")
    except Exception:
        return ""

    # Look for key sections
    for header in ["## How GRIM Knows Them", "## Summary", "## Role"]:
        idx = text.find(header)
        if idx < 0:
            continue
        # Get content after header until next ## or end
        after = text[idx + len(header):]
        next_header = after.find("\n## ")
        section = after[:next_header] if next_header > 0 else after[:500]
        summary = section.strip()[:300]
        if summary:
            return summary

    return ""


def _create_people_fdo(fdo_path: Any, profile: PeopleProfile) -> None:
    """Create a seed people FDO for a new Discord user."""
    today = datetime.now(timezone.utc).date().isoformat()
    frontmatter = (
        f"---\n"
        f"id: {profile.fdo_id}\n"
        f"title: \"{profile.display_name}\"\n"
        f"domain: people\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        f"status: seed\n"
        f"confidence: 0.3\n"
        f"confidence_basis: Auto-created from Discord interaction\n"
        f"related:\n"
        f"  - peter\n"
        f"tags:\n"
        f"  - person\n"
        f"  - discord\n"
        f"  - friend\n"
        f"extra:\n"
        f"  type: person\n"
        f"  role: friend\n"
        f"  discord_user_id: \"{profile.discord_user_id}\"\n"
        f"  discord_username: \"{profile.username}\"\n"
        f"---\n"
    )
    body = (
        f"# {profile.display_name}\n\n"
        f"*Discord user: {profile.username}*\n\n"
        f"## How GRIM Knows Them\n\n"
        f"Met via Discord on {today}. Friend of Peter's.\n\n"
        f"## Interests\n\n"
        f"(To be discovered through conversation.)\n\n"
        f"## Connections\n\n"
        f"- Peter: [[peter]]\n"
    )
    fdo_path.parent.mkdir(parents=True, exist_ok=True)
    fdo_path.write_text(frontmatter + "\n" + body, encoding="utf-8")


# ── Health check server ──────────────────────────────────────────

async def run_health_server(bot: GrimDiscordBot, port: int = 8081) -> asyncio.AbstractServer:
    """Start a tiny HTTP health check server for Docker."""

    async def handle_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        # Read the request line
        request_line = await reader.readline()
        # Drain remaining headers
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break

        path = request_line.decode().split(" ")[1] if request_line else "/"

        if path == "/health":
            body = json.dumps({"status": "ok", **bot.metrics})
            response = (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"\r\n{body}"
            )
        else:
            response = "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"

        writer.write(response.encode())
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle_request, "0.0.0.0", port)
    logger.info("Health check server on port %d", port)
    return server


# ── Discord.py integration ───────────────────────────────────────

def _parse_int_list(env_var: str) -> list[int] | None:
    """Parse comma-separated int list from env var. Returns None if empty."""
    val = os.environ.get(env_var, "").strip()
    if not val:
        return None
    return [int(x.strip()) for x in val.split(",") if x.strip()]


def run_bot(
    token: str | None = None,
    config: GrimConfig | None = None,
    guild_ids: list[int] | None = None,
    channel_ids: list[int] | None = None,
    owner_user_id: int | None = None,
    health_port: int = 8081,
):
    """Run the GRIM Discord bot. Requires discord.py >= 2.0.

    All config can come from env vars:
    - GRIM_DISCORD_TOKEN: Bot token (required)
    - GRIM_DISCORD_OWNER_ID: Peter's Discord user ID
    - GRIM_DISCORD_GUILDS: Comma-separated allowed guild IDs
    - GRIM_DISCORD_CHANNELS: Comma-separated allowed channel IDs
    - GRIM_DISCORD_HEALTH_PORT: Health check port (default 8081)
    """
    import discord

    token = token or os.environ.get("GRIM_DISCORD_TOKEN")
    if not token:
        raise ValueError(
            "Discord token required. Set GRIM_DISCORD_TOKEN env var "
            "or pass token= argument."
        )

    owner_user_id = owner_user_id or int(os.environ.get("GRIM_DISCORD_OWNER_ID", "0")) or None
    guild_ids = guild_ids or _parse_int_list("GRIM_DISCORD_GUILDS")
    channel_ids = channel_ids or _parse_int_list("GRIM_DISCORD_CHANNELS")
    health_port = int(os.environ.get("GRIM_DISCORD_HEALTH_PORT", str(health_port)))

    if config is None:
        config = load_config()

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True  # needed to resolve display names
    dc = discord.Client(intents=intents)

    bot = GrimDiscordBot(
        config,
        allowed_guild_ids=guild_ids,
        allowed_channel_ids=channel_ids,
        owner_user_id=owner_user_id,
    )

    health_server: asyncio.AbstractServer | None = None

    @dc.event
    async def on_ready():
        nonlocal health_server
        logger.info("GRIM Discord bot ready: %s (owner: %s)", dc.user, owner_user_id)
        if guild_ids:
            logger.info("Allowed guilds: %s", guild_ids)
        if channel_ids:
            logger.info("Allowed channels: %s", channel_ids)
        # Start health check server
        health_server = await run_health_server(bot, port=health_port)

    @dc.event
    async def on_message(message: discord.Message):
        if message.author == dc.user:
            return

        # Only respond to mentions or DMs (if allowed)
        is_mentioned = dc.user in message.mentions
        is_dm = message.guild is None
        if not is_mentioned and not is_dm:
            return

        # Strip the mention from the message
        content = message.content
        if dc.user:
            content = re.sub(rf"<@!?{dc.user.id}>", "", content).strip()

        if not content:
            return

        async with message.channel.typing():
            chunks = await bot.handle_message(
                content,
                guild_id=message.guild.id if message.guild else None,
                channel_id=message.channel.id,
                user_id=message.author.id,
                username=message.author.name,
                display_name=message.author.display_name,
                is_bot=message.author.bot,
            )

        for chunk in chunks:
            await message.reply(chunk, mention_author=False)

    def _shutdown(signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)

    try:
        dc.run(token)
    finally:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(bot.close_all())
        if health_server:
            health_server.close()
        loop.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_bot()
