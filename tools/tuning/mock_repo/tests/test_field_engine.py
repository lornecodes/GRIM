"""
Tests for the field engine.

Validates PAC conservation, SEC collapse dynamics,
and convergence of the RBF simulation.
"""

import math
import pytest
from src.field_engine import FieldCenter, RecursiveBalanceField, PHI, XI


class TestFieldCenter:
    """Unit tests for individual field centers."""

    def test_balance_ratio_at_phi(self):
        """A center with E/I = φ should be at golden balance."""
        center = FieldCenter(
            position=(0, 0, 0),
            energy=PHI,
            information=1.0,
        )
        assert abs(center.balance_ratio - PHI) < 1e-10

    def test_pac_conservation_leaf(self):
        """A leaf node (no children) trivially satisfies PAC."""
        center = FieldCenter(position=(0, 0, 0), energy=5.0, information=3.0)
        assert center.pac_conserved()

    def test_pac_conservation_with_children(self):
        """PAC: f(parent) = Σ f(children)."""
        parent = FieldCenter(position=(0, 0, 0), energy=5.0, information=3.0)
        child1 = FieldCenter(position=(1, 0, 0), energy=3.0, information=2.0)
        child2 = FieldCenter(position=(-1, 0, 0), energy=2.0, information=1.0)
        parent.children = [child1, child2]
        assert parent.pac_conserved()

    def test_pac_violation(self):
        """Non-conserving split should fail PAC check."""
        parent = FieldCenter(position=(0, 0, 0), energy=5.0, information=3.0)
        child = FieldCenter(position=(1, 0, 0), energy=1.0, information=1.0)
        parent.children = [child]
        assert not parent.pac_conserved()


class TestRecursiveBalanceField:
    """Integration tests for the RBF simulation."""

    def test_single_step(self):
        """Running one step should produce valid metrics."""
        rbf = RecursiveBalanceField()
        rbf.add_center((0, 0, 0))
        rbf.add_center((1, 0, 0))
        metrics = rbf.step()
        assert "collapsed" in metrics
        assert "stabilized" in metrics

    def test_collapse_creates_children(self):
        """A center exceeding XI threshold should fragment."""
        rbf = RecursiveBalanceField()
        center = rbf.add_center((0, 0, 0))
        center.entropy = center.energy * XI * 2  # Force collapse
        rbf.step()
        assert len(center.children) >= 2

    def test_energy_conservation(self):
        """Total energy should be approximately conserved."""
        rbf = RecursiveBalanceField()
        for i in range(5):
            rbf.add_center((i * 0.5, 0, 0))
        initial = sum(c.energy for c in rbf.centers)
        for _ in range(100):
            rbf.step()
        final = sum(c.energy for c in rbf.centers)
        # Allow some numerical drift
        assert abs(final - initial) / max(initial, 1e-10) < 0.5
