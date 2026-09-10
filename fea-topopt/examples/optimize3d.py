"""Three-dimensional topology optimisation with hexahedral elements.

The same SIMP optimiser as the two-dimensional cases, driven by the H8 solver.
The linear system is solved with warm-started Jacobi-preconditioned conjugate
gradients, which in three dimensions is several times faster than a sparse
direct factorisation.

Run from the project root::

    python examples/optimize3d.py --case cantilever
    python examples/optimize3d.py --case bridge --nx 48 --ny 16 --nz 12
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fea import (  # noqa: E402
    BoundaryConditions,
    FEModel,
    Material,
    TopologyOptimizer,
    TopOptSettings,
)
from fea.mesh import structured_grid_3d  # noqa: E402


def cantilever(nx: int, ny: int, nz: int):
    """Clamped at x = 0, loaded by a line load along the bottom edge of the free end."""
    grid = structured_grid_3d(nx, ny, nz, lx=nx / ny, ly=1.0, lz=nz / ny)
    model = FEModel(grid, Material(E=1.0, nu=0.3))

    bcs = BoundaryConditions()
    bcs.fix_nodes(grid.nodes_where(lambda x, y, z: np.isclose(x, 0.0)), "xyz", dofs_per_node=3)
    edge = grid.nodes_where(lambda x, y, z: np.isclose(x, grid.lx) & np.isclose(y, 0.0))
    for node in edge:
        bcs.add_force(3 * node + 1, -1.0 / len(edge))

    return "Cantilever, line load on the free end", model, bcs, 0.3, None


def bridge(nx: int, ny: int, nz: int):
    """Deck loaded uniformly, supported along both bottom end edges; deck forced solid."""
    grid = structured_grid_3d(nx, ny, nz, lx=nx / ny, ly=1.0, lz=nz / ny)
    model = FEModel(grid, Material(E=1.0, nu=0.3))

    bcs = BoundaryConditions()
    left = grid.nodes_where(lambda x, y, z: np.isclose(x, 0.0) & np.isclose(y, 0.0))
    right = grid.nodes_where(lambda x, y, z: np.isclose(x, grid.lx) & np.isclose(y, 0.0))
    bcs.fix_nodes(left, "xyz", dofs_per_node=3)
    bcs.fix_nodes(right, "yz", dofs_per_node=3)

    deck = grid.nodes_where(lambda x, y, z: np.isclose(y, grid.ly))
    for node in deck:
        bcs.add_force(3 * node + 1, -1.0 / len(deck))

    passive_solid = np.zeros(grid.n_elements, dtype=bool)
    for k in range(nz):
        for i in range(nx):
            passive_solid[grid.element_id(i, ny - 1, k)] = True

    return "Bridge deck, distributed load", model, bcs, 0.25, passive_solid


CASES = {"cantilever": cantilever, "bridge": bridge}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="cantilever", choices=[*CASES, "all"])
    parser.add_argument("--nx", type=int, default=32)
    parser.add_argument("--ny", type=int, default=16)
    parser.add_argument("--nz", type=int, default=8)
    parser.add_argument("--volfrac", type=float, default=None)
    parser.add_argument("--rmin", type=float, default=1.5)
    parser.add_argument("--iterations", type=int, default=60)
    parser.add_argument("--figures", default="results")
    args = parser.parse_args()

    names = list(CASES) if args.case == "all" else [args.case]
    for name in names:
        description, model, bcs, volfrac, passive_solid = CASES[name](args.nx, args.ny, args.nz)
        grid = model.mesh
        settings = TopOptSettings(
            volume_fraction=args.volfrac if args.volfrac is not None else volfrac,
            filter_radius=args.rmin,
            max_iterations=args.iterations,
        )

        print(f"\n{description}")
        print(
            f"  {grid.nx}x{grid.ny}x{grid.nz} = {grid.n_elements} elements, "
            f"{grid.n_dofs} DOFs, volume fraction {settings.volume_fraction}"
        )

        def report(iteration, compliance, change, density):
            if iteration % 10 == 0 or change < settings.tolerance:
                print(
                    f"    it {iteration:>4}   c = {compliance:>11.4f}   "
                    f"vol = {np.mean(density):>5.3f}   change = {change:>6.4f}"
                )

        start = time.perf_counter()
        result = TopologyOptimizer(model, bcs, settings, passive_solid=passive_solid).run(
            callback=report
        )
        elapsed = time.perf_counter() - start
        print(
            f"  {'converged' if result.converged else 'stopped at iteration cap'} after "
            f"{result.iterations} iterations in {elapsed:.1f}s "
            f"({elapsed / result.iterations:.2f}s per iteration)"
        )
        print(f"  final compliance {result.compliance:.4f}, greyness {100 * result.discreteness():.1f}%")

        if args.figures.lower() != "none":
            import matplotlib.pyplot as plt

            from fea.plotting import plot_voxels, save

            os.makedirs(args.figures, exist_ok=True)
            fig = plt.figure(figsize=(10, 6))
            ax = fig.add_subplot(111, projection="3d")
            plot_voxels(
                grid,
                result.density,
                ax=ax,
                title=f"{description}\n{grid.nx}x{grid.ny}x{grid.nz} H8 elements, "
                f"compliance {result.compliance:.3f}",
            )
            save(fig, os.path.join(args.figures, f"topopt3d_{name}.png"))
            print(f"  figure written to {args.figures}/topopt3d_{name}.png")


if __name__ == "__main__":
    main()
