from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
import time

from ..resource_reservation_lite.conflicts import all_conflicts, safe_frontier
from ..resource_reservation_lite.model import Instance, ReservationDecision, Schedule, canonical_reservations
from ..resource_reservation_lite.scheduler import schedule_from_reservations
from ..resource_reservation_lite.search import beam_search

EPS = 1e-9


@dataclass
class FirstFeasibleResult:
    strategy: str
    feasible: bool
    objective: float | None
    delay: float | None
    nodes_evaluated: int
    elapsed_seconds: float
    reservations: tuple[ReservationDecision, ...]
    schedule: Schedule | None
    safe_frontier_min: float
    safe_tasks: int


def first_feasible_search(instance: Instance, *, strategy: str = "dfs", max_nodes: int = 100_000) -> FirstFeasibleResult:
    """Search from the relaxed root without an initial incumbent.

    This intentionally measures *time/nodes to first feasible UB*. It is kept
    separate from the exact B&B, which already uses a guaranteed constructive UB.
    """

    if strategy not in {"dfs", "best_first"}:
        raise ValueError("strategy must be 'dfs' or 'best_first'")
    started = time.perf_counter()
    root = ((), schedule_from_reservations(instance, ()))
    nodes = 1

    if strategy == "dfs":
        container: list[tuple[tuple[ReservationDecision, ...], Schedule]] = [root]
        pop = container.pop
        push = container.append
    else:
        heap: list[tuple[float, int, float, int, tuple[ReservationDecision, ...], Schedule]] = []
        serial = 0
        root_conflicts = all_conflicts(instance, root[1])
        sf, safe = safe_frontier(instance, root[1], root_conflicts)
        heapq.heappush(heap, (root[1].objective_total_travel, -safe, -sf, serial, root[0], root[1]))

    while nodes <= max_nodes:
        if strategy == "dfs":
            if not container:
                break
            reservations, schedule = pop()
        else:
            if not heap:
                break
            _, _, _, _, reservations, schedule = heapq.heappop(heap)

        if not schedule.feasible_precedence:
            continue
        conflicts = all_conflicts(instance, schedule)
        if not conflicts:
            sf, safe = safe_frontier(instance, schedule, conflicts)
            return FirstFeasibleResult(
                strategy, True, schedule.objective_total_travel, schedule.total_delay,
                nodes, time.perf_counter() - started, reservations, schedule, sf, safe,
            )

        c = conflicts[0]
        children = []
        for first, second in ((c.left.key, c.right.key), (c.right.key, c.left.key)):
            d = ReservationDecision(c.resource_id, first, second)
            cres = canonical_reservations(reservations + (d,))
            if cres == reservations:
                continue
            cs = schedule_from_reservations(instance, cres)
            nodes += 1
            if not cs.feasible_precedence:
                continue
            cc = all_conflicts(instance, cs)
            sf, safe = safe_frontier(instance, cs, cc)
            children.append((cs.objective_total_travel, -safe, -sf, d.label(), cres, cs))
            if nodes > max_nodes:
                break
        children.sort(key=lambda row: row[:4])
        if strategy == "dfs":
            # Stack is LIFO; push worse first so the better child is explored next.
            for row in reversed(children):
                push((row[4], row[5]))
        else:
            for row in children:
                serial += 1
                heapq.heappush(heap, (row[0], row[1], row[2], serial, row[4], row[5]))

    return FirstFeasibleResult(strategy, False, None, None, nodes, time.perf_counter() - started, (), None, 0.0, 0)


def compare_first_ub_strategies(instance: Instance, *, beam_widths: tuple[int, ...] = (1, 2, 4, 8), max_nodes: int = 100_000) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for strategy in ("dfs", "best_first"):
        result = first_feasible_search(instance, strategy=strategy, max_nodes=max_nodes)
        rows.append({
            "strategy": strategy,
            "feasible": result.feasible,
            "first_ub": result.objective,
            "delay": result.delay,
            "nodes_to_first_ub": result.nodes_evaluated,
            "seconds_to_first_ub": result.elapsed_seconds,
            "safe_frontier_min": result.safe_frontier_min,
            "safe_tasks": result.safe_tasks,
        })
    for width in beam_widths:
        started = time.perf_counter()
        result = beam_search(instance, width=width)
        elapsed = time.perf_counter() - started
        rows.append({
            "strategy": f"beam_{width}",
            "feasible": result.schedule is not None,
            "first_ub": None if result.schedule is None else result.schedule.objective_total_travel,
            "delay": None if result.schedule is None else result.schedule.total_delay,
            "nodes_to_first_ub": result.nodes_evaluated,
            "seconds_to_first_ub": elapsed,
            "safe_frontier_min": None,
            "safe_tasks": None,
        })
    return rows
