"""The Method of Moving Asymptotes for a single general constraint.

MMA (Svanberg, 1987) replaces the objective and constraint at each iteration by
separable convex approximations built from the current gradient:

    f_i(x) ~ r_i + sum_j [ p_ij / (U_j - x_j) + q_ij / (x_j - L_j) ]

where the asymptotes ``L_j < x_j < U_j`` move in or out from one iteration to the
next depending on whether the variable is oscillating or advancing steadily. The
approximations are convex and separable, so the subproblem's dual is a smooth
concave function of the constraint multiplier alone, and with one constraint that
multiplier is found by bisection - a robust closed-form solve with no interior
point machinery.

The optimality criteria update in :mod:`fea.topopt` is a special case of this
that only works when the objective gradient is uniformly one sign and the single
constraint is linear. Stress-constrained optimisation satisfies neither, which is
why this module exists.

Only one constraint is supported; that covers minimum volume subject to an
aggregated stress constraint, which is the problem the stress optimiser solves.

References:
    Svanberg, "The method of moving asymptotes - a new method for structural
    optimization", *Int. J. Numer. Meth. Engng* 24 (1987), 359-373.
    Svanberg, "MMA and GCMMA - two methods for nonlinear optimization" (2007),
    for the asymptote and coefficient rules used here.
"""

from __future__ import annotations

import numpy as np


class MMA:
    """Moving asymptotes optimiser for ``min f0(x)`` subject to ``f1(x) <= 0``.

    Args:
        n: Number of design variables.
        xmin: Lower bound on each variable (scalar or array).
        xmax: Upper bound on each variable.
        move: Move limit as a fraction of ``xmax - xmin``.
        asymptote_init: Initial asymptote distance as a fraction of the range.
        asymptote_increase: Factor by which asymptotes recede when a variable
            moves in the same direction twice running.
        asymptote_decrease: Factor by which asymptotes tighten when a variable
            oscillates.
        constraint_penalty: The ``c`` coefficient of Svanberg's elastic variable:
            the multiplier is capped at this value, so an infeasible subproblem
            resolves to a bounded, well-defined step rather than failing.
    """

    def __init__(
        self,
        n: int,
        xmin,
        xmax,
        move: float = 0.2,
        asymptote_init: float = 0.5,
        asymptote_increase: float = 1.2,
        asymptote_decrease: float = 0.7,
        constraint_penalty: float = 1000.0,
    ) -> None:
        self.n = n
        self.xmin = np.broadcast_to(np.asarray(xmin, float), (n,)).copy()
        self.xmax = np.broadcast_to(np.asarray(xmax, float), (n,)).copy()
        if np.any(self.xmax <= self.xmin):
            raise ValueError("xmax must exceed xmin for every variable")
        self.move = move
        self.asymptote_init = asymptote_init
        self.asymptote_increase = asymptote_increase
        self.asymptote_decrease = asymptote_decrease
        self.constraint_penalty = constraint_penalty

        self.iteration = 0
        self.xold1: np.ndarray | None = None
        self.xold2: np.ndarray | None = None
        self.lower: np.ndarray | None = None
        self.upper: np.ndarray | None = None
        self.multiplier = 0.0

    def _update_asymptotes(self, x: np.ndarray) -> None:
        span = self.xmax - self.xmin
        if self.iteration <= 2 or self.xold1 is None or self.xold2 is None:
            self.lower = x - self.asymptote_init * span
            self.upper = x + self.asymptote_init * span
            return

        # A variable that reversed direction gets tighter asymptotes (damping);
        # one that kept going gets looser ones (acceleration).
        trend = (x - self.xold1) * (self.xold1 - self.xold2)
        factor = np.ones(self.n)
        factor[trend > 0.0] = self.asymptote_increase
        factor[trend < 0.0] = self.asymptote_decrease

        self.lower = x - factor * (self.xold1 - self.lower)
        self.upper = x + factor * (self.upper - self.xold1)

        self.lower = np.maximum(self.lower, x - 10.0 * span)
        self.lower = np.minimum(self.lower, x - 0.01 * span)
        self.upper = np.minimum(self.upper, x + 10.0 * span)
        self.upper = np.maximum(self.upper, x + 0.01 * span)

    @staticmethod
    def _coefficients(gradient: np.ndarray, x, lower, upper, span) -> tuple[np.ndarray, np.ndarray]:
        """Svanberg's (2007) p and q coefficients, strictly convex by construction."""
        positive = np.maximum(gradient, 0.0)
        negative = np.maximum(-gradient, 0.0)
        regularisation = 1e-5 / span
        p = (upper - x) ** 2 * (1.001 * positive + 0.001 * negative + regularisation)
        q = (x - lower) ** 2 * (0.001 * positive + 1.001 * negative + regularisation)
        return p, q

    def update(
        self,
        x: np.ndarray,
        f0: float,
        df0: np.ndarray,
        f1: float,
        df1: np.ndarray,
    ) -> np.ndarray:
        """Take one MMA step.

        Args:
            x: Current design.
            f0: Objective value at ``x`` (unused by the step, kept for symmetry).
            df0: Objective gradient at ``x``.
            f1: Constraint value at ``x``; feasible when ``f1 <= 0``.
            df1: Constraint gradient at ``x``.

        Returns:
            The next design, within the move limits and the box bounds.
        """
        x = np.asarray(x, float)
        self.iteration += 1
        self._update_asymptotes(x)
        lower, upper = self.lower, self.upper
        span = self.xmax - self.xmin

        # Feasible window for this step: inside the asymptotes, the move limit,
        # and the box bounds.
        alpha = np.maximum.reduce([self.xmin, lower + 0.1 * (x - lower), x - self.move * span])
        beta = np.minimum.reduce([self.xmax, upper - 0.1 * (upper - x), x + self.move * span])

        p0, q0 = self._coefficients(df0, x, lower, upper, span)
        p1, q1 = self._coefficients(df1, x, lower, upper, span)
        # Constant so the approximation interpolates the constraint at x.
        b = -(f1 - np.sum(p1 / (upper - x) + q1 / (x - lower)))

        def primal(multiplier: float) -> np.ndarray:
            """Minimiser of the Lagrangian for a given multiplier, closed form."""
            P = p0 + multiplier * p1
            Q = q0 + multiplier * q1
            sqrt_P = np.sqrt(P)
            sqrt_Q = np.sqrt(Q)
            candidate = (lower * sqrt_P + upper * sqrt_Q) / (sqrt_P + sqrt_Q)
            return np.clip(candidate, alpha, beta)

        def constraint_residual(multiplier: float) -> float:
            xs = primal(multiplier)
            return float(np.sum(p1 / (upper - xs) + q1 / (xs - lower)) - b)

        # The dual is concave in the multiplier and its derivative is the
        # constraint residual, so a bracketing search on the residual's sign
        # locates the optimum. Zero multiplier means the constraint is inactive.
        if constraint_residual(0.0) <= 0.0:
            self.multiplier = 0.0
        else:
            lo, hi = 0.0, self.constraint_penalty
            if constraint_residual(hi) > 0.0:
                # Infeasible within the move limits: the elastic variable takes
                # the violation and the multiplier saturates.
                self.multiplier = hi
            else:
                for _ in range(100):
                    mid = 0.5 * (lo + hi)
                    if constraint_residual(mid) > 0.0:
                        lo = mid
                    else:
                        hi = mid
                    if hi - lo < 1e-10 * max(1.0, hi):
                        break
                self.multiplier = 0.5 * (lo + hi)

        x_new = primal(self.multiplier)
        self.xold2 = self.xold1
        self.xold1 = x.copy()
        return x_new
