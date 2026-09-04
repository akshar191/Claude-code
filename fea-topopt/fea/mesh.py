"""Structured quadrilateral meshes and degree-of-freedom bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class QuadMesh:
    """A mesh of four-node quadrilaterals.

    Attributes:
        nodes: (n_nodes, 2) array of nodal coordinates.
        elements: (n_elements, 4) array of node indices, counter-clockwise.

    Each node owns two degrees of freedom: ``2 * node`` is the x displacement
    and ``2 * node + 1`` is the y displacement.
    """

    nodes: np.ndarray
    elements: np.ndarray

    @property
    def n_nodes(self) -> int:
        return int(self.nodes.shape[0])

    @property
    def n_elements(self) -> int:
        return int(self.elements.shape[0])

    @property
    def n_dofs(self) -> int:
        return 2 * self.n_nodes

    def element_coords(self, element: int) -> np.ndarray:
        """(4, 2) array of the corner coordinates of one element."""
        return self.nodes[self.elements[element]]

    def element_dofs(self, element: int) -> np.ndarray:
        """The eight global degrees of freedom of one element."""
        return dofs_of(self.elements[element])

    def all_element_dofs(self) -> np.ndarray:
        """(n_elements, 8) array of the global degrees of freedom of every element."""
        return dofs_of(self.elements)

    def nodes_where(self, predicate) -> np.ndarray:
        """Indices of nodes whose (x, y) coordinates satisfy ``predicate``.

        Args:
            predicate: Callable taking the arrays ``x`` and ``y`` and returning a
                boolean mask, e.g. ``lambda x, y: np.isclose(x, 0.0)``.
        """
        mask = predicate(self.nodes[:, 0], self.nodes[:, 1])
        return np.flatnonzero(mask)

    def nearest_node(self, x: float, y: float) -> int:
        """Index of the node closest to the point (x, y)."""
        distances = np.hypot(self.nodes[:, 0] - x, self.nodes[:, 1] - y)
        return int(np.argmin(distances))


def dofs_of(node_indices: np.ndarray) -> np.ndarray:
    """Map node indices to their interleaved [x, y] degrees of freedom."""
    node_indices = np.asarray(node_indices)
    return np.stack([2 * node_indices, 2 * node_indices + 1], axis=-1).reshape(
        *node_indices.shape[:-1], -1
    )


@dataclass
class StructuredGrid(QuadMesh):
    """A uniform ``nx`` by ``ny`` grid of rectangular elements.

    The regular topology lets the topology optimiser treat an element index as a
    grid position, and lets the solver reuse a single element stiffness matrix.
    """

    nx: int = 1
    ny: int = 1
    lx: float = 1.0
    ly: float = 1.0

    @property
    def dx(self) -> float:
        return self.lx / self.nx

    @property
    def dy(self) -> float:
        return self.ly / self.ny

    def node_id(self, i: int, j: int) -> int:
        """Node index at grid position (i, j), with i along x and j along y."""
        return j * (self.nx + 1) + i

    def element_id(self, i: int, j: int) -> int:
        """Element index at grid position (i, j)."""
        return j * self.nx + i

    def element_grid_shape(self) -> tuple[int, int]:
        """Shape ``(ny, nx)`` for reshaping per-element arrays into an image."""
        return self.ny, self.nx

    def element_centroids(self) -> np.ndarray:
        """(n_elements, 2) array of element centre coordinates."""
        return self.nodes[self.elements].mean(axis=1)


def structured_grid(
    nx: int, ny: int, lx: float = 1.0, ly: float = 1.0, y0: float = 0.0
) -> StructuredGrid:
    """Build a uniform grid of rectangular Q4 elements.

    Args:
        nx: Number of elements along x.
        ny: Number of elements along y.
        lx: Domain length along x.
        ly: Domain height along y.
        y0: Coordinate of the bottom edge (use ``-ly / 2`` to centre the domain).

    Returns:
        A :class:`StructuredGrid` with ``nx * ny`` elements.
    """
    if nx < 1 or ny < 1:
        raise ValueError(f"Grid must have at least one element per axis, got {nx}x{ny}")

    xs = np.linspace(0.0, lx, nx + 1)
    ys = np.linspace(y0, y0 + ly, ny + 1)
    grid_x, grid_y = np.meshgrid(xs, ys)
    nodes = np.column_stack([grid_x.ravel(), grid_y.ravel()])

    i, j = np.meshgrid(np.arange(nx), np.arange(ny))
    i, j = i.ravel(), j.ravel()
    n0 = j * (nx + 1) + i
    elements = np.column_stack([n0, n0 + 1, n0 + nx + 2, n0 + nx + 1])

    return StructuredGrid(
        nodes=nodes, elements=elements, nx=nx, ny=ny, lx=lx, ly=ly
    )
