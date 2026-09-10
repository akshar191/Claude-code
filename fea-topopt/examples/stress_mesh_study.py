"""Is a stress-constrained design a property of the part or of the mesh?

At a re-entrant corner the elasticity solution is singular, so the peak stress a
finite element model reports there grows without bound as the mesh is refined.
That has a consequence for both formulations, and they are not the same
consequence.

A minimum compliance design keeps the corner, so the peak stress reported for it
is largely a property of the discretisation: refine the mesh and the number
climbs, and any safety margin computed from it moves with it.

A stress-constrained design has to satisfy its limit on whichever mesh it is
solved on, so its peak stress is pinned by construction. The question that
matters for it is whether the *volume* it needs converges: if the same physical
requirement demands steadily more material as the mesh is refined, the answer is
a discretisation artefact too.

This script holds the stress limit fixed in absolute terms and the filter radius
fixed in physical units, then solves both problems on a sequence of meshes.

Run from the project root::

    python examples/stress_mesh_study.py
    python examples/stress_mesh_study.py --meshes 60 80 100 --volfrac 0.7
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fea.stress_topopt import StressConstrainedOptimizer, StressSettings  # noqa: E402
from stress_lbracket import (  # noqa: E402
    REFERENCE_N,
    REFERENCE_RMIN,
    compliance_design,
    l_bracket,
    make_probe,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meshes", type=int, nargs="+", default=[60, 80, 100])
    parser.add_argument(
        "--limit",
        type=float,
        default=52.0,
        help="absolute stress limit, held fixed across every mesh",
    )
    parser.add_argument(
        "--volfrac",
        type=float,
        default=0.7,
        help="volume for the compliance design; above its floor so the corner dominates",
    )
    parser.add_argument("--iterations", type=int, default=300)
    args = parser.parse_args()

    print(
        f"Stress limit {args.limit} on every mesh; compliance designs at volume "
        f"fraction {args.volfrac}; filter radius fixed in physical units.\n"
    )
    header = (
        f"{'mesh':>9} {'solid bracket':>14} {'compliance peak':>16} "
        f"{'constrained volume':>19} {'its peak':>9}"
    )
    print(header)
    print("-" * len(header))

    rows = []
    for n in args.meshes:
        rmin = REFERENCE_RMIN * n / REFERENCE_N
        model, bcs, passive_void, load_patch = l_bracket(n)
        probe = make_probe(model, bcs, rmin, passive_void, load_patch)

        full = probe.physical_density(np.ones(model.mesh.n_elements))
        peak_full = float(probe.analyse(full)[4][probe.design_mask].max())

        start = time.perf_counter()
        _, peak_compliance = compliance_design(
            model, bcs, rmin, passive_void, load_patch, args.volfrac, args.iterations
        )
        constrained = StressConstrainedOptimizer(
            model,
            bcs,
            StressSettings(
                stress_limit=args.limit, filter_radius=rmin, max_iterations=args.iterations
            ),
            passive_void=passive_void,
            passive_solid=load_patch,
            unconstrained=load_patch,
        ).run()
        elapsed = time.perf_counter() - start

        rows.append((n, peak_full, peak_compliance, constrained.volume_fraction, constrained.max_stress))
        print(
            f"{n:>4}x{n:<4} {peak_full:>14.2f} {peak_compliance:>16.2f} "
            f"{constrained.volume_fraction:>19.4f} {constrained.max_stress:>9.2f}"
            f"   ({elapsed:.0f}s)",
            flush=True,
        )

    peaks_full = np.array([r[1] for r in rows])
    peaks_compliance = np.array([r[2] for r in rows])
    volumes = np.array([r[3] for r in rows])

    print()
    print(
        f"  Solid bracket peak stress rises {100 * (peaks_full[-1] / peaks_full[0] - 1):.0f}% "
        f"across these meshes, and the compliance design's peak rises "
        f"{100 * (peaks_compliance[-1] / peaks_compliance[0] - 1):.0f}%: both are "
        "reading the\n  corner singularity, which the mesh resolves better each time."
    )
    spread = (volumes.max() - volumes.min()) / volumes.mean()
    monotone = bool(np.all(np.diff(volumes) > 0))
    print(
        f"  The stress-constrained volume moves {100 * spread:.1f}% "
        f"({volumes[0]:.3f} to {volumes[-1]:.3f}) for the same physical requirement"
        + (", rising with every refinement." if monotone else ".")
    )
    if monotone and spread > 0.05:
        print(
            "\n  That is a real limitation, and worth stating plainly: unlike the\n"
            "  compliance formulation, which reproduces its optimum to about 1% across a\n"
            "  sixteen-fold change in element count, this stress-constrained volume is not\n"
            "  mesh converged over this range. The stress is sampled at element centroids,\n"
            "  so on a finer mesh the sampling points sit closer to boundaries and re-entrant\n"
            "  corners where the field is higher; the same physical shape therefore reports a\n"
            "  higher peak and has to be given more material to meet the same limit.\n"
            "  Reporting a stress-constrained volume without saying which mesh produced it\n"
            "  would be meaningless."
        )


if __name__ == "__main__":
    main()
