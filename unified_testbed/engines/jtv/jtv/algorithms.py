from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from collections import Counter, defaultdict
import math

from .model import Agent, Network, ResourceKey, Trajectory
from .dp import single_agent_dp, joint_group_dp
from .master import solve_master, MasterResult


@dataclass
class LRRecord:
    iteration: int
    conflicts: int
    violation: float
    price_tv_step: float
    total_price: float
    entry_order: Tuple[int, ...]


def usage(paths: Sequence[Trajectory]) -> Counter:
    c = Counter()
    for p in paths:
        c.update(p.footprint)
    return c


def conflict_keys(paths: Sequence[Trajectory], capacity: float = 1.0) -> List[ResourceKey]:
    u = usage(paths)
    return [r for r, v in u.items() if v > capacity + 1e-9]


def lagrangian_relaxation(
    network: Network,
    agents: Sequence[Agent],
    iterations: int = 20,
    base_step: float = 2.0,
) -> Tuple[List[Trajectory], Dict[ResourceKey, float], List[LRRecord]]:
    prices: Dict[ResourceKey, float] = defaultdict(float)
    records: List[LRRecord] = []
    paths: List[Trajectory] = []
    previous_prices: Dict[ResourceKey, float] = {}
    for k in range(iterations):
        paths = []
        for a in agents:
            p = single_agent_dp(network, a, prices)
            if p is None:
                raise RuntimeError(f"No path for agent {a.id}")
            paths.append(p)
        u = usage(paths)
        keys = set(u) | set(prices)
        step = base_step / math.sqrt(k + 1.0)
        old = dict(prices)
        violation = 0.0
        for r in keys:
            subgrad = float(u.get(r, 0.0) - 1.0)
            violation += max(0.0, subgrad)
            prices[r] = max(0.0, prices.get(r, 0.0) + step * subgrad)
        tv = sum(abs(prices.get(r, 0.0) - old.get(r, 0.0)) for r in set(prices) | set(old))
        arr = sorted((p.arrival_times[a.id], a.id) for p, a in zip(paths, agents))
        records.append(LRRecord(k, len(conflict_keys(paths)), violation, tv, sum(prices.values()), tuple(aid for _, aid in arr)))
    return paths, dict(prices), records


def delayed_initial_columns(network: Network, agent: Agent, max_delay: int = 4) -> List[Trajectory]:
    pool: List[Trajectory] = []
    seen = set()
    for delay in range(max_delay + 1):
        p = single_agent_dp(network, agent, forced_release=agent.release + delay)
        if p is not None and p.signature not in seen:
            seen.add(p.signature)
            pool.append(p)
    return pool


def individual_column_generation(
    network: Network,
    agents: Sequence[Agent],
    max_iterations: int = 20,
) -> Tuple[Dict[Tuple[int, ...], List[Trajectory]], MasterResult, List[dict]]:
    pools: Dict[Tuple[int, ...], List[Trajectory]] = {
        (a.id,): delayed_initial_columns(network, a, 5) for a in agents
    }
    capacities: Dict[ResourceKey, float] = {}
    for pool in pools.values():
        for col in pool:
            for r in col.footprint:
                capacities[r] = 1.0
    history: List[dict] = []
    for it in range(max_iterations):
        master = solve_master(pools, capacities, integer=False)
        if not math.isfinite(master.objective):
            break
        added = 0
        min_rc = math.inf
        for a in agents:
            g = (a.id,)
            p = single_agent_dp(network, a, master.resource_prices)
            if p is None:
                continue
            pi = master.group_duals[g]
            rc = p.physical_cost + sum(master.resource_prices.get(r, 0.0) for r in p.footprint) - pi
            min_rc = min(min_rc, rc)
            if rc < -1e-7 and all(p.signature != q.signature for q in pools[g]):
                pools[g].append(p)
                for r in p.footprint:
                    capacities.setdefault(r, 1.0)
                added += 1
        history.append({"iteration": it, "objective": master.objective, "added": added, "min_rc": min_rc})
        if added == 0:
            return pools, master, history
    return pools, solve_master(pools, capacities, integer=False), history


def quadratic_resource_extra(
    current_usage: Mapping[ResourceKey, float],
    donor: Trajectory,
    gamma: float,
) -> Dict[ResourceKey, float]:
    keys = set(current_usage) | set(donor.footprint)
    return {
        r: gamma * current_usage.get(r, 0.0) + 0.5 * gamma * (1.0 - 2.0 * (1.0 if r in donor.footprint else 0.0))
        for r in keys
    }


def group_column_generation(
    network: Network,
    groups: Sequence[Sequence[Agent]],
    max_iterations: int = 12,
    use_quadratic: bool = False,
    gamma: float = 0.4,
    rho: float = 0.1,
) -> Tuple[Dict[Tuple[int, ...], List[Trajectory]], MasterResult, List[dict]]:
    pools: Dict[Tuple[int, ...], List[Trajectory]] = {}
    capacities: Dict[ResourceKey, float] = {}
    # Initial columns: a joint solution for each disjoint group.
    for agents in groups:
        g = tuple(a.id for a in agents)
        col = joint_group_dp(network, agents)
        if col is None:
            raise RuntimeError(f"No initial group column for {g}")
        pools[g] = [col]
        for r in col.footprint:
            capacities.setdefault(r, 1.0)
    history: List[dict] = []
    for it in range(max_iterations):
        master = solve_master(pools, capacities, integer=False)
        if not math.isfinite(master.objective):
            break
        # Current aggregate usage from the LP solution.
        current_usage: Dict[ResourceKey, float] = defaultdict(float)
        k = 0
        donors: Dict[Tuple[int, ...], Trajectory] = {}
        for g, pool in pools.items():
            best_w = -1.0
            for col in pool:
                w = float(master.weights[k])
                for r in col.footprint:
                    current_usage[r] += w
                if w > best_w:
                    best_w, donors[g] = w, col
                k += 1
        added = 0
        min_rc = math.inf
        qp_distinct = 0
        for agents in groups:
            g = tuple(a.id for a in agents)
            fo = joint_group_dp(network, agents, master.resource_prices)
            if fo is not None:
                rc = fo.physical_cost + sum(master.resource_prices.get(r, 0.0) for r in fo.footprint) - master.group_duals[g]
                min_rc = min(min_rc, rc)
                if rc < -1e-7 and all(fo.signature != q.signature for q in pools[g]):
                    pools[g].append(fo); added += 1
                    for r in fo.footprint: capacities.setdefault(r, 1.0)
            if use_quadratic:
                donor = donors[g]
                extra = quadratic_resource_extra(current_usage, donor, gamma)
                qp = joint_group_dp(network, agents, master.resource_prices, reference=donor, rho=rho, resource_extra=extra)
                if qp is not None and all(qp.signature != q.signature for q in pools[g]):
                    pools[g].append(qp); added += 1; qp_distinct += 1
                    for r in qp.footprint: capacities.setdefault(r, 1.0)
        history.append({"iteration": it, "objective": master.objective, "added": added, "min_rc": min_rc, "qp_distinct": qp_distinct})
        if added == 0:
            return pools, master, history
    return pools, solve_master(pools, capacities, integer=False), history
