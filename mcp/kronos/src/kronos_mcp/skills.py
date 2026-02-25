"""
Skills Engine — discovers and serves GRIM skill protocols.

Reads skill manifests and protocols from the skills directory,
making them available to any AI agent via MCP.
"""

from __future__ import annotations

import yaml
from pathlib import Path
from dataclasses import dataclass
from typing import Any


@dataclass
class Skill:
    """A GRIM skill — instruction protocol for AI agents."""

    name: str
    version: str
    description: str
    skill_type: str
    entry_point: str
    protocol: str  # Full protocol text
    manifest: dict[str, Any]  # Raw manifest
    path: str  # Directory path

    @property
    def phases(self) -> list[dict]:
        raw = self.manifest.get("phases", [])
        if isinstance(raw, dict):
            # Format: {key: description} — convert to list of dicts
            return [{"name": k, "description": v} for k, v in raw.items()]
        if isinstance(raw, list):
            # Could be list of dicts or list of strings
            result = []
            for item in raw:
                if isinstance(item, dict):
                    result.append(item)
                elif isinstance(item, str):
                    result.append({"name": item, "description": ""})
                else:
                    result.append({"name": str(item), "description": ""})
            return result
        return []

    @property
    def permissions(self) -> list[str]:
        return self.manifest.get("permissions", [])

    @property
    def quality_gates(self) -> dict:
        return self.manifest.get("quality_gates", {})

    def summary(self) -> dict[str, Any]:
        """Concise summary for listing."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "type": self.skill_type,
            "phases": [p.get("name", "unnamed") for p in self.phases],
            "permissions": self.permissions,
        }


class SkillsEngine:
    """Discovers and serves GRIM skills."""

    def __init__(self, skills_path: str):
        self.skills_path = Path(skills_path)
        self._skills: dict[str, Skill] | None = None

    def _discover(self) -> dict[str, Skill]:
        """Scan skills directory for manifests."""
        skills: dict[str, Skill] = {}

        if not self.skills_path.is_dir():
            return skills

        for child in self.skills_path.iterdir():
            if not child.is_dir():
                continue

            manifest_path = child / "manifest.yaml"
            if not manifest_path.exists():
                continue

            try:
                manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            if not isinstance(manifest, dict) or "name" not in manifest:
                continue

            # Read protocol/entry point
            entry = manifest.get("entry_point", "protocol.md")
            protocol_path = child / entry
            protocol = ""
            if protocol_path.exists():
                try:
                    protocol = protocol_path.read_text(encoding="utf-8")
                except Exception:
                    protocol = f"[Error reading {entry}]"

            skills[manifest["name"]] = Skill(
                name=manifest["name"],
                version=manifest.get("version", "0.0"),
                description=manifest.get("description", ""),
                skill_type=manifest.get("type", "unknown"),
                entry_point=entry,
                protocol=protocol,
                manifest=manifest,
                path=str(child),
            )

        return skills

    @property
    def skills(self) -> dict[str, Skill]:
        if self._skills is None:
            self._skills = self._discover()
        return self._skills

    def refresh(self):
        self._skills = None

    def list_skills(self) -> list[dict[str, Any]]:
        """List all available skills with summaries."""
        return [s.summary() for s in self.skills.values()]

    def get_skill(self, name: str) -> Skill | None:
        """Get a skill by name."""
        return self.skills.get(name)

    def get_protocol(self, name: str) -> str | None:
        """Get just the protocol text for a skill."""
        skill = self.get_skill(name)
        return skill.protocol if skill else None
