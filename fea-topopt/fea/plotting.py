"""Figures for deformed shapes, stress fields, and optimised layouts."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # headless rendering

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import PolyCollection
from matplotlib.colors import LinearSegmentedColormap

from .mesh import QuadMesh, StructuredGrid

# Perceptually ordered ramp for scalar fields, dark blue through to warm yellow.
STRESS_CMAP = "viridis"
# Solid material renders dark on a light ground, matching how layouts are printed.
LAYOUT_CMAP = LinearSegmentedColormap.from_list(
    "layout", ["#ffffff", "#111318"], N=256
)


def _polygons(mesh: QuadMesh, displacements: np.ndarray | None, scale: float):
    coords = mesh.nodes.copy()
    if displacements is not None:
        coords = coords + scale * displacements.reshape(-1, 2)
    return coords[mesh.elements]


def plot_mesh(
    mesh: QuadMesh,
    ax=None,
    displacements: np.ndarray | None = None,
    scale: float = 1.0,
    show_undeformed: bool = True,
    title: str | None = None,
):
    """Draw the mesh, optionally in its deformed configuration."""
    if ax is None:
        _, ax = plt.subplots(figsize=(8, 3.5))

    if show_undeformed and displacements is not None:
        ax.add_collection(
            PolyCollection(
                _polygons(mesh, None, 0.0),
                facecolors="none",
                edgecolors="#c7ccd4",
                linewidths=0.4,
            )
        )
    ax.add_collection(
        PolyCollection(
            _polygons(mesh, displacements, scale),
            facecolors="#dce6f5",
            edgecolors="#2f4a73",
            linewidths=0.5,
        )
    )
    ax.autoscale_view()
    ax.set_aspect("equal")
    if title:
        ax.set_title(title)
    return ax


def plot_field(
    mesh: QuadMesh,
    nodal_values: np.ndarray,
    ax=None,
    displacements: np.ndarray | None = None,
    scale: float = 0.0,
    title: str | None = None,
    label: str | None = None,
    cmap: str = STRESS_CMAP,
    levels: int = 24,
):
    """Filled contour plot of a nodal scalar field on the (deformed) mesh."""
    if ax is None:
        _, ax = plt.subplots(figsize=(9, 3.5))

    coords = mesh.nodes.copy()
    if displacements is not None and scale:
        coords = coords + scale * displacements.reshape(-1, 2)

    # Split each quadrilateral into two triangles for the contour routine.
    quads = mesh.elements
    triangles = np.vstack([quads[:, [0, 1, 2]], quads[:, [0, 2, 3]]])

    contour = ax.tricontourf(
        coords[:, 0], coords[:, 1], triangles, nodal_values, levels=levels, cmap=cmap
    )
    ax.set_aspect("equal")
    if title:
        ax.set_title(title)
    colorbar = plt.colorbar(contour, ax=ax, fraction=0.025, pad=0.02)
    if label:
        colorbar.set_label(label)
    return ax


def plot_density(
    grid: StructuredGrid,
    density: np.ndarray,
    ax=None,
    title: str | None = None,
    mirror: bool = False,
):
    """Render an optimised layout as a greyscale image.

    Args:
        grid: The design grid.
        density: Per-element density in [0, 1].
        ax: Optional existing axes.
        title: Optional title.
        mirror: Mirror the domain about its left edge, used to show the full MBB
            beam from the half-domain that symmetry lets us solve.
    """
    if ax is None:
        _, ax = plt.subplots(figsize=(9, 3.2))

    image = density.reshape(grid.element_grid_shape())
    extent = [0.0, grid.lx, 0.0, grid.ly]
    if mirror:
        image = np.hstack([image[:, ::-1], image])
        extent = [-grid.lx, grid.lx, 0.0, grid.ly]

    ax.imshow(
        image,
        cmap=LAYOUT_CMAP,
        vmin=0.0,
        vmax=1.0,
        origin="lower",
        extent=extent,
        interpolation="bilinear",
    )
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_edgecolor("#c7ccd4")
    if title:
        ax.set_title(title)
    return ax


def plot_voxels(
    grid,
    density: np.ndarray,
    ax=None,
    threshold: float = 0.5,
    title: str | None = None,
    elevation: float = 22.0,
    azimuth: float = -60.0,
):
    """Render a three-dimensional layout as the set of elements above ``threshold``.

    Faces are shaded by depth along the viewing direction so the structure reads
    as a solid rather than a flat silhouette.
    """
    if ax is None:
        fig = plt.figure(figsize=(9, 5.5))
        ax = fig.add_subplot(111, projection="3d")

    # The problem's y axis is "up" (as in the 2D cases); matplotlib draws its
    # third axis vertically, so the volume is laid out as (x, z, y).
    nz, ny, nx = grid.element_grid_shape()
    volume = density.reshape(nz, ny, nx).transpose(2, 0, 1)  # (nx, nz, ny)
    solid = volume >= threshold

    # Shade by density so partially dense elements read lighter.
    shade = np.clip((volume - threshold) / max(1e-9, 1.0 - threshold), 0.0, 1.0)
    colors = np.zeros(volume.shape + (4,))
    base = np.array([0.10, 0.12, 0.15])
    light = np.array([0.55, 0.58, 0.62])
    colors[..., :3] = light[None, None, None, :] * (1.0 - shade[..., None]) + base * shade[..., None]
    colors[..., 3] = 1.0

    ax.voxels(solid, facecolors=colors, edgecolor="#2a2f36", linewidth=0.15)
    ax.set_box_aspect((grid.lx, grid.lz, grid.ly))
    ax.view_init(elev=elevation, azim=azimuth)
    ax.set_xlabel("x")
    ax.set_ylabel("z")
    ax.set_zlabel("y")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_zticks([])
    if title:
        ax.set_title(title)
    return ax


def plot_convergence(result, ax=None, title: str | None = None):
    """Compliance and volume fraction against iteration number."""
    if ax is None:
        _, ax = plt.subplots(figsize=(7, 4))

    iterations = np.arange(1, len(result.history) + 1)
    ax.plot(iterations, result.history, color="#2f4a73", lw=1.8, label="Compliance")
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Compliance $c = f^T u$")
    ax.grid(alpha=0.25, lw=0.6)

    twin = ax.twinx()
    twin.plot(
        iterations,
        result.volume_history,
        color="#b4642a",
        lw=1.4,
        ls="--",
        label="Volume fraction",
    )
    twin.set_ylabel("Volume fraction")
    twin.set_ylim(0.0, 1.0)

    handles = ax.get_lines() + twin.get_lines()
    ax.legend(handles, [h.get_label() for h in handles], frameon=False)
    if title:
        ax.set_title(title)
    return ax


def save(fig, path: str, dpi: int = 150) -> None:
    """Write a figure to disk with a tight bounding box."""
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
