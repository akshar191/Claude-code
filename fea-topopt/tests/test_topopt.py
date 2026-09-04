"""Verification of the SIMP optimiser: sensitivities, constraints, and filtering."""

from __future__ import annotations

import numpy as np
import pytest

from fea import BoundaryConditions, FEModel, Material, TopologyOptimizer, TopOptSettings
from fea.mesh import structured_grid
from fea.topopt import build_filter_matrix


def cantilever_problem(nx: int = 24, ny: int = 12):
    """A small cantilever design problem used throughout these tests."""
    grid = structured_grid(nx, ny, lx=nx / ny, ly=1.0)
    model = FEModel(grid, Material(E=1.0, nu=0.3, plane="stress"))

    bcs = BoundaryConditions()
    bcs.fix_nodes(grid.nodes_where(lambda x, y: np.isclose(x, 0.0)), direction="xy")
    bcs.add_force(2 * grid.node_id(nx, ny // 2) + 1, -1.0)
    return model, bcs


def test_filter_matrix_is_symmetric_and_local():
    """Filter weights must be symmetric and vanish outside the radius."""
    grid = structured_grid(8, 6, lx=8.0, ly=6.0)
    H = build_filter_matrix(grid, radius=1.5).toarray()

    assert np.allclose(H, H.T)
    assert np.all(H >= 0.0)

    centroids = grid.element_centroids()
    distances = np.linalg.norm(centroids[:, None, :] - centroids[None, :, :], axis=-1)
    assert np.all(H[distances >= 1.5] == 0.0)


def test_analytical_sensitivity_matches_finite_differences():
    """The adjoint-free compliance gradient must match a numerical derivative.

    For self-adjoint minimum compliance the sensitivity is available in closed
    form; checking it against a central difference is the standard guard against
    a sign error or a missing penalty term, which would otherwise still produce
    a plausible-looking layout.
    """
    model, bcs = cantilever_problem(nx=12, ny=6)
    settings = TopOptSettings(volume_fraction=0.5, penalty=3.0, filter_radius=0.0)
    optimizer = TopologyOptimizer(model, bcs, settings)

    rng = np.random.default_rng(3)
    density = rng.uniform(0.3, 0.9, size=model.mesh.n_elements)

    _, analytical, _ = optimizer._compliance_and_sensitivity(density, settings.penalty)

    step = 1e-6
    for element in (0, 17, 43, 65):
        perturbed = density.copy()
        perturbed[element] += step
        c_plus, _, _ = optimizer._compliance_and_sensitivity(perturbed, settings.penalty)
        perturbed[element] -= 2.0 * step
        c_minus, _, _ = optimizer._compliance_and_sensitivity(perturbed, settings.penalty)

        numerical = (c_plus - c_minus) / (2.0 * step)
        assert numerical == pytest.approx(analytical[element], rel=1e-4)


def test_compliance_decreases_and_volume_constraint_is_met():
    """The optimiser must lower compliance while holding the volume constraint."""
    model, bcs = cantilever_problem()
    settings = TopOptSettings(volume_fraction=0.4, filter_radius=1.5, max_iterations=40)
    result = TopologyOptimizer(model, bcs, settings).run()

    assert result.compliance < result.history[0]
    assert np.mean(result.density) == pytest.approx(0.4, abs=1e-3)
    assert result.density.min() >= 0.0
    assert result.density.max() <= 1.0


def test_uniform_design_compliance_matches_a_direct_solve():
    """At uniform density the optimiser's compliance equals a plain FE solve."""
    model, bcs = cantilever_problem(nx=16, ny=8)
    settings = TopOptSettings(volume_fraction=0.5, penalty=3.0, filter_radius=0.0)
    optimizer = TopologyOptimizer(model, bcs, settings)

    density = np.full(model.mesh.n_elements, 0.6)
    compliance, _, _ = optimizer._compliance_and_sensitivity(density, settings.penalty)

    scale = settings.E_min + density**settings.penalty * (1.0 - settings.E_min)
    direct = model.solve(bcs, scale=scale).compliance
    assert compliance == pytest.approx(direct, rel=1e-9)


def test_passive_regions_are_respected():
    """Elements forced solid or void must keep those densities throughout."""
    model, bcs = cantilever_problem()
    grid = model.mesh
    centroids = grid.element_centroids()

    solid = centroids[:, 1] > 0.9 * grid.ly
    void = centroids[:, 1] < 0.1 * grid.ly

    settings = TopOptSettings(volume_fraction=0.5, filter_radius=0.0, max_iterations=15)
    result = TopologyOptimizer(
        model, bcs, settings, passive_solid=solid, passive_void=void
    ).run()

    assert np.allclose(result.density[solid], 1.0)
    assert np.allclose(result.density[void], 0.0)


def test_filter_suppresses_checkerboarding():
    """Filtering must remove the alternating solid-void instability.

    Without a filter the optimiser drives the design towards alternating solid
    and void elements: a pattern with no physical length scale that no process
    can manufacture. The compliance values of the two runs are deliberately not
    compared, because the filter also imposes a minimum member size whose
    stiffness cost has nothing to do with checkerboarding.
    """
    model, bcs = cantilever_problem(nx=40, ny=20)

    unfiltered = TopologyOptimizer(
        model, bcs, TopOptSettings(volume_fraction=0.4, filter_radius=0.0, max_iterations=120)
    ).run()
    filtered = TopologyOptimizer(
        model, bcs, TopOptSettings(volume_fraction=0.4, filter_radius=2.0, max_iterations=120)
    ).run()

    assert unfiltered.checkerboard_measure() > 0.05
    assert filtered.checkerboard_measure() < 0.4 * unfiltered.checkerboard_measure()
    assert filtered.discreteness() < 0.35


def test_density_filter_agrees_with_sensitivity_filter_in_character():
    """Both filter types must converge to a comparable, well-defined structure."""
    model, bcs = cantilever_problem()
    results = {}
    for filter_type in ("sensitivity", "density"):
        settings = TopOptSettings(
            volume_fraction=0.4,
            filter_radius=1.8,
            filter_type=filter_type,
            max_iterations=60,
        )
        results[filter_type] = TopologyOptimizer(model, bcs, settings).run()

    a, b = results["sensitivity"].compliance, results["density"].compliance
    assert abs(a - b) / a < 0.25
    for result in results.values():
        assert np.mean(result.density) == pytest.approx(0.4, abs=5e-3)


def test_invalid_settings_are_rejected():
    with pytest.raises(ValueError, match="volume_fraction"):
        TopOptSettings(volume_fraction=1.5)
    with pytest.raises(ValueError, match="penalty"):
        TopOptSettings(penalty=0.5)
    with pytest.raises(ValueError, match="filter_type"):
        TopOptSettings(filter_type="gaussian")


def test_non_structured_mesh_is_rejected():
    """Topology optimisation needs the grid structure and must say so."""
    from fea.mesh import QuadMesh

    mesh = QuadMesh(
        nodes=np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]),
        elements=np.array([[0, 1, 2, 3]]),
    )
    model = FEModel(mesh, Material())
    with pytest.raises(TypeError, match="StructuredGrid"):
        TopologyOptimizer(model, BoundaryConditions())
