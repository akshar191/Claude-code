"""Verification of the solver against the exact end-loaded cantilever solution.

The model is loaded by the exact parabolic shear traction on its free end and
driven by the exact displacement field on its supported end, so the only error
present is discretisation error. The script reports the tip deflection error and
the L2 displacement error norm over a sequence of refined meshes, and fits the
observed convergence rate.

Run from the project root::

    python examples/validate_cantilever.py
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
    cantilever_displacement,
    cantilever_shear_traction,
    cantilever_stress,
    cantilever_tip_deflection,
    edge_traction_forces,
    euler_bernoulli_tip_deflection,
    structured_grid,
)
from fea.material import von_mises  # noqa: E402

# Geometry and material of the benchmark beam.
LENGTH = 8.0
HEIGHT = 2.0
HALF_HEIGHT = HEIGHT / 2.0
THICKNESS = 1.0
LOAD = 10.0  # transverse resultant at the free end, +y
YOUNGS_MODULUS = 1000.0
POISSONS_RATIO = 0.3


def build_model(nx: int, ny: int):
    """Assemble the cantilever model on an ``nx`` by ``ny`` grid."""
    mesh = structured_grid(nx, ny, lx=LENGTH, ly=HEIGHT, y0=-HALF_HEIGHT)
    material = Material(E=YOUNGS_MODULUS, nu=POISSONS_RATIO, plane="stress")
    model = FEModel(mesh, material, thickness=THICKNESS)

    bcs = BoundaryConditions()

    # Supported end: impose the exact displacement field, which avoids the
    # spurious stress concentration a fully clamped end would introduce.
    left_nodes = mesh.nodes_where(lambda x, y: np.isclose(x, 0.0))
    u_exact, v_exact = cantilever_displacement(
        mesh.nodes[left_nodes, 0],
        mesh.nodes[left_nodes, 1],
        LOAD,
        LENGTH,
        HALF_HEIGHT,
        YOUNGS_MODULUS,
        POISSONS_RATIO,
        THICKNESS,
    )
    bcs.prescribe_values(2 * left_nodes, u_exact)
    bcs.prescribe_values(2 * left_nodes + 1, v_exact)

    # Loaded end: consistent nodal forces from the exact parabolic shear traction.
    right_nodes = mesh.nodes_where(lambda x, y: np.isclose(x, LENGTH))
    right_nodes = right_nodes[np.argsort(mesh.nodes[right_nodes, 1])]
    edges = list(zip(right_nodes[:-1], right_nodes[1:]))
    traction = cantilever_shear_traction(LOAD, HALF_HEIGHT, THICKNESS)
    for dof, value in edge_traction_forces(mesh, edges, traction, THICKNESS).items():
        bcs.add_force(dof, value)

    return mesh, model, bcs


def exact_fields(mesh):
    """Exact nodal displacement vector and von Mises stress for a mesh."""
    u_exact, v_exact = cantilever_displacement(
        mesh.nodes[:, 0],
        mesh.nodes[:, 1],
        LOAD,
        LENGTH,
        HALF_HEIGHT,
        YOUNGS_MODULUS,
        POISSONS_RATIO,
        THICKNESS,
    )
    displacement = np.empty(mesh.n_dofs)
    displacement[0::2] = u_exact
    displacement[1::2] = v_exact

    stress = cantilever_stress(
        mesh.nodes[:, 0], mesh.nodes[:, 1], LOAD, LENGTH, HALF_HEIGHT, THICKNESS
    )
    return displacement, von_mises(stress)


def run_convergence_study(refinements) -> list[dict]:
    """Solve on a sequence of meshes and measure the error in each."""
    exact_tip = cantilever_tip_deflection(
        LOAD, LENGTH, HALF_HEIGHT, YOUNGS_MODULUS, POISSONS_RATIO, THICKNESS
    )
    rows = []

    for nx, ny in refinements:
        mesh, model, bcs = build_model(nx, ny)
        solution = model.solve(bcs)

        tip_node = mesh.nearest_node(LENGTH, 0.0)
        tip = solution.displacements[2 * tip_node + 1]

        u_exact, vm_exact = exact_fields(mesh)
        error_norm = np.linalg.norm(solution.displacements - u_exact) / np.linalg.norm(
            u_exact
        )

        vm_fe = model.nodal_von_mises(solution.displacements)
        stress_error = np.linalg.norm(vm_fe - vm_exact) / np.linalg.norm(vm_exact)

        rows.append(
            {
                "nx": nx,
                "ny": ny,
                "elements": mesh.n_elements,
                "dofs": mesh.n_dofs,
                "h": HEIGHT / ny,
                "tip": tip,
                "tip_error": abs(tip - exact_tip) / abs(exact_tip),
                "u_error": error_norm,
                "stress_error": stress_error,
            }
        )
    return rows


def print_report(rows: list[dict]) -> None:
    """Print the convergence table and the fitted rates."""
    exact_tip = cantilever_tip_deflection(
        LOAD, LENGTH, HALF_HEIGHT, YOUNGS_MODULUS, POISSONS_RATIO, THICKNESS
    )
    eb_tip = euler_bernoulli_tip_deflection(
        LOAD, LENGTH, HALF_HEIGHT, YOUNGS_MODULUS, THICKNESS
    )

    print("End-loaded cantilever, plane stress")
    print(f"  L = {LENGTH}, h = {HEIGHT}, t = {THICKNESS}, P = {LOAD}")
    print(f"  E = {YOUNGS_MODULUS}, nu = {POISSONS_RATIO}")
    print()
    print(f"  Exact elasticity tip deflection   v(L,0) = {exact_tip:.8f}")
    print(f"  Euler-Bernoulli PL^3/(3EI)               = {eb_tip:.8f}")
    print(
        f"  Shear correction captured by the 2D solution = "
        f"{100.0 * (exact_tip - eb_tip) / exact_tip:.2f}% of the total"
    )
    print()

    header = (
        f"{'mesh':>10} {'elements':>9} {'DOFs':>7} {'tip v':>12} "
        f"{'tip err':>9} {'||u|| err':>10} {'||vm|| err':>11}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['nx']:>4}x{row['ny']:<5} {row['elements']:>9} {row['dofs']:>7} "
            f"{row['tip']:>12.6f} {100 * row['tip_error']:>8.3f}% "
            f"{100 * row['u_error']:>9.3f}% {100 * row['stress_error']:>10.3f}%"
        )
    print()

    h = np.array([row["h"] for row in rows])
    for key, name in (("u_error", "displacement"), ("stress_error", "stress")):
        errors = np.array([row[key] for row in rows])
        rate = np.polyfit(np.log(h), np.log(errors), 1)[0]
        print(f"  Observed {name} convergence rate: O(h^{rate:.2f})")

    best = rows[-1]
    print()
    print(
        f"  Finest mesh tip deflection error: {100 * best['tip_error']:.3f}% "
        f"({best['elements']} elements, {best['dofs']} DOFs)"
    )


def write_figures(output_dir: str, rows: list[dict]) -> None:
    """Save the stress contour and convergence figures."""
    import matplotlib.pyplot as plt

    from fea.plotting import plot_field, save

    os.makedirs(output_dir, exist_ok=True)

    mesh, model, bcs = build_model(96, 24)
    solution = model.solve(bcs)
    vm = model.nodal_von_mises(solution.displacements)

    fig, ax = plt.subplots(figsize=(10, 3.4))
    peak = np.abs(solution.displacements).max()
    plot_field(
        mesh,
        vm,
        ax=ax,
        displacements=solution.displacements,
        scale=0.35 * HEIGHT / peak,
        title="Cantilever: von Mises stress on the deformed shape (displacement scaled)",
        label=r"$\sigma_{vm}$",
    )
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    save(fig, os.path.join(output_dir, "cantilever_von_mises.png"))

    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    h = np.array([row["h"] for row in rows])
    ax.loglog(
        h,
        [100 * row["u_error"] for row in rows],
        "o-",
        color="#2f4a73",
        label="Displacement $L_2$ error",
    )
    ax.loglog(
        h,
        [100 * row["tip_error"] for row in rows],
        "s-",
        color="#b4642a",
        label="Tip deflection error",
    )
    reference = 100 * rows[0]["u_error"] * (h / h[0]) ** 2
    ax.loglog(h, reference, "k--", lw=1.0, alpha=0.6, label=r"$O(h^2)$ reference")
    ax.set_xlabel("Element size $h$")
    ax.set_ylabel("Relative error (%)")
    ax.set_title("Mesh convergence against the exact elasticity solution")
    ax.grid(which="both", alpha=0.25, lw=0.6)
    ax.legend(frameon=False)
    save(fig, os.path.join(output_dir, "cantilever_convergence.png"))

    print(f"  Figures written to {output_dir}/")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--figures",
        default="results",
        help="directory for output figures, or 'none' to skip plotting",
    )
    args = parser.parse_args()

    refinements = [(8, 2), (16, 4), (32, 8), (64, 16), (128, 32)]
    rows = run_convergence_study(refinements)
    print_report(rows)

    if args.figures.lower() != "none":
        write_figures(args.figures, rows)


if __name__ == "__main__":
    main()
