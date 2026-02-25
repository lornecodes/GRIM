"""
Tuning configuration and hyperparameters.
"""

from pathlib import Path

# Paths
TUNING_DIR = Path(__file__).resolve().parent
MOCK_REPO = TUNING_DIR / "mock_repo"
CASES_DIR = TUNING_DIR / "cases"
RESULTS_DIR = TUNING_DIR / "results"

# Prompts live in the actualization package
PROMPTS_FILE = TUNING_DIR.parent / "actualization" / "prompts.py"

# Tuning hyperparameters
MAX_ITERATIONS = 10          # Max optimization rounds per agent
MIN_ITERATIONS = 3           # Minimum rounds before convergence allowed
STALL_PATIENCE = 4           # Consecutive flat rounds before switching strategy
CONVERGENCE_THRESHOLD = 0.02 # Stop if loss drops less than this between rounds
TEMPERATURE_EVAL = 0.1       # Temperature for running agents
TEMPERATURE_OPTIMIZE = 0.7   # Temperature for prompt rewriting
TEMPERATURE_EXPLORE = 0.9    # Higher temperature when stalled (explore more)
MODEL = "claude-sonnet-4-20250514"

# Scoring weights per criterion (agent-specific weights in cases)
DEFAULT_WEIGHTS = {
    "accuracy": 1.0,       # Core output matches expected
    "completeness": 0.8,   # All expected fields present
    "no_hallucination": 1.5,  # Extra penalty for fabrication
    "conciseness": 0.5,    # Not over-verbose
    "format": 0.3,         # Output structure correct
}

# Agents that can be tuned
AGENTS = ["extract", "judge", "actualize", "validate", "crosslink"]
