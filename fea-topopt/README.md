# Finite Element Solver and Topology Optimiser

A two-dimensional linear elasticity finite element solver written from scratch in
Python, verified against a closed-form elasticity solution, and extended into a
SIMP topology optimiser that uses the same solver as its engine.

Nothing here wraps a commercial package. The element stiffness matrices, the
sparse assembly, the constraint handling, the stress recovery, the compliance
sensitivities and the optimality criteria update are all implemented directly.

![Optimised MBB beam](results/topopt_mbb.png)

## What it does

**Solver.** Four-node isoparametric quadrilateral (Q4) elements under plane
stress or plane strain, integrated with a 2×2 Gauss rule. Sparse assembly with a
precomputed connectivity pattern, prescribed displacements handled by static
partitioning rather than a penalty, consistent nodal loads from distributed edge
tractions, and stress recovery by Gauss-point extrapolation with nodal averaging.

**Optimiser.** Minimum compliance topology optimisation by the SIMP method, with
analytical sensitivities, a choice of sensitivity or density filter, optimality
criteria updates driven by a bisection search on the volume multiplier, and
support for regions forced solid or void.

## Verification

The solver is checked against the end-loaded cantilever of Timoshenko and
Goodier — a complete solution of the elasticity equations, not a beam theory
approximation. The exact displacement field is imposed on the supported end and
the exact parabolic shear traction on the loaded end, so the only error present
is discretisation error.

```
      mesh  elements    DOFs        tip v   tip err  ||u|| err  ||vm|| err
--------------------------------------------------------------------------
   8x2            16      54     2.390605   10.464%    10.449%     16.897%
  16x4            64     170     2.593498    2.865%     2.862%      6.119%
  32x8           256     594     2.650382    0.735%     0.734%      2.040%
  64x16         1024    2210     2.665062    0.185%     0.185%      0.678%
 128x32         4096    8514     2.668763    0.046%     0.046%      0.229%

  Observed displacement convergence rate: O(h^1.96)
  Observed stress convergence rate:       O(h^1.56)
```

Tip deflection converges to **0.046% of the exact value**, and the displacement
error falls at the O(h²) rate the bilinear element is expected to deliver.

![Convergence](results/cantilever_convergence.png)

The reference deflection is `v(L,0) = PL³/3EI + P(4+5ν)c²L/6EI`. The second term
is transverse shear, worth **4.1%** of the total for this beam and entirely
absent from Euler–Bernoulli theory. A solver that merely reproduced beam theory
would fail this test; the test suite asserts the shear contribution explicitly.

Further checks run as unit tests:

| Check | What it proves |
| --- | --- |
| Element stiffness vs. the published matrix in Andreassen et al. (2011) | Correct to machine precision (1.7 × 10⁻¹⁶) |
| Patch test on a mesh with randomly displaced interior nodes | Constant strain reproduced exactly on distorted elements |
| Rank of the free element stiffness matrix | Exactly 5 — three rigid body modes and no spurious mechanisms |
| Compliance sensitivity vs. central finite differences | Analytical gradient correct to 1 part in 10⁴ |
| Support reactions vs. applied load | Global equilibrium satisfied to 10⁻⁸ |

![Cantilever stress](results/cantilever_von_mises.png)

## Optimised layouts

Each layout below is produced by the verified solver, on a 120×40 grid, in
roughly ten seconds.

| | |
| --- | --- |
| ![Cantilever](results/topopt_cantilever.png) | ![Bridge](results/topopt_bridge.png) |

The bridge case is worth a second look: given only a deck to keep solid, supports
at the two lower corners, and a distributed load, the optimiser converges on an
arch. Nobody told it about arches.

![L-bracket](results/topopt_lbracket.png)

The L-bracket rounds off the re-entrant corner — where the elasticity solution is
singular and where real brackets crack — and fans a set of struts out to the
loaded tip.

## Why the filter matters

Unregularised, this optimisation problem is ill-posed. Two consequences are
measured in `examples/filter_study.py`.

**Checkerboarding.** The unfiltered optimiser drives the design towards
alternating solid and void elements: a pattern the Q4 element space rewards, with
no physical length scale, that no process can manufacture. Filtering reduces the
checkerboard measure by a factor of 5.

```
variant                 rmin  compliance  checkerboard  greyness
----------------------------------------------------------------
No filter                0.0      85.917        0.0505      0.4%
Sensitivity filter       2.0      82.993        0.0133     14.7%
Density filter           2.0      87.282        0.0100     18.5%
```

The compliance column is deliberately *not* the argument. The filter also imposes
a minimum member size, and that constraint has a real stiffness cost, so filtered
compliance moves with the filter radius for reasons unrelated to checkerboarding.

**Mesh independence.** The property that actually makes a result trustworthy:
holding the filter radius fixed in physical units, the optimised compliance is
reproduced to **1.1% across a sixteen-fold increase in element count**, and the
structure is visibly the same at every resolution.

```
      mesh  elements  rmin (elem)  compliance  checkerboard
-----------------------------------------------------------
  40x20          800          1.5      90.380        0.0293
  80x40         3200          3.0      89.640        0.0084
 160x80        12800          6.0      90.673        0.0021
```

![Mesh independence](results/mesh_independence.png)

## Interactive demo

`web/load-paths.html` is a self-contained page that runs the same optimisation in
a browser, with all four benchmark problems and live control of the volume
fraction, penalty exponent, filter radius, and mesh resolution. The element
stiffness matrix, the SIMP interpolation, the sensitivity filter and the
optimality criteria update are ports of the Python above; the only difference is
the linear solve, which uses warm-started conjugate gradients instead of a sparse
direct factorisation.

It reproduces the Python result to five significant figures — compliance 90.381
against 90.380 for the 40 × 20 cantilever, in the same 43 iterations. Open the
file directly in a browser, or use the hosted copy:

**[claude.ai/code/artifact/d6229698-0e0c-4284-a193-e1333116457b](https://claude.ai/code/artifact/d6229698-0e0c-4284-a193-e1333116457b)**

## Method

The discrete problem is `K u = f`. Element stiffness is integrated as

```
k = t ∫ Bᵀ D B dA
```

over the 2×2 Gauss rule, with `B` built from the isoparametric shape function
derivatives and the Jacobian, and `D` the plane stress or plane strain
constitutive matrix. Prescribed displacements are applied by partitioning into
free and constrained sets and solving `K_ff u_f = f_f − K_fc u_c`, so non-zero
prescribed displacements are exact rather than approximated by a large diagonal
term.

The optimiser minimises compliance subject to a volume constraint:

```
minimise    c(x) = fᵀu = Σ E_e(x_e) u_eᵀ k_e u_e
subject to  K(x) u = f,  Σ v_e x_e / Σ v_e ≤ V,  0 ≤ x_e ≤ 1
```

with the modified SIMP interpolation `E_e(x_e) = E_min + x_e^p (E_0 − E_min)`,
which penalises intermediate densities while keeping the stiffness matrix
non-singular in void regions. Because the problem is self-adjoint, the
sensitivity is available in closed form:

```
∂c/∂x_e = −p x_e^(p−1) (E_0 − E_min) u_eᵀ k_e u_e
```

The gradient is filtered over a radius `rmin` to impose a minimum length scale,
and the design advances by the optimality criteria update
`x_new = x (−∂c/∂x / λ ∂v/∂x)^η`, clipped to a move limit, with `λ` found by
bisection so the volume constraint stays active.

## Usage

```bash
pip install -r requirements.txt

python examples/validate_cantilever.py         # verification study
python examples/optimize.py --case all         # all four benchmark layouts
python examples/optimize.py --case mbb --nx 240 --ny 80 --rmin 4
python examples/filter_study.py                # regularisation study
python -m pytest tests/ -q                     # 22 tests
```

As a library:

```python
import numpy as np
from fea import (BoundaryConditions, FEModel, Material,
                 TopologyOptimizer, TopOptSettings, structured_grid)

grid = structured_grid(120, 40, lx=3.0, ly=1.0)
model = FEModel(grid, Material(E=1.0, nu=0.3, plane="stress"))

bcs = BoundaryConditions()
bcs.fix_nodes(grid.nodes_where(lambda x, y: np.isclose(x, 0.0)), direction="xy")
bcs.add_force(2 * grid.node_id(120, 20) + 1, -1.0)

result = TopologyOptimizer(
    model, bcs, TopOptSettings(volume_fraction=0.4, filter_radius=2.5)
).run()

print(result.compliance, result.as_image().shape)
```

## Layout

```
fea/
  material.py    constitutive matrices, von Mises and principal stresses
  element.py     Q4 shape functions, B matrix, stiffness, stress extrapolation
  mesh.py        structured grids, connectivity, DOF mapping
  solver.py      assembly, boundary conditions, solution, stress recovery
  analytical.py  exact elasticity solutions used for verification
  topopt.py      SIMP interpolation, filters, optimality criteria update
  plotting.py    deformed shapes, stress contours, layouts, convergence
examples/
  validate_cantilever.py   convergence study against the exact solution
  optimize.py              MBB, cantilever, bridge, and L-bracket cases
  filter_study.py          checkerboarding and mesh independence
tests/                     22 unit and verification tests
web/
  load-paths.html          self-contained browser port of the optimiser
```

## References

- Timoshenko & Goodier, *Theory of Elasticity*, 3rd ed., Art. 21.
- Bendsøe & Sigmund, *Topology Optimization: Theory, Methods and Applications*, 2003.
- Andreassen, Clausen, Schevenels, Lazarov & Sigmund, "Efficient topology
  optimization in MATLAB using 88 lines of code", *Structural and
  Multidisciplinary Optimization* 43 (2011), 1–16.
- Sigmund & Petersson, "Numerical instabilities in topology optimization",
  *Structural Optimization* 16 (1998), 68–75.
