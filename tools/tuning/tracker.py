"""
Tracker — Persistent tuning history with loss curves and prompt snapshots.

Saves all state to tuning/results/ so optimization can be:
- Paused and resumed
- Rolled back to best-performing prompt version
- Visualized (loss curves, per-case deltas)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import RESULTS_DIR, MAX_ITERATIONS, MIN_ITERATIONS, STALL_PATIENCE, CONVERGENCE_THRESHOLD, PROMPTS_FILE


class TuningTracker:
    """Tracks optimization runs per agent."""

    def __init__(self, agent: str):
        self.agent = agent
        self.agent_dir = RESULTS_DIR / agent
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.agent_dir / "history.json"
        self.history: List[Dict[str, Any]] = self._load_history()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_history(self) -> List[Dict]:
        if self.history_file.exists():
            data = json.loads(self.history_file.read_text(encoding="utf-8"))
            return data.get("iterations", [])
        return []

    def _save_history(self):
        payload = {
            "agent": self.agent,
            "last_updated": datetime.now().isoformat(),
            "total_iterations": len(self.history),
            "iterations": self.history,
        }
        self.history_file.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Record an iteration
    # ------------------------------------------------------------------

    def record(
        self,
        iteration: int,
        loss: float,
        accuracy: float,
        per_case: List[Dict],
        optimizer_result: Dict,
        prompt_snapshot: Optional[Dict[str, str]] = None,
    ):
        """Record a single optimization iteration."""
        entry = {
            "iteration": iteration,
            "timestamp": datetime.now().isoformat(),
            "loss": round(loss, 6),
            "accuracy": round(accuracy, 2),
            "per_case": _serialize_per_case(per_case),
            "rejected": optimizer_result.get("rejected", False),
            "optimizer": {
                "updates_applied": optimizer_result.get("updates", {}),
                "updates_proposed": optimizer_result.get("updates_proposed", []),
                "tokens": optimizer_result.get("optimizer_tokens", {}),
                "converged": optimizer_result.get("converged", False),
                "dry_run": optimizer_result.get("dry_run", False),
            },
        }
        # Store rejected attempt details so the optimizer can learn from failures
        if optimizer_result.get("rejected"):
            entry["rejected_changes"] = optimizer_result.get("rejected_changes", {})
            entry["rejected_accuracy"] = optimizer_result.get("rejected_accuracy")
            entry["rejected_cases"] = optimizer_result.get("rejected_cases", [])

        self.history.append(entry)
        self._save_history()

        # Save prompt snapshot
        if prompt_snapshot:
            snap_dir = self.agent_dir / "snapshots"
            snap_dir.mkdir(exist_ok=True)
            snap_file = snap_dir / f"iter_{iteration:03d}.json"
            snap_file.write_text(
                json.dumps(prompt_snapshot, indent=2), encoding="utf-8"
            )

        # Track best
        if self.is_best(loss):
            self._save_best(iteration, prompt_snapshot)

    # ------------------------------------------------------------------
    # Best tracking
    # ------------------------------------------------------------------

    def is_best(self, loss: float) -> bool:
        """Check if this loss is the best so far."""
        if not self.history:
            return True
        previous_losses = [h["loss"] for h in self.history[:-1]]
        if not previous_losses:
            return True
        return loss <= min(previous_losses)

    def _save_best(self, iteration: int, prompt_snapshot: Optional[Dict]):
        best_file = self.agent_dir / "best.json"
        best_file.write_text(
            json.dumps(
                {
                    "iteration": iteration,
                    "timestamp": datetime.now().isoformat(),
                    "prompts": prompt_snapshot or {},
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def get_best(self) -> Optional[Dict]:
        best_file = self.agent_dir / "best.json"
        if best_file.exists():
            return json.loads(best_file.read_text(encoding="utf-8"))
        return None

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def rollback_to_best(self) -> bool:
        """Restore prompts.py to the best-performing snapshot."""
        from .optimizer import write_prompt, AGENT_TUNABLE

        best = self.get_best()
        if not best or not best.get("prompts"):
            return False

        prompt_vars = AGENT_TUNABLE.get(self.agent, [])
        for var_name in prompt_vars:
            if var_name in best["prompts"]:
                write_prompt(var_name, best["prompts"][var_name])
        return True

    # ------------------------------------------------------------------
    # Convergence check
    # ------------------------------------------------------------------

    def check_convergence(self) -> bool:
        """True if loss is stable, we've done enough iterations, AND we're at/near best.

        Won't converge at a bad local minimum — requires current loss to be
        within threshold of the best loss seen.
        Also won't converge if we haven't had any genuine improvements yet."""
        if len(self.history) < max(2, MIN_ITERATIONS):
            return False
        # Must have at least one accepted (non-rejected) improvement
        accepted = [h for h in self.history if not h.get("rejected", False)]
        if len(accepted) < 2:
            return False
        last = accepted[-1]["loss"]
        prev = accepted[-2]["loss"]
        best = self.best_loss
        # Must be stable across accepted iterations
        if abs(prev - last) >= CONVERGENCE_THRESHOLD:
            return False
        # Must be at or near the best performance
        if best is not None and last > best + CONVERGENCE_THRESHOLD:
            return False
        return True

    def get_stall_count(self) -> int:
        """Count consecutive accepted (non-rejected) iterations with no meaningful loss change.

        Rejected iterations (rollbacks) are skipped — they don't count as stalls
        because the optimizer DID try something new, it just got rolled back."""
        accepted = [h for h in self.history if not h.get("rejected", False)]
        if len(accepted) < 2:
            return 0
        count = 0
        for i in range(len(accepted) - 1, 0, -1):
            delta = abs(accepted[i]["loss"] - accepted[i - 1]["loss"])
            if delta < CONVERGENCE_THRESHOLD:
                count += 1
            else:
                break
        return count

    def is_stalled(self) -> bool:
        """True if we've been flat for STALL_PATIENCE iterations."""
        return self.get_stall_count() >= STALL_PATIENCE

    def get_per_case_deltas(self) -> Dict[str, Dict]:
        """Compare per-case scores between last two iterations.
        Returns {case_id: {prev_score, curr_score, delta, prev_pct, curr_pct}}."""
        if len(self.history) < 2:
            return {}
        prev_cases = {c["case_id"]: c for c in self.history[-2].get("per_case", [])}
        curr_cases = {c["case_id"]: c for c in self.history[-1].get("per_case", [])}
        deltas = {}
        for cid in set(list(prev_cases.keys()) + list(curr_cases.keys())):
            p = prev_cases.get(cid, {})
            c = curr_cases.get(cid, {})
            ps = p.get("score", 0)
            cs = c.get("score", 0)
            pp = p.get("pct", 0)
            cp = c.get("pct", 0)
            deltas[cid] = {
                "prev_score": ps, "curr_score": cs, "delta": cs - ps,
                "prev_pct": pp, "curr_pct": cp,
            }
        return deltas

    # ------------------------------------------------------------------
    # Status / reporting
    # ------------------------------------------------------------------

    @property
    def last_loss(self) -> Optional[float]:
        return self.history[-1]["loss"] if self.history else None

    @property
    def best_loss(self) -> Optional[float]:
        if not self.history:
            return None
        return min(h["loss"] for h in self.history)

    @property
    def best_accuracy(self) -> Optional[float]:
        if not self.history:
            return None
        best_iter = min(self.history, key=lambda h: h["loss"])
        return best_iter["accuracy"]

    @property
    def iteration_count(self) -> int:
        return len(self.history)

    def get_loss_curve(self) -> List[float]:
        return [h["loss"] for h in self.history]

    def format_status(self) -> str:
        """Return a formatted status string for console display."""
        if not self.history:
            return f"  {self.agent}: No tuning runs yet"

        lines = [f"  {self.agent}:"]
        lines.append(f"    Iterations: {len(self.history)}")
        lines.append(f"    Best loss:  {self.best_loss:.4f} ({self.best_accuracy:.1f}%)")
        lines.append(f"    Last loss:  {self.last_loss:.4f}")

        curve = self.get_loss_curve()
        if len(curve) > 1:
            deltas = [curve[i] - curve[i - 1] for i in range(1, len(curve))]
            trend = "↓" if deltas[-1] < 0 else "↑" if deltas[-1] > 0 else "→"
            lines.append(f"    Trend:      {trend} (Δ={deltas[-1]:+.4f})")

            # Mini sparkline
            if len(curve) >= 3:
                mn, mx = min(curve), max(curve)
                span = mx - mn if mx > mn else 1
                bars = "▁▂▃▄▅▆▇█"
                spark = ""
                for v in curve:
                    idx = int((v - mn) / span * (len(bars) - 1))
                    spark += bars[idx]
                lines.append(f"    Loss curve: {spark}")

        stall = self.get_stall_count()
        converged = self.check_convergence()
        if converged:
            lines.append("    Status:     CONVERGED ✓")
        elif self.is_stalled():
            lines.append(f"    Status:     STALLED ({stall} flat iterations)")
        elif len(self.history) >= MAX_ITERATIONS:
            lines.append(f"    Status:     MAX ITERATIONS ({MAX_ITERATIONS}) reached")
        else:
            remaining = max(0, MIN_ITERATIONS - len(self.history))
            if remaining > 0:
                lines.append(f"    Status:     In progress (min {remaining} more before convergence check)")
            else:
                lines.append("    Status:     In progress")

        return "\n".join(lines)

    def get_summary(self) -> Dict:
        """Return a machine-readable summary."""
        return {
            "agent": self.agent,
            "iterations": len(self.history),
            "best_loss": self.best_loss,
            "best_accuracy": self.best_accuracy,
            "last_loss": self.last_loss,
            "converged": self.check_convergence(),
            "loss_curve": self.get_loss_curve(),
        }


# =========================================================================
# Helpers
# =========================================================================

def _serialize_per_case(results: List[Dict]) -> List[Dict]:
    """Make per-case results JSON-serializable."""
    out = []
    for r in results:
        entry = {
            "case_id": r.get("case_id", "unknown"),
            "description": r.get("description", ""),
            "weight": r.get("weight", 1.0),
            "error": r.get("error"),
        }
        if r.get("score"):
            s = r["score"]
            entry["score"] = s.score
            entry["max_score"] = s.max_score
            entry["pct"] = s.pct
            entry["passes"] = s.passes
            entry["failures"] = s.failures
        out.append(entry)
    return out


def get_all_agent_status() -> str:
    """Return formatted status for all agents."""
    from .config import AGENTS
    lines = ["Tuning Status", "=" * 50]
    for agent in AGENTS:
        tracker = TuningTracker(agent)
        lines.append(tracker.format_status())
        lines.append("")
    return "\n".join(lines)
