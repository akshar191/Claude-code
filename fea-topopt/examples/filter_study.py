"""Why the filter is not optional: checkerboarding and mesh dependence.

Minimum compliance topology optimisation without regularisation is ill-posed.
Two consequences are measured here.

1. **Checkerboarding.** The unfiltered optimiser drives the design towards
   alternating solid and void elements, a pattern the Q4 element space rewards
   but that no manufacturing process can produce and that carries no physical
   length scale.

2. **Mesh dependence.** Refining the mesh should refine the *resolution* of an
   answer, not change the answer. Holding the filter radius fixed in physical
   units, the optimised compliance is reproduced across a sixteen-fold increase
   in element count. That is the property that makes the result trustworthy.

Note that the compliance of the unfiltered design is not a meaningful comparison
point: the filter also imposes a minimum member size, and that constraint has a
genuine stiffness cost, so filtered compliance moves with the filter radius for
reasons that have nothing to do with checkerboarding.

Run from the project root::

    python examples/filter_study.py
"""

from __future__ import annotations

import argparse
import os
import sys

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


def cantilever(nx: int, ny: int):
    """Left-clamped cantilever with a point load at the free end, mid-height."""
    grid = structured_grid(nx, ny, lx=nx / ny, ly=1.0)
    model = FEModel(grid, Material(E=1.0, nu=0.3, plane="stress"))

    bcs = BoundaryConditions()
    bcs.fix_nodes(grid.nodes_where(lambda x, y: np.isclose(x, 0.0)), direction="xy")
    bcs.add_force(2 * grid.node_id(nx, ny // 2) + 1, -1.0)
    return model, bcs


def optimise(nx: int, ny: int, radius: float, filter_type: str, iterations: int):
    """Solve the cantilever with the given regularisation."""
    model, bcs = cantilever(nx, ny)
    settings = TopOptSettings(
        volume_fraction=0.4,
        filter_radius=radius,
        filter_type=filter_type,
        max_iterations=iterations,
    )
    return model, TopologyOptimizer(model, bcs, settings).run()


def checkerboard_study(nx: int, ny: int, iterations: int):
    """Compare no filter, the sensitivity filter, and the density filter."""
    variants = [
        ("No filter", 0.0, "sensitivity"),
        ("Sensitivity filter", 2.0, "sensitivity"),
        ("Density filter", 2.0, "density"),
    ]

    print(f"Checkerboarding: cantilever {nx}x{ny}, volume fraction 0.4, p = 3\n")
    header = f"{'variant':<22} {'rmin':>5} {'compliance':>11} {'checkerboard':>13} {'greyness':>9}"
    print(header)
    print("-" * len(header))

    results = []
    for label, radius, filter_type in variants:
        model, result = optimise(nx, ny, radius, filter_type, iterations)
        results.append((label, model, result))
        print(
            f"{label:<22} {radius:>5.1f} {result.compliance:>11.3f} "
            f"{result.checkerboard_measure():>13.4f} "
            f"{100 * result.discreteness():>8.1f}%"
        )

    baseline = results[0][2].checkerboard_measure()
    best = min(r.checkerboard_measure() for _, _, r in results[1:])
    print(
        f"\nFiltering reduces the checkerboard measure by a factor of {baseline / best:.1f}."
    )
    return results


def mesh_independence_study(iterations: int):
    """Refine the mesh at a constant physical filter radius."""
    print("\n\nMesh independence: filter radius held constant in physical units\n")
    header = (
        f"{'mesh':>10} {'elements':>9} {'rmin (elem)':>12} {'compliance':>11} "
        f"{'checkerboard':>13}"
    )
    print(header)
    print("-" * len(header))

    # The physical radius is 3/80 of the beam length; rmin is expressed in element
    # widths, so it doubles whenever the mesh does.
    schedule = [(40, 20, 1.5), (80, 40, 3.0), (160, 80, 6.0)]
    results = []
    for nx, ny, rmin in schedule:
        model, result = optimise(nx, ny, rmin, "sensitivity", iterations)
        results.append((f"{nx}x{ny}", model, result))
        print(
            f"{nx:>4}x{ny:<5} {nx * ny:>9} {rmin:>12.1f} {result.compliance:>11.3f} "
            f"{result.checkerboard_measure():>13.4f}"
        )

    compliances = np.array([r.compliance for _, _, r in results])
    spread = (compliances.max() - compliances.min()) / compliances.mean()
    print(
        f"\nCompliance varies by {100 * spread:.1f}% across a "
        f"{schedule[-1][0] * schedule[-1][1] // (schedule[0][0] * schedule[0][1])}x "
        "increase in element count: the optimised design is a property of the\n"
        "problem, not of the discretisation."
    )
    return results


def write_figures(checkerboard_results, mesh_results, output_dir: str) -> None:
    """Save the two comparison figures."""
    import matplotlib.pyplot as plt

    from fea.plotting import plot_density, save

    os.makedirs(output_dir, exist_ok=True)

    fig, axes = plt.subplots(len(checkerboard_results), 1, figsize=(9, 8.4))
    for ax, (label, model, result) in zip(axes, checkerboard_results):
        plot_density(
            model.mesh,
            result.density,
            ax=ax,
            title=(
                f"{label}   |   compliance {result.compliance:.2f}, "
                f"checkerboard measure {result.checkerboard_measure():.3f}"
            ),
        )
    fig.suptitle("Regularisation decides whether the layout is manufacturable", y=0.98)
    save(fig, os.path.join(output_dir, "filter_study.png"))

    fig, axes = plt.subplots(len(mesh_results), 1, figsize=(9, 8.4))
    for ax, (label, model, result) in zip(axes, mesh_results):
        plot_density(
            model.mesh,
            result.density,
            ax=ax,
            title=(
                f"{label} elements   |   compliance {result.compliance:.2f}"
            ),
        )
    fig.suptitle(
        "Constant physical filter radius: the same structure at every resolution",
        y=0.98,
    )
    save(fig, os.path.join(output_dir, "mesh_independence.png"))
    print(f"\nFigures written to {output_dir}/")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nx", type=int, default=80)
    parser.add_argument("--ny", type=int, default=40)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--figures", default="results")
    args = parser.parse_args()

    checkerboard_results = checkerboard_study(args.nx, args.ny, args.iterations)
    mesh_results = mesh_independence_study(args.iterations)

    if args.figures.lower() != "none":
        write_figures(checkerboard_results, mesh_results, args.figures)


if __name__ == "__main__":
    main()
