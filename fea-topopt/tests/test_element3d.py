"""Verification of the H8 hexahedral element and the three-dimensional solver."""

from __future__ import annotations

import numpy as np
import pytest

from fea.analytical import pure_bending_displacement, pure_bending_stress
from fea.element3d import (
    element_stiffness,
    element_volume,
    face_traction_forces,
    shape_functions,
    strain_displacement_matrix,
)
from fea.material import Material, von_mises
from fea.mesh import structured_grid_3d
from fea.solver import BoundaryConditions, FEModel

UNIT_CUBE = np.array(
    [
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0], [1.0, 0.0, 1.0], [1.0, 1.0, 1.0], [0.0, 1.0, 1.0],
    ]
)


def test_shape_functions_are_a_partition_of_unity_and_interpolate_corners():
    """N_i sums to one everywhere and equals the Kronecker delta at the corners."""
    from fea.element3d import NODE_NATURAL_COORDS

    for point in ((0.0, 0.0, 0.0), (0.3, -0.7, 0.5), (-1.0, 1.0, -1.0)):
        assert shape_functions(*point).sum() == pytest.approx(1.0)

    for i, corner in enumerate(NODE_NATURAL_COORDS):
        N = shape_functions(*corner)
        expected = np.zeros(8)
        expected[i] = 1.0
        assert np.allclose(N, expected)


def test_stiffness_is_symmetric_with_six_rigid_body_modes():
    """A free hexahedron is singular in exactly the six rigid body modes."""
    D = Material(E=210e3, nu=0.3).constitutive_matrix(ndim=3)
    ke = element_stiffness(UNIT_CUBE, D)

    assert ke.shape == (24, 24)
    assert np.allclose(ke, ke.T)
    assert np.linalg.matrix_rank(ke, tol=1e-8 * np.abs(ke).max()) == 18

    x, y, z = UNIT_CUBE.T
    modes = []
    for axis in range(3):
        translation = np.zeros(24)
        translation[axis::3] = 1.0
        modes.append(translation)
    # Infinitesimal rotations about each axis.
    for a, b in ((1, 2), (2, 0), (0, 1)):
        rotation = np.zeros(24)
        rotation[a::3] = -UNIT_CUBE[:, b]
        rotation[b::3] = UNIT_CUBE[:, a]
        modes.append(rotation)

    for mode in modes:
        assert np.allclose(ke @ mode, 0.0, atol=1e-9 * np.abs(ke).max())


def test_constant_strain_is_reproduced_on_a_distorted_hexahedron():
    """A linear displacement field yields exactly its own strain everywhere."""
    rng = np.random.default_rng(11)
    coords = UNIT_CUBE + rng.uniform(-0.15, 0.15, size=(8, 3))

    strain = np.array([1e-3, -4e-4, 2e-4, 5e-4, -3e-4, 1.5e-4])  # xx yy zz xy yz xz
    grad = np.array(
        [
            [strain[0], 0.5 * strain[3], 0.5 * strain[5]],
            [0.5 * strain[3], strain[1], 0.5 * strain[4]],
            [0.5 * strain[5], 0.5 * strain[4], strain[2]],
        ]
    )
    u = (coords @ grad.T).ravel()

    for point in ((0.0, 0.0, 0.0), (0.4, -0.6, 0.2), (-0.9, 0.9, 0.9)):
        B, detJ = strain_displacement_matrix(coords, *point)
        assert detJ > 0.0
        assert np.allclose(B @ u, strain, atol=1e-12)


def test_element_volume():
    assert element_volume(UNIT_CUBE) == pytest.approx(1.0)
    assert element_volume(UNIT_CUBE * [2.0, 3.0, 0.5]) == pytest.approx(3.0)


def test_inverted_hexahedron_is_rejected():
    tangled = UNIT_CUBE.copy()
    tangled[[0, 1]] = tangled[[1, 0]]
    with pytest.raises(ValueError, match="Jacobian"):
        element_stiffness(tangled, Material().constitutive_matrix(ndim=3))


def test_face_traction_integrates_to_the_resultant():
    """A uniform pressure on one face must sum to pressure times area."""
    grid = structured_grid_3d(3, 2, 2, lx=3.0, ly=2.0, lz=1.0)
    face_nodes = grid.nodes_where(lambda x, y, z: np.isclose(x, 3.0))

    faces = []
    for k in range(grid.nz):
        for j in range(grid.ny):
            faces.append(
                (
                    grid.node_id(grid.nx, j, k),
                    grid.node_id(grid.nx, j + 1, k),
                    grid.node_id(grid.nx, j + 1, k + 1),
                    grid.node_id(grid.nx, j, k + 1),
                )
            )
    forces = face_traction_forces(grid.nodes, faces, lambda x, y, z: (2.5, 0.0, 0.0))
    total_x = sum(v for d, v in forces.items() if d % 3 == 0)
    assert total_x == pytest.approx(2.5 * 2.0 * 1.0)
    assert set(d // 3 for d in forces) <= set(face_nodes.tolist())


def test_three_dimensional_patch_test():
    """Constant strain on a randomly distorted brick mesh, driven from the boundary."""
    rng = np.random.default_rng(5)
    grid = structured_grid_3d(3, 3, 3, lx=3.0, ly=3.0, lz=3.0)
    interior = grid.nodes_where(
        lambda x, y, z: (x > 1e-9) & (x < 3 - 1e-9) & (y > 1e-9) & (y < 3 - 1e-9)
        & (z > 1e-9) & (z < 3 - 1e-9)
    )
    grid.nodes[interior] += rng.uniform(-0.25, 0.25, size=(interior.size, 3))

    material = Material(E=1000.0, nu=0.3)
    model = FEModel(grid, material)
    assert not model.has_uniform_elements

    strain = np.array([8e-4, -2e-4, 3e-4, 4e-4, -1e-4, 2e-4])
    grad = np.array(
        [
            [strain[0], 0.5 * strain[3], 0.5 * strain[5]],
            [0.5 * strain[3], strain[1], 0.5 * strain[4]],
            [0.5 * strain[5], 0.5 * strain[4], strain[2]],
        ]
    )
    u_exact = (grid.nodes @ grad.T).ravel()

    boundary = np.setdiff1d(np.arange(grid.n_nodes), interior)
    bcs = BoundaryConditions()
    for node in boundary:
        for c in range(3):
            bcs.fix(3 * node + c, u_exact[3 * node + c])

    solution = model.solve(bcs)
    assert np.allclose(solution.displacements, u_exact, atol=1e-10)

    expected_stress = material.constitutive_matrix(ndim=3) @ strain
    assert np.allclose(model.element_stresses(solution.displacements), expected_stress, atol=1e-8)


def test_uniaxial_tension_is_exact():
    """A bar in uniform tension: exact strain and full Poisson contraction."""
    E, nu, sigma = 200.0, 0.3, 5.0
    grid = structured_grid_3d(4, 2, 2, lx=4.0, ly=1.0, lz=1.0)
    model = FEModel(grid, Material(E=E, nu=nu))

    bcs = BoundaryConditions()
    bcs.fix_nodes(grid.nodes_where(lambda x, y, z: np.isclose(x, 0.0)), "x", dofs_per_node=3)
    bcs.fix_nodes(grid.nodes_where(lambda x, y, z: np.isclose(y, 0.0)), "y", dofs_per_node=3)
    bcs.fix_nodes(grid.nodes_where(lambda x, y, z: np.isclose(z, 0.0)), "z", dofs_per_node=3)

    faces = [
        (grid.node_id(4, j, k), grid.node_id(4, j + 1, k), grid.node_id(4, j + 1, k + 1), grid.node_id(4, j, k + 1))
        for k in range(2) for j in range(2)
    ]
    for dof, value in face_traction_forces(grid.nodes, faces, lambda x, y, z: (sigma, 0, 0)).items():
        bcs.add_force(dof, value)

    # The direct solver isolates the element's exactness from iterative tolerance.
    displacements = model.solve(bcs, method="direct").displacements
    u = displacements.reshape(-1, 3)
    assert np.allclose(u[:, 0], sigma / E * grid.nodes[:, 0], atol=1e-12)
    assert np.allclose(u[:, 1], -nu * sigma / E * grid.nodes[:, 1], atol=1e-12)
    assert np.allclose(u[:, 2], -nu * sigma / E * grid.nodes[:, 2], atol=1e-12)

    stresses = model.element_stresses(displacements)
    assert np.allclose(stresses[..., 0], sigma, atol=1e-10)
    assert np.allclose(stresses[..., 1:], 0.0, atol=1e-10)


def bending_model(nx: int, ny: int, nz: int):
    """Bar in pure bending: exact displacements on one end, the exact moment traction on the other."""
    L, h, b = 4.0, 1.0, 0.5
    E, nu, M = 1000.0, 0.3, 2.0
    I = b * h**3 / 12.0
    grid = structured_grid_3d(nx, ny, nz, lx=L, ly=h, lz=b, y0=-h / 2, z0=-b / 2)
    model = FEModel(grid, Material(E=E, nu=nu))

    bcs = BoundaryConditions()
    left = grid.nodes_where(lambda x, y, z: np.isclose(x, 0.0))
    u, v, w = pure_bending_displacement(*grid.nodes[left].T, M, I, E, nu)
    bcs.prescribe_values(3 * left, u)
    bcs.prescribe_values(3 * left + 1, v)
    bcs.prescribe_values(3 * left + 2, w)

    faces = [
        (grid.node_id(nx, j, k), grid.node_id(nx, j + 1, k), grid.node_id(nx, j + 1, k + 1), grid.node_id(nx, j, k + 1))
        for k in range(nz) for j in range(ny)
    ]
    traction = lambda x, y, z: (-M * y / I, 0.0, 0.0)
    for dof, value in face_traction_forces(grid.nodes, faces, traction).items():
        bcs.add_force(dof, value)

    u_exact = np.empty(grid.n_dofs)
    u_exact[0::3], u_exact[1::3], u_exact[2::3] = pure_bending_displacement(*grid.nodes.T, M, I, E, nu)
    return grid, model, bcs, u_exact, (M, I)


def test_pure_bending_converges_at_second_order():
    """The H8 solution approaches Saint-Venant's exact bending field at O(h^2)."""
    sizes, errors = [], []
    for nx, ny, nz in ((8, 2, 1), (16, 4, 2), (32, 8, 4)):
        grid, model, bcs, u_exact, _ = bending_model(nx, ny, nz)
        u = model.solve(bcs).displacements
        errors.append(np.linalg.norm(u - u_exact) / np.linalg.norm(u_exact))
        sizes.append(1.0 / ny)

    rate = np.polyfit(np.log(sizes), np.log(errors), 1)[0]
    assert errors[-1] < 1e-2
    assert 1.7 < rate < 2.3, f"expected O(h^2), measured O(h^{rate:.2f})"


def test_pure_bending_stress_recovery():
    """Bending stress matches -M y / I; parasitic transverse stress decays with h.

    The fully integrated trilinear hexahedron is known to produce spurious
    transverse normal stresses in bending (a Poisson-coupled form of locking).
    They are a discretisation artefact, so they must shrink under refinement
    while the bending stress itself is captured accurately.
    """
    parasitic = []
    for nx, ny, nz in ((16, 4, 2), (32, 8, 4)):
        grid, model, bcs, _, (M, I) = bending_model(nx, ny, nz)
        u = model.solve(bcs).displacements
        nodal = model.nodal_stresses(u)

        sample = grid.nodes_where(lambda x, y, z: np.isclose(x, 2.0))
        exact = pure_bending_stress(grid.nodes[sample, 1], M, I)
        scale = np.abs(exact[:, 0]).max()
        assert np.allclose(nodal[sample, 0], exact[:, 0], atol=0.02 * scale)
        assert von_mises(nodal[sample]).max() == pytest.approx(scale, rel=0.06)
        parasitic.append(np.abs(nodal[sample, 1:3]).max() / scale)

    assert parasitic[1] < 0.6 * parasitic[0]
    assert parasitic[1] < 0.06
