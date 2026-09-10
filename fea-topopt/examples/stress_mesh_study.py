"""Mesh dependence of the peak stress: compliance-optimal versus stress-constrained.

At a re-entrant corner the elasticity solution is singular, so the peak stress a
finite element model reports there is not a property of the part but of the
mesh: refine the mesh and it grows without bound. A minimum compliance design
keeps that corner, so its reported peak stress is a mesh artefact and any margin
computed from it is fictitious.

A stress-constrained design has to satisfy its limit on whichever mesh it is
solved on, so its peak stress is a design property. With the filter radius held
fixed in physical units, this script solves both problems on a sequence of
meshes and reports how each design's peak stress moves.

Run from the project root::

    python examples/stress_mesh_study.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fea import TopologyOptimizer, TopOptSettings  # noqa: E402
from fea.stress_topopt import StressConstrainedOptimizer, StressSettings  # noqa: E402
from stress_lbracket import REFERENCE_N, REFERENCE_RMIN, l_bracket  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meshes", type=int, nargs="+", default=[60, 80, 100])
    parser.add_argument("--limit-ratio", type=float, default=0.85)
    parser.add_argument("--volfrac", type=float, default=0.45, help="compliance design volume")
    parser.add_argument("--iterations", type=int, default=200)
    args = parser.parse_args()

    # The stress limit is fixed in absolute terms from the coarsest mesh, so that
    # every run targets the same physical allowable.
    limit = None
    rows = []

    for n in args.meshes:
        rmin = REFERENCE_RMIN * n / REFERENCE_N
        model, bcs, passive_void, load_patch = l_bracket(n)
        probe = StressConstrainedOptimizer(
            model,
            bcs,
            StressSettings(stress_limit=1.0, filter_radius=rmin),
            passive_void=passive_void,
            passive_solid=load_patch,
            unconstrained=load_patch,
        )
        full = probe.physical_density(np.ones(model.mesh.n_elements))
        peak_full = float(probe.analyse(full)[4][probe.design_mask].max())
        if limit is None:
            limit = args.limit_ratio * peak_full

        start = time.perf_counter()
        compliance = TopologyOptimizer(
            model,
            bcs,
            TopOptSettings(
                volume_fraction=args.volfrac,
                filter_radius=rmin,
                filter_type="density",
                max_iterations=args.iterations,
            ),
            passive_void=passive_void,
            passive_solid=load_patch,
        ).run()
        peak_compliance = float(probe.analyse(compliance.density)[4][probe.design_mask].max())

        constrained = StressConstrainedOptimizer(
            model,
            bcs,
            StressSettings(stress_limit=limit, filter_radius=rmin, max_iterations=args.iterations),
            passive_void=passive_void,
            passive_solid=load_patch,
            unconstrained=load_patch,
        ).run()
        elapsed = time.perf_counter() - start

        rows.append((n, peak_full, peak_compliance, constrained.volume_fraction, constrained.max_stress))
        print(
            f"{n}x{n}: full bracket {peak_full:.1f}, compliance design {peak_compliance:.1f}, "
            f"stress-constrained {constrained.max_stress:.1f} at volume "
            f"{constrained.volume_fraction:.3f}  ({elapsed:.0f}s)",
            flush=True,
        )

    print(f"\nStress limit {limit:.2f}; compliance designs at volume fraction {args.volfrac}\n")
    header = (
        f"{'mesh':>9} {'full bracket':>13} {'compliance design':>18} "
        f"{'stress-constrained':>19} {'its volume':>11}"
    )
    print(header)
    print("-" * len(header))
    for n, peak_full, peak_compliance, volume, peak_constrained in rows:
        print(
            f"{n:>4}x{n:<4} {peak_full:>13.1f} {peak_compliance:>18.1f} "
            f"{peak_constrained:>19.1f} {volume:>11.3f}"
        )

    first, last = rows[0], rows[-1]
    print(
        f"\nRefining from {first[0]}x{first[0]} to {last[0]}x{last[0]}: the compliance design's "
        f"peak rises {100 * (last[2] / first[2] - 1):+.0f}%, the stress-constrained design's "
        f"{100 * (last[4] / first[4] - 1):+.0f}%."
    )


if __name__ == "__main__":
    main()
