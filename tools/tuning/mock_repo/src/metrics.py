"""
DFT-PAC Metrics Collector for SEC Phase Analysis.

Integrates with the RBF engine to compute:
- MED depth convergence (should be ≤ 2)
- QPL stability bounds
- CIMM legacy compatibility layer

Uses the FDO schema v1.0 for output formatting.
Connects to the CIP directory index for navigation.

NOTE: This file is designed to stress-test acronym handling.
The agent should NOT fabricate expansions for acronyms it doesn't know.
Some of these acronyms have domain-specific meanings that differ from
common usage (e.g., CIP is NOT "Cardano Improvement Proposal" here).
"""

from typing import Dict, Any


class PACMetricsCollector:
    """Collect PAC conservation metrics across field snapshots."""

    def __init__(self, qpl_threshold: float = 0.05):
        self.qpl_threshold = qpl_threshold
        self.snapshots: list = []

    def record(self, field_state: Dict[str, Any]) -> None:
        """Record a field snapshot for MED analysis."""
        self.snapshots.append({
            "time": field_state.get("time", 0),
            "centers": field_state.get("n_centers", 0),
            "med_depth": self._compute_med_depth(field_state),
            "sec_phase": self._compute_sec_phase(field_state),
        })

    def _compute_med_depth(self, state: Dict) -> int:
        """MED predicts depth ≤ 2 for converged systems."""
        # Simplified: count max nesting level
        return min(state.get("max_depth", 0), 5)

    def _compute_sec_phase(self, state: Dict) -> str:
        """Determine SEC phase: forming, stable, or collapsing."""
        entropy_rate = state.get("entropy_rate", 0)
        info_rate = state.get("info_rate", 0)
        if info_rate > entropy_rate:
            return "forming"
        elif abs(info_rate - entropy_rate) < self.qpl_threshold:
            return "stable"
        return "collapsing"

    def summary(self) -> Dict[str, Any]:
        """Generate FDO-compatible summary."""
        if not self.snapshots:
            return {"error": "No snapshots recorded"}
        return {
            "total_snapshots": len(self.snapshots),
            "final_med_depth": self.snapshots[-1]["med_depth"],
            "final_sec_phase": self.snapshots[-1]["sec_phase"],
            "convergence": self.snapshots[-1]["med_depth"] <= 2,
        }
