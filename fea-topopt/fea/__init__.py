"""A plane elasticity finite element solver and SIMP topology optimiser.

The package is deliberately small and layered:

* :mod:`fea.material`  - constitutive models and stress invariants
* :mod:`fea.element`   - the four-node isoparametric quadrilateral
* :mod:`fea.mesh`      - structured meshes and degree-of-freedom mapping
* :mod:`fea.solver`    - assembly, boundary conditions, stress recovery
* :mod:`fea.analytical`- closed-form solutions used to verify the solver
* :mod:`fea.topopt`    - SIMP topology optimisation driven by the same solver
* :mod:`fea.plotting`  - figures for deformed shapes, stress fields, layouts
"""

from .analytical import (
    cantilever_displacement,
    cantilever_shear_traction,
    cantilever_stress,
    cantilever_tip_deflection,
    euler_bernoulli_tip_deflection,
)
from .material import Material, principal_stresses, von_mises
from .mesh import QuadMesh, StructuredGrid, structured_grid
from .solver import BoundaryConditions, FEModel, Solution, edge_traction_forces
from .topopt import TopologyOptimizer, TopOptResult, TopOptSettings

__all__ = [
    "BoundaryConditions",
    "FEModel",
    "Material",
    "QuadMesh",
    "Solution",
    "StructuredGrid",
    "TopOptResult",
    "TopOptSettings",
    "TopologyOptimizer",
    "cantilever_displacement",
    "cantilever_shear_traction",
    "cantilever_stress",
    "cantilever_tip_deflection",
    "edge_traction_forces",
    "euler_bernoulli_tip_deflection",
    "principal_stresses",
    "structured_grid",
    "von_mises",
]

__version__ = "1.0.0"
