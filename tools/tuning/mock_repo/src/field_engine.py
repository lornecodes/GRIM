"""
Entropy-driven field balancer — core simulation engine.

Implements the Recursive Balance Field (RBF) algorithm for
modeling far-from-equilibrium dynamics. Uses PAC conservation
to track potential-actualization flows across field boundaries.

Key concepts:
- Field centers with Poincaré activation
- Möbius topology for phase recovery
- SEC phase-shifting at quantum boundaries
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


PHI = (1 + math.sqrt(5)) / 2  # Golden ratio
XI = 1 + math.pi / 55          # Balance operator ≈ 1.0571


@dataclass
class FieldCenter:
    """A node in the recursive balance field."""
    position: Tuple[float, float, float]
    energy: float = 1.0
    information: float = 0.0
    entropy: float = 0.0
    children: List["FieldCenter"] = field(default_factory=list)
    parent: Optional["FieldCenter"] = None

    @property
    def balance_ratio(self) -> float:
        """Compute E/I balance — stable structures cluster near φ."""
        if self.information == 0:
            return float("inf")
        return self.energy / self.information

    def pac_conserved(self) -> bool:
        """Check PAC: f(parent) = Σ f(children)."""
        if not self.children:
            return True
        child_sum = sum(c.energy + c.information for c in self.children)
        parent_total = self.energy + self.information
        return abs(parent_total - child_sum) < 1e-6


class RecursiveBalanceField:
    """
    Simulates field evolution using SEC dynamics.

    The field equation: ∂S/∂t = α∇I - β∇H
    Structure forms where information gradient dominates.
    Collapse occurs where entropy gradient overtakes.
    """

    def __init__(self, alpha: float = 1.0, beta: float = PHI):
        self.alpha = alpha
        self.beta = beta
        self.centers: List[FieldCenter] = []
        self.time = 0.0
        self._history: List[dict] = []

    def add_center(self, pos: Tuple[float, float, float]) -> FieldCenter:
        center = FieldCenter(position=pos)
        self.centers.append(center)
        return center

    def step(self, dt: float = 0.01) -> dict:
        """Evolve the field by one timestep."""
        metrics = {"collapsed": 0, "stabilized": 0, "total_energy": 0.0}

        for center in self.centers:
            # Information gradient
            grad_i = self._compute_gradient(center, "information")
            # Entropy gradient
            grad_h = self._compute_gradient(center, "entropy")

            # SEC: ∂S/∂t = α∇I - β∇H
            ds_dt = self.alpha * grad_i - self.beta * grad_h

            if ds_dt > 0:
                # Structure forming
                center.information += ds_dt * dt
                center.entropy -= ds_dt * dt * 0.5
                metrics["stabilized"] += 1
            else:
                # Collapse — but creative collapse generates new structure
                center.entropy += abs(ds_dt) * dt
                if center.entropy > center.energy * XI:
                    self._collapse(center)
                    metrics["collapsed"] += 1

            metrics["total_energy"] += center.energy

        self.time += dt
        self._history.append(metrics)
        return metrics

    def _compute_gradient(self, center: FieldCenter, field_name: str) -> float:
        """Approximate gradient from neighboring centers."""
        if not self.centers:
            return 0.0
        val = getattr(center, field_name)
        neighbors = [c for c in self.centers if c is not center]
        if not neighbors:
            return 0.0
        avg = sum(getattr(n, field_name) for n in neighbors) / len(neighbors)
        return val - avg

    def _collapse(self, center: FieldCenter) -> None:
        """
        SEC collapse — singularity to plurality.
        When entropy exceeds threshold, the center fragments
        into children, conserving total via PAC.
        """
        n_children = max(2, int(center.energy / PHI))
        child_share = (center.energy + center.information) / n_children

        for i in range(n_children):
            offset = (
                math.cos(2 * math.pi * i / n_children) * 0.1,
                math.sin(2 * math.pi * i / n_children) * 0.1,
                0.0,
            )
            child_pos = tuple(p + o for p, o in zip(center.position, offset))
            child = FieldCenter(
                position=child_pos,
                energy=child_share * PHI / (1 + PHI),
                information=child_share / (1 + PHI),
                parent=center,
            )
            center.children.append(child)
            self.centers.append(child)

        # Parent retains residual
        center.energy = 0.0
        center.information = 0.0
        center.entropy = 0.0
