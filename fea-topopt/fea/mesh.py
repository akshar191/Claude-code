"""Structured meshes in two and three dimensions, with degree-of-freedom bookkeeping.

Every node owns ``ndim`` degrees of freedom stored contiguously, so node ``n``
has displacement components ``ndim * n + c`` for ``c`` in ``range(ndim)``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def dofs_of(node_indices: np.ndarray, dofs_per_node: int = 2) -> np.ndarray:
    """Map node indices to their interleaved degrees of freedom.

    Args:
        node_indices: Array of node indices of any shape.
        dofs_per_node: Number of displacement components per node.

    Returns:
        Array whose last axis is ``dofs_per_node`` times longer than the input's,
        e.g. shape (n_elements, 4) becomes (n_elements, 8) in two dimensions.
    """
    node_indices = np.asarray(node_indices)
    components = np.arange(dofs_per_node)
    dofs = dofs_per_node * node_indices[..., None] + components
    return dofs.reshape(*node_indices.shape[:-1], -1)


@dataclass
class Mesh:
    """A mesh of isoparametric elements sharing one node count per element.

    Attributes:
        nodes: (n_nodes, ndim) array of nodal coordinates.
        elements: (n_elements, nodes_per_element) array of node indices.
    """

    nodes: np.ndarray
    elements: np.ndarray

    @property
    def ndim(self) -> int:
        return int(self.nodes.shape[1])

    @property
    def dofs_per_node(self) -> int:
        return self.ndim

    @property
    def n_nodes(self) -> int:
        return int(self.nodes.shape[0])

    @property
    def n_elements(self) -> int:
        return int(self.elements.shape[0])

    @property
    def n_dofs(self) -> int:
        return self.dofs_per_node * self.n_nodes

    def element_coords(self, element: int) -> np.ndarray:
        """Corner coordinates of one element, shape (nodes_per_element, ndim)."""
        return self.nodes[self.elements[element]]

    def element_dofs(self, element: int) -> np.ndarray:
        """Global degrees of freedom of one element."""
        return dofs_of(self.elements[element], self.dofs_per_node)

    def all_element_dofs(self) -> np.ndarray:
        """Global degrees of freedom of every element, one row per element."""
        return dofs_of(self.elements, self.dofs_per_node)

    def nodes_where(self, predicate) -> np.ndarray:
        """Indices of nodes whose coordinates satisfy ``predicate``.

        Args:
            predicate: Callable taking one array per coordinate (``x, y`` in two
                dimensions, ``x, y, z`` in three) and returning a boolean mask.
        """
        mask = predicate(*(self.nodes[:, c] for c in range(self.ndim)))
        return np.flatnonzero(mask)

    def nearest_node(self, *point: float) -> int:
        """Index of the node closest to ``point``."""
        target = np.asarray(point, dtype=float)
        distances = np.linalg.norm(self.nodes - target, axis=1)
        return int(np.argmin(distances))

    def element_centroids(self) -> np.ndarray:
        """(n_elements, ndim) array of element centre coordinates."""
        return self.nodes[self.elements].mean(axis=1)


@dataclass
class QuadMesh(Mesh):
    """A mesh of four-node quadrilaterals, counter-clockwise node order."""


@dataclass
class HexMesh(Mesh):
    """A mesh of eight-node hexahedra in the ordering of :mod:`fea.element3d`."""


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

    @property
    def spacing(self) -> np.ndarray:
        """Element size along each axis."""
        return np.array([self.dx, self.dy])

    def node_id(self, i: int, j: int) -> int:
        """Node index at grid position (i, j), with i along x and j along y."""
        return j * (self.nx + 1) + i

    def element_id(self, i: int, j: int) -> int:
        """Element index at grid position (i, j)."""
        return j * self.nx + i

    def element_grid_shape(self) -> tuple[int, int]:
        """Shape ``(ny, nx)`` for reshaping per-element arrays into an image."""
        return self.ny, self.nx


@dataclass
class StructuredGrid3D(HexMesh):
    """A uniform ``nx`` by ``ny`` by ``nz`` grid of brick elements."""

    nx: int = 1
    ny: int = 1
    nz: int = 1
    lx: float = 1.0
    ly: float = 1.0
    lz: float = 1.0

    @property
    def dx(self) -> float:
        return self.lx / self.nx

    @property
    def dy(self) -> float:
        return self.ly / self.ny

    @property
    def dz(self) -> float:
        return self.lz / self.nz

    @property
    def spacing(self) -> np.ndarray:
        return np.array([self.dx, self.dy, self.dz])

    def node_id(self, i: int, j: int, k: int) -> int:
        """Node index at grid position (i, j, k)."""
        return k * (self.nx + 1) * (self.ny + 1) + j * (self.nx + 1) + i

    def element_id(self, i: int, j: int, k: int) -> int:
        """Element index at grid position (i, j, k)."""
        return k * self.nx * self.ny + j * self.nx + i

    def element_grid_shape(self) -> tuple[int, int, int]:
        """Shape ``(nz, ny, nx)`` for reshaping per-element arrays into a volume."""
        return self.nz, self.ny, self.nx


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

    return StructuredGrid(nodes=nodes, elements=elements, nx=nx, ny=ny, lx=lx, ly=ly)


def structured_grid_3d(
    nx: int,
    ny: int,
    nz: int,
    lx: float = 1.0,
    ly: float = 1.0,
    lz: float = 1.0,
    y0: float = 0.0,
    z0: float = 0.0,
) -> StructuredGrid3D:
    """Build a uniform grid of brick H8 elements.

    Args:
        nx, ny, nz: Number of elements along each axis.
        lx, ly, lz: Domain extent along each axis.
        y0, z0: Coordinates of the lower faces along y and z.
    """
    if min(nx, ny, nz) < 1:
        raise ValueError(
            f"Grid must have at least one element per axis, got {nx}x{ny}x{nz}"
        )

    xs = np.linspace(0.0, lx, nx + 1)
    ys = np.linspace(y0, y0 + ly, ny + 1)
    zs = np.linspace(z0, z0 + lz, nz + 1)
    grid_z, grid_y, grid_x = np.meshgrid(zs, ys, xs, indexing="ij")
    nodes = np.column_stack([grid_x.ravel(), grid_y.ravel(), grid_z.ravel()])

    k, j, i = np.meshgrid(np.arange(nz), np.arange(ny), np.arange(nx), indexing="ij")
    i, j, k = i.ravel(), j.ravel(), k.ravel()
    layer = (nx + 1) * (ny + 1)
    n0 = k * layer + j * (nx + 1) + i
    bottom = np.column_stack([n0, n0 + 1, n0 + nx + 2, n0 + nx + 1])
    elements = np.hstack([bottom, bottom + layer])

    return StructuredGrid3D(
        nodes=nodes, elements=elements, nx=nx, ny=ny, nz=nz, lx=lx, ly=ly, lz=lz
    )
