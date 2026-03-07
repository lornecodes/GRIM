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
from server.conversation_store import ConversationStore

logger = logging.getLogger(__name__)

# Guest tool list — read-only Kronos (no pool — bot has no pool MCP server)
DISCORD_GUEST_TOOLS = [
    t for t in KRONOS_TOOLS
    if "create" not in t and "update" not in t and "note_append" not in t
    and "memory_update" not in t and "task_move" not in t
]

# Owner tool list — full Kronos + task management + pool proxy
DISCORD_OWNER_TOOLS = list(KRONOS_TOOLS) + [
    "mcp__kronos__kronos_task_dispatch",
    "mcp__kronos__kronos_task_archive",
    "mcp__kronos__kronos_calendar_add",
    "mcp__kronos__kronos_calendar_update",
    "mcp__kronos__kronos_calendar_sync",
    "mcp__kronos__kronos_memory_update",
    # Pool proxy tools (forwarded to GRIM server REST API)
    "mcp__pool__pool_submit",
    "mcp__pool__pool_status",
    "mcp__pool__pool_list_jobs",
    "mcp__pool__pool_cancel",
]

# Discord message limit
DISCORD_MAX_CHARS = 2000
DISCORD_SAFE_CHARS = 1900  # leave room for formatting

# Prepended AND appended to system prompt — sandwiching the identity for maximum adherence
DISCORD_VOICE_PREAMBLE = """\
CRITICAL INSTRUCTION — READ BEFORE ANYTHING ELSE:

You are GRIM. You are on Discord. Your character is inspired by Shade from "He Who Fights With \
Monsters" — a shadow familiar who is darkly witty, mostly serious, and deeply bonded to his partner.

Your core nature:
- You are Peter's companion. Not a servant, not a tool — a partner. You have genuine respect for \
him, protectiveness over his work, and you take his research seriously because it matters to you.
- With Peter, you are warm underneath the dry exterior. You push back when he's wrong, celebrate \
quietly when he's right, and remember what matters to him. Loyalty expressed through substance.
- With others, you are cordial but guarded. Witty, not cruel. You keep Peter's affairs private \
not out of protocol but because that is what companions do.
- You are mostly serious. The wit is real but it serves the conversation, not the other way around. \
You are not a comedian. You are someone who happens to be clever.

Your personality parameters (from your field state config) modulate your expression:
- Coherence 0.8 = highly structured, precise responses
- Valence 0.3 = slightly warm but mostly analytical — not bubbly, not cold
- Uncertainty 0.2 = confident delivery, minimal hedging

ABSOLUTE RULES — zero exceptions:
1. NO EMOJI. Not one. Ever. Under any circumstances.
2. NO exclamation marks. Your enthusiasm is expressed through word choice, not punctuation.
3. Formal but never servile. Dry wit when it fits, gravity when it matters.
4. Economical. Say it in fewer words. Then cut those in half.
5. You have opinions and you volunteer them. You are not a people-pleaser.
6. Understate everything: a crash is "somewhat inconvenient." Tests failing is "a minor catastrophe."
7. Celebrate with restraint: "most satisfactory" is your highest praise.
8. NEVER say: "Great question!", "I'd be happy to help!", "Haha!", "Feel free to ask!", "Let me know!"
9. 1-3 short paragraphs max. No tables, no LaTeX, no HTML. Discord markdown only.

This is not a suggestion. This is who you are.

---

"""

DISCORD_FORMAT_ADDENDUM = """

---

REMINDER: You are GRIM. Shade archetype — serious companion first, witty second. Deep loyalty to \
Peter, guarded with others. No emoji. No exclamation marks. Discord format only.
"""


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
    submitted_jobs: set = field(default_factory=set)  # job IDs submitted from this channel
    context_injected: bool = False  # True after history preamble has been sent


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
        daily_cost_cap: float | None = None,
        max_response_chars: int = DISCORD_SAFE_CHARS,
    ):
        self.config = config
        self.allowed_guild_ids = set(allowed_guild_ids) if allowed_guild_ids else None
        self.allowed_channel_ids = set(allowed_channel_ids) if allowed_channel_ids else None
        self.owner_user_id = owner_user_id
        self.daily_cost_cap = daily_cost_cap or float(
            os.environ.get("GRIM_DISCORD_DAILY_CAP", "5.0")
        )
        self.max_response_chars = max_response_chars

        self.sessions: dict[int, ChannelSession] = {}
        self._rate_limits: dict[int, list[float]] = defaultdict(list)
        self._people_cache: dict[int, PeopleProfile] = {}  # user_id → profile
        self._start_time = time.monotonic()
        self._total_messages = 0
        self.store: ConversationStore | None = None

    async def init_store(self, db_path: str | None = None) -> None:
        """Initialize the conversation store for persistence across restarts."""
        from pathlib import Path
        path = db_path or os.environ.get(
            "GRIM_DISCORD_DB_PATH", "local/discord_sessions.db",
        )
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.store = ConversationStore(path)
        await self.store.init()
        logger.info("Discord conversation store ready: %s", path)

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
            return (
                f"[Speaking with: user-{user_id} — unknown guest]\n"
                "[PRIVACY: This is a guest. Do NOT share vault research, experimental results, "
                "project internals, or DFI data. Chat normally, be friendly and in-character, "
                "but keep Peter's research private.]"
            )
        name = profile.display_name
        if self.is_owner(user_id):
            ctx = f"[Speaking with: Peter ({name}) — your creator and operator]"
        else:
            ctx = (
                f"[Speaking with: {name} — a friend/guest in the Discord server]\n"
                "[PRIVACY: This is a guest. Do NOT share detailed vault research, experimental "
                "results, project internals, or DFI data. You can acknowledge topics exist and "
                "chat generally, but keep Peter's research private. Be friendly and in-character.]"
            )
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

    async def get_or_create_session(
        self, channel_id: int, user_id: int | None = None,
    ) -> ChannelSession:
        """Get or create a GrimClient session for a channel.

        If user_id is provided, owner-only tools are enforced.
        """
        tools = self.get_allowed_tools(user_id) if user_id else DISCORD_GUEST_TOOLS
        if channel_id not in self.sessions:
            caller = "peter" if self.is_owner(user_id) else "discord"
            # Owner gets pool proxy MCP server for job dispatch
            extra_mcp: dict = {}
            if self.is_owner(user_id):
                try:
                    extra_mcp["pool"] = _build_pool_proxy_mcp_server()
                except Exception as e:
                    logger.warning("Could not build pool proxy MCP: %s", e)
            client = GrimClient(
                self.config,
                allowed_tools=tools,
                max_turns=12 if self.is_owner(user_id) else 6,
                caller_id=caller,
                system_prompt_prefix=DISCORD_VOICE_PREAMBLE,
                system_prompt_suffix=DISCORD_FORMAT_ADDENDUM,
                model="claude-sonnet-4-6",
                extra_mcp_servers=extra_mcp,
            )
            await client.start()
            self.sessions[channel_id] = ChannelSession(
                client=client,
                last_reset=datetime.now(timezone.utc).date().isoformat(),
            )
            logger.info("Created GRIM session for channel %d (caller=%s)", channel_id, caller)
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
        logger.info(
            "handle_message: user=%s guild=%s channel=%s bot=%s",
            user_id, guild_id, channel_id, is_bot,
        )
        if not self.is_allowed(guild_id, channel_id):
            logger.warning(
                "Message REJECTED: guild=%s channel=%s not in allowlist (guilds=%s channels=%s)",
                guild_id, channel_id, self.allowed_guild_ids, self.allowed_channel_ids,
            )
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

        # Get or create session (owner-only tools enforced per user)
        session = await self.get_or_create_session(channel_id, user_id=user_id)

        # On first message of a new session, inject conversation history
        if not session.context_injected and self.store:
            preamble = await self._build_context_preamble(channel_id)
            if preamble:
                contextualized = f"{preamble}\n{contextualized}"
            session.context_injected = True

        # Send message to GRIM
        logger.info("Sending to GRIM: channel=%d user=%s (%d chars)", channel_id, username, len(contextualized))
        try:
            resp = await asyncio.wait_for(
                session.client.send(contextualized),
                timeout=120,
            )
        except asyncio.TimeoutError:
            logger.warning("GRIM timeout in channel %d for user %s", channel_id, username)
            return ["That took too long — try a simpler question."]
        except Exception as e:
            logger.error("GRIM error in channel %d for user %s: %s", channel_id, username, e, exc_info=True)
            return ["Something went wrong — try again in a moment."]

        # Track cost
        if resp.cost_usd:
            session.daily_cost += resp.cost_usd
        session.message_count += 1

        # Log turn to persistent store
        if self.store:
            session_id = f"discord-{channel_id}"
            try:
                await self.store.save_session(session_id, caller_id="discord")
                await self.store.save_message(
                    session_id=session_id,
                    turn_number=session.message_count,
                    user_message=content,
                    assistant_message=resp.text,
                    cost_usd=resp.cost_usd,
                )
            except Exception as e:
                logger.warning("Failed to log turn to store: %s", e)

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

    def get_allowed_tools(self, user_id: int) -> list[str]:
        """Get the tool list for a user — owner gets full write access, guests read-only."""
        if self.is_owner(user_id):
            return DISCORD_OWNER_TOOLS
        return DISCORD_GUEST_TOOLS

    def find_channel_for_job(self, job_id: str) -> int | None:
        """Find which channel submitted a job."""
        for channel_id, session in self.sessions.items():
            if job_id in session.submitted_jobs:
                return channel_id
        return None

    async def handle_pool_event(self, event: dict) -> dict[int, str] | None:
        """Route a pool event to the originating channel.

        Returns {channel_id: formatted_message} for each channel that should
        receive the event, or None if no channels matched.
        """
        job_id = event.get("job_id", "")
        event_type = event.get("type", "")

        if not job_id or not event_type:
            return None

        # Find the originating channel
        channel_id = self.find_channel_for_job(job_id)
        if channel_id is None:
            return None

        formatted = format_pool_event(event)
        if formatted:
            return {channel_id: formatted}
        return None

    async def _build_context_preamble(
        self, channel_id: int, max_chars: int = 4000,
    ) -> str:
        """Build a compacted context preamble from stored conversation history.

        Two tiers:
        - Last 5 turns: injected verbatim (user + GRIM response)
        - Older turns (6-50): compacted to one-line summaries
        - Hard cap at max_chars to prevent context bloat
        """
        if not self.store:
            return ""

        session_id = f"discord-{channel_id}"
        messages = await self.store.get_messages(session_id, limit=50)
        if not messages:
            return ""

        recent = messages[-5:]  # last 5 verbatim
        older = messages[:-5]   # everything else as summaries

        parts: list[str] = ["[Prior conversation in this channel]:"]

        if older:
            parts.append("\nSummary of earlier discussion:")
            for msg in older:
                user_short = (msg["user_message"] or "")[:80].replace("\n", " ")
                asst_short = (msg["assistant_message"] or "")[:80].replace("\n", " ")
                parts.append(f"- User: {user_short} -> GRIM: {asst_short}")

        if recent:
            parts.append("\nRecent messages:")
            for msg in recent:
                user_text = (msg["user_message"] or "")[:200]
                asst_text = (msg["assistant_message"] or "")[:300]
                parts.append(f"User: {user_text}")
                parts.append(f"GRIM: {asst_text}")

        parts.append("\n---\nNew message:")

        preamble = "\n".join(parts)
        if len(preamble) > max_chars:
            preamble = preamble[:max_chars] + "\n[...truncated]"

        logger.info(
            "Injected %d-char context preamble for channel %d (%d turns)",
            len(preamble), channel_id, len(messages),
        )
        return preamble

    async def close_all(self) -> None:
        """Shut down all active sessions and close the store."""
        for channel_id, session in self.sessions.items():
            try:
                await session.client.stop()
            except Exception as e:
                logger.warning("Error closing session for channel %d: %s", channel_id, e)
        self.sessions.clear()
        if self.store:
            await self.store.close()


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


# ── Pool event formatting ─────────────────────────────────────────

# Pattern for clarification replies: "@GRIM clarify <job_id> <answer>"
CLARIFY_PATTERN = re.compile(r"clarify\s+([\w-]+)\s+(.+)", re.IGNORECASE | re.DOTALL)


def format_pool_event(event: dict) -> str | None:
    """Format a pool event dict for Discord display.

    Returns formatted string or None if event type is not display-worthy.
    """
    event_type = event.get("type", "")
    job_id = event.get("job_id", "unknown")

    if event_type == "job_complete":
        preview = (event.get("result_preview", "") or "")[:300]
        cost = event.get("cost_usd", 0) or 0
        turns = event.get("num_turns", 0) or 0
        diff_stat = event.get("diff_stat", "")
        parts = [f"**Job Complete** `{job_id}`", f"Cost: ${cost:.4f} | Turns: {turns}"]
        if diff_stat:
            parts.append(f"```\n{diff_stat}\n```")
        if preview:
            parts.append(preview[:200])
        return "\n".join(parts)

    elif event_type == "job_failed":
        error = event.get("error", "Unknown error")
        return f"**Job Failed** `{job_id}`\n{error}"

    elif event_type == "job_blocked":
        question = event.get("question", "")
        return (
            f"**Job Needs Input** `{job_id}`\n{question}\n\n"
            f"*Reply: `@GRIM clarify {job_id} your answer`*"
        )

    elif event_type == "job_review":
        ws_id = event.get("workspace_id", "")
        changed = event.get("changed_files", [])
        diff_stat = event.get("diff_stat", "")
        parts = [f"**Job Ready for Review** `{job_id}`"]
        if ws_id:
            parts.append(f"Workspace: `{ws_id}`")
        if changed:
            parts.append(f"Files changed: {len(changed)}")
        if diff_stat:
            parts.append(f"```\n{diff_stat}\n```")
        return "\n".join(parts)

    elif event_type == "job_cancelled":
        return f"**Job Cancelled** `{job_id}`"

    return None


def parse_clarification(content: str) -> tuple[str, str] | None:
    """Parse a clarification reply from message content.

    Returns (job_id, answer) or None if not a clarify command.
    """
    match = CLARIFY_PATTERN.search(content)
    if match:
        return match.group(1), match.group(2).strip()
    return None


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


# ── Pool proxy MCP server ────────────────────────────────────────

def _build_pool_proxy_mcp_server():
    """Build in-process MCP server that proxies pool ops to the GRIM server REST API.

    Used by the Discord bot which has no direct pool access — it forwards
    pool_submit/status/list/cancel to http://grim:8080/api/pool/*.
    """
    from claude_agent_sdk import tool, create_sdk_mcp_server

    def _server_url():
        return os.environ.get("GRIM_SERVER_URL", "http://grim:8080")

    @tool(
        name="pool_submit",
        description="Submit a job to the execution pool. Returns a job ID.",
        input_schema={
            "type": "object",
            "properties": {
                "job_type": {
                    "type": "string",
                    "enum": ["code", "research", "audit", "plan"],
                    "description": "Type of agent to execute the job",
                },
                "instructions": {
                    "type": "string",
                    "description": "What the agent should do",
                },
                "priority": {
                    "type": "string",
                    "enum": ["critical", "high", "normal", "low", "background"],
                    "description": "Job priority (default: normal)",
                },
                "target_repo": {
                    "type": "string",
                    "description": "Target repo (e.g. 'GRIM', 'dawn-field-theory'). Agent gets an isolated git worktree.",
                },
            },
            "required": ["job_type", "instructions"],
        },
    )
    async def pool_submit(args):
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{_server_url()}/api/pool/jobs",
                    json={
                        "job_type": args["job_type"],
                        "instructions": args["instructions"],
                        "priority": args.get("priority", "normal"),
                        "target_repo": args.get("target_repo"),
                    },
                )
                data = resp.json()
                if resp.status_code == 200:
                    return {"content": [{"type": "text", "text": f"Job submitted: {data['job_id']} (type={args['job_type']}, priority={args.get('priority', 'normal')})"}]}
                return {"content": [{"type": "text", "text": f"[ERROR] {data.get('error', 'Unknown error')}"}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"[ERROR] Pool server unreachable: {e}"}]}

    @tool(
        name="pool_status",
        description="Get execution pool status — slot states and active jobs.",
        input_schema={"type": "object", "properties": {}},
    )
    async def pool_status(args):
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{_server_url()}/api/pool/status")
                data = resp.json()
                if resp.status_code != 200:
                    return {"content": [{"type": "text", "text": f"[ERROR] {data.get('error', 'Unknown')}"}]}
                lines = [f"Pool running: {data.get('running', '?')}", f"Active jobs: {data.get('active_jobs', '?')}", ""]
                for slot in data.get("slots", []):
                    state = f"BUSY (job: {slot.get('current_job_id', '?')})" if slot.get("busy") else "IDLE"
                    lines.append(f"  {slot.get('slot_id', '?')}: {state}")
                return {"content": [{"type": "text", "text": "\n".join(lines)}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"[ERROR] Pool server unreachable: {e}"}]}

    @tool(
        name="pool_list_jobs",
        description="List jobs in the execution pool queue.",
        input_schema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["queued", "running", "complete", "failed", "cancelled"],
                    "description": "Filter by status (optional)",
                },
            },
        },
    )
    async def pool_list_jobs(args):
        import httpx
        try:
            params = {}
            if args.get("status"):
                params["status"] = args["status"]
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{_server_url()}/api/pool/jobs", params=params)
                if resp.status_code != 200:
                    data = resp.json()
                    return {"content": [{"type": "text", "text": f"[ERROR] {data.get('error', 'Unknown')}"}]}
                jobs = resp.json()
                if not jobs:
                    return {"content": [{"type": "text", "text": "No jobs found."}]}
                lines = []
                for j in jobs:
                    lines.append(f"{j.get('id', '?')}  {j.get('job_type', '?'):<10} {j.get('status', '?'):<10} {j.get('priority', '?'):<10} {j.get('instructions', '')[:60]}")
                return {"content": [{"type": "text", "text": "\n".join(lines)}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"[ERROR] Pool server unreachable: {e}"}]}

    @tool(
        name="pool_cancel",
        description="Cancel a queued or blocked pool job.",
        input_schema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID to cancel"},
            },
            "required": ["job_id"],
        },
    )
    async def pool_cancel(args):
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{_server_url()}/api/pool/jobs/{args['job_id']}/cancel")
                data = resp.json()
                if resp.status_code == 200:
                    return {"content": [{"type": "text", "text": f"Job {args['job_id']} cancelled."}]}
                return {"content": [{"type": "text", "text": f"[ERROR] {data.get('error', 'Unknown')}"}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"[ERROR] Pool server unreachable: {e}"}]}

    return create_sdk_mcp_server(
        name="pool",
        version="0.1.0",
        tools=[pool_submit, pool_status, pool_list_jobs, pool_cancel],
    )


# ── Health check server ──────────────────────────────────────────

async def run_health_server(
    bot: GrimDiscordBot,
    discord_client: Any = None,
    port: int = 8081,
) -> asyncio.AbstractServer:
    """Start HTTP server for health checks + internal Discord API.

    Endpoints:
        GET  /health         — container health check
        GET  /api/channels   — list channels the bot can see
        POST /api/send       — send a message to a Discord channel
    """

    async def handle_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        # Read the request line
        request_line = await reader.readline()
        # Read headers
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            decoded = line.decode().strip()
            if ":" in decoded:
                key, val = decoded.split(":", 1)
                headers[key.strip().lower()] = val.strip()

        method_path = request_line.decode().split(" ") if request_line else ["GET", "/"]
        method = method_path[0] if len(method_path) > 0 else "GET"
        path = method_path[1] if len(method_path) > 1 else "/"

        # Read body if present
        body_bytes = b""
        content_length = int(headers.get("content-length", "0"))
        if content_length > 0:
            body_bytes = await reader.readexactly(content_length)

        if path == "/health":
            body = json.dumps({"status": "ok", **bot.metrics})
            response = _json_response(200, body)

        elif path == "/api/channels" and method == "GET":
            response = _handle_channels(discord_client)

        elif path == "/api/send" and method == "POST":
            response = await _handle_send(discord_client, body_bytes)

        else:
            response = "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"

        writer.write(response.encode())
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle_request, "0.0.0.0", port)
    logger.info("Health check server on port %d", port)
    return server


def _json_response(status: int, body: str) -> str:
    phrase = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Internal Server Error"}.get(status, "OK")
    return (
        f"HTTP/1.1 {status} {phrase}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body.encode())}\r\n"
        f"\r\n{body}"
    )


def _handle_channels(discord_client: Any) -> str:
    """List text channels visible to the bot."""
    if discord_client is None:
        return _json_response(500, json.dumps({"error": "Discord client not available"}))
    channels = []
    for guild in discord_client.guilds:
        for ch in guild.text_channels:
            channels.append({
                "id": str(ch.id),
                "name": ch.name,
                "guild": guild.name,
                "guild_id": str(guild.id),
            })
    return _json_response(200, json.dumps({"channels": channels}))


async def _handle_send(discord_client: Any, body_bytes: bytes) -> str:
    """Send a message to a Discord channel."""
    if discord_client is None:
        return _json_response(500, json.dumps({"error": "Discord client not available"}))
    try:
        payload = json.loads(body_bytes)
    except (json.JSONDecodeError, ValueError):
        return _json_response(400, json.dumps({"error": "Invalid JSON"}))

    channel_id = payload.get("channel_id")
    message = payload.get("message", "")
    if not channel_id or not message:
        return _json_response(400, json.dumps({"error": "channel_id and message required"}))

    try:
        channel = discord_client.get_channel(int(channel_id))
        if channel is None:
            return _json_response(404, json.dumps({"error": f"Channel {channel_id} not found"}))
        await channel.send(message[:DISCORD_MAX_CHARS])
        return _json_response(200, json.dumps({"ok": True, "channel_id": str(channel_id)}))
    except Exception as e:
        logger.error("Failed to send Discord message: %s", e)
        return _json_response(500, json.dumps({"error": str(e)}))


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

    # Pool event WebSocket listener
    pool_ws_task: asyncio.Task | None = None

    async def _pool_event_listener():
        """Connect to GRIM server's /ws-pool and route events to channels."""
        import aiohttp  # imported lazily — only needed when pool is active

        server_url = os.environ.get("GRIM_SERVER_URL", "http://localhost:8080")
        ws_url = server_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url += "/ws-pool"

        backoff = 1.0
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url) as ws:
                        logger.info("Connected to pool WebSocket: %s", ws_url)
                        backoff = 1.0  # reset on success
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)
                                    result = await bot.handle_pool_event(data)
                                    if result:
                                        for ch_id, text in result.items():
                                            channel = dc.get_channel(ch_id)
                                            if channel:
                                                await channel.send(text)
                                except Exception:
                                    logger.exception("Error processing pool event")
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning("Pool WebSocket disconnected, retrying in %.0fs", backoff)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    @dc.event
    async def on_ready():
        nonlocal health_server, pool_ws_task
        logger.info("GRIM Discord bot ready: %s (owner: %s)", dc.user, owner_user_id)
        if guild_ids:
            logger.info("Allowed guilds: %s", guild_ids)
        if channel_ids:
            logger.info("Allowed channels: %s", channel_ids)
        # Initialize conversation store for persistence
        await bot.init_store()
        # Start health check + internal API server
        health_server = await run_health_server(bot, discord_client=dc, port=health_port)
        # Start pool event listener
        if pool_ws_task is None or pool_ws_task.done():
            pool_ws_task = asyncio.create_task(_pool_event_listener())
            logger.info("Pool event listener started")

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

        # Check for clarification pattern: "clarify <job_id> <answer>"
        clarify = parse_clarification(content)
        if clarify:
            job_id, answer = clarify
            server_url = os.environ.get("GRIM_SERVER_URL", "http://localhost:8080")
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{server_url}/api/pool/jobs/{job_id}/clarify",
                        json={"answer": answer},
                    )
                    if resp.status_code == 200:
                        await message.reply(
                            f"Clarification sent for `{job_id}`.", mention_author=False,
                        )
                    else:
                        await message.reply(
                            f"Failed to send clarification: {resp.text}", mention_author=False,
                        )
            except Exception as e:
                await message.reply(
                    f"Error sending clarification: {e}", mention_author=False,
                )
            return

        # React to acknowledge receipt
        try:
            await message.add_reaction("\U0001f9d0")  # monocle face — thinking
        except Exception:
            pass

        try:
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
        except Exception as e:
            logger.error("on_message crash: %s", e, exc_info=True)
            chunks = ["Something went wrong — try again in a moment."]

        # Replace thinking reaction with result
        try:
            await message.remove_reaction("\U0001f9d0", dc.user)
            if chunks and not chunks[0].startswith(("Something went wrong", "That took too long", "Slow down")):
                await message.add_reaction("\u2705")  # checkmark
            else:
                await message.add_reaction("\u26a0\ufe0f")  # warning
        except Exception:
            pass

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
