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

    def constitutive_matrix(self) -> np.ndarray:
        """Return the 3x3 matrix D relating stress to strain.

        Voigt ordering is [sigma_xx, sigma_yy, tau_xy] = D [eps_xx, eps_yy, gamma_xy],
        where gamma_xy is the engineering shear strain.
        """
        E, nu = self.E, self.nu
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
    """Equivalent von Mises stress for plane stress states.

    Args:
        stress: Array of shape (..., 3) holding [sigma_xx, sigma_yy, tau_xy].

    Returns:
        Array of shape (...) with the von Mises equivalent stress.
    """
    sxx = stress[..., 0]
    syy = stress[..., 1]
    txy = stress[..., 2]
    return np.sqrt(sxx**2 - sxx * syy + syy**2 + 3.0 * txy**2)


def principal_stresses(stress: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return the in-plane principal stresses (sigma_1 >= sigma_2)."""
    sxx = stress[..., 0]
    syy = stress[..., 1]
    txy = stress[..., 2]
    mean = 0.5 * (sxx + syy)
    radius = np.sqrt((0.5 * (sxx - syy)) ** 2 + txy**2)
    return mean + radius, mean - radius
