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
    skills_disabled: list[str] = field(default_factory=list)

    # Models — disabled tiers (e.g. ["opus"] to block expensive tier)
    models_disabled: list[str] = field(default_factory=list)

    # Agents — disabled agent IDs (e.g. ["code"] to disable code execution)
    agents_disabled: list[str] = field(default_factory=lambda: ["code"])

    # Evolution
    evolution_dir: Path = field(default_factory=lambda: Path("local/evolution"))
    evolution_frequency: str = "per_session"

    # Context window management
    context_max_tokens: int = 160_000  # trigger compression above this
    context_keep_recent: int = 12  # keep last N messages (6 turns) intact

    # Persistent objectives
    objectives_path: Path = field(default_factory=lambda: Path("local/objectives"))
    objectives_max_active: int = 10

    # Model routing
    routing_enabled: bool = True
    routing_default_tier: str = "sonnet"
    routing_classifier_enabled: bool = False  # enable after calibration
    routing_confidence_threshold: float = 0.6
    routing_timeout: float = 3.0  # intent classifier LLM call timeout (seconds)
    use_companion_router: bool = False  # v0.10: LLM-backed companion router (replaces keyword router)

    # Codebase — workspace-level repo awareness
    workspace_root: Path = field(default_factory=lambda: Path(".."))
    repos_manifest: str = "repos.yaml"  # relative to workspace_root

    # Execution Pool (Project Charizard)
    pool_enabled: bool = False
    pool_num_slots: int = 2
    pool_poll_interval: float = 2.0
    pool_db_path: Path = field(default_factory=lambda: Path("local/pool.db"))
    pool_max_turns_per_job: int = 20
    pool_job_timeout_secs: int = 300
    pool_discord_webhook_url: str = ""

    # Management Daemon (Project Mewtwo)
    daemon_enabled: bool = False
    daemon_poll_interval: float = 30.0          # seconds between scan cycles
    daemon_max_concurrent_jobs: int = 1         # max stories dispatched at once
    daemon_project_filter: list[str] = field(default_factory=list)  # limit to specific proj-* IDs
    daemon_auto_dispatch: bool = True           # auto-dispatch READY stories
    daemon_db_path: Path = field(default_factory=lambda: Path("local/daemon.db"))

    # Redis (optional — for reasoning cache)
    redis_url: str = ""

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
    cfg.objectives_path = _resolve(cfg.objectives_path, grim_root)
    cfg.workspace_root = _resolve(cfg.workspace_root, grim_root)
    cfg.pool_db_path = _resolve(cfg.pool_db_path, grim_root)
    cfg.daemon_db_path = _resolve(cfg.daemon_db_path, grim_root)

    # Redis URL
    redis_override = os.getenv("GRIM_REDIS_URL", os.getenv("KRONOS_REDIS_URL", ""))
    if redis_override:
        cfg.redis_url = redis_override

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
    cfg.skills_disabled = skills.get("disabled", cfg.skills_disabled)

    # Models
    models = raw.get("models", {})
    cfg.models_disabled = models.get("disabled", cfg.models_disabled)

    # Agents
    agents = raw.get("agents", {})
    cfg.agents_disabled = agents.get("disabled", cfg.agents_disabled)

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

    # Routing
    routing = raw.get("routing", {})
    if "enabled" in routing:
        cfg.routing_enabled = routing["enabled"]
    if "default_tier" in routing:
        cfg.routing_default_tier = routing["default_tier"]
    if "classifier_enabled" in routing:
        cfg.routing_classifier_enabled = routing["classifier_enabled"]
    if "confidence_threshold" in routing:
        cfg.routing_confidence_threshold = routing["confidence_threshold"]

    # Context management
    ctx = raw.get("context_management", {})
    if "max_tokens" in ctx:
        cfg.context_max_tokens = ctx["max_tokens"]
    if "keep_recent" in ctx:
        cfg.context_keep_recent = ctx["keep_recent"]

    # Codebase
    codebase = raw.get("codebase", {})
    if "workspace_root" in codebase:
        cfg.workspace_root = Path(codebase["workspace_root"])
    if "repos_manifest" in codebase:
        cfg.repos_manifest = codebase["repos_manifest"]

    # Objectives
    objectives = raw.get("objectives", {})
    if "path" in objectives:
        cfg.objectives_path = Path(objectives["path"])
    if "max_active" in objectives:
        cfg.objectives_max_active = objectives["max_active"]

    # Execution Pool (Project Charizard)
    pool = raw.get("pool", {})
    if "enabled" in pool:
        cfg.pool_enabled = pool["enabled"]
    if "num_slots" in pool:
        cfg.pool_num_slots = pool["num_slots"]
    if "poll_interval" in pool:
        cfg.pool_poll_interval = pool["poll_interval"]
    if "db_path" in pool:
        cfg.pool_db_path = Path(pool["db_path"])
    if "max_turns_per_job" in pool:
        cfg.pool_max_turns_per_job = pool["max_turns_per_job"]
    if "job_timeout_secs" in pool:
        cfg.pool_job_timeout_secs = pool["job_timeout_secs"]

    # Management Daemon (Project Mewtwo)
    daemon = raw.get("daemon", {})
    if "enabled" in daemon:
        cfg.daemon_enabled = daemon["enabled"]
    if "poll_interval" in daemon:
        cfg.daemon_poll_interval = daemon["poll_interval"]
    if "max_concurrent_jobs" in daemon:
        cfg.daemon_max_concurrent_jobs = daemon["max_concurrent_jobs"]
    if "project_filter" in daemon:
        cfg.daemon_project_filter = daemon["project_filter"]
    if "auto_dispatch" in daemon:
        cfg.daemon_auto_dispatch = daemon["auto_dispatch"]
    if "db_path" in daemon:
        cfg.daemon_db_path = Path(daemon["db_path"])


def save_config_updates(updates: dict, grim_root: Path | None = None) -> GrimConfig:
    """Apply partial updates to grim.yaml and reload config.

    Only updates keys that are present in the updates dict.
    Returns the newly loaded config.
    """
    if grim_root is None:
        grim_root = Path(__file__).resolve().parent.parent

    config_path = grim_root / "config" / "grim.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    # Apply updates — map flat API keys to nested YAML structure
    if "env" in updates:
        raw["env"] = updates["env"]
    if "model" in updates:
        raw.setdefault("agent", {})["default_model"] = updates["model"]
    if "temperature" in updates:
        raw.setdefault("agent", {})["temperature"] = updates["temperature"]
    if "max_tokens" in updates:
        raw.setdefault("agent", {})["max_tokens"] = updates["max_tokens"]
    if "vault_path" in updates:
        raw["vault_path"] = updates["vault_path"]

    # Routing updates
    if "routing" in updates and isinstance(updates["routing"], dict):
        raw.setdefault("routing", {})
        for k, v in updates["routing"].items():
            raw["routing"][k] = v

    # Context updates
    if "context" in updates and isinstance(updates["context"], dict):
        raw.setdefault("context_management", {})
        for k, v in updates["context"].items():
            raw["context_management"][k] = v

    # Skills updates
    if "skills" in updates and isinstance(updates["skills"], dict):
        raw.setdefault("skills", {})
        for k, v in updates["skills"].items():
            raw["skills"][k] = v

    # Objectives
    if "objectives_max_active" in updates:
        raw.setdefault("objectives", {})["max_active"] = updates["objectives_max_active"]

    # Models (disabled list)
    if "models" in updates and isinstance(updates["models"], dict):
        raw.setdefault("models", {})
        for k, v in updates["models"].items():
            raw["models"][k] = v

    # Agents (disabled list)
    if "agents" in updates and isinstance(updates["agents"], dict):
        raw.setdefault("agents", {})
        for k, v in updates["agents"].items():
            raw["agents"][k] = v

    # Write back
    config_path.write_text(
        yaml.dump(raw, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    # Reload and return
    return load_config(config_path=config_path, grim_root=grim_root)


def _resolve(p: Path, root: Path) -> Path:
    """Resolve a path against root if it's relative."""
    if p.is_absolute():
        return p
    return (root / p).resolve()
