from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple
import math
import numpy as np
from scipy.optimize import linprog, milp, Bounds, LinearConstraint

from .model import ResourceKey, Trajectory


@dataclass
class MasterResult:
    objective: float
    weights: np.ndarray
    group_duals: Dict[Tuple[int, ...], float]
    resource_prices: Dict[ResourceKey, float]
    status: str
    mip_node_count: int = 0


def solve_master(
    pools: Mapping[Tuple[int, ...], Sequence[Trajectory]],
    capacities: Mapping[ResourceKey, float],
    integer: bool = False,
) -> MasterResult:
    columns: List[Tuple[Tuple[int, ...], Trajectory]] = []
    for group, pool in pools.items():
        for col in pool:
            columns.append((group, col))
    groups = list(pools)
    resources = list(capacities)
    n = len(columns)
    c = np.array([col.physical_cost for _, col in columns], dtype=float)

    Aeq = np.zeros((len(groups), n))
    beq = np.ones(len(groups))
    for j, (g, _) in enumerate(columns):
        Aeq[groups.index(g), j] = 1.0
    Aub = np.zeros((len(resources), n))
    bub = np.array([capacities[r] for r in resources], dtype=float)
    for j, (_, col) in enumerate(columns):
        for r in col.footprint:
            if r in capacities:
                Aub[resources.index(r), j] = 1.0

    if not integer:
        res = linprog(c, A_ub=Aub, b_ub=bub, A_eq=Aeq, b_eq=beq, bounds=(0.0, None), method="highs")
        if not res.success:
            return MasterResult(math.inf, np.zeros(n), {}, {}, res.message)
        group_duals = {g: float(res.eqlin.marginals[i]) for i, g in enumerate(groups)}
        resource_prices = {r: max(0.0, float(-res.ineqlin.marginals[i])) for i, r in enumerate(resources)}
        return MasterResult(float(res.fun), np.asarray(res.x), group_duals, resource_prices, res.message)

    constraints = [LinearConstraint(Aeq, beq, beq), LinearConstraint(Aub, -np.inf, bub)]
    res = milp(c, integrality=np.ones(n), bounds=Bounds(np.zeros(n), np.ones(n)), constraints=constraints)
    if not res.success:
        return MasterResult(math.inf, np.zeros(n), {}, {}, res.message, int(getattr(res, "mip_node_count", 0) or 0))
    return MasterResult(float(res.fun), np.asarray(res.x), {}, {}, res.message, int(getattr(res, "mip_node_count", 0) or 0))


def selected_columns(
    pools: Mapping[Tuple[int, ...], Sequence[Trajectory]],
    weights: np.ndarray,
    threshold: float = 1e-7,
) -> List[Tuple[Tuple[int, ...], Trajectory, float]]:
    out = []
    k = 0
    for group, pool in pools.items():
        for col in pool:
            if weights[k] > threshold:
                out.append((group, col, float(weights[k])))
            k += 1
    return out
