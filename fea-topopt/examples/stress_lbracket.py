"""Stress-constrained versus compliance-optimal design of an L-bracket.

The L-bracket is the canonical stress-constrained benchmark because minimum
compliance gets it wrong in a specific, instructive way: the stiffest layout for
a given volume keeps the sharp re-entrant corner, where the elasticity solution
is singular and a real part would crack. Compliance is an integral of the whole
structure and barely notices a local hot spot, so no amount of extra material
persuades it to round the corner off.

That leads to the comparison this script makes, which is *not* peak stress at
equal volume. The two formulations optimise different things, and the
stress-constrained design deliberately sits right at its limit, so comparing
their peak stresses at equal volume flatters whichever one happens to be further
from its own optimum. The engineering question is instead:

    given a strength requirement, how much material does each formulation need?

Answering it needs a search over the compliance design's volume, since minimum
compliance has no stress input: the script bisects on volume fraction to find
the lightest compliance-optimal design that meets the limit, and compares that
with the volume the stress-constrained optimiser needs for the same limit.

The result splits into two regimes, and the script reports whichever applies.
Below the compliance design's stress floor no volume is enough, because the
corner is still there. Above it the corner stops binding and the two are
comparable, with minimum compliance usually a little lighter - the
stress-constrained problem is strongly non-convex and its aggregated constraint
is an approximation, so it does not win everywhere, only where the constraint is
actually doing work that compliance cannot do.

Run from the project root::

    python examples/stress_lbracket.py
    python examples/stress_lbracket.py --limit-ratio 0.75 --n 100
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
        lambda x, y: np.isclose(x, grid.lx)
        & (y <= top_of_arm + 1e-9)
        & (y >= top_of_arm - LOAD_LENGTH - 1e-9)
    )
    for node in load_nodes:
        bcs.add_force(2 * node + 1, -1.0 / len(load_nodes))

    load_patch = (
        (centroids[:, 0] > grid.lx - PATCH_DEPTH)
        & (centroids[:, 1] <= top_of_arm)
        & (centroids[:, 1] >= top_of_arm - LOAD_LENGTH)
    )

    return model, bcs, passive_void, load_patch


def make_probe(model, bcs, rmin, passive_void, load_patch):
    """An optimiser used only to evaluate the stress measure on a given design."""
    return StressConstrainedOptimizer(
        model,
        bcs,
        StressSettings(stress_limit=1.0, filter_radius=rmin),
        passive_void=passive_void,
        passive_solid=load_patch,
        unconstrained=load_patch,
    )


def compliance_design(model, bcs, rmin, passive_void, load_patch, volfrac, iterations):
    """Minimum compliance at a fixed volume, with its peak stress measured."""
    result = TopologyOptimizer(
        model,
        bcs,
        TopOptSettings(
            volume_fraction=volfrac,
            filter_radius=rmin,
            filter_type="density",
            max_iterations=iterations,
        ),
        passive_void=passive_void,
        passive_solid=load_patch,
    ).run()
    probe = make_probe(model, bcs, rmin, passive_void, load_patch)
    peak = float(probe.analyse(result.density)[4][probe.design_mask].max())
    return result, peak


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=80, help="elements along each side")
    parser.add_argument(
        "--limit-ratio",
        type=float,
        default=0.85,
        help="stress limit as a fraction of the full bracket's peak stress",
    )
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument(
        "--bisection-steps", type=int, default=5, help="volume search steps for the compliance design"
    )
    parser.add_argument("--figures", default="results")
    args = parser.parse_args()

    rmin = REFERENCE_RMIN * args.n / REFERENCE_N
    model, bcs, passive_void, load_patch = l_bracket(args.n)
    grid = model.mesh
    probe = make_probe(model, bcs, rmin, passive_void, load_patch)
    print(f"L-bracket, {grid.nx}x{grid.ny} grid, {grid.n_dofs} DOFs, filter radius {rmin:.2f} elements")

    full = probe.physical_density(np.ones(grid.n_elements))
    peak_full = float(probe.analyse(full)[4][probe.design_mask].max())
    limit = args.limit_ratio * peak_full
    print(f"  solid bracket: peak relaxed von Mises stress {peak_full:.3f}")
    print(f"  stress limit:  {limit:.3f} ({args.limit_ratio:.0%} of that)")

    # -- 1. The compliance design's stress floor -----------------------------------
    # Minimum compliance takes no stress input, so the only lever is volume. Give
    # it most of the domain and see how low its peak stress can go.
    print("\nMinimum compliance: peak stress against volume fraction")
    floor_volumes = (0.4, 0.5, 0.6, 0.8)
    floor = np.inf
    floor_design = None
    for volfrac in floor_volumes:
        result, peak = compliance_design(
            model, bcs, rmin, passive_void, load_patch, volfrac, args.iterations
        )
        marker = "  <- limit met" if peak <= limit else ""
        print(f"    volume {volfrac:.2f}:  peak {peak:7.3f}   compliance {result.compliance:8.3f}{marker}")
        if peak < floor:
            floor, floor_design = peak, (result, volfrac, peak)
    print(f"  floor: {floor:.3f}. Extra material past this point buys no strength —")
    print("  the design keeps the sharp corner, and the corner sets the peak.")

    # -- 2. Minimum volume under the stress limit ----------------------------------
    print(f"\nStress-constrained: minimum volume with peak stress <= {limit:.3f}")
    settings = StressSettings(
        stress_limit=limit,
        filter_radius=rmin,
        max_iterations=args.iterations,
        initial_density=1.0,
    )

    def report(iteration, volume, max_stress, constraint, density):
        if iteration % 25 == 0 or iteration == 1:
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

    # -- 3. The verdict -------------------------------------------------------------
    print("\nMaterial needed to meet the limit:")
    if floor > limit:
        print(f"  minimum compliance   —  unattainable at any volume (floor {floor:.3f} > {limit:.3f})")
        print(f"  stress-constrained   {stress_result.volume_fraction:.4f}")
        print(
            f"\n  The requirement is below the compliance design's floor, so no amount of\n"
            f"  material makes that layout safe. The stress-constrained design meets it\n"
            f"  using {stress_result.volume_fraction:.0%} of the domain."
        )
        comparison = None
    else:
        # Bisect for the lightest compliance design that still meets the limit.
        print("  searching for the lightest compliance-optimal design that meets it")
        lo, hi = 0.05, max(floor_volumes)
        best = hi
        for _ in range(args.bisection_steps):
            mid = 0.5 * (lo + hi)
            _, peak = compliance_design(
                model, bcs, rmin, passive_void, load_patch, mid, args.iterations
            )
            ok = peak <= limit
            print(f"    volume {mid:.4f}: peak {peak:7.3f} {'meets' if ok else 'over'}")
            if ok:
                hi, best = mid, mid
            else:
                lo = mid
        comparison = best
        print(f"\n  minimum compliance   {best:.4f}")
        print(f"  stress-constrained   {stress_result.volume_fraction:.4f}")
        delta = 100.0 * (stress_result.volume_fraction - best) / best
        verdict = "lighter" if delta < 0 else "heavier"
        print(
            f"\n  The limit sits above the compliance floor, so the corner is no longer\n"
            f"  what binds. Here the stress-constrained design is {abs(delta):.0f}% {verdict}:\n"
            f"  its aggregated constraint is an approximation and the problem is strongly\n"
            f"  non-convex, so it wins only where the constraint does work compliance cannot."
        )

    if args.figures.lower() == "none":
        return

    import matplotlib.pyplot as plt

    from fea.plotting import plot_density, save

    os.makedirs(args.figures, exist_ok=True)
    reference_result, reference_volume, reference_peak = floor_design
    reference_stress = probe.analyse(reference_result.density)[4]

    fig, axes = plt.subplots(2, 2, figsize=(11, 10.5))
    plot_density(
        grid,
        reference_result.density,
        ax=axes[0, 0],
        title=(
            f"Minimum compliance, volume {reference_volume:.2f}\n"
            f"peak stress {reference_peak:.1f} — its floor, at any volume"
        ),
    )
    plot_density(
        grid,
        stress_result.density,
        ax=axes[0, 1],
        title=(
            f"Stress-constrained, limit {limit:.1f}\n"
            f"volume {stress_result.volume_fraction:.2f}, peak stress {stress_result.max_stress:.1f}"
        ),
    )

    vmax = max(reference_peak, stress_result.max_stress)
    for ax, stress, label in (
        (axes[1, 0], reference_stress, "minimum compliance"),
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
        "Minimum compliance keeps the sharp corner at any volume; the stress "
        "constraint rounds it",
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
