"""Keyword-based delegation routing.

Fallback routing layer — used when no skill consumer or continuity
signal matches. Maps substring patterns in user messages to delegation
targets.

NOTE: The dispatch pipeline is removed — all routes go through companion
nodes now. These keywords still influence delegation_type for logging
and continuity, but the graph routes everything to companion.
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
    "operate": [
        # Code operations
        "write code", "implement", "create file",
        "fix this code", "refactor", "add a test",
        "write a function", "write a class", "edit the code",
        "modify the file", "update the code", "debug this",
        "write a script", "code this", "code me",
        "build this", "build me", "build a ",
        "write a program", "write a server", "write a webserver",
        "write a web server", "write a tool", "write a module",
        "write a library", "write a package", "write a test",
        "write a bot", "write a cli", "write a plugin",
        "write a parser", "write a wrapper", "write an api",
        "write me a", "create a program", "create a server",
        "create a webserver", "create a web server", "create a tool",
        "create a module", "create a bot", "create a cli",
        "make a server", "make a webserver", "make a web server",
        "make a program", "make a bot", "make a tool",
        "set up a server", "set up a webserver", "setup a server",
        "spin up a", "scaffold a", "scaffold me",
        "program that", "server that", "app that",
        # Shell / command execution
        "run command", "run this", "run pytest", "run test",
        "execute this",
        "shell", "powershell", "bash", "terminal",
        "echo ", "mkdir", "ls ", "dir ", "pwd",
        "curl ", "wget ",
        # Git operations
        "git status", "git log", "git diff", "git pull",
        "git push", "commit", "push to github",
        # File operations
        "list files", "show me the directory", "what files",
        "read the file", "show me the file", "cat ",
        # HTTP execution
        "http request", "fetch ", "call the api",
        "check the weather", "hit the endpoint",
        "make a request",
        # Ops / deployment
        "upload to zenodo", "deploy", "test execution",
        # Network / system queries
        "ip address", "my ip", "what is my ip",
        "ping ", "traceroute", "nslookup", "dig ",
        "ifconfig", "ipconfig", "hostname",
        "netstat", "who am i", "whoami",
        "uname", "uptime",
        "which ", "where ",
        "what os", "what operating system",
        "system info", "disk space", "memory usage",
        # Infrastructure awareness
        "check the status", "sync vault",
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
        Delegation type (e.g., "memory", "operate") or None.
    """
    for delegation_type, keywords in DELEGATION_KEYWORDS.items():
        for keyword in keywords:
            if keyword in message:
                return delegation_type
    return None


def match_action_intent(message: str) -> str | None:
    """Match action-intent patterns (verb + target).

    Catches requests like "run the command" that miss specific keywords.
    Routes to operate — action-intent means execution.

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
