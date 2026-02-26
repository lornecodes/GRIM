"""Skill registry — in-memory store of loaded skills."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SkillConsumer:
    """A consumer declaration from a skill manifest."""

    name: str  # e.g. "grim", "memory-agent", "coder-agent"
    role: str  # "recognition" | "execution" | "delegation"
    description: str = ""
    reads: list[str] = field(default_factory=list)


@dataclass
class Skill:
    """A loaded skill with its manifest metadata and protocol content."""

    name: str
    version: str
    description: str
    protocol: str  # Full protocol.md content
    entry_point: str = "protocol.md"
    skill_type: str = "instruction-protocol"
    permissions: list[str] = field(default_factory=list)
    triggers: dict[str, list[str]] = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    consumers: list[SkillConsumer] = field(default_factory=list)
    quality_gates: list[str] = field(default_factory=list)
    raw_manifest: dict = field(default_factory=dict)

    @property
    def requires_write(self) -> bool:
        """Does this skill require vault write access?"""
        return any("write" in p for p in self.permissions)

    @property
    def has_grim_consumer(self) -> bool:
        """Is GRIM a declared consumer of this skill?"""
        return any(c.name == "grim" for c in self.consumers)

    def consumer_for(self, agent_name: str) -> SkillConsumer | None:
        """Get the consumer declaration for a specific agent.

        Handles aliases: "code" matches "coder-agent", "operate" matches "ops-agent", etc.
        """
        aliases = _AGENT_ALIASES.get(agent_name, set())
        for c in self.consumers:
            # Direct match
            if c.name == agent_name:
                return c
            # Strip "-agent" suffix
            base_name = c.name.replace("-agent", "")
            if base_name == agent_name:
                return c
            # Alias match
            if c.name in aliases or base_name in aliases:
                return c
        return None

    def delegation_target(self) -> str | None:
        """Determine which agent should execute this skill.

        Looks at consumers with role=execution and returns the agent name.
        """
        for c in self.consumers:
            if c.role == "execution":
                # "memory-agent" → "memory", "coder-agent" → "code"
                return _consumer_to_delegation(c.name)
        return None


def _consumer_to_delegation(consumer_name: str) -> str:
    """Map a consumer name to a delegation type."""
    mapping = {
        "memory-agent": "memory",
        "coder-agent": "code",
        "research-agent": "research",
        "operator-agent": "operate",
        "ops-agent": "operate",
    }
    return mapping.get(consumer_name, consumer_name.replace("-agent", ""))


# Maps delegation type → set of consumer names that should match
_AGENT_ALIASES: dict[str, set[str]] = {
    "code": {"coder-agent", "coder"},
    "memory": {"memory-agent"},
    "research": {"research-agent"},
    "operate": {"operator-agent", "ops-agent", "ops"},
}


class SkillRegistry:
    """In-memory registry of all loaded skills.

    Populated at boot by the skill loader. Queried per-turn
    by the skill matcher.
    """

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """Register a skill in the registry."""
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        """Get a skill by name."""
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        """Return all registered skills."""
        return list(self._skills.values())

    def names(self) -> list[str]:
        """Return all skill names."""
        return list(self._skills.keys())

    def for_grim(self) -> list[Skill]:
        """Return skills that GRIM (the thinker) should be aware of.

        These are skills with a grim consumer or skills without any
        consumer declarations (backward compatible).
        """
        return [s for s in self._skills.values()
                if s.has_grim_consumer or not s.consumers]

    def for_agent(self, agent_name: str) -> list[Skill]:
        """Return skills that a specific agent can execute.

        Args:
            agent_name: Agent identifier (e.g. "memory", "code", "research")
        """
        result = []
        for s in self._skills.values():
            if s.consumer_for(agent_name):
                result.append(s)
        return result

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __repr__(self) -> str:
        return f"SkillRegistry({len(self._skills)} skills: {', '.join(self.names())})"
