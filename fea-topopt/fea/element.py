"""Four-node isoparametric quadrilateral (Q4) element for plane elasticity.

Node ordering is counter-clockwise starting from the lower-left corner::

    3 ------- 2          eta
    |         |           ^
    |         |           |
    0 ------- 1           +--> xi

Natural coordinates (xi, eta) each run over [-1, 1].
"""

from __future__ import annotations

import numpy as np

# 2x2 Gauss-Legendre rule: exact for the bilinear element on any parallelogram.
GAUSS_POINTS_2X2 = np.array(
    [
        [-1.0 / np.sqrt(3.0), -1.0 / np.sqrt(3.0)],
        [+1.0 / np.sqrt(3.0), -1.0 / np.sqrt(3.0)],
        [+1.0 / np.sqrt(3.0), +1.0 / np.sqrt(3.0)],
        [-1.0 / np.sqrt(3.0), +1.0 / np.sqrt(3.0)],
    ]
)
GAUSS_WEIGHTS_2X2 = np.ones(4)

# Corner positions in natural coordinates, used for stress extrapolation.
NODE_NATURAL_COORDS = np.array(
    [[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]]
)


def shape_functions(xi: float, eta: float) -> np.ndarray:
    """Bilinear shape functions N_i evaluated at (xi, eta)."""
    return 0.25 * np.array(
        [
            (1.0 - xi) * (1.0 - eta),
            (1.0 + xi) * (1.0 - eta),
            (1.0 + xi) * (1.0 + eta),
            (1.0 - xi) * (1.0 + eta),
        ]
    )


def shape_function_derivatives(xi: float, eta: float) -> np.ndarray:
    """Shape function derivatives with respect to the natural coordinates.

    Returns:
        Array of shape (2, 4): row 0 is dN/dxi, row 1 is dN/deta.
    """
    return 0.25 * np.array(
        [
            [-(1.0 - eta), (1.0 - eta), (1.0 + eta), -(1.0 + eta)],
            [-(1.0 - xi), -(1.0 + xi), (1.0 + xi), (1.0 - xi)],
        ]
    )


def strain_displacement_matrix(
    coords: np.ndarray, xi: float, eta: float
) -> tuple[np.ndarray, float]:
    """Build the strain-displacement matrix B and the Jacobian determinant.

    Args:
        coords: (4, 2) array of nodal x, y coordinates in element order.
        xi, eta: Natural coordinates of the evaluation point.

    Returns:
        ``(B, detJ)`` where ``B`` has shape (3, 8) mapping the element
        displacement vector [u0, v0, u1, v1, ...] to [eps_xx, eps_yy, gamma_xy],
        and ``detJ`` is the determinant of the Jacobian.

    Raises:
        ValueError: If the element is inverted or degenerate at this point.
    """
    dN_natural = shape_function_derivatives(xi, eta)  # (2, 4)
    jacobian = dN_natural @ coords  # (2, 2): [[dx/dxi, dy/dxi], [dx/deta, dy/deta]]
    detJ = float(np.linalg.det(jacobian))
    if detJ <= 0.0:
        raise ValueError(
            f"Non-positive Jacobian determinant ({detJ:.3e}); element is inverted "
            "or has a re-entrant corner."
        )

    dN_xy = np.linalg.solve(jacobian, dN_natural)  # (2, 4): rows dN/dx, dN/dy

    B = np.zeros((3, 8))
    B[0, 0::2] = dN_xy[0]  # eps_xx = du/dx
    B[1, 1::2] = dN_xy[1]  # eps_yy = dv/dy
    B[2, 0::2] = dN_xy[1]  # gamma_xy = du/dy + dv/dx
    B[2, 1::2] = dN_xy[0]
    return B, detJ


def element_stiffness(
    coords: np.ndarray, D: np.ndarray, thickness: float = 1.0
) -> np.ndarray:
    """Integrate the 8x8 element stiffness matrix k = t * int B^T D B dA."""
    k = np.zeros((8, 8))
    for (xi, eta), weight in zip(GAUSS_POINTS_2X2, GAUSS_WEIGHTS_2X2):
        B, detJ = strain_displacement_matrix(coords, xi, eta)
        k += weight * thickness * detJ * (B.T @ D @ B)
    return k


def element_strains_at_gauss_points(
    coords: np.ndarray, u_element: np.ndarray
) -> np.ndarray:
    """Strains at the four Gauss points; shape (4, 3)."""
    strains = np.empty((4, 3))
    for gp, (xi, eta) in enumerate(GAUSS_POINTS_2X2):
        B, _ = strain_displacement_matrix(coords, xi, eta)
        strains[gp] = B @ u_element
    return strains


def extrapolate_gauss_to_nodes(gauss_values: np.ndarray) -> np.ndarray:
    """Extrapolate Gauss-point quantities to the element corners.

    The 2x2 Gauss points form a bilinear "sub-element" scaled by sqrt(3); the
    standard extrapolation evaluates that bilinear field at the corner nodes.

    Args:
        gauss_values: (4, m) array of values at the Gauss points.

    Returns:
        (4, m) array of values at the corner nodes.
    """
    r = np.sqrt(3.0)
    extrapolation = np.array(
        [shape_functions(r * xi, r * eta) for xi, eta in NODE_NATURAL_COORDS]
    )
    return extrapolation @ gauss_values


def element_area(coords: np.ndarray) -> float:
    """Area of the quadrilateral via the shoelace formula."""
    x, y = coords[:, 0], coords[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
