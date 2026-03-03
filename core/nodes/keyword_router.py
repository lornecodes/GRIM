"""Keyword-based delegation routing.

Fallback routing layer — used when no skill consumer or continuity
signal matches. Maps substring patterns in user messages to delegation
targets.

v0.0.6 boundaries:
  - GRIM = management layer (plan, manage, orchestrate, review)
  - IronClaw = execution layer (code writes, shell, testing, file ops)
  - "code" delegation removed — all code ops route to "ironclaw"
  - "operate" narrowed to git reads + file reads — execution → "ironclaw"
  - "planning" added for task breakdown and board management
"""
from __future__ import annotations

# Fallback keywords for delegation when no skill consumer matches.
# Uses substring matching (keyword in message.lower()), so keep terms
# short and atomic — "echo" matches "do an echo command in powershell".
DELEGATION_KEYWORDS: dict[str, list[str]] = {
    "memory": [
        "capture this", "remember this", "save this",
        "promote", "organize vault", "triage inbox",
        "connect these", "relate these", "link these",
        "review vault", "vault health",
        "store this", "add to vault", "update the vault",
        "create an fdo", "new knowledge entry",
        # Task management (write operations)
        "create a story", "create a task", "add a story", "add a task",
        "move to active", "move to in progress", "move to resolved", "move to closed",
        "update the story", "update the task", "close this story",
        "archive closed", "sync calendar", "add calendar event",
    ],
    # NOTE: "planning" removed from delegation keywords — planning is now a
    # graph-level branch handled by graph_router.py, not a delegation target.
    "research": [
        "analyze this", "ingest", "summarize this paper",
        "deep dive", "review this document",
        "research this", "look into this", "investigate",
        "what does the literature say", "find papers on",
        "summarize this", "break this down",
    ],
    "ironclaw": [
        # Direct IronClaw references
        "ironclaw", "iron claw", "iron-claw",
        "use the engine", "engine agent",
        # Code operations (was "code" delegation — all go to IronClaw)
        "write code", "implement", "create file",
        "fix this code", "refactor", "add a test",
        "write a function", "write a class", "edit the code",
        "modify the file", "update the code", "debug this",
        "write a script", "code this", "code me",
        "build this", "build me", "build a ",
        # Shell / command execution (was "operate")
        "run command", "run this", "run pytest", "run test",
        "execute this",
        "shell", "powershell", "bash", "terminal",
        "echo ", "mkdir", "ls ", "dir ", "pwd",
        "curl ", "wget ",
        # Git write operations (was "operate")
        "git push", "commit", "push to github",
        # HTTP execution (was "operate")
        "http request", "fetch ", "call the api",
        "check the weather", "hit the endpoint",
        "make a request",
        # Ops / deployment (was "operate")
        "upload to zenodo", "deploy", "test execution",
        # Network / system queries (was "operate")
        "ip address", "my ip", "what is my ip",
        "ping ", "traceroute", "nslookup", "dig ",
        "ifconfig", "ipconfig", "hostname",
        "netstat", "who am i", "whoami",
        "uname", "uptime",
        "which ", "where ",
        "what os", "what operating system",
        "system info", "disk space", "memory usage",
        # Sandboxed execution
        "run sandboxed", "execute safely", "isolated shell",
        "sandboxed execution", "run in sandbox",
        "secure execute", "run securely",
        "run this safely", "execute in sandbox",
        "run isolated", "safe execution",
        # Agent dispatch
        "dispatch agent", "agent workflow", "multi-agent",
        "dispatch workflow",
        # Security scanning
        "security scan", "scan for vulnerabilities", "security audit",
        "security analysis", "vulnerability scan",
        "scan this code", "audit this code",
        # Container/Docker tasks
        "container analysis", "docker analysis",
        "analyze the container", "inspect the container",
    ],
    "operate": [
        # Git reads only (narrowed — no writes, no shell)
        "git status", "git log", "git diff", "git pull",
        # File reads only
        "list files", "show me the directory", "what files",
        "read the file", "show me the file", "cat ",
        # Infrastructure awareness
        "check the status", "sync vault",
    ],
    "audit": [
        "review staging", "audit output", "check staged",
        "staging review", "review the output",
        "audit the files", "review execution output",
    ],
    "codebase": [
        "look at the code", "check the code", "show me the code",
        "repo structure", "codebase", "code structure",
        "how does this module work", "explain the architecture",
        "where is the code for", "find the source", "source code",
        "navigate the repo", "browse the code", "code architecture",
        "what changed in", "recent changes to",
        "trace through", "walk me through the code",
        "what does the code do", "how does this work in code",
        "meta.yaml", "directory structure",
        "index the repo", "deep index",
    ],
}

# Action-intent detection — catch action requests that miss keyword lists
_ACTION_VERBS = [
    "run", "execute", "check", "test", "ping",
    "show me", "get me", "tell me my",
]
_ACTION_TARGETS = [
    "command", "shell", "ip", "system", "server",
    "network", "file", "directory", "process",
    "port", "dns", "connection", "cli", "terminal",
    "bash", "tool", "access", "capability",
]

# Continuity follow-up signals
_FOLLOW_UP_SIGNALS = [
    "now ", "also ", "next ", "again", "another",
    "same thing", "do it", "try ", "what about",
    "how about", "can you also", "one more",
    "run that", "do that", "test that",
    "don't you", "dont you", "can't you", "cant you",
    "just use", "use the same", "you just",
    "why didn't", "why didnt", "why can't", "why cant",
    "i just asked", "you have", "you already",
]


def match_keywords(message: str) -> str | None:
    """Match a message against delegation keywords.

    Args:
        message: Lowercased user message.

    Returns:
        Delegation type (e.g., "memory", "code") or None.
    """
    for delegation_type, keywords in DELEGATION_KEYWORDS.items():
        for keyword in keywords:
            if keyword in message:
                return delegation_type
    return None


def match_action_intent(message: str) -> str | None:
    """Match action-intent patterns (verb + target).

    Catches requests like "run the command" that miss specific keywords.
    Routes to ironclaw — action-intent means execution.

    Args:
        message: Lowercased user message.

    Returns:
        "ironclaw" if matched, else None.
    """
    for verb in _ACTION_VERBS:
        if verb in message and any(t in message for t in _ACTION_TARGETS):
            return "ironclaw"
    return None


def is_follow_up(message: str) -> bool:
    """Check if a message looks like a follow-up to a previous delegation.

    Args:
        message: Lowercased user message.
    """
    return any(sig in message for sig in _FOLLOW_UP_SIGNALS)
