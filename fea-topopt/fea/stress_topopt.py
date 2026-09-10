"""Stress-constrained topology optimisation: minimum volume under a stress limit.

Minimum compliance says nothing about whether the part will break. This module
solves the problem a designer actually has - use as little material as possible
without exceeding an allowable stress anywhere:

    minimise    V(x) = (1/n) sum_e x_e
    subject to  sigma_vm,e(x) <= sigma_lim   for every element e
                K(x) u = f,   0 < x_e <= 1

Three well-known difficulties make this much harder than compliance, and each is
handled the standard way:

**Singularity.** As an element's density goes to zero its stiffness vanishes
but the stress in whatever material remains does not, so the feasible set has
degenerate low-dimensional appendages the optimiser cannot reach. The
qp-relaxation (Bruggi 2008; Le et al. 2010) scales the stress by ``x_e^q`` with
``q < p``, so the relaxed stress ``x_e^q sigma_vm,e`` tends to zero with the
density and the appendages open up.

**Locality.** One constraint per element would mean thousands of constraints.
They are aggregated with a p-norm,

    sigma_PN = ( sum_e (x_e^q sigma_vm,e / sigma_lim)^P )^(1/P),

which approaches the maximum as ``P`` grows while remaining differentiable.

**Accuracy.** A finite ``P`` overestimates the maximum, so the constraint is
``c sigma_PN <= 1`` with ``c`` updated adaptively (Le et al.) to make
``c sigma_PN`` track the true maximum relaxed stress from the previous iteration.

Sensitivities are computed by the adjoint method: one extra linear solve per
iteration, with the same stiffness matrix, regardless of the number of elements.
Because the objective gradient is constant and the constraint gradient changes
sign across the domain, the optimality criteria update does not apply; the design
is advanced by the Method of Moving Asymptotes in :mod:`fea.mma`.

References:
    Le, Norato, Bruns, Ha & Tortorelli, "Stress-based topology optimization for
    continua", *Struct Multidisc Optim* 41 (2010), 605-620.
    Bruggi, "On an alternative approach to stress constraints relaxation in
    topology optimization", *Struct Multidisc Optim* 36 (2008), 125-141.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import scipy.sparse.linalg as spla

from .material import VON_MISES_FORM_2D
from .mesh import StructuredGrid
from .mma import MMA
from .solver import BoundaryConditions, FEModel
from .topopt import build_filter_matrix


@dataclass
class StressSettings:
    """Algorithm parameters for the stress-constrained problem.

    Attributes:
        stress_limit: Allowable von Mises stress in the solid material.
        penalty: SIMP stiffness exponent ``p``.
        relaxation: Stress relaxation exponent ``q``; must be below ``penalty``.
        pnorm: Aggregation exponent ``P``. Larger tracks the maximum more
            closely at the cost of a rougher, harder problem.
        filter_radius: Density filter radius in element widths.
        move_limit: MMA move limit as a fraction of the density range.
        max_iterations: Iteration cap.
        tolerance: Convergence threshold on the largest density change, applied
            only once the constraint is satisfied.
        x_min: Smallest admissible density, kept positive so ``x^(q-1)`` in the
            sensitivity stays finite.
        E_min: Void stiffness as a fraction of the solid modulus.
        adaptive_scaling: Relaxation factor ``alpha`` of the p-norm correction;
            1 follows the previous iteration exactly, smaller values damp it.
        initial_density: Starting density everywhere in the design domain.
    """

    stress_limit: float = 1.0
    penalty: float = 3.0
    relaxation: float = 0.5
    pnorm: float = 8.0
    filter_radius: float = 2.0
    move_limit: float = 0.1
    max_iterations: int = 150
    tolerance: float = 0.01
    x_min: float = 1e-3
    E_min: float = 1e-9
    adaptive_scaling: float = 0.5
    initial_density: float = 1.0

    def __post_init__(self) -> None:
        if self.stress_limit <= 0.0:
            raise ValueError(f"stress_limit must be positive, got {self.stress_limit}")
        if not 0.0 < self.relaxation < self.penalty:
            raise ValueError(
                f"relaxation q={self.relaxation} must lie in (0, penalty={self.penalty})"
            )
        if self.pnorm < 1.0:
            raise ValueError(f"pnorm must be at least 1, got {self.pnorm}")
        if not 0.0 < self.adaptive_scaling <= 1.0:
            raise ValueError("adaptive_scaling must lie in (0, 1]")


@dataclass
class StressResult:
    """Outcome of a stress-constrained run.

    Attributes:
        density: Final physical density of every element.
        volume_fraction: Final volume fraction of the design domain.
        max_stress: Largest relaxed von Mises stress in the design domain.
        stress: Relaxed von Mises stress ``x^q sigma_vm`` of every element.
        volume_history, stress_history: Per-iteration values.
        iterations, converged, grid_shape: As for :class:`fea.topopt.TopOptResult`.
    """

    density: np.ndarray
    volume_fraction: float
    max_stress: float
    stress: np.ndarray
    volume_history: list[float] = field(default_factory=list)
    stress_history: list[float] = field(default_factory=list)
    iterations: int = 0
    converged: bool = False
    grid_shape: tuple[int, ...] = ()

    def as_image(self) -> np.ndarray:
        return self.density.reshape(self.grid_shape)

    def stress_image(self) -> np.ndarray:
        return self.stress.reshape(self.grid_shape)


class StressConstrainedOptimizer:
    """Minimum volume topology optimisation under a von Mises stress limit.

    Args:
        model: Plane elasticity model on a uniform :class:`StructuredGrid`.
        bcs: Loads and supports with homogeneous prescribed displacements.
        settings: Algorithm parameters.
        passive_void: Elements outside the design domain, held at ``x_min`` and
            excluded from the stress aggregation.
        passive_solid: Elements held at full density.
        unconstrained: Elements whose stress is not aggregated. Used for the
            load introduction region: the stress under a concentrated load is a
            singularity of the model rather than of the part, cannot be relieved
            by any layout, and would otherwise bind the constraint on any mesh
            fine enough to resolve it.
    """

    def __init__(
        self,
        model: FEModel,
        bcs: BoundaryConditions,
        settings: StressSettings | None = None,
        passive_void: np.ndarray | None = None,
        passive_solid: np.ndarray | None = None,
        unconstrained: np.ndarray | None = None,
    ) -> None:
        if model.ndim != 2 or not isinstance(model.mesh, StructuredGrid):
            raise TypeError("Stress-constrained optimisation requires a 2D StructuredGrid")
        if not model.has_uniform_elements:
            raise ValueError("The design grid must be made of congruent elements")
        if any(abs(v) > 0.0 for v in bcs.prescribed.values()):
            raise ValueError("Prescribed displacements must be zero for the adjoint solve")

        self.model = model
        self.grid: StructuredGrid = model.mesh
        self.bcs = bcs
        self.settings = settings or StressSettings()
        n = self.grid.n_elements

        self.passive_void = (
            np.zeros(n, bool) if passive_void is None else np.asarray(passive_void, bool)
        )
        self.passive_solid = (
            np.zeros(n, bool) if passive_solid is None else np.asarray(passive_solid, bool)
        )
        unconstrained = (
            np.zeros(n, bool) if unconstrained is None else np.asarray(unconstrained, bool)
        )
        self.domain_mask = ~self.passive_void  # elements that count as material
        self.design_mask = self.domain_mask & ~unconstrained  # elements whose stress counts
        self.free_design = ~(self.passive_void | self.passive_solid)

        self.element_dofs = self.grid.all_element_dofs()
        self.ke = model.element_stiffnesses()[0]
        self.B = model.centroid_strain_matrix()  # (3, 8)
        self.D = model.D
        # Maps element displacements straight to centroid stress.
        self.DB = self.D @ self.B  # (3, 8)

        self.H = build_filter_matrix(self.grid, self.settings.filter_radius)
        self.Hs = np.asarray(self.H.sum(axis=1)).ravel()

        # Free / constrained partition, fixed for the whole run.
        self.constrained = np.array(sorted(bcs.prescribed), dtype=int)
        mask = np.ones(self.grid.n_dofs, bool)
        mask[self.constrained] = False
        self.free = np.flatnonzero(mask)
        self.f = bcs.force_vector(self.grid.n_dofs)

        self.scaling = 1.0  # adaptive p-norm correction c

    # -- analysis ------------------------------------------------------------------

    def physical_density(self, design: np.ndarray) -> np.ndarray:
        """Filtered density with the passive regions imposed.

        This is the field :meth:`analyse` expects; pass a design of ones through
        it to evaluate the full, unoptimised part with its cut-outs in place.
        """
        physical = np.asarray(self.H @ np.asarray(design, float)).ravel() / self.Hs
        physical[self.passive_void] = self.settings.x_min
        physical[self.passive_solid] = 1.0
        return physical

    _physical = physical_density

    def _filter_back(self, gradient: np.ndarray) -> np.ndarray:
        """Chain rule through the density filter: d/dx = H^T (d/dx_phys / Hs)."""
        pulled = np.asarray(self.H @ (gradient / self.Hs)).ravel()
        pulled[~self.free_design] = 0.0
        return pulled

    def _stiffness_scale(self, physical: np.ndarray) -> np.ndarray:
        e_min = self.settings.E_min
        return e_min + physical**self.settings.penalty * (1.0 - e_min)

    def analyse(self, physical: np.ndarray):
        """State solve, stress evaluation, and adjoint sensitivities.

        Returns:
            ``(volume, dvolume, constraint, dconstraint, relaxed_stress)`` where
            gradients are with respect to the physical density and the constraint
            is ``c * sigma_PN - 1``.
        """
        s = self.settings
        n = self.grid.n_elements
        # x^(q-1) is singular at zero; designs from other optimisers may carry
        # exact zeros, so the floor is applied here as well as in the update.
        physical = np.maximum(np.asarray(physical, float), s.x_min)
        scale = self._stiffness_scale(physical)

        K = self.model.assemble(scale)
        K_ff = K[self.free][:, self.free].tocsc()
        lu = spla.splu(K_ff)
        u = np.zeros(self.grid.n_dofs)
        u[self.free] = lu.solve(self.f[self.free])

        # Centroid stress in the solid material, and its von Mises value.
        u_e = u[self.element_dofs]  # (n, 8)
        sigma = u_e @ self.DB.T  # (n, 3)
        vm = np.sqrt(np.einsum("ij,jk,ik->i", sigma, VON_MISES_FORM_2D, sigma))
        vm = np.maximum(vm, 1e-12 * max(1.0, vm.max()))

        relaxed = physical**s.relaxation * vm
        normalised = np.where(self.design_mask, relaxed / s.stress_limit, 0.0)

        pnorm = float(np.sum(normalised**s.pnorm) ** (1.0 / s.pnorm))
        constraint = self.scaling * pnorm - 1.0

        # d(sigma_PN)/d(normalised_e), then split into the explicit density
        # term and the implicit displacement term handled by the adjoint.
        dpn_dn = pnorm ** (1.0 - s.pnorm) * normalised ** (s.pnorm - 1.0)
        explicit = dpn_dn * s.relaxation * physical ** (s.relaxation - 1.0) * vm / s.stress_limit

        # Adjoint load: sum over elements of the stress sensitivity to u_e.
        weight = dpn_dn * physical**s.relaxation / (s.stress_limit * vm)  # (n,)
        dvm_dsigma = sigma @ VON_MISES_FORM_2D  # (n, 3) = V sigma (V symmetric)
        gamma = (weight[:, None] * dvm_dsigma) @ self.DB  # (n, 8)
        adjoint_rhs = np.zeros(self.grid.n_dofs)
        np.add.at(adjoint_rhs, self.element_dofs.ravel(), gamma.ravel())

        lam = np.zeros(self.grid.n_dofs)
        lam[self.free] = lu.solve(adjoint_rhs[self.free])
        lam_e = lam[self.element_dofs]

        dscale = s.penalty * physical ** (s.penalty - 1.0) * (1.0 - s.E_min)
        implicit = -dscale * np.einsum("ij,jk,ik->i", lam_e, self.ke, u_e)

        dconstraint = self.scaling * (explicit + implicit)
        dconstraint[self.passive_void] = 0.0

        volume = float(np.mean(physical[self.domain_mask]))
        dvolume = np.where(self.domain_mask, 1.0 / self.domain_mask.sum(), 0.0)

        return volume, dvolume, constraint, dconstraint, relaxed, pnorm

    # -- optimisation loop -------------------------------------------------------

    def run(
        self, callback: Callable[[int, float, float, float, np.ndarray], None] | None = None
    ) -> StressResult:
        """Run the optimisation.

        Args:
            callback: Optional ``f(iteration, volume, max_stress, constraint, density)``.
        """
        s = self.settings
        n = self.grid.n_elements

        design = np.full(n, s.initial_density)
        design[self.passive_void] = s.x_min
        design[self.passive_solid] = 1.0
        design = np.clip(design, s.x_min, 1.0)

        mma = MMA(n, s.x_min, 1.0, move=s.move_limit)
        result = StressResult(
            density=design.copy(),
            volume_fraction=1.0,
            max_stress=0.0,
            stress=np.zeros(n),
            grid_shape=self.grid.element_grid_shape(),
        )

        for iteration in range(1, s.max_iterations + 1):
            physical = self._physical(design)
            volume, dvolume, constraint, dconstraint, relaxed, pnorm = self.analyse(physical)
            max_stress = float(relaxed[self.design_mask].max())

            new_design = mma.update(
                design,
                volume,
                self._filter_back(dvolume),
                constraint,
                self._filter_back(dconstraint),
            )
            new_design[self.passive_void] = s.x_min
            new_design[self.passive_solid] = 1.0

            change = float(np.max(np.abs(new_design - design)[self.free_design]))
            design = new_design

            # Adaptive correction so c * sigma_PN tracks the true maximum.
            target = max_stress / (pnorm * s.stress_limit)
            self.scaling = s.adaptive_scaling * target + (1.0 - s.adaptive_scaling) * self.scaling

            result.volume_history.append(volume)
            result.stress_history.append(max_stress)
            result.iterations = iteration
            result.density = physical
            result.stress = relaxed
            result.volume_fraction = volume
            result.max_stress = max_stress

            if callback is not None:
                callback(iteration, volume, max_stress, constraint, physical)

            feasible = max_stress <= 1.02 * s.stress_limit
            if change < s.tolerance and feasible and iteration > 10:
                result.converged = True
                break

        # Report the final design's own analysis, not the one before the last step.
        physical = self._physical(design)
        _, _, _, _, relaxed, _ = self.analyse(physical)
        result.density = physical
        result.stress = relaxed
        result.volume_fraction = float(np.mean(physical[self.domain_mask]))
        result.max_stress = float(relaxed[self.design_mask].max())
        return result
