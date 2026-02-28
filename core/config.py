"""GRIM configuration — load and resolve grim.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass
class GrimConfig:
    """Resolved runtime configuration for GRIM."""

    # Environment
    env: str = "debug"  # "production" | "debug"

    # Paths (resolved at load time)
    vault_path: Path = field(default_factory=lambda: Path("../kronos-vault"))
    skills_path: Path = field(default_factory=lambda: Path("skills"))
    identity_prompt_path: Path = field(default_factory=lambda: Path("identity/system_prompt.md"))
    identity_personality_path: Path = field(default_factory=lambda: Path("identity/personality.yaml"))
    personality_cache_path: Path = field(default_factory=lambda: Path("identity/personality.cache.md"))
    local_dir: Path = field(default_factory=lambda: Path("local"))

    # LLM
    model: str = "claude-sonnet-4-6"
    temperature: float = 0.7
    max_tokens: int = 4096

    # Kronos MCP
    kronos_mcp_command: str = "python"
    kronos_mcp_args: list[str] = field(default_factory=lambda: ["-m", "kronos_mcp"])

    # Persistence
    checkpoint_backend: str = "sqlite"
    checkpoint_path: Path = field(default_factory=lambda: Path("local/checkpoints.db"))

    # Skills
    skills_auto_load: bool = True
    skills_match_per_turn: bool = True

    # Evolution
    evolution_dir: Path = field(default_factory=lambda: Path("local/evolution"))
    evolution_frequency: str = "per_session"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_debug(self) -> bool:
        return self.env == "debug"


# ---------------------------------------------------------------------------
# Load from YAML
# ---------------------------------------------------------------------------

def load_config(config_path: Path | None = None, grim_root: Path | None = None) -> GrimConfig:
    """Load GRIM configuration from grim.yaml.

    Resolution order:
    1. Explicit config_path argument
    2. GRIM_CONFIG env var
    3. {grim_root}/config/grim.yaml
    4. ./config/grim.yaml (cwd)

    Environment variables:
    - GRIM_ENV: override env setting ("production" | "debug")
    - GRIM_VAULT_PATH: override vault path
    """
    if grim_root is None:
        grim_root = Path(__file__).resolve().parent.parent

    if config_path is None:
        env_path = os.getenv("GRIM_CONFIG")
        if env_path:
            config_path = Path(env_path)
        else:
            config_path = grim_root / "config" / "grim.yaml"

    cfg = GrimConfig()

    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        _apply_yaml(cfg, raw, grim_root)

    # Environment overrides
    env_override = os.getenv("GRIM_ENV")
    if env_override:
        cfg.env = env_override

    vault_override = os.getenv("GRIM_VAULT_PATH")
    if vault_override:
        cfg.vault_path = Path(vault_override)

    # Resolve relative paths against grim_root
    cfg.vault_path = _resolve(cfg.vault_path, grim_root)
    cfg.skills_path = _resolve(cfg.skills_path, grim_root)
    cfg.identity_prompt_path = _resolve(cfg.identity_prompt_path, grim_root)
    cfg.identity_personality_path = _resolve(cfg.identity_personality_path, grim_root)
    cfg.personality_cache_path = _resolve(cfg.personality_cache_path, grim_root)
    cfg.local_dir = _resolve(cfg.local_dir, grim_root)
    cfg.checkpoint_path = _resolve(cfg.checkpoint_path, grim_root)
    cfg.evolution_dir = _resolve(cfg.evolution_dir, grim_root)

    # Use test vault in debug mode if real vault doesn't exist
    if cfg.is_debug and not cfg.vault_path.exists():
        test_vault = grim_root / "tests" / "vault"
        if test_vault.exists():
            cfg.vault_path = test_vault

    return cfg


def _apply_yaml(cfg: GrimConfig, raw: dict, root: Path) -> None:
    """Apply raw YAML dict to config, handling nested keys."""
    # Environment
    cfg.env = raw.get("env", cfg.env)

    # Vault
    cfg.vault_path = Path(raw.get("vault_path", str(cfg.vault_path)))

    # Identity
    identity = raw.get("identity", {})
    if "system_prompt_path" in identity:
        cfg.identity_prompt_path = Path(identity["system_prompt_path"])
    if "personality_path" in identity:
        cfg.identity_personality_path = Path(identity["personality_path"])

    # Skills
    skills = raw.get("skills", {})
    if "path" in skills or "directory" in skills:
        cfg.skills_path = Path(skills.get("path", skills.get("directory", str(cfg.skills_path))))
    cfg.skills_auto_load = skills.get("auto_load", cfg.skills_auto_load)
    cfg.skills_match_per_turn = skills.get("match_per_turn", cfg.skills_match_per_turn)

    # Agent / LLM
    agent = raw.get("agent", {})
    cfg.model = agent.get("default_model", agent.get("model", cfg.model))
    cfg.temperature = agent.get("temperature", cfg.temperature)
    cfg.max_tokens = agent.get("max_tokens", cfg.max_tokens)

    # Kronos MCP
    mcp = raw.get("mcp_servers", {}).get("kronos", {})
    if mcp:
        cfg.kronos_mcp_command = mcp.get("command", cfg.kronos_mcp_command)
        cfg.kronos_mcp_args = mcp.get("args", cfg.kronos_mcp_args)

    # Persistence
    persistence = raw.get("persistence", {})
    cfg.checkpoint_backend = persistence.get("backend", cfg.checkpoint_backend)
    if "path" in persistence:
        cfg.checkpoint_path = Path(persistence["path"])

    # Evolution
    evolution = raw.get("evolution", {})
    if "snapshot_dir" in evolution:
        cfg.evolution_dir = Path(evolution["snapshot_dir"])
    cfg.evolution_frequency = evolution.get("snapshot_frequency", cfg.evolution_frequency)


def _resolve(p: Path, root: Path) -> Path:
    """Resolve a path against root if it's relative."""
    if p.is_absolute():
        return p
    return (root / p).resolve()
