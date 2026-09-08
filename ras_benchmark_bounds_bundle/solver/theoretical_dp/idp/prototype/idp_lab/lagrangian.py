from __future__ import annotations

from dataclasses import dataclass
import math

from .physical_chain_dp import ResourceCalendar, solve_physical_chain_dp
from ..resource_reservation_lite.constructive import priority_upper_bound
from ..resource_reservation_lite.model import Instance

EPS = 1e-9


@dataclass
class LRIteration:
    iteration: int
    dual_bound: float
    best_dual_bound: float
    upper_bound: float
    absolute_gap_to_ub: float
    relative_gap_to_ub: float
    total_positive_violation: float
    step_size: float
    max_multiplier: float


@dataclass
class LRResult:
    best_dual_bound: float
    upper_bound: float
    iterations: list[LRIteration]
    multipliers: dict[tuple[int, int], float]
    horizon_min: float


def _slots(value: float, dt: float) -> int:
    return int(math.ceil(value / dt - EPS))


def run_lagrangian_relaxation(
    instance: Instance,
    *,
    iterations: int = 100,
    theta: float = 1.5,
    horizon_min: float | None = None,
) -> LRResult:
    """IDP-4: resource-time Lagrangian relaxation with a pricing DP per train.

    Relaxed capacity for each protected resource-time slot is
    ``sum_i x[i,r,t] <= 1``. Nonnegative multipliers decompose the problem into
    independent physical-chain pricing DPs. A Polyak-style subgradient step uses
    the certified constructive UB as the target.
    """

    if iterations < 1:
        raise ValueError("iterations must be positive")
    dt = instance.time_step_min
    h = instance.effective_headway_min
    hslot = _slots(h, dt)
    ub_result = priority_upper_bound(instance)
    ub = ub_result.schedule.objective_total_travel
    if horizon_min is None:
        latest = max(t.end_min for t in ub_result.schedule.timings.values())
        horizon_min = instance.snap_up(latest + max(10 * dt, 2 * h))
    horizon_slots = _slots(horizon_min, dt)

    multipliers: dict[tuple[int, int], float] = {}
    best_dual = -math.inf
    history: list[LRIteration] = []

    for it in range(iterations):
        train_results = []
        load: dict[tuple[int, int], int] = {}
        lagrangian_train_sum = 0.0
        for job in instance.jobs:
            result = solve_physical_chain_dp(
                instance,
                job.train_id,
                calendar=ResourceCalendar(),
                horizon_min=horizon_min,
                slot_prices=multipliers,
                include_headway_in_price=True,
            )
            if not result.feasible:
                raise RuntimeError(f"pricing DP infeasible for {job.train_id}: {result.reason}")
            train_results.append(result)
            lagrangian_train_sum += result.objective_travel_min
            for k, task in enumerate(job.tasks):
                start_slot = _slots(result.task_starts[k], dt)
                dur_slot = _slots(instance.snapped_duration(task), dt)
                for s in range(start_slot, min(horizon_slots + 1, start_slot + dur_slot + hslot)):
                    key = (task.resource_id, s)
                    load[key] = load.get(key, 0) + 1

        dual = lagrangian_train_sum - sum(multipliers.values())
        # Finite-horizon LR can exhibit tiny numerical overshoots; keep diagnostics honest.
        best_dual = max(best_dual, dual)
        violations: dict[tuple[int, int], float] = {}
        norm2 = 0.0
        total_positive = 0.0
        keys = set(multipliers) | set(load)
        for key in keys:
            g = float(load.get(key, 0) - 1)
            violations[key] = g
            norm2 += g * g
            if g > 0:
                total_positive += g

        if norm2 <= EPS:
            step = 0.0
        else:
            target_gap = max(0.0, ub - dual)
            step = theta * target_gap / norm2
            # If the current dual numerically touches the UB but remains violated,
            # keep a diminishing positive step to escape a degenerate plateau.
            if step <= EPS and total_positive > 0:
                step = theta / math.sqrt(it + 1.0)

        new_multipliers: dict[tuple[int, int], float] = {}
        for key in keys:
            val = max(0.0, multipliers.get(key, 0.0) + step * violations[key])
            if val > 1e-12:
                new_multipliers[key] = val
        multipliers = new_multipliers

        abs_gap = max(0.0, ub - best_dual)
        history.append(LRIteration(
            iteration=it,
            dual_bound=dual,
            best_dual_bound=best_dual,
            upper_bound=ub,
            absolute_gap_to_ub=abs_gap,
            relative_gap_to_ub=abs_gap / max(abs(ub), 1e-12),
            total_positive_violation=total_positive,
            step_size=step,
            max_multiplier=max(multipliers.values(), default=0.0),
        ))

    return LRResult(best_dual, ub, history, multipliers, horizon_min)
