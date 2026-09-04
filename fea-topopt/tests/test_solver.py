"""Solver verification against the exact end-loaded cantilever solution."""

from __future__ import annotations

import numpy as np
import pytest

from fea import (
    BoundaryConditions,
    FEModel,
    Material,
    cantilever_displacement,
    cantilever_shear_traction,
    cantilever_tip_deflection,
    edge_traction_forces,
    euler_bernoulli_tip_deflection,
    structured_grid,
)

LENGTH, HEIGHT, THICKNESS = 8.0, 2.0, 1.0
HALF_HEIGHT = HEIGHT / 2.0
LOAD, E, NU = 10.0, 1000.0, 0.3


def build(nx: int, ny: int):
    """The verification cantilever: exact displacements in, exact traction out."""
    mesh = structured_grid(nx, ny, lx=LENGTH, ly=HEIGHT, y0=-HALF_HEIGHT)
    model = FEModel(mesh, Material(E=E, nu=NU, plane="stress"), thickness=THICKNESS)

    bcs = BoundaryConditions()
    left = mesh.nodes_where(lambda x, y: np.isclose(x, 0.0))
    u, v = cantilever_displacement(
        mesh.nodes[left, 0], mesh.nodes[left, 1], LOAD, LENGTH, HALF_HEIGHT, E, NU, THICKNESS
    )
    bcs.prescribe_values(2 * left, u)
    bcs.prescribe_values(2 * left + 1, v)

    right = mesh.nodes_where(lambda x, y: np.isclose(x, LENGTH))
    right = right[np.argsort(mesh.nodes[right, 1])]
    traction = cantilever_shear_traction(LOAD, HALF_HEIGHT, THICKNESS)
    edges = list(zip(right[:-1], right[1:]))
    for dof, value in edge_traction_forces(mesh, edges, traction, THICKNESS).items():
        bcs.add_force(dof, value)

    return mesh, model, bcs


def test_consistent_edge_traction_recovers_the_load_resultant():
    """The integrated parabolic shear traction must sum to the applied load."""
    mesh, _, bcs = build(8, 8)
    total_y = sum(value for dof, value in bcs.forces.items() if dof % 2 == 1)
    assert total_y == pytest.approx(LOAD, rel=1e-12)


def test_tip_deflection_matches_elasticity_solution():
    """A fine mesh reproduces the exact tip deflection to better than 0.1%."""
    mesh, model, bcs = build(128, 32)
    solution = model.solve(bcs)

    tip = solution.displacements[2 * mesh.nearest_node(LENGTH, 0.0) + 1]
    exact = cantilever_tip_deflection(LOAD, LENGTH, HALF_HEIGHT, E, NU, THICKNESS)

    assert abs(tip - exact) / exact < 1e-3


def test_shear_deformation_is_captured():
    """The 2D solution must exceed the Euler-Bernoulli deflection by the shear term.

    Beam theory ignores transverse shear, so it under-predicts the deflection of
    this moderately stubby beam by about 4%. A plane elasticity solver has to
    recover that difference; a solver that merely reproduced beam theory would
    be wrong here.
    """
    exact = cantilever_tip_deflection(LOAD, LENGTH, HALF_HEIGHT, E, NU, THICKNESS)
    beam_theory = euler_bernoulli_tip_deflection(LOAD, LENGTH, HALF_HEIGHT, E, THICKNESS)
    shear_fraction = (exact - beam_theory) / exact
    assert shear_fraction == pytest.approx(0.0412, abs=5e-4)

    _, model, bcs = build(128, 32)
    tip_fe = model.solve(bcs).displacements[
        2 * structured_grid(128, 32, LENGTH, HEIGHT, -HALF_HEIGHT).nearest_node(LENGTH, 0.0) + 1
    ]
    assert tip_fe > beam_theory * 1.03


def test_convergence_is_second_order():
    """Refining the mesh must reduce the displacement error at the expected rate."""
    sizes, errors = [], []
    for nx, ny in ((16, 4), (32, 8), (64, 16), (128, 32)):
        mesh, model, bcs = build(nx, ny)
        solution = model.solve(bcs)

        u_exact = np.empty(mesh.n_dofs)
        u_exact[0::2], u_exact[1::2] = cantilever_displacement(
            mesh.nodes[:, 0], mesh.nodes[:, 1], LOAD, LENGTH, HALF_HEIGHT, E, NU, THICKNESS
        )
        errors.append(
            np.linalg.norm(solution.displacements - u_exact) / np.linalg.norm(u_exact)
        )
        sizes.append(HEIGHT / ny)

    rate = np.polyfit(np.log(sizes), np.log(errors), 1)[0]
    assert 1.8 < rate < 2.2, f"expected O(h^2) convergence, measured O(h^{rate:.2f})"
    assert errors[-1] < 1e-3


def test_reactions_balance_the_applied_load():
    """Global equilibrium: support reactions must cancel the applied forces."""
    mesh, model, bcs = build(32, 8)
    solution = model.solve(bcs)

    left = mesh.nodes_where(lambda x, y: np.isclose(x, 0.0))
    reaction_y = solution.reactions[2 * left + 1].sum()
    assert reaction_y == pytest.approx(-LOAD, rel=1e-8)


def test_stress_recovery_matches_the_exact_bending_stress():
    """Recovered nodal stress at the support matches the analytical distribution."""
    from fea.analytical import cantilever_stress

    mesh, model, bcs = build(64, 16)
    solution = model.solve(bcs)
    stresses = model.nodal_stresses(solution.displacements)

    # Sample away from the loaded and supported ends, where the recovered field
    # is smooth and the exact solution applies without end effects.
    sample = mesh.nodes_where(lambda x, y: np.isclose(x, LENGTH / 2.0))
    exact = cantilever_stress(
        mesh.nodes[sample, 0], mesh.nodes[sample, 1], LOAD, LENGTH, HALF_HEIGHT, THICKNESS
    )
    scale = np.abs(exact[:, 0]).max()
    assert np.allclose(stresses[sample, 0], exact[:, 0], atol=0.02 * scale)


def test_unconstrained_model_is_rejected():
    """A model with no supports must raise rather than return rigid body motion."""
    mesh = structured_grid(2, 2)
    model = FEModel(mesh, Material())
    bcs = BoundaryConditions().add_force(0, 1.0)
    with pytest.raises(ValueError, match="rigid body"):
        model.solve(bcs)
