"""Topology optimisation benchmark problems solved with the SIMP optimiser.

Every case defines a design domain, its supports and loads, and any regions
forced to be solid or void. The finite element model that drives the
optimisation is the same one verified in ``validate_cantilever.py``.

Run from the project root::

    python examples/optimize.py --case mbb
    python examples/optimize.py --case all --nx 120 --ny 40
    python examples/optimize.py --case mbb --filter none   # shows checkerboarding
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fea import (  # noqa: E402
    BoundaryConditions,
    FEModel,
    Material,
    TopologyOptimizer,
    TopOptSettings,
    structured_grid,
)


@dataclass
class Case:
    """A design problem: domain, boundary conditions, and passive regions."""

    name: str
    description: str
    model: FEModel
    bcs: BoundaryConditions
    volume_fraction: float
    passive_solid: np.ndarray | None = None
    passive_void: np.ndarray | None = None
    mirror: bool = False


def _model(nx: int, ny: int, lx: float, ly: float) -> FEModel:
    """Unit-modulus model on a structured grid, as SIMP scales stiffness itself."""
    grid = structured_grid(nx, ny, lx=lx, ly=ly)
    return FEModel(grid, Material(E=1.0, nu=0.3, plane="stress"), thickness=1.0)


def mbb_beam(nx: int, ny: int) -> Case:
    """Half of the Messerschmitt-Bolkow-Blohm beam, exploiting symmetry.

    A simply supported beam under a central point load. Only the left half is
    modelled: the symmetry plane is the left edge, where horizontal motion is
    suppressed, and the roller support sits at the bottom-right corner.
    """
    model = _model(nx, ny, lx=nx / ny, ly=1.0)
    grid = model.mesh
    bcs = BoundaryConditions()

    symmetry_nodes = grid.nodes_where(lambda x, y: np.isclose(x, 0.0))
    bcs.fix_nodes(symmetry_nodes, direction="x")
    bcs.fix_nodes(grid.node_id(nx, 0), direction="y")
    bcs.add_force(2 * grid.node_id(0, ny) + 1, -1.0)

    return Case(
        name="mbb",
        description="MBB beam (half domain, symmetric)",
        model=model,
        bcs=bcs,
        volume_fraction=0.5,
        mirror=True,
    )


def cantilever(nx: int, ny: int) -> Case:
    """Cantilever clamped along its left edge with a point load at mid-height."""
    model = _model(nx, ny, lx=nx / ny, ly=1.0)
    grid = model.mesh
    bcs = BoundaryConditions()

    bcs.fix_nodes(grid.nodes_where(lambda x, y: np.isclose(x, 0.0)), direction="xy")
    bcs.add_force(2 * grid.node_id(nx, ny // 2) + 1, -1.0)

    return Case(
        name="cantilever",
        description="Cantilever with an end point load",
        model=model,
        bcs=bcs,
        volume_fraction=0.4,
    )


def bridge(nx: int, ny: int) -> Case:
    """A deck carrying a distributed load, pinned at both lower corners.

    The top row of elements is forced solid so the optimiser must keep a running
    surface, which is what turns the result into a recognisable bridge rather
    than a truss with no deck.
    """
    model = _model(nx, ny, lx=nx / ny, ly=1.0)
    grid = model.mesh
    bcs = BoundaryConditions()

    bcs.fix_nodes(grid.node_id(0, 0), direction="xy")
    bcs.fix_nodes(grid.node_id(nx, 0), direction="y")

    deck_nodes = grid.nodes_where(lambda x, y: np.isclose(y, grid.ly))
    load_per_node = -1.0 / len(deck_nodes)
    for node in deck_nodes:
        bcs.add_force(2 * node + 1, load_per_node)

    passive_solid = np.zeros(grid.n_elements, dtype=bool)
    passive_solid[grid.element_id(0, ny - 1) : grid.element_id(0, ny - 1) + nx] = True

    return Case(
        name="bridge",
        description="Bridge deck under a distributed load",
        model=model,
        bcs=bcs,
        volume_fraction=0.35,
        passive_solid=passive_solid,
    )


def l_bracket(nx: int, ny: int) -> Case:
    """An L-shaped bracket, the classic re-entrant corner problem.

    The upper right quadrant is removed by forcing it void. The optimiser has to
    route load around the re-entrant corner, where the elasticity solution is
    singular and where real brackets crack.

    The domain is square regardless of the requested grid, so the finer of the
    two requested resolutions is used for both axes.
    """
    n = max(nx, ny)
    nx = ny = n
    model = _model(nx, ny, lx=1.0, ly=1.0)
    grid = model.mesh
    bcs = BoundaryConditions()

    # Only the vertical leg reaches the top edge, so only that part is clamped.
    bcs.fix_nodes(
        grid.nodes_where(
            lambda x, y: np.isclose(y, grid.ly) & (x <= 0.4 * grid.lx + 1e-12)
        ),
        direction="xy",
    )

    centroids = grid.element_centroids()
    cutout = (centroids[:, 0] > 0.4 * grid.lx) & (centroids[:, 1] > 0.4 * grid.ly)

    # Load at the tip of the horizontal leg.
    load_node = grid.nearest_node(grid.lx, 0.4 * grid.ly)
    bcs.add_force(2 * load_node + 1, -1.0)

    return Case(
        name="lbracket",
        description="L-shaped bracket with a re-entrant corner",
        model=model,
        bcs=bcs,
        volume_fraction=0.35,
        passive_void=cutout,
    )


CASES = {
    "mbb": mbb_beam,
    "cantilever": cantilever,
    "bridge": bridge,
    "lbracket": l_bracket,
}


def run_case(case: Case, settings: TopOptSettings, quiet: bool = False):
    """Optimise one case, printing iteration history."""
    optimizer = TopologyOptimizer(
        case.model,
        case.bcs,
        settings,
        passive_solid=case.passive_solid,
        passive_void=case.passive_void,
    )

    grid = case.model.mesh
    print(f"\n{case.description}")
    print(
        f"  {grid.nx}x{grid.ny} = {grid.n_elements} elements, {grid.n_dofs} DOFs, "
        f"volume fraction {settings.volume_fraction}, p = {settings.penalty}, "
        f"rmin = {settings.filter_radius}"
    )

    def report(iteration, compliance, change, density):
        if quiet or (iteration % 10 and change >= settings.tolerance):
            return
        print(
            f"    it {iteration:>4}   c = {compliance:>11.4f}   "
            f"vol = {np.mean(density):>5.3f}   change = {change:>6.4f}"
        )

    start = time.perf_counter()
    result = optimizer.run(callback=report)
    elapsed = time.perf_counter() - start

    print(
        f"  {'converged' if result.converged else 'stopped at iteration cap'} after "
        f"{result.iterations} iterations in {elapsed:.1f}s "
        f"({elapsed / result.iterations:.2f}s per iteration)"
    )
    print(
        f"  final compliance {result.compliance:.4f}, "
        f"greyness Mnd = {100 * result.discreteness():.1f}%"
    )
    return result


def write_figures(case: Case, result, settings: TopOptSettings, output_dir: str) -> None:
    """Save the layout and convergence figures for a case."""
    import matplotlib.pyplot as plt

    from fea.plotting import plot_convergence, plot_density, save

    os.makedirs(output_dir, exist_ok=True)
    grid = case.model.mesh

    fig, ax = plt.subplots(figsize=(10, 10 * grid.ly / grid.lx / (2 if case.mirror else 1)))
    plot_density(
        grid,
        result.density,
        ax=ax,
        mirror=case.mirror,
        title=(
            f"{case.description}\n"
            f"volume fraction {settings.volume_fraction}, "
            f"compliance {result.compliance:.3f}, "
            f"{result.iterations} iterations"
        ),
    )
    save(fig, os.path.join(output_dir, f"topopt_{case.name}.png"))

    fig, ax = plt.subplots(figsize=(7, 4))
    plot_convergence(result, ax=ax, title=f"{case.description}: convergence")
    save(fig, os.path.join(output_dir, f"topopt_{case.name}_convergence.png"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case", default="mbb", choices=[*CASES, "all"], help="benchmark problem"
    )
    parser.add_argument("--nx", type=int, default=120, help="elements along x")
    parser.add_argument("--ny", type=int, default=40, help="elements along y")
    parser.add_argument("--volfrac", type=float, default=None, help="override volume fraction")
    parser.add_argument("--penalty", type=float, default=3.0, help="SIMP exponent")
    parser.add_argument("--rmin", type=float, default=2.0, help="filter radius in elements")
    parser.add_argument(
        "--filter",
        default="sensitivity",
        choices=["sensitivity", "density", "none"],
        help="regularisation filter; 'none' reproduces the checkerboard instability",
    )
    parser.add_argument("--iterations", type=int, default=150, help="iteration cap")
    parser.add_argument("--figures", default="results", help="output directory, or 'none'")
    args = parser.parse_args()

    names = list(CASES) if args.case == "all" else [args.case]
    for name in names:
        case = CASES[name](args.nx, args.ny)
        settings = TopOptSettings(
            volume_fraction=args.volfrac if args.volfrac is not None else case.volume_fraction,
            penalty=args.penalty,
            filter_radius=0.0 if args.filter == "none" else args.rmin,
            filter_type="sensitivity" if args.filter == "none" else args.filter,
            max_iterations=args.iterations,
        )
        result = run_case(case, settings)
        if args.figures.lower() != "none":
            write_figures(case, result, settings, args.figures)
            print(f"  figures written to {args.figures}/")


if __name__ == "__main__":
    main()
