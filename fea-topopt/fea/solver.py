"""Assembly and solution of the linear elastic finite element problem.

The discrete problem is ``K u = f`` with prescribed displacements handled by
static partitioning: with the degrees of freedom split into free (f) and
constrained (c) sets,

    K_ff u_f = f_f - K_fc u_c,

so non-zero prescribed displacements are supported exactly rather than through
a penalty.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from .element import (
    element_stiffness,
    element_strains_at_gauss_points,
    extrapolate_gauss_to_nodes,
)
from .material import Material, von_mises
from .mesh import QuadMesh, StructuredGrid


@dataclass
class BoundaryConditions:
    """Prescribed displacements and applied nodal forces.

    Attributes:
        prescribed: Map from global degree of freedom to its imposed value.
        forces: Map from global degree of freedom to the applied force.
    """

    prescribed: dict[int, float] = field(default_factory=dict)
    forces: dict[int, float] = field(default_factory=dict)

    def fix(self, dofs, value: float = 0.0) -> "BoundaryConditions":
        """Prescribe ``value`` on one or more degrees of freedom."""
        for dof in np.atleast_1d(dofs).ravel():
            self.prescribed[int(dof)] = float(value)
        return self

    def fix_nodes(self, nodes, direction: str = "xy") -> "BoundaryConditions":
        """Clamp nodes in the x direction, the y direction, or both."""
        for node in np.atleast_1d(nodes).ravel():
            if "x" in direction:
                self.prescribed[2 * int(node)] = 0.0
            if "y" in direction:
                self.prescribed[2 * int(node) + 1] = 0.0
        return self

    def prescribe_values(self, dofs, values) -> "BoundaryConditions":
        """Prescribe a vector of values on a vector of degrees of freedom."""
        dofs = np.atleast_1d(dofs).ravel()
        values = np.atleast_1d(values).ravel()
        if dofs.shape != values.shape:
            raise ValueError(
                f"dofs and values must have equal length, got {dofs.size} and {values.size}"
            )
        for dof, value in zip(dofs, values):
            self.prescribed[int(dof)] = float(value)
        return self

    def add_force(self, dof: int, value: float) -> "BoundaryConditions":
        """Accumulate a nodal force on one degree of freedom."""
        self.forces[int(dof)] = self.forces.get(int(dof), 0.0) + float(value)
        return self

    def add_forces(self, dofs, values) -> "BoundaryConditions":
        """Accumulate a vector of nodal forces."""
        dofs = np.atleast_1d(dofs).ravel()
        values = np.atleast_1d(values).ravel()
        for dof, value in zip(dofs, values):
            self.add_force(int(dof), float(value))
        return self

    def force_vector(self, n_dofs: int) -> np.ndarray:
        """Assemble the global load vector."""
        f = np.zeros(n_dofs)
        for dof, value in self.forces.items():
            f[dof] += value
        return f


@dataclass
class Solution:
    """Result of a static analysis."""

    displacements: np.ndarray
    reactions: np.ndarray
    compliance: float

    def node_displacements(self) -> np.ndarray:
        """(n_nodes, 2) array of nodal displacement components."""
        return self.displacements.reshape(-1, 2)


class FEModel:
    """Plane elasticity model over a quadrilateral mesh.

    Args:
        mesh: The discretised domain.
        material: Isotropic elastic material.
        thickness: Out-of-plane thickness.
    """

    def __init__(
        self, mesh: QuadMesh, material: Material, thickness: float = 1.0
    ) -> None:
        if thickness <= 0.0:
            raise ValueError(f"Thickness must be positive, got {thickness}")
        self.mesh = mesh
        self.material = material
        self.thickness = thickness
        self.D = material.constitutive_matrix()

        self._element_dofs = mesh.all_element_dofs()
        # Sparse triplet pattern, built once and reused for every assembly.
        self._rows = np.repeat(self._element_dofs, 8, axis=1).ravel()
        self._cols = np.tile(self._element_dofs, (1, 8)).ravel()
        self._uniform_stiffness = self._uniform_element_stiffness()

    def _uniform_element_stiffness(self) -> np.ndarray | None:
        """Element stiffness shared by all elements, when that sharing is valid.

        A structured grid is normally made of identical elements, so one stiffness
        matrix can serve the whole mesh. The nodes are public and may have been
        moved, so congruence is verified rather than assumed: every element must
        match the first one up to a translation.
        """
        if not isinstance(self.mesh, StructuredGrid):
            return None

        coords = self.mesh.nodes[self.mesh.elements]  # (n_elements, 4, 2)
        relative = coords - coords[:, :1, :]
        if not np.allclose(relative, relative[0], atol=1e-12):
            return None

        return element_stiffness(
            self.mesh.element_coords(0), self.D, self.thickness
        )

    @property
    def has_uniform_elements(self) -> bool:
        """True when every element is congruent, so one stiffness matrix serves all."""
        return self._uniform_stiffness is not None

    def element_stiffnesses(self) -> np.ndarray:
        """(n_elements, 8, 8) array of unscaled element stiffness matrices."""
        if self._uniform_stiffness is not None:
            return np.broadcast_to(
                self._uniform_stiffness, (self.mesh.n_elements, 8, 8)
            )
        return np.array(
            [
                element_stiffness(self.mesh.element_coords(e), self.D, self.thickness)
                for e in range(self.mesh.n_elements)
            ]
        )

    def assemble(self, scale: np.ndarray | None = None) -> sp.csc_matrix:
        """Assemble the global stiffness matrix.

        Args:
            scale: Optional per-element stiffness multiplier of length
                ``n_elements``. Used by the topology optimiser to apply the SIMP
                interpolation ``E(x) = E_min + x^p (E_0 - E_min)``.

        Returns:
            The global stiffness matrix in CSC format.
        """
        ke = self.element_stiffnesses()
        if scale is not None:
            scale = np.asarray(scale, dtype=float)
            if scale.shape != (self.mesh.n_elements,):
                raise ValueError(
                    f"scale must have length {self.mesh.n_elements}, got {scale.shape}"
                )
            ke = ke * scale[:, None, None]

        K = sp.coo_matrix(
            (ke.ravel(), (self._rows, self._cols)),
            shape=(self.mesh.n_dofs, self.mesh.n_dofs),
        ).tocsc()
        return K

    def solve(
        self, bcs: BoundaryConditions, scale: np.ndarray | None = None
    ) -> Solution:
        """Solve the static problem for the given boundary conditions.

        Raises:
            ValueError: If no degree of freedom is prescribed, which would leave
                the stiffness matrix singular through rigid body motion.
        """
        if not bcs.prescribed:
            raise ValueError(
                "No prescribed degrees of freedom: the model is free to move as a "
                "rigid body and the stiffness matrix is singular."
            )

        n_dofs = self.mesh.n_dofs
        K = self.assemble(scale)
        f = bcs.force_vector(n_dofs)

        constrained = np.array(sorted(bcs.prescribed), dtype=int)
        u = np.zeros(n_dofs)
        u[constrained] = [bcs.prescribed[dof] for dof in constrained]

        free_mask = np.ones(n_dofs, dtype=bool)
        free_mask[constrained] = False
        free = np.flatnonzero(free_mask)

        K_ff = K[free][:, free]
        rhs = f[free] - K[free][:, constrained] @ u[constrained]
        u[free] = spla.spsolve(K_ff.tocsc(), rhs)

        reactions = K @ u - f
        compliance = float(f @ u)
        return Solution(displacements=u, reactions=reactions, compliance=compliance)

    def element_stresses(self, u: np.ndarray) -> np.ndarray:
        """Stresses at the Gauss points of every element.

        Returns:
            (n_elements, 4, 3) array of [sigma_xx, sigma_yy, tau_xy].
        """
        stresses = np.empty((self.mesh.n_elements, 4, 3))
        for e in range(self.mesh.n_elements):
            coords = self.mesh.element_coords(e)
            u_e = u[self.mesh.element_dofs(e)]
            strains = element_strains_at_gauss_points(coords, u_e)
            stresses[e] = strains @ self.D.T
        return stresses

    def nodal_stresses(self, u: np.ndarray) -> np.ndarray:
        """Continuous nodal stresses by extrapolation and area-weighted averaging.

        Gauss-point stresses are extrapolated to the element corners and then
        averaged over the elements sharing each node, which recovers a smooth
        field from the discontinuous element-wise solution.

        Returns:
            (n_nodes, 3) array of averaged nodal stresses.
        """
        gauss_stresses = self.element_stresses(u)
        totals = np.zeros((self.mesh.n_nodes, 3))
        counts = np.zeros(self.mesh.n_nodes)
        for e in range(self.mesh.n_elements):
            corner_stresses = extrapolate_gauss_to_nodes(gauss_stresses[e])
            nodes = self.mesh.elements[e]
            np.add.at(totals, nodes, corner_stresses)
            np.add.at(counts, nodes, 1.0)
        return totals / counts[:, None]

    def nodal_von_mises(self, u: np.ndarray) -> np.ndarray:
        """Averaged nodal von Mises stress."""
        return von_mises(self.nodal_stresses(u))


def edge_traction_forces(
    mesh: QuadMesh, node_pairs, traction, thickness: float = 1.0
) -> dict[int, float]:
    """Convert a distributed edge traction into consistent nodal forces.

    The consistent load vector for an edge is ``int N_i t(s) ds`` along the edge,
    integrated here with a two-point Gauss rule (exact for a traction that varies
    quadratically along a straight edge).

    Args:
        mesh: The mesh the edges belong to.
        node_pairs: Iterable of ``(node_a, node_b)`` node index pairs, each pair
            being one element edge.
        traction: Callable ``(x, y) -> (tx, ty)`` giving the traction vector.
        thickness: Out-of-plane thickness.

    Returns:
        Map from global degree of freedom to the accumulated nodal force.
    """
    gauss = np.array([-1.0 / np.sqrt(3.0), 1.0 / np.sqrt(3.0)])
    forces: dict[int, float] = {}

    for node_a, node_b in node_pairs:
        pa = mesh.nodes[node_a]
        pb = mesh.nodes[node_b]
        length = float(np.linalg.norm(pb - pa))
        jacobian = 0.5 * length  # ds/dxi for a straight two-node edge

        for xi in gauss:
            shape = np.array([0.5 * (1.0 - xi), 0.5 * (1.0 + xi)])
            point = shape[0] * pa + shape[1] * pb
            tx, ty = traction(point[0], point[1])
            for local, node in enumerate((node_a, node_b)):
                weight = shape[local] * jacobian * thickness  # Gauss weight is 1
                forces[2 * node] = forces.get(2 * node, 0.0) + weight * tx
                forces[2 * node + 1] = forces.get(2 * node + 1, 0.0) + weight * ty

    return forces
