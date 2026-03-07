"""Domain-level write guards + caller-based ACL for Kronos FDOs.

Protected domains (physics, modelling, etc.) require owner-level caller.
Pool agents get read access to everything but can only write to open domains.
Writes to protected domains return an approval_required payload that routes
through the pool clarification → Discord approval flow.
"""
from __future__ import annotations

PROTECTED_DOMAINS = frozenset({
    "physics",      # DFT experiments, core research
    "modelling",    # DFT models, mathematical derivations
    "personal",     # Private notes
    "journal",      # Personal journal entries
    "decisions",    # ADRs — architectural decisions need review
})

# Caller permission matrix
# read/write values: "*" = all domains, "open_only" = non-protected, None = blocked
CALLER_PERMISSIONS: dict[str, dict[str, str | None]] = {
    "peter":   {"read": "*",         "write": "*"},          # Owner — full access
    "pool":    {"read": "*",         "write": "open_only"},  # Pool agents — read all, write open only
    "discord": {"read": "open_only", "write": None},         # Discord guests — read open, no writes
}

DEFAULT_CALLER = "peter"  # Backward compat — unidentified callers get owner access


def can_write(caller_id: str, domain: str) -> bool:
    """Check if a caller can write to a domain."""
    perms = CALLER_PERMISSIONS.get(caller_id, CALLER_PERMISSIONS[DEFAULT_CALLER])
    w = perms["write"]
    if w == "*":
        return True
    if w == "open_only":
        return domain not in PROTECTED_DOMAINS
    return False


def can_read(caller_id: str, domain: str) -> bool:
    """Check if a caller can read from a domain."""
    perms = CALLER_PERMISSIONS.get(caller_id, CALLER_PERMISSIONS[DEFAULT_CALLER])
    r = perms["read"]
    if r == "*":
        return True
    if r == "open_only":
        return domain not in PROTECTED_DOMAINS
    return False


def is_protected(domain: str) -> bool:
    """Check if a domain is protected."""
    return domain in PROTECTED_DOMAINS
