"""Topology optimisation by the SIMP method, driven by the finite element solver.

The problem solved is minimum compliance under a volume constraint:

    minimise    c(x) = f^T u = sum_e E_e(x_e) * u_e^T k_e u_e
    subject to  K(x) u = f
                sum_e v_e x_e / sum_e v_e <= volume_fraction
                0 <= x_e <= 1

Element stiffness is interpolated with the modified SIMP law

    E_e(x_e) = E_min + x_e^p (E_0 - E_min),

which penalises intermediate densities towards 0 or 1 while keeping the
stiffness matrix non-singular in void regions. The analytical sensitivity

    dc/dx_e = -p x_e^(p-1) (E_0 - E_min) u_e^T k_e u_e

is filtered over a radius ``rmin`` to remove the checkerboard instability and to
impose a minimum length scale, then the design is advanced by the optimality
criteria update with a bisection search on the Lagrange multiplier.

References:
    Bendsoe & Sigmund, *Topology Optimization* (2003).
    Andreassen et al., "Efficient topology optimization in MATLAB using 88 lines
    of code", *Struct Multidisc Optim* 43 (2011).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import scipy.sparse as sp
from scipy.spatial import cKDTree

from .mesh import StructuredGrid
from .solver import BoundaryConditions, FEModel


@dataclass
class TopOptSettings:
    """Algorithm parameters.

    Attributes:
        volume_fraction: Fraction of the design domain allowed to hold material.
        penalty: SIMP exponent ``p``; 3.0 is the standard choice.
        filter_radius: Filter radius in units of element size. Values below 1.0
            disable the filter and admit checkerboard patterns.
        filter_type: ``"sensitivity"`` (Sigmund's original) or ``"density"``.
        move_limit: Largest density change permitted in one iteration.
        damping: Exponent of the optimality criteria update, conventionally 0.5.
        max_iterations: Iteration cap.
        tolerance: Convergence threshold on the largest density change.
        E_min: Void stiffness as a fraction of the solid modulus.
        continuation: If true, ramp the penalty from 1.0 to ``penalty`` over the
            first iterations, which reduces the chance of settling in a poor
            local minimum.
    """

    volume_fraction: float = 0.4
    penalty: float = 3.0
    filter_radius: float = 1.5
    filter_type: str = "sensitivity"
    move_limit: float = 0.2
    damping: float = 0.5
    max_iterations: int = 150
    tolerance: float = 0.01
    E_min: float = 1e-9
    continuation: bool = False

    def __post_init__(self) -> None:
        if not 0.0 < self.volume_fraction <= 1.0:
            raise ValueError(
                f"volume_fraction must lie in (0, 1], got {self.volume_fraction}"
            )
        if self.penalty < 1.0:
            raise ValueError(f"penalty must be at least 1.0, got {self.penalty}")
        if self.filter_type not in ("sensitivity", "density"):
            raise ValueError(
                f"filter_type must be 'sensitivity' or 'density', got {self.filter_type!r}"
            )


@dataclass
class TopOptResult:
    """Outcome of an optimisation run.

    Attributes:
        density: Final physical density of every element, in [0, 1].
        compliance: Final structural compliance.
        history: Compliance at each iteration.
        volume_history: Volume fraction at each iteration.
        change_history: Largest density change at each iteration.
        iterations: Number of iterations performed.
        converged: Whether the change tolerance was met before the iteration cap.
        grid_shape: ``(ny, nx)`` for reshaping ``density`` into an image.
    """

    density: np.ndarray
    compliance: float
    history: list[float] = field(default_factory=list)
    volume_history: list[float] = field(default_factory=list)
    change_history: list[float] = field(default_factory=list)
    iterations: int = 0
    converged: bool = False
    grid_shape: tuple[int, int] = (0, 0)

    def as_image(self) -> np.ndarray:
        """Density reshaped to ``(ny, nx)``, row 0 being the bottom of the domain."""
        return self.density.reshape(self.grid_shape)

    def discreteness(self) -> float:
        """Fraction of grey material, ``Mnd`` in the literature.

        Zero means a perfectly black and white design; larger values mean more
        elements are stuck at intermediate density and the result is less
        manufacturable.
        """
        x = self.density
        return float(np.mean(4.0 * x * (1.0 - x)))

    def checkerboard_measure(self) -> float:
        """Strength of the alternating solid-void instability, in [0, 1].

        Formed by averaging the alternating-sign difference over every 2x2 block
        of elements. Smooth regions and straight structural boundaries cancel out
        of that stencil, so the measure responds only to the checkerboard pattern
        itself: it is near zero for a filtered design and approaches 0.5 for a
        fully developed checkerboard.
        """
        image = self.as_image()
        if min(image.shape) < 2:
            return 0.0
        stencil = image[:-1, :-1] - image[1:, :-1] - image[:-1, 1:] + image[1:, 1:]
        return float(np.mean(np.abs(stencil))) / 4.0


def build_filter_matrix(grid: StructuredGrid, radius: float) -> sp.csr_matrix:
    """Cone-shaped neighbourhood weights ``H_ef = max(0, rmin - dist(e, f))``.

    Distance is measured in element widths, so ``radius`` is independent of the
    physical size of the domain.

    Args:
        grid: The structured design grid.
        radius: Filter radius in element-size units.

    Returns:
        Sparse ``(n_elements, n_elements)`` weight matrix.
    """
    centroids = grid.element_centroids() / np.array([grid.dx, grid.dy])
    tree = cKDTree(centroids)
    pairs = tree.query_pairs(radius, output_type="ndarray")

    # Off-diagonal entries, symmetric, plus the self weight on the diagonal.
    distances = np.linalg.norm(centroids[pairs[:, 0]] - centroids[pairs[:, 1]], axis=1)
    weights = radius - distances
    rows = np.concatenate([pairs[:, 0], pairs[:, 1], np.arange(grid.n_elements)])
    cols = np.concatenate([pairs[:, 1], pairs[:, 0], np.arange(grid.n_elements)])
    values = np.concatenate([weights, weights, np.full(grid.n_elements, radius)])

    return sp.coo_matrix(
        (values, (rows, cols)), shape=(grid.n_elements, grid.n_elements)
    ).tocsr()


class TopologyOptimizer:
    """Minimum compliance topology optimisation over a structured grid.

    Args:
        model: Finite element model whose mesh is a :class:`StructuredGrid`.
        bcs: Loads and supports, held fixed throughout the optimisation.
        settings: Algorithm parameters.
        passive_solid: Boolean mask of elements forced to full density.
        passive_void: Boolean mask of elements forced to zero density.
    """

    def __init__(
        self,
        model: FEModel,
        bcs: BoundaryConditions,
        settings: TopOptSettings | None = None,
        passive_solid: np.ndarray | None = None,
        passive_void: np.ndarray | None = None,
    ) -> None:
        if not isinstance(model.mesh, StructuredGrid):
            raise TypeError(
                "Topology optimisation requires a StructuredGrid design domain, got "
                f"{type(model.mesh).__name__}"
            )
        if not model.has_uniform_elements:
            raise ValueError(
                "The design grid must be made of congruent elements so that one "
                "element stiffness matrix can be reused; the mesh nodes appear to "
                "have been moved."
            )
        self.model = model
        self.grid: StructuredGrid = model.mesh
        self.bcs = bcs
        self.settings = settings or TopOptSettings()

        n = self.grid.n_elements
        self.passive_solid = (
            np.zeros(n, dtype=bool) if passive_solid is None else np.asarray(passive_solid, bool)
        )
        self.passive_void = (
            np.zeros(n, dtype=bool) if passive_void is None else np.asarray(passive_void, bool)
        )
        if np.any(self.passive_solid & self.passive_void):
            raise ValueError("An element cannot be both passive solid and passive void")

        self.element_dofs = self.grid.all_element_dofs()
        self.ke = model.element_stiffnesses()[0]  # identical for a uniform grid

        if self.settings.filter_radius > 1.0:
            self.H = build_filter_matrix(self.grid, self.settings.filter_radius)
            self.Hs = np.asarray(self.H.sum(axis=1)).ravel()
        else:
            self.H = None
            self.Hs = None

    def _stiffness_scale(self, density: np.ndarray, penalty: float) -> np.ndarray:
        """SIMP stiffness multiplier relative to the solid modulus."""
        e_min = self.settings.E_min
        return e_min + density**penalty * (1.0 - e_min)

    def _compliance_and_sensitivity(
        self, density: np.ndarray, penalty: float
    ) -> tuple[float, np.ndarray, np.ndarray]:
        """Solve the state equation and differentiate the compliance.

        Returns:
            ``(compliance, dc_dx, strain_energy)`` where ``strain_energy`` is the
            unscaled element quantity ``u_e^T k_e u_e``.
        """
        solution = self.model.solve(self.bcs, scale=self._stiffness_scale(density, penalty))
        u = solution.displacements

        u_elements = u[self.element_dofs]  # (n_elements, 8)
        strain_energy = np.einsum("ij,jk,ik->i", u_elements, self.ke, u_elements)

        scale = self._stiffness_scale(density, penalty)
        compliance = float(np.sum(scale * strain_energy))
        dc_dx = -penalty * density ** (penalty - 1.0) * (1.0 - self.settings.E_min) * strain_energy
        return compliance, dc_dx, strain_energy

    def _apply_filter(
        self, density: np.ndarray, dc_dx: np.ndarray, dv_dx: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Regularise the sensitivities, returning ``(dc_dx, dv_dx)``."""
        if self.H is None:
            return dc_dx, dv_dx

        if self.settings.filter_type == "sensitivity":
            # Sigmund's mesh-independence filter: a density-weighted average of
            # neighbouring sensitivities. It modifies only the gradient, so the
            # physical density is the design variable itself.
            denominator = np.maximum(1e-3, density) * self.Hs
            return np.asarray(self.H @ (density * dc_dx)).ravel() / denominator, dv_dx

        # Density filter: the physical field is a weighted average of the design
        # field, so both sensitivities are pulled back through the chain rule.
        return (
            np.asarray(self.H @ (dc_dx / self.Hs)).ravel(),
            np.asarray(self.H @ (dv_dx / self.Hs)).ravel(),
        )

    def _physical_density(self, design: np.ndarray) -> np.ndarray:
        """Map design variables to physical densities."""
        if self.H is not None and self.settings.filter_type == "density":
            return np.asarray(self.H @ design).ravel() / self.Hs
        return design

    def _optimality_criteria_update(
        self, design: np.ndarray, dc_dx: np.ndarray, dv_dx: np.ndarray
    ) -> np.ndarray:
        """Advance the design by bisection on the volume constraint multiplier."""
        settings = self.settings
        move = settings.move_limit
        target_volume = settings.volume_fraction * self.grid.n_elements

        # The update is x_new = x * (-dc / (lambda * dv))^damping, clipped to the
        # move limit and the box constraints; lambda is found so the volume
        # constraint is active. The bracket is widened until it is valid.
        lower, upper = 1e-9, 1e9
        new_design = design
        while (upper - lower) / (lower + upper) > 1e-6:
            mid = 0.5 * (lower + upper)
            ratio = np.maximum(0.0, -dc_dx / (mid * dv_dx)) ** settings.damping
            candidate = design * ratio
            candidate = np.clip(candidate, design - move, design + move)
            candidate = np.clip(candidate, 0.0, 1.0)
            candidate[self.passive_solid] = 1.0
            candidate[self.passive_void] = 0.0

            new_design = candidate
            if np.sum(self._physical_density(candidate)) > target_volume:
                lower = mid
            else:
                upper = mid

        return new_design

    def run(
        self, callback: Callable[[int, float, float, np.ndarray], None] | None = None
    ) -> TopOptResult:
        """Run the optimisation loop.

        Args:
            callback: Optional ``f(iteration, compliance, change, density)`` called
                after every iteration, for progress reporting or animation frames.

        Returns:
            A :class:`TopOptResult` holding the final layout and the histories.
        """
        settings = self.settings
        n = self.grid.n_elements

        design = np.full(n, settings.volume_fraction)
        design[self.passive_solid] = 1.0
        design[self.passive_void] = 0.0

        result = TopOptResult(
            density=design.copy(),
            compliance=np.inf,
            grid_shape=self.grid.element_grid_shape(),
        )

        change = 1.0
        for iteration in range(1, settings.max_iterations + 1):
            penalty = settings.penalty
            if settings.continuation:
                # Ramp p from 1 to its target over the first 40 iterations.
                penalty = min(settings.penalty, 1.0 + (settings.penalty - 1.0) * iteration / 40.0)

            physical = self._physical_density(design)
            compliance, dc_dx, _ = self._compliance_and_sensitivity(physical, penalty)
            dv_dx = np.ones(n)

            dc_filtered, dv_filtered = self._apply_filter(physical, dc_dx, dv_dx)
            new_design = self._optimality_criteria_update(design, dc_filtered, dv_filtered)

            change = float(np.max(np.abs(new_design - design)))
            design = new_design
            physical = self._physical_density(design)

            result.history.append(compliance)
            result.volume_history.append(float(np.mean(physical)))
            result.change_history.append(change)
            result.iterations = iteration
            result.compliance = compliance
            result.density = physical.copy()

            if callback is not None:
                callback(iteration, compliance, change, physical)

            if change < settings.tolerance:
                result.converged = True
                break

        # Report the compliance of the final design rather than of the design that
        # produced the last gradient.
        result.compliance, _, _ = self._compliance_and_sensitivity(
            result.density, settings.penalty
        )
        return result
