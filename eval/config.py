"""Eval configuration — paths, thresholds, judge model."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


EVAL_ROOT = Path(__file__).parent
DATASETS_DIR = EVAL_ROOT / "datasets"
FIXTURES_DIR = EVAL_ROOT / "fixtures"
RESULTS_DIR = EVAL_ROOT / "results"


@dataclass
class EvalConfig:
    """Configuration for evaluation runs."""

    # Paths
    datasets_dir: Path = DATASETS_DIR
    fixtures_dir: Path = FIXTURES_DIR
    results_dir: Path = RESULTS_DIR

    # Tier 1 thresholds
    tier1_pass_threshold: float = 1.0  # 100% required

    # Tier 2 thresholds
    tier2_overall_threshold: float = 0.70
    tier2_dimension_min: float = 0.50

    # Tier 2 judge config
    judge_model: str = "claude-sonnet-4-6"
    judge_temperature: float = 0.1
    judge_max_tokens: int = 2048

    # Regression detection
    regression_tolerance: float = 0.05  # 5% drop = regression

    # Execution
    max_concurrent_cases: int = 4
    tier2_mode: str = "isolated"  # "isolated" or "full_graph"

    # GRIM config for agent instantiation
    grim_model: str = "claude-sonnet-4-6"
    vault_path: Path | None = None  # defaults to fixtures/vault

    # Skills path for skill matching tests
    skills_path: Path | None = None

    # --- Tier 3: Live integration ---
    tier3_docker_url: str = "http://localhost:8080"
    tier3_ws_url: str = "ws://localhost:8080"
    tier3_timeout_ms: int = 120_000  # 2 minutes per case
    tier3_sandbox: bool = True       # always sandbox by default
    tier3_max_concurrent: int = 1    # sequential by default (live server)

    # Tier 3 judge config
    tier3_judge_model: str = "claude-sonnet-4-6"

    # Tier 3 thresholds
    tier3_routing_threshold: float = 0.90   # 90% routing accuracy
    tier3_quality_threshold: float = 0.70
    tier3_domain_threshold: float = 0.80
    tier3_efficiency_threshold: float = 0.60

    # Tier 3 efficiency baselines (populated after first run)
    tier3_baselines_path: Path | None = None

    # Tier 3 datasets
    tier3_datasets_dir: Path | None = None

    # Vault path for ground truth (real vault, not fixtures)
    ground_truth_vault_path: Path | None = None

    def __post_init__(self) -> None:
        if self.vault_path is None:
            self.vault_path = self.fixtures_dir / "vault"
        if self.tier3_datasets_dir is None:
            self.tier3_datasets_dir = self.datasets_dir / "tier3"
        if self.tier3_baselines_path is None:
            self.tier3_baselines_path = self.results_dir / "tier3" / "baselines.yaml"

    @classmethod
    def from_env(cls) -> EvalConfig:
        """Create config from environment variables."""
        import os

        config = cls()
        if p := os.environ.get("GRIM_EVAL_DATASETS"):
            config.datasets_dir = Path(p)
        if p := os.environ.get("GRIM_EVAL_RESULTS"):
            config.results_dir = Path(p)
        if m := os.environ.get("GRIM_EVAL_JUDGE_MODEL"):
            config.judge_model = m
        if p := os.environ.get("GRIM_SKILLS_PATH"):
            config.skills_path = Path(p)
        if u := os.environ.get("GRIM_TIER3_URL"):
            config.tier3_docker_url = u
            config.tier3_ws_url = u.replace("http", "ws")
        if p := os.environ.get("GRIM_VAULT_PATH"):
            config.ground_truth_vault_path = Path(p)
        return config
