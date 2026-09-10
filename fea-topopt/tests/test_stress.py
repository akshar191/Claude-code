"""Verification of the stress-constrained optimiser."""

from __future__ import annotations

import numpy as np
import pytest

from fea import BoundaryConditions, FEModel, Material, structured_grid
from fea.material import VON_MISES_FORM_2D, von_mises
from fea.stress_topopt import StressConstrainedOptimizer, StressSettings


def cantilever(nx: int = 16, ny: int = 8, load: float = 0.05):
    grid = structured_grid(nx, ny, lx=nx / ny, ly=1.0)
    model = FEModel(grid, Material(E=1.0, nu=0.3, plane="stress"))
    bcs = BoundaryConditions()
    bcs.fix_nodes(grid.nodes_where(lambda x, y: np.isclose(x, 0.0)), "xy")
    # Load spread over three nodes so the stress field has no point singularity.
    for j in (ny // 2 - 1, ny // 2, ny // 2 + 1):
        bcs.add_force(2 * grid.node_id(nx, j) + 1, -load / 3.0)
    return model, bcs


def test_von_mises_quadratic_form_matches_the_closed_form():
    rng = np.random.default_rng(0)
    sigma = rng.normal(size=(20, 3))
    quadratic = np.sqrt(np.einsum("ij,jk,ik->i", sigma, VON_MISES_FORM_2D, sigma))
    assert np.allclose(quadratic, von_mises(sigma))


def test_constraint_gradient_matches_finite_differences():
    """The adjoint sensitivity of the aggregated stress must match central differences.

    The gradient mixes an explicit relaxation term with an implicit term through
    the displacement field; a sign error in either still produces plausible
    looking designs, so this is the check that matters.
    """
    model, bcs = cantilever()
    optimiser = StressConstrainedOptimizer(
        model, bcs, StressSettings(stress_limit=1.0, filter_radius=1.5, pnorm=8.0)
    )
    optimiser.scaling = 1.3  # any fixed value; it is a constant in the gradient

    rng = np.random.default_rng(2)
    density = rng.uniform(0.2, 1.0, model.mesh.n_elements)
    _, _, _, analytic, _, _ = optimiser.analyse(density)

    step = 1e-6
    for element in (0, 7, 33, 91):
        perturbed = density.copy()
        perturbed[element] += step
        plus = optimiser.analyse(perturbed)[2]
        perturbed[element] -= 2.0 * step
        minus = optimiser.analyse(perturbed)[2]
        numerical = (plus - minus) / (2.0 * step)
        assert numerical == pytest.approx(analytic[element], rel=1e-4, abs=1e-9)

    # The gradient must change sign across the domain: stiffening a highly
    # stressed element lowers the peak, thickening an idle one raises the norm.
    assert np.any(analytic > 0.0) and np.any(analytic < 0.0)


def test_volume_gradient_pulls_back_through_the_filter_to_unit_sum():
    """Filtering conserves mass, so the pulled-back volume gradient sums to one."""
    model, bcs = cantilever()
    optimiser = StressConstrainedOptimizer(model, bcs, StressSettings(filter_radius=2.0))
    gradient = optimiser._filter_back(np.full(model.mesh.n_elements, 1.0 / model.mesh.n_elements))
    assert gradient.sum() == pytest.approx(1.0)


def test_pnorm_tracks_the_maximum_after_adaptive_scaling():
    """After a few iterations ``c * sigma_PN`` matches the true peak relaxed stress."""
    model, bcs = cantilever()
    settings = StressSettings(stress_limit=0.5, filter_radius=1.5, max_iterations=12, adaptive_scaling=1.0)
    optimiser = StressConstrainedOptimizer(model, bcs, settings)
    optimiser.run()
    physical = optimiser._physical(np.full(model.mesh.n_elements, 0.7))
    _, _, constraint, _, relaxed, pnorm = optimiser.analyse(physical)
    # scaling was set from the previous design, so agreement is approximate.
    assert optimiser.scaling * pnorm == pytest.approx(relaxed.max() / settings.stress_limit, rel=0.3)


def test_volume_is_reduced_while_the_stress_limit_holds():
    """Starting from the full domain, material is removed without breaching the limit.

    The limit is set above the full block's own peak so the start is feasible;
    the optimiser must then shed volume and end feasible, and the final peak must
    sit at the limit (the constraint is active at a minimum-volume optimum).
    """
    model, bcs = cantilever(nx=24, ny=12)
    probe = StressConstrainedOptimizer(model, bcs, StressSettings(stress_limit=1.0, filter_radius=1.5))
    full = np.ones(model.mesh.n_elements)
    peak_full = probe.analyse(full)[4].max()

    limit = 1.25 * peak_full
    settings = StressSettings(stress_limit=limit, filter_radius=1.5, max_iterations=120, initial_density=1.0)
    result = StressConstrainedOptimizer(model, bcs, settings).run()

    assert result.volume_fraction < 0.8
    assert result.max_stress <= 1.03 * limit
    assert result.max_stress >= 0.9 * limit
    assert result.volume_history[0] > result.volume_history[-1]


def test_passive_void_is_excluded_from_design_and_aggregation():
    model, bcs = cantilever()
    grid = model.mesh
    void = grid.element_centroids()[:, 1] > 0.85
    settings = StressSettings(stress_limit=2.0, filter_radius=1.5, max_iterations=8)
    result = StressConstrainedOptimizer(model, bcs, settings, passive_void=void).run()
    assert np.allclose(result.density[void], settings.x_min)
    assert np.all(result.stress[void] < result.stress[~void].max())


def test_invalid_settings_are_rejected():
    with pytest.raises(ValueError, match="relaxation"):
        StressSettings(relaxation=3.5, penalty=3.0)
    with pytest.raises(ValueError, match="stress_limit"):
        StressSettings(stress_limit=0.0)
    with pytest.raises(ValueError, match="pnorm"):
        StressSettings(pnorm=0.5)


def test_non_homogeneous_supports_are_rejected():
    model, bcs = cantilever()
    bcs.fix(0, 0.01)
    with pytest.raises(ValueError, match="Prescribed"):
        StressConstrainedOptimizer(model, bcs)
