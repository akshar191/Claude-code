"""Stress-constrained versus compliance-optimal design of an L-bracket.

The L-bracket is the canonical stress-constrained benchmark because minimum
compliance gets it wrong in a specific, instructive way: the stiffest layout for
a given volume keeps the sharp re-entrant corner, where the elasticity solution
is singular and a real part would crack. A stress constraint has to trade some
stiffness for a rounded corner and a more evenly loaded structure.

The comparison is made at equal volume. The stress-constrained optimiser is run
first, to find the least material that keeps the peak von Mises stress under the
limit; the compliance optimiser is then given exactly that much material, and
both designs are evaluated with the same stress measure.

The stress limit is set relative to the peak stress of the full, unoptimised
bracket. Because the corner stress is singular, the finite element peak grows
under mesh refinement and the smallest attainable peak depends on the filter
radius in physical units; the filter is therefore scaled with the mesh so that
the physical length scale, and hence the achievable limit, stays fixed.

Run from the project root::

    python examples/stress_lbracket.py
    python examples/stress_lbracket.py --n 100 --limit-ratio 0.8
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
    structured_grid,
)
from fea.stress_topopt import StressConstrainedOptimizer, StressSettings  # noqa: E402

REFERENCE_N = 60
REFERENCE_RMIN = 2.5  # filter radius in elements at the reference resolution


LOAD_LENGTH = 0.08  # physical length of the loaded edge at the arm tip
PATCH_DEPTH = 0.06  # depth of the solid load-introduction patch behind it


def l_bracket(n: int):
    """Square domain with the upper-right 60% cut away; clamped top, loaded arm tip.

    The load is spread along a fixed physical length of the arm tip, and the
    elements immediately behind it form a solid load-introduction patch that is
    excluded from the stress constraint. A concentrated load produces a stress
    singularity of the model rather than of the part; without the patch it would
    bind the constraint on any mesh fine enough to resolve it, regardless of the
    layout elsewhere.

    Returns:
        ``(model, bcs, passive_void, load_patch)``.
    """
    grid = structured_grid(n, n, lx=1.0, ly=1.0)
    model = FEModel(grid, Material(E=1.0, nu=0.3, plane="stress"))
    top_of_arm = 0.4 * grid.ly

    bcs = BoundaryConditions()
    bcs.fix_nodes(
        grid.nodes_where(lambda x, y: np.isclose(y, grid.ly) & (x <= 0.4 * grid.lx + 1e-9)),
        direction="xy",
    )

    centroids = grid.element_centroids()
    passive_void = (centroids[:, 0] > 0.4 * grid.lx) & (centroids[:, 1] > top_of_arm)

    load_nodes = grid.nodes_where(
        lambda x, y: np.isclose(x, grid.lx) & (y <= top_of_arm + 1e-9) & (y >= top_of_arm - LOAD_LENGTH - 1e-9)
    )
    for node in load_nodes:
        bcs.add_force(2 * node + 1, -1.0 / len(load_nodes))

    load_patch = (
        (centroids[:, 0] > grid.lx - PATCH_DEPTH)
        & (centroids[:, 1] <= top_of_arm)
        & (centroids[:, 1] >= top_of_arm - LOAD_LENGTH)
    )

    return model, bcs, passive_void, load_patch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=80, help="elements along each side")
    parser.add_argument(
        "--limit-ratio",
        type=float,
        default=0.85,
        help="stress limit as a fraction of the full bracket's peak stress",
    )
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--figures", default="results")
    args = parser.parse_args()

    rmin = REFERENCE_RMIN * args.n / REFERENCE_N
    model, bcs, passive_void, load_patch = l_bracket(args.n)
    grid = model.mesh
    print(f"L-bracket, {grid.nx}x{grid.ny} grid, {grid.n_dofs} DOFs, filter radius {rmin:.2f} elements")

    # -- Reference: the full bracket ----------------------------------------------
    probe = StressConstrainedOptimizer(
        model,
        bcs,
        StressSettings(stress_limit=1.0, filter_radius=rmin),
        passive_void=passive_void,
        passive_solid=load_patch,
        unconstrained=load_patch,
    )
    full = probe.physical_density(np.ones(grid.n_elements))
    peak_full = float(probe.analyse(full)[4][probe.design_mask].max())
    limit = args.limit_ratio * peak_full
    print(f"  full bracket: peak relaxed von Mises stress {peak_full:.3f}")
    print(f"  stress limit: {limit:.3f} ({args.limit_ratio:.0%} of that)")

    # -- 1. Minimum volume under the stress limit ----------------------------------
    print("\nMinimum volume, stress-constrained")
    settings = StressSettings(
        stress_limit=limit,
        filter_radius=rmin,
        max_iterations=args.iterations,
        initial_density=1.0,
    )

    def report(iteration, volume, max_stress, constraint, density):
        if iteration % 20 == 0 or iteration == 1:
            print(
                f"    it {iteration:>4}   volume = {volume:.4f}   "
                f"peak stress = {max_stress:.3f}   constraint = {constraint:+.4f}"
            )

    start = time.perf_counter()
    stress_result = StressConstrainedOptimizer(
        model,
        bcs,
        settings,
        passive_void=passive_void,
        passive_solid=load_patch,
        unconstrained=load_patch,
    ).run(callback=report)
    print(
        f"  {'converged' if stress_result.converged else 'iteration cap'} after "
        f"{stress_result.iterations} iterations in {time.perf_counter() - start:.0f}s"
    )
    print(
        f"  volume fraction {stress_result.volume_fraction:.4f}, "
        f"peak stress {stress_result.max_stress:.3f} "
        f"({stress_result.max_stress / limit:.1%} of the limit)"
    )

    # -- 2. Minimum compliance with the same amount of material ---------------------
    volume = stress_result.volume_fraction
    print(f"\nMinimum compliance at the same volume fraction, {volume:.3f}")
    start = time.perf_counter()
    compliance_result = TopologyOptimizer(
        model,
        bcs,
        TopOptSettings(
            volume_fraction=volume,
            filter_radius=rmin,
            filter_type="density",
            max_iterations=args.iterations,
        ),
        passive_void=passive_void,
        passive_solid=load_patch,
    ).run()
    compliance_stress = probe.analyse(compliance_result.density)[4]
    peak_compliance = float(compliance_stress[probe.design_mask].max())
    print(
        f"  {compliance_result.iterations} iterations in {time.perf_counter() - start:.0f}s, "
        f"compliance {compliance_result.compliance:.3f}"
    )
    print(f"  peak relaxed von Mises stress {peak_compliance:.3f} ({peak_compliance / limit:.0%} of the limit)")

    # Compliance of the stress-constrained design, for the cost of the constraint.
    stress_design_compliance = model.solve(
        bcs, scale=settings.E_min + stress_result.density**settings.penalty * (1 - settings.E_min)
    ).compliance

    print("\nSame material, same load:")
    print(f"  {'design':<22} {'volume':>8} {'compliance':>11} {'peak stress':>12} {'vs limit':>9}")
    print(
        f"  {'minimum compliance':<22} {volume:>8.3f} {compliance_result.compliance:>11.3f} "
        f"{peak_compliance:>12.3f} {peak_compliance / limit:>8.0%}"
    )
    print(
        f"  {'stress-constrained':<22} {volume:>8.3f} {stress_design_compliance:>11.3f} "
        f"{stress_result.max_stress:>12.3f} {stress_result.max_stress / limit:>8.0%}"
    )
    print(
        f"\n  The stress constraint costs "
        f"{100 * (stress_design_compliance / compliance_result.compliance - 1):.1f}% stiffness "
        f"and buys a {100 * (1 - stress_result.max_stress / peak_compliance):.0f}% lower peak stress."
    )

    if args.figures.lower() == "none":
        return

    import matplotlib.pyplot as plt

    from fea.plotting import plot_density, save

    os.makedirs(args.figures, exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(11, 10.5))

    plot_density(
        grid,
        compliance_result.density,
        ax=axes[0, 0],
        title=(
            f"Minimum compliance, volume {volume:.2f}\n"
            f"compliance {compliance_result.compliance:.1f}, peak stress {peak_compliance:.1f}"
        ),
    )
    plot_density(
        grid,
        stress_result.density,
        ax=axes[0, 1],
        title=(
            f"Stress-constrained, volume {volume:.2f}, limit {limit:.1f}\n"
            f"compliance {stress_design_compliance:.1f}, peak stress {stress_result.max_stress:.1f}"
        ),
    )

    vmax = peak_compliance
    for ax, stress, label in (
        (axes[1, 0], compliance_stress, "minimum compliance"),
        (axes[1, 1], stress_result.stress, "stress-constrained"),
    ):
        image = stress.reshape(grid.element_grid_shape())
        im = ax.imshow(
            image,
            origin="lower",
            extent=[0, grid.lx, 0, grid.ly],
            cmap="magma",
            vmin=0.0,
            vmax=vmax,
            interpolation="nearest",
        )
        ax.set_title(f"Relaxed von Mises stress, {label}")
        ax.set_xticks([])
        ax.set_yticks([])
        colorbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.02)
        colorbar.set_label(r"$x^{q}\,\sigma_{vm}$")
        colorbar.ax.axhline(limit, color="white", lw=1.2)

    fig.suptitle(
        "Same bracket, same load, same material: stiffness-optimal keeps the sharp "
        "corner, stress-constrained rounds it",
        y=0.995,
    )
    save(fig, os.path.join(args.figures, "stress_lbracket.png"))

    fig, ax = plt.subplots(figsize=(7, 4))
    iterations = np.arange(1, len(stress_result.volume_history) + 1)
    ax.plot(iterations, stress_result.volume_history, color="#2f4a73", lw=1.8, label="Volume fraction")
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Volume fraction")
    ax.grid(alpha=0.25, lw=0.6)
    twin = ax.twinx()
    twin.plot(
        iterations,
        np.array(stress_result.stress_history) / limit,
        color="#b4642a",
        lw=1.4,
        ls="--",
        label="Peak stress / limit",
    )
    twin.axhline(1.0, color="#b4642a", lw=0.8, alpha=0.5)
    twin.set_ylabel("Peak stress / limit")
    handles = ax.get_lines() + twin.get_lines()[:1]
    ax.legend(handles, [h.get_label() for h in handles], frameon=False)
    ax.set_title("Stress-constrained L-bracket: convergence")
    save(fig, os.path.join(args.figures, "stress_lbracket_convergence.png"))
    print(f"\nFigures written to {args.figures}/")


if __name__ == "__main__":
    main()
