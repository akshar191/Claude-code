"""Verification of the Method of Moving Asymptotes against closed-form optima."""

from __future__ import annotations

import numpy as np
import pytest

from fea.mma import MMA


def test_reciprocal_constraint_problem_reaches_the_analytical_optimum():
    """Minimise a linear weight subject to a reciprocal (compliance-like) constraint.

    ``min w.x  s.t.  sum a_j / x_j <= b`` is the classic MMA test problem: the
    KKT conditions give ``x_j = sqrt(lambda a_j / w_j)`` with the multiplier set
    by the active constraint, so the optimum is known exactly.
    """
    rng = np.random.default_rng(1)
    n = 12
    w = rng.uniform(0.5, 2.0, n)
    a = rng.uniform(0.5, 2.0, n)
    b = 4.0

    multiplier = (np.sum(np.sqrt(a * w)) / b) ** 2
    x_exact = np.sqrt(multiplier * a / w)
    assert np.all((x_exact > 0.1) & (x_exact < 10.0))

    optimiser = MMA(n, 0.1, 10.0, move=0.5)
    x = np.full(n, 2.0)
    for _ in range(60):
        f0, df0 = float(w @ x), w
        f1, df1 = float(np.sum(a / x) - b), -a / x**2
        x = optimiser.update(x, f0, df0, f1, df1)

    assert np.allclose(x, x_exact, rtol=1e-8)
    assert np.sum(a / x) - b == pytest.approx(0.0, abs=1e-8)


def test_inactive_constraint_gives_zero_multiplier_and_the_unconstrained_optimum():
    """With the constraint slack the step is pure descent on the objective."""
    n = 5
    target = np.array([0.3, 0.6, 0.2, 0.9, 0.5])
    optimiser = MMA(n, 0.0, 1.0, move=0.3)
    x = np.full(n, 0.5)
    for _ in range(80):
        f0 = float(np.sum((x - target) ** 2))
        df0 = 2.0 * (x - target)
        f1, df1 = float(np.sum(x) - 100.0), np.ones(n)  # never active
        x = optimiser.update(x, f0, df0, f1, df1)
    assert optimiser.multiplier == 0.0
    # MMA converges linearly on a problem with no active constraint.
    assert np.allclose(x, target, atol=1e-2)


def test_step_respects_move_limit_and_bounds():
    """No variable moves further than the move limit or outside its box."""
    n = 6
    optimiser = MMA(n, 0.0, 1.0, move=0.1)
    x = np.array([0.05, 0.5, 0.95, 0.5, 0.5, 0.5])
    df0 = np.array([1.0, 1.0, -1.0, -50.0, 50.0, 0.0])
    x_new = optimiser.update(x, 0.0, df0, -1.0, np.zeros(n))
    assert np.all(x_new >= 0.0) and np.all(x_new <= 1.0)
    assert np.all(np.abs(x_new - x) <= 0.1 + 1e-12)
    assert x_new[3] > x[3] and x_new[4] < x[4]


def test_infeasible_subproblem_saturates_rather_than_failing():
    """A constraint that cannot be met within the move limit still yields a step."""
    n = 4
    optimiser = MMA(n, 0.0, 1.0, move=0.05)
    x = np.full(n, 0.5)
    # Constraint demands a change far beyond the move limit.
    x_new = optimiser.update(x, 0.0, np.ones(n), 10.0, -np.ones(n))
    assert optimiser.multiplier == optimiser.constraint_penalty
    assert np.all(x_new > x)  # pushed towards feasibility
    assert np.all(np.abs(x_new - x) <= 0.05 + 1e-12)


def test_invalid_bounds_are_rejected():
    with pytest.raises(ValueError, match="xmax"):
        MMA(3, 1.0, 1.0)
