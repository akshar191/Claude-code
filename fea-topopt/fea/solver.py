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

from . import element as q4
from . import element3d as h8
from .material import Material, von_mises
from .mesh import Mesh, StructuredGrid, StructuredGrid3D


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

    def fix_nodes(
        self, nodes, direction: str = "xy", dofs_per_node: int = 2
    ) -> "BoundaryConditions":
        """Clamp nodes along any combination of the axes named in ``direction``.

        Args:
            nodes: Node indices.
            direction: Any subset of ``"xyz"``; ``"xy"`` clamps both in-plane
                components, ``"xyz"`` fully clamps a three-dimensional node.
            dofs_per_node: 2 for a plane model, 3 for a solid model.
        """
        components = [c for c, axis in enumerate("xyz"[:dofs_per_node]) if axis in direction]
        for node in np.atleast_1d(nodes).ravel():
            for c in components:
                self.prescribed[dofs_per_node * int(node) + c] = 0.0
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
    """Linear elasticity model over a quadrilateral (2D) or hexahedral (3D) mesh.

    The dimension is taken from the mesh: a :class:`fea.mesh.QuadMesh` gives a
    plane model with Q4 elements, a :class:`fea.mesh.HexMesh` a solid model with
    H8 elements. Everything downstream - assembly, boundary conditions, stress
    recovery, topology optimisation - works unchanged in either.

    Args:
        mesh: The discretised domain.
        material: Isotropic elastic material.
        thickness: Out-of-plane thickness; used only by plane models.
    """

    def __init__(self, mesh: Mesh, material: Material, thickness: float = 1.0) -> None:
        if thickness <= 0.0:
            raise ValueError(f"Thickness must be positive, got {thickness}")
        if mesh.ndim not in (2, 3):
            raise ValueError(f"Mesh must be two- or three-dimensional, got {mesh.ndim}")
        self.mesh = mesh
        self.material = material
        self.thickness = thickness
        self.ndim = mesh.ndim
        self.D = material.constitutive_matrix(self.ndim)

        self._element_dofs = mesh.all_element_dofs()
        self.n_local = int(self._element_dofs.shape[1])
        # Sparse triplet pattern, built once and reused for every assembly.
        self._rows = np.repeat(self._element_dofs, self.n_local, axis=1).ravel()
        self._cols = np.tile(self._element_dofs, (1, self.n_local)).ravel()
        self._uniform_stiffness = self._uniform_element_stiffness()

    # -- element-level dispatch -------------------------------------------------

    def element_stiffness(self, coords: np.ndarray) -> np.ndarray:
        """Stiffness matrix of one element with the given corner coordinates."""
        if self.ndim == 2:
            return q4.element_stiffness(coords, self.D, self.thickness)
        return h8.element_stiffness(coords, self.D)

    def _gauss_strains(self, coords: np.ndarray, u_element: np.ndarray) -> np.ndarray:
        if self.ndim == 2:
            return q4.element_strains_at_gauss_points(coords, u_element)
        return h8.element_strains_at_gauss_points(coords, u_element)

    def _extrapolate(self, gauss_values: np.ndarray) -> np.ndarray:
        if self.ndim == 2:
            return q4.extrapolate_gauss_to_nodes(gauss_values)
        return h8.extrapolate_gauss_to_nodes(gauss_values)

    @property
    def n_stress_components(self) -> int:
        return 3 if self.ndim == 2 else 6

    # -- assembly ------------------------------------------------------------------

    def _uniform_element_stiffness(self) -> np.ndarray | None:
        """Element stiffness shared by all elements, when that sharing is valid.

        A structured grid is normally made of identical elements, so one stiffness
        matrix can serve the whole mesh. The nodes are public and may have been
        moved, so congruence is verified rather than assumed: every element must
        match the first one up to a translation.
        """
        if not isinstance(self.mesh, (StructuredGrid, StructuredGrid3D)):
            return None

        coords = self.mesh.nodes[self.mesh.elements]  # (n_elements, nodes, ndim)
        relative = coords - coords[:, :1, :]
        if not np.allclose(relative, relative[0], atol=1e-12):
            return None

        return self.element_stiffness(self.mesh.element_coords(0))

    @property
    def has_uniform_elements(self) -> bool:
        """True when every element is congruent, so one stiffness matrix serves all."""
        return self._uniform_stiffness is not None

    def element_stiffnesses(self) -> np.ndarray:
        """(n_elements, n_local, n_local) array of unscaled element stiffness matrices."""
        n = self.mesh.n_elements
        if self._uniform_stiffness is not None:
            return np.broadcast_to(self._uniform_stiffness, (n, self.n_local, self.n_local))
        return np.array([self.element_stiffness(self.mesh.element_coords(e)) for e in range(n)])

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
        self,
        bcs: BoundaryConditions,
        scale: np.ndarray | None = None,
        method: str = "auto",
        initial_guess: np.ndarray | None = None,
        rtol: float = 1e-8,
    ) -> Solution:
        """Solve the static problem for the given boundary conditions.

        Args:
            bcs: Prescribed displacements and applied forces.
            scale: Optional per-element stiffness multiplier (see :meth:`assemble`).
            method: ``"direct"`` for a sparse LU factorisation, ``"cg"`` for
                Jacobi-preconditioned conjugate gradients, or ``"auto"`` to use
                the direct solver in two dimensions and CG in three, where the
                fill-in of a factorisation makes it several times slower.
            initial_guess: Starting vector for CG; passing the previous
                iteration's displacements in an optimisation loop shortens the
                solve considerably.
            rtol: Relative residual tolerance for CG.

        Raises:
            ValueError: If no degree of freedom is prescribed, which would leave
                the stiffness matrix singular through rigid body motion.
            RuntimeError: If CG fails to converge.
        """
        if not bcs.prescribed:
            raise ValueError(
                "No prescribed degrees of freedom: the model is free to move as a "
                "rigid body and the stiffness matrix is singular."
            )
        if method == "auto":
            method = "direct" if self.ndim == 2 else "cg"
        if method not in ("direct", "cg"):
            raise ValueError(f"method must be 'direct', 'cg' or 'auto', got {method!r}")

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

        if method == "direct":
            u[free] = spla.spsolve(K_ff.tocsc(), rhs)
        else:
            K_ff = K_ff.tocsr()
            diagonal = K_ff.diagonal()
            diagonal[diagonal <= 0.0] = 1.0
            preconditioner = spla.LinearOperator(
                K_ff.shape, matvec=lambda v: v / diagonal, dtype=float
            )
            x0 = None if initial_guess is None else initial_guess[free]
            u_free, info = spla.cg(
                K_ff, rhs, x0=x0, M=preconditioner, rtol=rtol, maxiter=50 * len(free)
            )
            if info != 0:
                raise RuntimeError(f"Conjugate gradient solve did not converge (info={info})")
            u[free] = u_free

        reactions = K @ u - f
        compliance = float(f @ u)
        return Solution(displacements=u, reactions=reactions, compliance=compliance)

    def element_stresses(self, u: np.ndarray) -> np.ndarray:
        """Stresses at the Gauss points of every element.

        Returns:
            (n_elements, n_gauss, n_components) array: 4 points and 3 components
            in two dimensions, 8 points and 6 components in three.
        """
        n_gauss = 4 if self.ndim == 2 else 8
        stresses = np.empty((self.mesh.n_elements, n_gauss, self.n_stress_components))
        for e in range(self.mesh.n_elements):
            coords = self.mesh.element_coords(e)
            u_e = u[self._element_dofs[e]]
            stresses[e] = self._gauss_strains(coords, u_e) @ self.D.T
        return stresses

    def centroid_strain_matrix(self) -> np.ndarray:
        """Strain-displacement matrix at the element centre, for a uniform grid.

        Returns:
            Array of shape (n_components, n_local) mapping element displacements
            to strains at the centroid. Only valid when every element is
            congruent, which is checked.
        """
        if not self.has_uniform_elements:
            raise ValueError("Centroid strain matrix requires a uniform grid")
        coords = self.mesh.element_coords(0)
        if self.ndim == 2:
            B, _ = q4.strain_displacement_matrix(coords, 0.0, 0.0)
        else:
            B, _ = h8.strain_displacement_matrix(coords, 0.0, 0.0, 0.0)
        return B

    def nodal_stresses(self, u: np.ndarray) -> np.ndarray:
        """Continuous nodal stresses by extrapolation and averaging.

        Gauss-point stresses are extrapolated to the element corners and then
        averaged over the elements sharing each node, which recovers a smooth
        field from the discontinuous element-wise solution.

        Returns:
            (n_nodes, n_components) array of averaged nodal stresses.
        """
        gauss_stresses = self.element_stresses(u)
        totals = np.zeros((self.mesh.n_nodes, self.n_stress_components))
        counts = np.zeros(self.mesh.n_nodes)
        for e in range(self.mesh.n_elements):
            corner_stresses = self._extrapolate(gauss_stresses[e])
            nodes = self.mesh.elements[e]
            np.add.at(totals, nodes, corner_stresses)
            np.add.at(counts, nodes, 1.0)
        return totals / counts[:, None]

    def nodal_von_mises(self, u: np.ndarray) -> np.ndarray:
        """Averaged nodal von Mises stress."""
        return von_mises(self.nodal_stresses(u))


def edge_traction_forces(
    mesh: Mesh, node_pairs, traction, thickness: float = 1.0
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
