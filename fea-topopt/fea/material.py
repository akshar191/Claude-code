"""Linear elastic constitutive models for two-dimensional analysis."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Material:
    """An isotropic linear elastic material.

    Attributes:
        E: Young's modulus.
        nu: Poisson's ratio.
        plane: Either ``"stress"`` (thin plate, sigma_zz = 0) or
            ``"strain"`` (thick body, epsilon_zz = 0).
    """

    E: float = 1.0
    nu: float = 0.3
    plane: str = "stress"

    def __post_init__(self) -> None:
        if self.E <= 0.0:
            raise ValueError(f"Young's modulus must be positive, got {self.E}")
        if not -1.0 < self.nu < 0.5:
            raise ValueError(f"Poisson's ratio must lie in (-1, 0.5), got {self.nu}")
        if self.plane not in ("stress", "strain"):
            raise ValueError(f"plane must be 'stress' or 'strain', got {self.plane!r}")

    @property
    def G(self) -> float:
        """Shear modulus."""
        return self.E / (2.0 * (1.0 + self.nu))

    def constitutive_matrix(self, ndim: int = 2) -> np.ndarray:
        """Return the matrix D relating stress to strain.

        For ``ndim == 2`` the result is 3x3 in the Voigt ordering
        [sigma_xx, sigma_yy, tau_xy] = D [eps_xx, eps_yy, gamma_xy], and the
        ``plane`` attribute selects plane stress or plane strain.

        For ``ndim == 3`` the result is the full 6x6 isotropic matrix in the
        ordering [xx, yy, zz, xy, yz, xz]; ``plane`` is ignored. Shear strains
        are engineering strains throughout.
        """
        E, nu = self.E, self.nu
        if ndim == 3:
            factor = E / ((1.0 + nu) * (1.0 - 2.0 * nu))
            D = np.zeros((6, 6))
            D[:3, :3] = nu
            np.fill_diagonal(D[:3, :3], 1.0 - nu)
            D[3, 3] = D[4, 4] = D[5, 5] = 0.5 * (1.0 - 2.0 * nu)
            return factor * D
        if ndim != 2:
            raise ValueError(f"ndim must be 2 or 3, got {ndim}")
        if self.plane == "stress":
            factor = E / (1.0 - nu**2)
            return factor * np.array(
                [
                    [1.0, nu, 0.0],
                    [nu, 1.0, 0.0],
                    [0.0, 0.0, 0.5 * (1.0 - nu)],
                ]
            )
        factor = E / ((1.0 + nu) * (1.0 - 2.0 * nu))
        return factor * np.array(
            [
                [1.0 - nu, nu, 0.0],
                [nu, 1.0 - nu, 0.0],
                [0.0, 0.0, 0.5 * (1.0 - 2.0 * nu)],
            ]
        )


def von_mises(stress: np.ndarray) -> np.ndarray:
    """Equivalent von Mises stress.

    Args:
        stress: Array of shape (..., 3) holding [sigma_xx, sigma_yy, tau_xy] for
            plane stress, or (..., 6) holding [xx, yy, zz, xy, yz, xz] for a
            full three-dimensional state.

    Returns:
        Array of shape (...) with the von Mises equivalent stress.
    """
    if stress.shape[-1] == 3:
        sxx = stress[..., 0]
        syy = stress[..., 1]
        txy = stress[..., 2]
        return np.sqrt(sxx**2 - sxx * syy + syy**2 + 3.0 * txy**2)
    if stress.shape[-1] == 6:
        sxx, syy, szz = stress[..., 0], stress[..., 1], stress[..., 2]
        txy, tyz, txz = stress[..., 3], stress[..., 4], stress[..., 5]
        return np.sqrt(
            0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
            + 3.0 * (txy**2 + tyz**2 + txz**2)
        )
    raise ValueError(f"stress must have 3 or 6 components, got {stress.shape[-1]}")


# Quadratic form V such that sigma_vm^2 = sigma^T V sigma, for the plane stress
# ordering [xx, yy, xy]. Used by the stress-constrained optimiser, whose
# sensitivities need d(sigma_vm)/d(sigma) = V sigma / sigma_vm.
VON_MISES_FORM_2D = np.array(
    [
        [1.0, -0.5, 0.0],
        [-0.5, 1.0, 0.0],
        [0.0, 0.0, 3.0],
    ]
)


def principal_stresses(stress: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return the in-plane principal stresses (sigma_1 >= sigma_2)."""
    sxx = stress[..., 0]
    syy = stress[..., 1]
    txy = stress[..., 2]
    mean = 0.5 * (sxx + syy)
    radius = np.sqrt((0.5 * (sxx - syy)) ** 2 + txy**2)
    return mean + radius, mean - radius
