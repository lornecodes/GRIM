"""
Utility functions — math helpers and data transforms.

Generic utilities with no domain-specific knowledge.
Tests that the actualize agent doesn't over-interpret simple code.
"""

from typing import List, Optional
import hashlib
import json


def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    return text.lower().strip().replace(" ", "-").replace("_", "-")


def sha256(content: str) -> str:
    """SHA-256 hash of string content."""
    return hashlib.sha256(content.encode()).hexdigest()


def chunk_list(items: List, size: int) -> List[List]:
    """Split a list into chunks of given size."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def safe_json_loads(text: str, default=None):
    """Parse JSON with fallback."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a value between lo and hi."""
    return max(lo, min(hi, value))


def moving_average(values: List[float], window: int = 5) -> List[float]:
    """Simple moving average."""
    if len(values) < window:
        return values
    result = []
    for i in range(len(values) - window + 1):
        avg = sum(values[i:i + window]) / window
        result.append(avg)
    return result
