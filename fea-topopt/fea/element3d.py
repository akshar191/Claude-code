"""Eight-node trilinear hexahedral (H8) element for three-dimensional elasticity.

Node ordering follows the usual convention: the bottom face (zeta = -1) is
numbered counter-clockwise from the corner at (-1, -1), then the top face in the
same order directly above it::

        7 -------- 6
       /|         /|          zeta
      4 -------- 5 |           ^   eta
      | |        | |           |  /
      | 3 -------|-2           | /
      |/         |/            +-----> xi
      0 -------- 1

Strain and stress use the Voigt ordering [xx, yy, zz, xy, yz, xz] with
engineering shear strains, matching :meth:`fea.material.Material.constitutive_matrix`.
"""

from __future__ import annotations

import itertools

import numpy as np

NODE_NATURAL_COORDS = np.array(
    [
        [-1.0, -1.0, -1.0],
        [1.0, -1.0, -1.0],
        [1.0, 1.0, -1.0],
        [-1.0, 1.0, -1.0],
        [-1.0, -1.0, 1.0],
        [1.0, -1.0, 1.0],
        [1.0, 1.0, 1.0],
        [-1.0, 1.0, 1.0],
    ]
)

_g = 1.0 / np.sqrt(3.0)
# 2x2x2 Gauss-Legendre rule, exact for the trilinear element on a parallelepiped.
GAUSS_POINTS_2X2X2 = np.array(list(itertools.product([-_g, _g], repeat=3)))
GAUSS_WEIGHTS_2X2X2 = np.ones(8)


def shape_functions(xi: float, eta: float, zeta: float) -> np.ndarray:
    """Trilinear shape functions N_i evaluated at (xi, eta, zeta)."""
    s = NODE_NATURAL_COORDS
    return 0.125 * (1.0 + s[:, 0] * xi) * (1.0 + s[:, 1] * eta) * (1.0 + s[:, 2] * zeta)


def shape_function_derivatives(xi: float, eta: float, zeta: float) -> np.ndarray:
    """Derivatives with respect to the natural coordinates; shape (3, 8)."""
    s = NODE_NATURAL_COORDS
    return 0.125 * np.array(
        [
            s[:, 0] * (1.0 + s[:, 1] * eta) * (1.0 + s[:, 2] * zeta),
            s[:, 1] * (1.0 + s[:, 0] * xi) * (1.0 + s[:, 2] * zeta),
            s[:, 2] * (1.0 + s[:, 0] * xi) * (1.0 + s[:, 1] * eta),
        ]
    )


def strain_displacement_matrix(
    coords: np.ndarray, xi: float, eta: float, zeta: float
) -> tuple[np.ndarray, float]:
    """Strain-displacement matrix B (6 x 24) and the Jacobian determinant.

    The element displacement vector is ordered [u0, v0, w0, u1, v1, w1, ...].

    Raises:
        ValueError: If the element is inverted or degenerate at this point.
    """
    dN_natural = shape_function_derivatives(xi, eta, zeta)  # (3, 8)
    jacobian = dN_natural @ coords  # (3, 3)
    detJ = float(np.linalg.det(jacobian))
    if detJ <= 0.0:
        raise ValueError(
            f"Non-positive Jacobian determinant ({detJ:.3e}); hexahedron is inverted "
            "or degenerate."
        )

    dN = np.linalg.solve(jacobian, dN_natural)  # rows dN/dx, dN/dy, dN/dz

    B = np.zeros((6, 24))
    B[0, 0::3] = dN[0]  # eps_xx
    B[1, 1::3] = dN[1]  # eps_yy
    B[2, 2::3] = dN[2]  # eps_zz
    B[3, 0::3] = dN[1]  # gamma_xy = du/dy + dv/dx
    B[3, 1::3] = dN[0]
    B[4, 1::3] = dN[2]  # gamma_yz = dv/dz + dw/dy
    B[4, 2::3] = dN[1]
    B[5, 0::3] = dN[2]  # gamma_xz = du/dz + dw/dx
    B[5, 2::3] = dN[0]
    return B, detJ


def element_stiffness(coords: np.ndarray, D: np.ndarray) -> np.ndarray:
    """Integrate the 24x24 element stiffness matrix k = int B^T D B dV."""
    k = np.zeros((24, 24))
    for (xi, eta, zeta), weight in zip(GAUSS_POINTS_2X2X2, GAUSS_WEIGHTS_2X2X2):
        B, detJ = strain_displacement_matrix(coords, xi, eta, zeta)
        k += weight * detJ * (B.T @ D @ B)
    return k


def element_strains_at_gauss_points(coords: np.ndarray, u_element: np.ndarray) -> np.ndarray:
    """Strains at the eight Gauss points; shape (8, 6)."""
    strains = np.empty((8, 6))
    for gp, (xi, eta, zeta) in enumerate(GAUSS_POINTS_2X2X2):
        B, _ = strain_displacement_matrix(coords, xi, eta, zeta)
        strains[gp] = B @ u_element
    return strains


def extrapolate_gauss_to_nodes(gauss_values: np.ndarray) -> np.ndarray:
    """Extrapolate Gauss-point quantities to the eight corners.

    The Gauss points form a trilinear sub-element scaled by sqrt(3); evaluating
    that field at the corners gives the standard nodal extrapolation.
    """
    r = np.sqrt(3.0)
    extrapolation = np.array(
        [shape_functions(r * xi, r * eta, r * zeta) for xi, eta, zeta in NODE_NATURAL_COORDS]
    )
    return extrapolation @ gauss_values


def element_volume(coords: np.ndarray) -> float:
    """Volume of the hexahedron by Gauss integration of the Jacobian."""
    volume = 0.0
    for (xi, eta, zeta), weight in zip(GAUSS_POINTS_2X2X2, GAUSS_WEIGHTS_2X2X2):
        _, detJ = strain_displacement_matrix(coords, xi, eta, zeta)
        volume += weight * detJ
    return volume


def face_traction_forces(
    nodes: np.ndarray, faces, traction
) -> dict[int, float]:
    """Consistent nodal forces from a traction on bilinear quadrilateral faces.

    Args:
        nodes: (n_nodes, 3) array of coordinates.
        faces: Iterable of 4-tuples of node indices, each a face traversed in a
            consistent order (the orientation does not affect the result).
        traction: Callable ``(x, y, z) -> (tx, ty, tz)``.

    Returns:
        Map from global degree of freedom to accumulated nodal force.
    """
    corners = np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]])
    gauss = [(-_g, -_g), (_g, -_g), (_g, _g), (-_g, _g)]
    forces: dict[int, float] = {}

    for face in faces:
        face = np.asarray(face)
        coords = nodes[face]  # (4, 3)
        for xi, eta in gauss:
            N = 0.25 * (1.0 + corners[:, 0] * xi) * (1.0 + corners[:, 1] * eta)
            dN = 0.25 * np.array(
                [corners[:, 0] * (1.0 + corners[:, 1] * eta), corners[:, 1] * (1.0 + corners[:, 0] * xi)]
            )  # (2, 4)
            tangents = dN @ coords  # (2, 3)
            area_element = float(np.linalg.norm(np.cross(tangents[0], tangents[1])))
            point = N @ coords
            t = traction(point[0], point[1], point[2])
            for local, node in enumerate(face):
                weight = N[local] * area_element  # Gauss weight is 1
                for component in range(3):
                    dof = 3 * int(node) + component
                    forces[dof] = forces.get(dof, 0.0) + weight * t[component]
    return forces
