"""Fluid-Queue-driven Lagrangian evaluation for the clean architecture."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .fluid_queue import (
    FluidQueueCell,
    arrival_averaging_rho as arrival_averaging_weight,
    fluid_lambda_update,
    fluid_queue_cells,
    resource_arrivals,
    update_arrival_average,
)
from .dp_interface import (
    Arc,
    BranchRestrictions,
    DPConfig,
    DPResult,
    NetworkDP,
    Train,
)


EPS = 1e-9
DEFAULT_ALPHA = 1.0
DEFAULT_BETA = 4.0
DEFAULT_GAMMA = 0.15
DEFAULT_WAIT_REFERENCE_MINUTES = 30.0
DEFAULT_MAX_PRICE_WAIT_MINUTES = 180.0
DEFAULT_QUEUE_WAIT_COST_PER_MIN = 1.0
DEFAULT_ARRIVAL_AVERAGING_POLICY = "none"
DEFAULT_ARRIVAL_AVERAGING_RHO = 0.20


def protected_cell_bounds(
    start: float, exit_: float, *, bin_minutes: float, headway: float,
) -> range:
    """Return the DP's protected aggregate cells for one trajectory leg."""
    first = max(0, int(math.floor(start / bin_minutes + EPS)))
    last = int(math.ceil(
        (exit_ + max(0.0, headway)) / bin_minutes - EPS)) - 1
    return range(first, max(first, last + 1))


def resource_time_occupancy(
    result: DPResult, *, bin_minutes: float, headway: float,
) -> dict[tuple[int, int], float]:
    """Return LR occupancy ``a(i,r,b)`` separately from arrival events.

    Each transition touching a protected cell contributes one unit.  This is
    the LR feature map; Fluid Queue arrivals are counted independently by
    :func:`fluid_queue.resource_arrivals` at each arc entry.
    """
    usage: dict[tuple[int, int], float] = {}
    for arc_id, start, exit_ in result.legs:
        for time_bin in protected_cell_bounds(
            start, exit_, bin_minutes=bin_minutes, headway=headway):
            key = (int(arc_id), int(time_bin))
            usage[key] = usage.get(key, 0.0) + 1.0
    return usage


def safe_capacity(
    trains: Iterable[Train], *, config: DPConfig,
) -> dict[tuple[int, int], float]:
    """Return the audited unrestricted-domain-safe LR envelope.

    This is intentionally separate from Fluid Queue ``C_q``.  It bounds the
    number of positive-time DP transitions that can contribute to one cell
    over the finite configured grid.  It is safe but weak until a tighter
    unrestricted-domain derivation is justified.
    """
    train_list = list(trains)
    count = len(train_list)
    max_transitions_per_train = math.floor(
        config.horizon / config.time_step) + 1
    return {(-1, -1): float(count * max_transitions_per_train)}


def capacity_for(
    key: tuple[int, int], *, train_count: int, config: DPConfig,
    capacity_map: Mapping[tuple[int, int], float] | None = None,
) -> float:
    if capacity_map is not None and key in capacity_map:
        return max(0.0, float(capacity_map[key]))
    return float(max(0, train_count) * (
        math.floor(config.horizon / config.time_step) + 1))


def lambda_dot_capacity(
    lambdas: Mapping[tuple[int, int], float], *, train_count: int,
    config: DPConfig,
    capacity_map: Mapping[tuple[int, int], float] | None = None,
) -> float:
    """Compute only the LR RHS term ``lambda^T C_LR``."""
    return sum(
        max(0.0, float(value)) * capacity_for(
            key, train_count=train_count, config=config,
            capacity_map=capacity_map)
        for key, value in lambdas.items()
    )


@dataclass(frozen=True)
class LREvaluation:
    node_id: int | str
    iteration: int
    dual_value: float
    minimum_sum: float
    lambda_dot_capacity: float
    paths: dict[str, DPResult]
    occupancy: dict[tuple[int, int], float]
    arrivals: dict[tuple[int, int], float]
    averaged_arrivals: dict[tuple[int, int], float]
    fluid_cells: dict[tuple[int, int], FluidQueueCell]
    lambda_target: dict[tuple[int, int], float]
    next_lambda: dict[tuple[int, int], float]
    subgradient: dict[tuple[int, int], float]
    positive_violation_l1: float
    positive_violation_l2: float
    lambda_norm_l2: float
    path_changes: int
    dp_calls: int


@dataclass
class LRRun:
    node_id: int | str
    restrictions: BranchRestrictions
    initial_lambda: dict[tuple[int, int], float]
    best_dual_lb: float = -math.inf
    best_lambda: dict[tuple[int, int], float] = field(default_factory=dict)
    best_paths: dict[str, DPResult] = field(default_factory=dict)
    final_lambda: dict[tuple[int, int], float] = field(default_factory=dict)
    final_paths: dict[str, DPResult] = field(default_factory=dict)
    final_occupancy: dict[tuple[int, int], float] = field(default_factory=dict)
    final_arrivals: dict[tuple[int, int], float] = field(default_factory=dict)
    final_averaged_arrivals: dict[tuple[int, int], float] = field(default_factory=dict)
    final_fluid_cells: dict[tuple[int, int], FluidQueueCell] = field(default_factory=dict)
    final_subgradient: dict[tuple[int, int], float] = field(default_factory=dict)
    iteration_log: list[dict[str, object]] = field(default_factory=list)
    fluid_queue_log: list[dict[str, object]] = field(default_factory=list)
    status: str = "UNRESOLVED"
    iterations: int = 0
    dp_calls: int = 0
    runtime_sec: float = 0.0


def _norm(values: Mapping[tuple[int, int], float]) -> float:
    return math.sqrt(sum(float(value) ** 2 for value in values.values()))


def evaluate_at_lambda(
    dp: NetworkDP,
    *,
    node_id: int | str,
    iteration: int,
    arcs: Mapping[int, Arc],
    trains: list[Train],
    mow: list[Mapping[str, float | int]],
    lambdas: Mapping[tuple[int, int], float],
    restrictions: BranchRestrictions,
    config: DPConfig,
    capacity_map: Mapping[tuple[int, int], float] | None = None,
    previous_paths: Mapping[str, DPResult] | None = None,
    previous_averaged_arrivals: Mapping[tuple[int, int], float] | None = None,
    alpha: float = DEFAULT_ALPHA,
    beta: float = DEFAULT_BETA,
    gamma: float = DEFAULT_GAMMA,
    wait_reference_minutes: float = DEFAULT_WAIT_REFERENCE_MINUTES,
    queue_wait_cost_per_min: float = DEFAULT_QUEUE_WAIT_COST_PER_MIN,
    max_price_wait_minutes: float = DEFAULT_MAX_PRICE_WAIT_MINUTES,
    arrival_averaging_policy: str = DEFAULT_ARRIVAL_AVERAGING_POLICY,
    arrival_averaging_rho: float = DEFAULT_ARRIVAL_AVERAGING_RHO,
) -> LREvaluation | None:
    """Evaluate ``g_i(lambda)`` and advance Fluid Queue by one iteration."""
    paths = dp.solve(arcs, trains, mow, lambdas, restrictions, config)
    if any(not path.feasible for path in paths.values()):
        return None

    occupancy: dict[tuple[int, int], float] = {}
    for path in paths.values():
        for key, value in resource_time_occupancy(
            path, bin_minutes=config.bin_minutes,
            headway=config.safety_headway).items():
            occupancy[key] = occupancy.get(key, 0.0) + value
    arrivals = resource_arrivals(paths, bin_minutes=config.bin_minutes)
    rho = arrival_averaging_weight(
        arrival_averaging_policy, iteration, arrival_averaging_rho)
    averaged_arrivals = update_arrival_average(
        previous_averaged_arrivals, arrivals,
        policy=arrival_averaging_policy, rho=rho)
    cells = fluid_queue_cells(
        arrivals, lambdas, arcs, mow, bin_minutes=config.bin_minutes,
        horizon=config.horizon, price_arrivals=averaged_arrivals)
    next_lambda, target = fluid_lambda_update(
        cells, lambdas, alpha=alpha, beta=beta, gamma=gamma,
        wait_reference_minutes=wait_reference_minutes,
        queue_wait_cost_per_min=queue_wait_cost_per_min,
        max_price_wait_minutes=max_price_wait_minutes,
        use_price_queue=True,
    )

    minimum_sum = sum(path.generalized_cost for path in paths.values())
    dot_capacity = lambda_dot_capacity(
        lambdas, train_count=len(trains), config=config,
        capacity_map=capacity_map)
    dual = minimum_sum - dot_capacity
    keys = set(occupancy) | set(lambdas)
    subgradient = {
        key: occupancy.get(key, 0.0) - capacity_for(
            key, train_count=len(trains), config=config,
            capacity_map=capacity_map)
        for key in keys
    }
    positive = [max(0.0, value) for value in subgradient.values()]
    changes = 0
    if previous_paths is not None:
        for train in trains:
            before = previous_paths.get(train.train_id)
            after = paths.get(train.train_id)
            if before is None or after is None or (
                    before.arc_ids, before.legs) != (after.arc_ids, after.legs):
                changes += 1
    return LREvaluation(
        node_id=node_id, iteration=iteration, dual_value=dual,
        minimum_sum=minimum_sum, lambda_dot_capacity=dot_capacity,
        paths=paths, occupancy=occupancy, arrivals=arrivals,
        averaged_arrivals=averaged_arrivals, fluid_cells=cells,
        lambda_target=target, next_lambda=next_lambda,
        subgradient=subgradient,
        positive_violation_l1=sum(positive),
        positive_violation_l2=math.sqrt(sum(value * value for value in positive)),
        lambda_norm_l2=_norm(lambdas), path_changes=changes,
        dp_calls=len(trains),
    )


def _fluid_log_row(
    evaluation: LREvaluation, best_dual: float, rho: float,
    started: float, tick: float,
    max_price_wait_minutes: float,
) -> dict[str, object]:
    cells = evaluation.fluid_cells
    actual_counts = [cell.actual_arrival_count for cell in cells.values()]
    average_counts = [cell.averaged_arrival_count for cell in cells.values()]
    queues = [cell.queue_after_train for cell in cells.values()]
    price_queues = [cell.price_queue_after_train for cell in cells.values()]
    waits = [cell.clearance_wait_min for cell in cells.values()
             if cell.queue_after_train > EPS]
    price_waits = [cell.price_clearance_wait_min for cell in cells.values()
                   if cell.price_queue_after_train > EPS]
    finite_price_waits = [value for value in price_waits if math.isfinite(value)]
    unresolved_count = sum(
        cell.unresolved_queue for cell in cells.values())
    price_unresolved_count = sum(
        cell.price_queue_after_train > EPS
        and not math.isfinite(cell.price_clearance_wait_min)
        for cell in cells.values())
    lambdas = list(evaluation.next_lambda.values())
    lambda_changes = [abs(evaluation.next_lambda.get(key, 0.0)
                           - cell.lambda_old)
                      for key, cell in cells.items()]
    finite_waits = [value for value in waits if math.isfinite(value)]
    capped_waits = [
        min(max(value, 0.0), max_price_wait_minutes)
        if math.isfinite(value) else max_price_wait_minutes
        for value in waits
    ]
    capped_price_waits = [
        min(max(value, 0.0), max_price_wait_minutes)
        if math.isfinite(value) else max_price_wait_minutes
        for value in price_waits
    ]
    occupancy_values = list(evaluation.occupancy.values())
    return {
        "node_id": evaluation.node_id,
        "iteration": evaluation.iteration,
        "current_lr_value": evaluation.dual_value,
        "best_lr_value": best_dual,
        "minimum_sum": evaluation.minimum_sum,
        "lambda_dot_C_LR": evaluation.lambda_dot_capacity,
        "lambda_norm": evaluation.lambda_norm_l2,
        "lambda_max": max(lambdas, default=0.0),
        "lambda_mean": sum(lambdas) / len(lambdas) if lambdas else 0.0,
        "lambda_nonzero_count": sum(abs(value) > EPS for value in lambdas),
        "lambda_linf_change": max(lambda_changes, default=0.0),
        "actual_arrival_count": sum(actual_counts),
        "averaged_arrival_count": sum(average_counts),
        "actual_arrival_cells": len(evaluation.arrivals),
        "averaged_arrival_cells": len(evaluation.averaged_arrivals),
        "price_queue_before": sum(
            cell.price_queue_before_train for cell in cells.values()),
        "price_queue_after": sum(price_queues),
        "max_queue": max(queues, default=0.0),
        # Keep the historical finite field while making the raw and capped
        # interpretations explicit for audit/reporting.
        "max_clearance_wait": max(finite_waits, default=0.0),
        "max_clearance_wait_raw": max(waits, default=0.0),
        "max_clearance_wait_capped": max(capped_waits, default=0.0),
        "unresolved_queue_count": unresolved_count,
        "clearance_wait_cap_or_infinite": int(unresolved_count > 0),
        "max_price_clearance_wait": max(finite_price_waits, default=0.0),
        "max_price_clearance_wait_raw": max(price_waits, default=0.0),
        "max_price_clearance_wait_capped": max(capped_price_waits, default=0.0),
        "price_unresolved_queue_count": price_unresolved_count,
        "max_service_capacity_C_q": max(
            (cell.queue_service_capacity_train for cell in cells.values()),
            default=0.0),
        "max_service_rate_mu": max(
            (cell.mu_train_per_min for cell in cells.values()), default=0.0),
        "positive_queue_cells": sum(cell.queue_after_train > EPS
                                     for cell in cells.values()),
        "price_wait_cap_hit_count": sum(
            cell.price_queue_after_train > EPS and (
                not math.isfinite(cell.price_clearance_wait_min)
                or cell.price_clearance_wait_min >= max_price_wait_minutes)
            for cell in cells.values()),
        "rho": rho,
        "subgradient_norm": _norm(evaluation.subgradient),
        "positive_capacity_violation_l1": evaluation.positive_violation_l1,
        "positive_capacity_violation_l2": evaluation.positive_violation_l2,
        "number_of_path_changes": evaluation.path_changes,
        "number_of_train_DPs": evaluation.dp_calls,
        "max_lr_occupancy_usage": max(occupancy_values, default=0.0),
        "sum_lr_occupancy_usage": sum(occupancy_values),
        "runtime_sec": time.monotonic() - tick,
        "wall_time_sec": time.monotonic() - started,
        "price_policy": "fluid-queue",
        "lr_capacity_scope": "unrestricted-safe-envelope-or-explicit-audit-map",
    }


def run_lr(
    dp: NetworkDP,
    *,
    node_id: int | str,
    arcs: Mapping[int, Arc],
    trains: list[Train],
    mow: list[Mapping[str, float | int]],
    restrictions: BranchRestrictions,
    config: DPConfig,
    capacity_map: Mapping[tuple[int, int], float] | None = None,
    initial_lambda: Mapping[tuple[int, int], float] | None = None,
    max_iterations: int = 12,
    min_iterations: int = 1,
    alpha: float = DEFAULT_ALPHA,
    beta: float = DEFAULT_BETA,
    gamma: float = DEFAULT_GAMMA,
    wait_reference_minutes: float = DEFAULT_WAIT_REFERENCE_MINUTES,
    queue_wait_cost_per_min: float = DEFAULT_QUEUE_WAIT_COST_PER_MIN,
    max_price_wait_minutes: float = DEFAULT_MAX_PRICE_WAIT_MINUTES,
    arrival_averaging_policy: str = DEFAULT_ARRIVAL_AVERAGING_POLICY,
    arrival_averaging_rho: float = DEFAULT_ARRIVAL_AVERAGING_RHO,
) -> LRRun:
    """Run only the original Fluid Queue price policy.

    ``min_iterations`` remains as a compatibility parameter, but it does not
    select a second update policy.  Every evaluated lambda is priced by Fluid
    Queue and every evaluated lambda gets its separate LR value.
    """
    del min_iterations
    started = time.monotonic()
    current = {key: max(0.0, float(value))
               for key, value in (initial_lambda or {}).items()}
    run = LRRun(node_id=node_id, restrictions=restrictions,
                initial_lambda=dict(current))
    previous_paths: dict[str, DPResult] | None = None
    previous_averaged_arrivals: dict[tuple[int, int], float] | None = None

    for iteration in range(max_iterations + 1):
        tick = time.monotonic()
        evaluation = evaluate_at_lambda(
            dp, node_id=node_id, iteration=iteration, arcs=arcs,
            trains=trains, mow=mow, lambdas=current,
            restrictions=restrictions, config=config,
            capacity_map=capacity_map, previous_paths=previous_paths,
            previous_averaged_arrivals=previous_averaged_arrivals,
            alpha=alpha, beta=beta, gamma=gamma,
            wait_reference_minutes=wait_reference_minutes,
            queue_wait_cost_per_min=queue_wait_cost_per_min,
            max_price_wait_minutes=max_price_wait_minutes,
            arrival_averaging_policy=arrival_averaging_policy,
            arrival_averaging_rho=arrival_averaging_rho,
        )
        run.dp_calls += len(trains)
        if evaluation is None:
            run.status = "INFEASIBLE"
            run.iterations = iteration
            run.runtime_sec = time.monotonic() - started
            return run

        if evaluation.dual_value > run.best_dual_lb + EPS:
            run.best_dual_lb = evaluation.dual_value
            run.best_lambda = dict(current)
            run.best_paths = dict(evaluation.paths)
        run.final_lambda = dict(current)
        run.final_paths = dict(evaluation.paths)
        run.final_occupancy = dict(evaluation.occupancy)
        run.final_arrivals = dict(evaluation.arrivals)
        run.final_averaged_arrivals = dict(evaluation.averaged_arrivals)
        run.final_fluid_cells = dict(evaluation.fluid_cells)
        run.final_subgradient = dict(evaluation.subgradient)
        # The queue cells and LR occupancy cells are deliberately joined only
        # for logging.  They remain separate calculations and use separate
        # names so a C_q value cannot be mistaken for C_LR.
        log_keys = (set(evaluation.fluid_cells) | set(evaluation.occupancy)
                    | set(evaluation.next_lambda))
        for key in sorted(log_keys):
            cell = evaluation.fluid_cells.get(key)
            if cell is not None:
                cell.lambda_target = float(evaluation.lambda_target.get(key, 0.0))
                cell.lambda_new = float(evaluation.next_lambda.get(key, 0.0))
            arc_id, time_bin = key
            run.fluid_queue_log.append({
                "node_id": node_id,
                "iteration": iteration,
                "arc_id": arc_id,
                "time_bin": time_bin,
                "actual_arrival_count": None if cell is None else cell.actual_arrival_count,
                "averaged_arrival_count": None if cell is None else cell.averaged_arrival_count,
                "mow_open_minutes": None if cell is None else cell.mow_open_minutes,
                "C_q": None if cell is None else cell.queue_service_capacity_train,
                "mu": None if cell is None else cell.mu_train_per_min,
                "Q_before": None if cell is None else cell.queue_before_train,
                "Q_after": None if cell is None else cell.queue_after_train,
                "clearance_wait_min": None if cell is None else cell.clearance_wait_min,
                "clearance_wait_raw_min": None if cell is None else cell.clearance_wait_min,
                "clearance_wait_capped_min": None if cell is None else (
                    min(cell.clearance_wait_min, max_price_wait_minutes)
                    if math.isfinite(cell.clearance_wait_min)
                    else max_price_wait_minutes),
                "unresolved_queue": None if cell is None else cell.unresolved_queue,
                "price_queue_before": None if cell is None else cell.price_queue_before_train,
                "price_queue_after": None if cell is None else cell.price_queue_after_train,
                "price_clearance_wait": None if cell is None else cell.price_clearance_wait_min,
                "price_clearance_wait_raw_min": None if cell is None else cell.price_clearance_wait_min,
                "price_clearance_wait_capped_min": None if cell is None else (
                    min(cell.price_clearance_wait_min, max_price_wait_minutes)
                    if math.isfinite(cell.price_clearance_wait_min)
                    else max_price_wait_minutes),
                "lambda_old": 0.0 if cell is None else cell.lambda_old,
                "lambda_hat": float(evaluation.lambda_target.get(key, 0.0)),
                "lambda_new": float(evaluation.next_lambda.get(key, 0.0)),
                "lr_occupancy_usage": evaluation.occupancy.get(key, 0.0),
                "C_LR": capacity_for(
                    key, train_count=len(trains), config=config,
                    capacity_map=capacity_map),
                "lr_capacity_scope": (
                    "explicit-audit-map" if capacity_map and key in capacity_map
                    else "unrestricted-safe-envelope-v1"),
            })
        rho = arrival_averaging_weight(
            arrival_averaging_policy, iteration, arrival_averaging_rho)
        run.iteration_log.append(_fluid_log_row(
            evaluation, run.best_dual_lb, rho, started, tick,
            max_price_wait_minutes))
        previous_paths = dict(evaluation.paths)
        previous_averaged_arrivals = dict(evaluation.averaged_arrivals)
        run.iterations = iteration + 1
        current = dict(evaluation.next_lambda)

    run.status = "FLUID_QUEUE_COMPLETED"
    run.runtime_sec = time.monotonic() - started
    return run
