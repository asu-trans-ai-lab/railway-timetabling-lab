"""The original Fluid-Queue Soft-Lagrangian price update.

This module intentionally contains only the queue mathematics used by the
clean production loop.  Fluid-Queue service ``C_q`` and the LR relaxation RHS
``C_LR`` are different quantities: the former drives queue prices, while the
latter is used only in ``sum_i g_i(lambda) - lambda @ C_LR``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping

from dp.network_dp_interface import Arc, DPResult


EPS = 1e-9


@dataclass
class FluidQueueCell:
    arc_id: int
    time_bin: int
    actual_arrival_count: float
    averaged_arrival_count: float
    mow_open_minutes: float
    queue_service_capacity_train: float
    mu_train_per_min: float
    queue_before_train: float
    queue_after_train: float
    clearance_wait_min: float
    unresolved_queue: int
    price_queue_before_train: float
    price_queue_after_train: float
    price_clearance_wait_min: float
    lambda_old: float = 0.0
    lambda_target: float = 0.0
    lambda_new: float = 0.0

    @property
    def arrival_count(self) -> float:
        """Compatibility alias for the actual arrival count."""
        return self.actual_arrival_count


def resource_arrivals(
    paths: Mapping[str, DPResult] | Iterable[DPResult], bin_minutes: float,
) -> dict[tuple[int, int], float]:
    """Count one Fluid-Queue arrival for every selected arc-entry event."""
    values = paths.values() if isinstance(paths, Mapping) else paths
    arrivals: dict[tuple[int, int], float] = defaultdict(float)
    for result in values:
        for arc_id, start, _ in result.legs:
            time_bin = max(0, int(math.floor(start / bin_minutes + EPS)))
            arrivals[(int(arc_id), time_bin)] += 1.0
    return dict(arrivals)


def resource_arrival_train_ids(
    paths: Mapping[str, DPResult] | Iterable[DPResult], bin_minutes: float,
) -> dict[tuple[int, int], set[str]]:
    """Return train IDs producing each aggregate arrival cell."""
    values = paths.values() if isinstance(paths, Mapping) else paths
    arriving: dict[tuple[int, int], set[str]] = defaultdict(set)
    for result in values:
        for arc_id, start, _ in result.legs:
            time_bin = max(0, int(math.floor(start / bin_minutes + EPS)))
            arriving[(int(arc_id), time_bin)].add(result.train_id)
    return dict(arriving)


def arrival_averaging_rho(policy: str, iteration: int, rho_constant: float) -> float:
    """Return the original zero-based iteration averaging weight."""
    if policy == "none":
        return 1.0
    if policy == "msa":
        return 1.0 / (iteration + 1.0)
    if policy == "sqrt-msa":
        return 1.0 / math.sqrt(iteration + 1.0)
    if policy == "constant":
        return float(rho_constant)
    raise ValueError(f"unknown arrival averaging policy: {policy}")


def update_arrival_average(
    previous: Mapping[tuple[int, int], float] | None,
    actual: Mapping[tuple[int, int], float], *, policy: str, rho: float,
) -> dict[tuple[int, int], float]:
    """Preserve actual arrivals while updating the price-driving average."""
    if policy == "none" or previous is None:
        return {key: float(value) for key, value in actual.items()
                if abs(float(value)) > EPS}
    averaged: dict[tuple[int, int], float] = {}
    for key in set(previous) | set(actual):
        value = ((1.0 - rho) * float(previous.get(key, 0.0))
                 + rho * float(actual.get(key, 0.0)))
        if abs(value) > EPS:
            averaged[key] = value
    return averaged


def mow_windows_by_arc(
    arcs: Mapping[int, Arc], mow: Iterable[Mapping[str, float | int]],
) -> dict[int, list[tuple[float, float]]]:
    windows: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for window in mow:
        a, b = int(window["a"]), int(window["b"])
        for arc in arcs.values():
            if {arc.a, arc.b} == {a, b}:
                windows[arc.arc_id].append(
                    (float(window["start"]), float(window["end"])))
    return dict(windows)


def open_minutes_for_block(
    windows: Mapping[int, Iterable[tuple[float, float]]], arc_id: int,
    block_start: float, block_end: float,
) -> float:
    """Return the original union-of-MOW-windows open duration."""
    intervals: list[tuple[float, float]] = []
    for start, end in windows.get(arc_id, ()):
        left, right = max(block_start, start), min(block_end, end)
        if right - left > EPS:
            intervals.append((left, right))
    if not intervals:
        return block_end - block_start
    intervals.sort()
    closed = 0.0
    merged_left, merged_right = intervals[0]
    for left, right in intervals[1:]:
        if left <= merged_right + EPS:
            merged_right = max(merged_right, right)
        else:
            closed += merged_right - merged_left
            merged_left, merged_right = left, right
    closed += merged_right - merged_left
    open_minutes = block_end - block_start - closed
    if abs(open_minutes) <= EPS:
        return 0.0
    return min(block_end - block_start, max(0.0, open_minutes))


def clearance_wait_minutes(
    queue_after: float, current_bin: int, service: list[float],
    bin_minutes: float,
) -> tuple[float, int]:
    """Compute original backlog clearance time over future service blocks."""
    remaining = max(0.0, queue_after)
    if remaining <= EPS:
        return 0.0, 0
    elapsed = 0.0
    for future in range(current_bin + 1, len(service)):
        capacity = max(0.0, service[future])
        if capacity <= EPS:
            elapsed += bin_minutes
            continue
        if remaining <= capacity + EPS:
            elapsed += remaining * bin_minutes / capacity
            return elapsed, 0
        remaining -= capacity
        elapsed += bin_minutes
    return math.inf, 1


def fluid_queue_cells(
    arrivals: Mapping[tuple[int, int], float],
    old_lambda: Mapping[tuple[int, int], float],
    arcs: Mapping[int, Arc],
    mow: Iterable[Mapping[str, float | int]], *, bin_minutes: float,
    horizon: float,
    price_arrivals: Mapping[tuple[int, int], float] | None = None,
) -> dict[tuple[int, int], FluidQueueCell]:
    """Run original ``C_q``, queue, clearance-wait, and price-state recursions."""
    if price_arrivals is None:
        price_arrivals = arrivals
    windows = mow_windows_by_arc(arcs, mow)
    n_bins = max(1, int(math.ceil(horizon / bin_minutes)))
    resource_ids = (
        {arc_id for arc_id, _ in arrivals}
        | {arc_id for arc_id, _ in price_arrivals}
        | {arc_id for arc_id, _ in old_lambda}
    )
    cells: dict[tuple[int, int], FluidQueueCell] = {}
    for arc_id in sorted(resource_ids):
        service: list[float] = []
        open_by_bin: list[float] = []
        for time_bin in range(n_bins):
            start = time_bin * bin_minutes
            end = (time_bin + 1) * bin_minutes
            open_minutes = open_minutes_for_block(
                windows, arc_id, start, end)
            open_by_bin.append(open_minutes)
            # C_q is the open fraction of one 30-minute service slot.
            service.append(open_minutes / bin_minutes)

        queue_before = 0.0
        price_queue_before = 0.0
        for time_bin in range(n_bins):
            key = (arc_id, time_bin)
            actual_arrival = float(arrivals.get(key, 0.0))
            queue_after = max(0.0, queue_before + actual_arrival
                              - service[time_bin])
            clearance, unresolved = clearance_wait_minutes(
                queue_after, time_bin, service, bin_minutes)
            averaged_arrival = float(price_arrivals.get(key, 0.0))
            price_after = max(
                0.0, price_queue_before + averaged_arrival - service[time_bin])
            price_clearance, _ = clearance_wait_minutes(
                price_after, time_bin, service, bin_minutes)
            cells[key] = FluidQueueCell(
                arc_id=arc_id, time_bin=time_bin,
                actual_arrival_count=actual_arrival,
                averaged_arrival_count=averaged_arrival,
                mow_open_minutes=open_by_bin[time_bin],
                queue_service_capacity_train=service[time_bin],
                mu_train_per_min=service[time_bin] / bin_minutes,
                queue_before_train=queue_before,
                queue_after_train=queue_after,
                clearance_wait_min=clearance,
                unresolved_queue=unresolved,
                price_queue_before_train=price_queue_before,
                price_queue_after_train=price_after,
                price_clearance_wait_min=price_clearance,
                lambda_old=float(old_lambda.get(key, 0.0)),
            )
            queue_before = queue_after
            price_queue_before = price_after
    return cells


def fluid_lambda_target(
    cell: FluidQueueCell, *, alpha: float, beta: float,
    wait_reference_minutes: float, queue_wait_cost_per_min: float,
    max_price_wait_minutes: float, use_price_queue: bool = True,
) -> float:
    """Compute the original clipped Fluid-Queue target price."""
    queue_after = (cell.price_queue_after_train if use_price_queue
                   else cell.queue_after_train)
    clearance_wait = (cell.price_clearance_wait_min if use_price_queue
                      else cell.clearance_wait_min)
    if queue_after <= EPS:
        return 0.0
    wait = clearance_wait
    if not math.isfinite(wait):
        wait = max_price_wait_minutes
    wait = min(max(wait, 0.0), max_price_wait_minutes)
    if wait_reference_minutes <= 0.0:
        raise ValueError("wait_reference_minutes must be positive")
    return max(
        0.0,
        alpha * queue_wait_cost_per_min * wait_reference_minutes
        * (wait / wait_reference_minutes) ** beta,
    )


def fluid_lambda_update(
    cells: Mapping[tuple[int, int], FluidQueueCell],
    old_lambda: Mapping[tuple[int, int], float], *, alpha: float,
    beta: float, gamma: float, wait_reference_minutes: float,
    queue_wait_cost_per_min: float, max_price_wait_minutes: float,
    use_price_queue: bool = True,
) -> tuple[dict[tuple[int, int], float], dict[tuple[int, int], float]]:
    """Apply the original gamma smoothing to every current price cell."""
    if not 0.0 <= gamma <= 1.0:
        raise ValueError("gamma must be in [0, 1]")
    target: dict[tuple[int, int], float] = {}
    new: dict[tuple[int, int], float] = {}
    for key in set(old_lambda) | set(cells):
        cell = cells.get(key)
        target[key] = (0.0 if cell is None else fluid_lambda_target(
            cell, alpha=alpha, beta=beta,
            wait_reference_minutes=wait_reference_minutes,
            queue_wait_cost_per_min=queue_wait_cost_per_min,
            max_price_wait_minutes=max_price_wait_minutes,
            use_price_queue=use_price_queue,
        ))
        old = max(0.0, float(old_lambda.get(key, 0.0)))
        new[key] = max(0.0, (1.0 - gamma) * old + gamma * target[key])
    return new, target
