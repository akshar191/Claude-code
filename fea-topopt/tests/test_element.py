"""Element-level verification: reference stiffness, rank, and the patch test."""

from __future__ import annotations

import numpy as np
import pytest

from fea.element import element_area, element_stiffness, strain_displacement_matrix
from fea.material import Material
from fea.mesh import structured_grid
from fea.solver import BoundaryConditions, FEModel

UNIT_SQUARE = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])


def reference_stiffness(nu: float = 0.3) -> np.ndarray:
    """The published unit-square plane stress stiffness matrix.

    Taken from Andreassen et al. (2011), "Efficient topology optimization in
    MATLAB using 88 lines of code", for E = 1 and unit thickness.
    """
    A11 = np.array([[12, 3, -6, -3], [3, 12, 3, 0], [-6, 3, 12, -3], [-3, 0, -3, 12]])
    A12 = np.array([[-6, -3, 0, 3], [-3, -6, -3, -6], [0, -3, -6, 3], [3, -6, 3, -6]])
    B11 = np.array([[-4, 3, -2, 9], [3, -4, -9, 4], [-2, -9, -4, -3], [9, 4, -3, -4]])
    B12 = np.array([[2, -3, 4, -9], [-3, 2, 9, -2], [4, 9, 2, 3], [-9, -2, 3, 2]])
    A = np.block([[A11, A12], [A12.T, A11]])
    B = np.block([[B11, B12], [B12.T, B11]])
    return (A + nu * B) / (24.0 * (1.0 - nu**2))


def test_stiffness_matches_published_reference():
    """The element stiffness reproduces the published matrix to machine precision."""
    D = Material(E=1.0, nu=0.3, plane="stress").constitutive_matrix()
    ke = element_stiffness(UNIT_SQUARE, D)
    assert np.allclose(ke, reference_stiffness(), atol=1e-12)


def test_stiffness_is_symmetric_and_has_three_rigid_body_modes():
    """A free element must be singular in exactly the three rigid body modes."""
    D = Material(E=210e3, nu=0.3).constitutive_matrix()
    ke = element_stiffness(UNIT_SQUARE, D)

    assert np.allclose(ke, ke.T)
    assert np.linalg.matrix_rank(ke, tol=1e-8 * np.abs(ke).max()) == 5

    x, y = UNIT_SQUARE[:, 0], UNIT_SQUARE[:, 1]
    translation_x = np.zeros(8)
    translation_x[0::2] = 1.0
    translation_y = np.zeros(8)
    translation_y[1::2] = 1.0
    rotation = np.zeros(8)
    rotation[0::2] = -y
    rotation[1::2] = x

    for mode in (translation_x, translation_y, rotation):
        assert np.allclose(ke @ mode, 0.0, atol=1e-9 * np.abs(ke).max())


def test_strain_displacement_matrix_reproduces_constant_strain():
    """A linear displacement field must produce exactly its own constant strain."""
    coords = np.array([[0.0, 0.0], [2.0, 0.3], [2.4, 1.7], [0.2, 1.4]])
    eps_xx, eps_yy, gamma_xy = 0.002, -0.0007, 0.0013

    u = np.empty(8)
    for node, (x, y) in enumerate(coords):
        u[2 * node] = eps_xx * x + 0.5 * gamma_xy * y
        u[2 * node + 1] = 0.5 * gamma_xy * x + eps_yy * y

    for xi, eta in ((0.0, 0.0), (-0.5, 0.7), (0.9, -0.2)):
        B, detJ = strain_displacement_matrix(coords, xi, eta)
        assert detJ > 0.0
        assert np.allclose(B @ u, [eps_xx, eps_yy, gamma_xy], atol=1e-12)


def test_inverted_element_is_rejected():
    """A tangled element must fail loudly rather than produce negative stiffness."""
    tangled = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    D = Material().constitutive_matrix()
    with pytest.raises(ValueError, match="Jacobian"):
        element_stiffness(tangled, D)


def test_element_area():
    assert element_area(UNIT_SQUARE) == pytest.approx(1.0)
    assert element_area(np.array([[0.0, 0.0], [3.0, 0.0], [3.0, 2.0], [0.0, 2.0]])) == (
        pytest.approx(6.0)
    )


def test_patch_test_on_a_distorted_mesh():
    """Constant strain must be reproduced exactly on an irregular mesh.

    The patch test is the standard convergence requirement for an element: if a
    linear displacement field is imposed on the boundary of a patch of distorted
    elements, every interior node must land on that same field and every element
    must report the corresponding constant stress.
    """
    rng = np.random.default_rng(7)
    mesh = structured_grid(4, 4, lx=4.0, ly=4.0)

    interior = mesh.nodes_where(
        lambda x, y: (x > 1e-9) & (x < 4.0 - 1e-9) & (y > 1e-9) & (y < 4.0 - 1e-9)
    )
    mesh.nodes[interior] += rng.uniform(-0.3, 0.3, size=(interior.size, 2))

    material = Material(E=2000.0, nu=0.25, plane="stress")
    model = FEModel(mesh, material, thickness=1.0)

    eps_xx, eps_yy, gamma_xy = 1e-3, -4e-4, 6e-4

    def exact(x, y):
        return eps_xx * x + 0.5 * gamma_xy * y, 0.5 * gamma_xy * x + eps_yy * y

    boundary = mesh.nodes_where(
        lambda x, y: np.isclose(x, 0.0)
        | np.isclose(x, 4.0)
        | np.isclose(y, 0.0)
        | np.isclose(y, 4.0)
    )
    bcs = BoundaryConditions()
    for node in boundary:
        u, v = exact(*mesh.nodes[node])
        bcs.fix(2 * node, u)
        bcs.fix(2 * node + 1, v)

    solution = model.solve(bcs)

    u_exact = np.empty(mesh.n_dofs)
    u_exact[0::2], u_exact[1::2] = exact(mesh.nodes[:, 0], mesh.nodes[:, 1])
    assert np.allclose(solution.displacements, u_exact, atol=1e-10)

    expected_stress = material.constitutive_matrix() @ [eps_xx, eps_yy, gamma_xy]
    assert np.allclose(model.element_stresses(solution.displacements), expected_stress, atol=1e-9)
