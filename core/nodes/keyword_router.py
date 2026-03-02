"""Keyword-based delegation routing.

Fallback routing layer — used when no skill consumer or continuity
signal matches. Maps substring patterns in user messages to delegation
targets.
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
        "plan sprint", "groom backlog",
    ],
    "code": [
        "write code", "implement", "create file",
        "fix this code", "refactor", "add a test",
        "write a function", "write a class", "edit the code",
        "modify the file", "update the code", "debug this",
        "write a script", "code this", "build this",
    ],
    "research": [
        "analyze this", "ingest", "summarize this paper",
        "deep dive", "review this document",
        "research this", "look into this", "investigate",
        "what does the literature say", "find papers on",
        "summarize this", "break this down",
    ],
    "ironclaw": [
        "run sandboxed", "execute safely", "isolated shell",
        "sandboxed execution", "run in sandbox",
        "secure execute", "run securely",
        "run this safely", "execute in sandbox",
        "run isolated", "safe execution",
    ],
    "operate": [
        # Shell / commands
        "run command", "run this", "execute this",
        "shell", "powershell", "bash", "terminal",
        "echo ", "mkdir", "ls ", "dir ", "pwd",
        "curl ", "wget ",
        # Git
        "git status", "git log", "git diff", "git pull",
        "git push", "commit", "push to github",
        # Files
        "list files", "show me the directory", "what files",
        "read the file", "show me the file", "cat ",
        # HTTP
        "http request", "fetch ", "call the api",
        "check the weather", "hit the endpoint",
        "make a request",
        # Ops
        "upload to zenodo", "sync vault", "deploy",
        "check the status", "test execution",
        # Network / system queries
        "ip address", "my ip", "what is my ip",
        "ping ", "traceroute", "nslookup", "dig ",
        "ifconfig", "ipconfig", "hostname",
        "netstat", "who am i", "whoami",
        "uname", "uptime",
        "which ", "where ",
        "what os", "what operating system",
        "system info", "disk space", "memory usage",
    ],
    "audit": [
        "review staging", "audit output", "check staged",
        "staging review", "review the output",
        "audit the files", "review execution output",
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

    Args:
        message: Lowercased user message.

    Returns:
        "operate" if matched, else None.
    """
    for verb in _ACTION_VERBS:
        if verb in message and any(t in message for t in _ACTION_TARGETS):
            return "operate"
    return None


def is_follow_up(message: str) -> bool:
    """Check if a message looks like a follow-up to a previous delegation.

    Args:
        message: Lowercased user message.
    """
    return any(sig in message for sig in _FOLLOW_UP_SIGNALS)
