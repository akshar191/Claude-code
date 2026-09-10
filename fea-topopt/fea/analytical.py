"""Closed-form elasticity solutions used to verify the finite element solver.

The end-loaded cantilever of Timoshenko and Goodier, *Theory of Elasticity*,
Art. 21, is the reference case. For a beam occupying ``0 <= x <= L`` and
``-c <= y <= c`` with thickness ``t``, loaded at ``x = L`` by a transverse
resultant ``P`` in the +y direction distributed as a parabolic shear traction,
the exact plane stress solution is

    sigma_xx = -P (L - x) y / I
    sigma_yy = 0
    tau_xy   =  P (c^2 - y^2) / (2 I)

with second moment of area ``I = 2 t c^3 / 3``. The matching displacement field
is fixed by ``u = v = 0`` and ``du/dy = 0`` at the origin.

This is a *complete* solution of the elasticity equations rather than a beam
theory approximation, so a converging finite element model must reproduce it to
within discretisation error - including the shear contribution that
Euler-Bernoulli theory omits.
"""

from __future__ import annotations

import numpy as np


def second_moment_of_area(c: float, thickness: float) -> float:
    """Second moment of area of a rectangle of half-height ``c``."""
    return 2.0 * thickness * c**3 / 3.0


def cantilever_displacement(
    x: np.ndarray,
    y: np.ndarray,
    P: float,
    L: float,
    c: float,
    E: float,
    nu: float,
    thickness: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Exact displacement field of the end-loaded cantilever.

    Args:
        x, y: Coordinates at which to evaluate the field.
        P: Transverse end load resultant, positive in +y.
        L: Beam length.
        c: Beam half-height.
        E: Young's modulus.
        nu: Poisson's ratio.
        thickness: Out-of-plane thickness.

    Returns:
        ``(u, v)``, the x and y displacement components.
    """
    I = second_moment_of_area(c, thickness)
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    u = -(P * y) / (6.0 * E * I) * ((6.0 * L - 3.0 * x) * x + (2.0 + nu) * (y**2 - c**2))
    v = (P) / (6.0 * E * I) * (
        3.0 * nu * y**2 * (L - x) + (4.0 + 5.0 * nu) * c**2 * x + (3.0 * L - x) * x**2
    )
    return u, v


def cantilever_stress(
    x: np.ndarray,
    y: np.ndarray,
    P: float,
    L: float,
    c: float,
    thickness: float = 1.0,
) -> np.ndarray:
    """Exact stress field ``[sigma_xx, sigma_yy, tau_xy]`` of the cantilever."""
    I = second_moment_of_area(c, thickness)
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    sxx = -P * (L - x) * y / I
    syy = np.zeros_like(sxx)
    txy = P * (c**2 - y**2) / (2.0 * I)
    return np.stack([sxx, syy, txy], axis=-1)


def cantilever_shear_traction(
    P: float, c: float, thickness: float = 1.0
):
    """Traction callable for the loaded end face, for use with edge integration."""
    I = second_moment_of_area(c, thickness)

    def traction(x: float, y: float) -> tuple[float, float]:
        return 0.0, P * (c**2 - y**2) / (2.0 * I)

    return traction


def cantilever_tip_deflection(
    P: float, L: float, c: float, E: float, nu: float, thickness: float = 1.0
) -> float:
    """Exact centreline deflection at the loaded end, ``v(L, 0)``.

    Equal to the Euler-Bernoulli term ``P L^3 / (3 E I)`` plus a shear
    correction ``P (4 + 5 nu) c^2 L / (6 E I)``.
    """
    I = second_moment_of_area(c, thickness)
    return P / (6.0 * E * I) * ((4.0 + 5.0 * nu) * c**2 * L + 2.0 * L**3)


def pure_bending_displacement(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    M: float,
    I: float,
    E: float,
    nu: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Exact displacement field of a prismatic bar in pure bending.

    Saint-Venant's solution for a bar along x bent by end moments ``M`` about
    the z axis, with the neutral axis at ``y = 0`` and ``I`` the second moment of
    area about z. The only non-zero stress is ``sigma_xx = -M y / I``; the
    cross-section contracts on the tension side and expands on the compression
    side through Poisson's ratio (anticlastic curvature), which a two-dimensional
    model cannot represent. Displacements are fixed by ``u = v = w = 0`` and no
    rotation at the origin.

    Returns:
        ``(u, v, w)``, the displacement components.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    z = np.asarray(z, dtype=float)
    k = M / (E * I)
    u = -k * x * y
    v = 0.5 * k * (x**2 + nu * (y**2 - z**2))
    w = nu * k * y * z
    return u, v, w


def pure_bending_stress(y: np.ndarray, M: float, I: float) -> np.ndarray:
    """Exact stress ``[xx, yy, zz, xy, yz, xz]`` in pure bending."""
    y = np.asarray(y, dtype=float)
    stress = np.zeros(y.shape + (6,))
    stress[..., 0] = -M * y / I
    return stress


def euler_bernoulli_tip_deflection(
    P: float, L: float, c: float, E: float, thickness: float = 1.0
) -> float:
    """Engineering beam theory tip deflection ``P L^3 / (3 E I)``."""
    I = second_moment_of_area(c, thickness)
    return P * L**3 / (3.0 * E * I)
