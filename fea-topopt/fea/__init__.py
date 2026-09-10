"""A linear elasticity finite element solver and topology optimiser, in 2D and 3D.

The package is deliberately small and layered:

* :mod:`fea.material`      - constitutive models and stress invariants
* :mod:`fea.element`       - the four-node quadrilateral (Q4)
* :mod:`fea.element3d`     - the eight-node hexahedron (H8)
* :mod:`fea.mesh`          - structured meshes in 2D and 3D, DOF mapping
* :mod:`fea.solver`        - assembly, boundary conditions, stress recovery
* :mod:`fea.analytical`    - closed-form solutions used to verify the solver
* :mod:`fea.topopt`        - minimum compliance SIMP optimisation (2D and 3D)
* :mod:`fea.mma`           - the Method of Moving Asymptotes
* :mod:`fea.stress_topopt` - minimum volume under a von Mises stress limit
* :mod:`fea.plotting`      - figures for stress fields, layouts, voxels
"""

from .analytical import (
    cantilever_displacement,
    cantilever_shear_traction,
    cantilever_stress,
    cantilever_tip_deflection,
    euler_bernoulli_tip_deflection,
    pure_bending_displacement,
    pure_bending_stress,
)
from .material import Material, principal_stresses, von_mises
from .mesh import (
    HexMesh,
    QuadMesh,
    StructuredGrid,
    StructuredGrid3D,
    structured_grid,
    structured_grid_3d,
)
from .mma import MMA
from .solver import BoundaryConditions, FEModel, Solution, edge_traction_forces
from .stress_topopt import StressConstrainedOptimizer, StressResult, StressSettings
from .topopt import TopologyOptimizer, TopOptResult, TopOptSettings

__all__ = [
    "MMA",
    "BoundaryConditions",
    "FEModel",
    "HexMesh",
    "Material",
    "QuadMesh",
    "Solution",
    "StressConstrainedOptimizer",
    "StressResult",
    "StressSettings",
    "StructuredGrid",
    "StructuredGrid3D",
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
    "pure_bending_displacement",
    "pure_bending_stress",
    "structured_grid",
    "structured_grid_3d",
    "von_mises",
]

__version__ = "2.0.0"
