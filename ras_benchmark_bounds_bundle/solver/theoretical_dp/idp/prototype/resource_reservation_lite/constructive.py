from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .conflicts import all_conflicts, safe_frontier
from .model import Instance, ReservationDecision, Schedule, TaskKey, canonical_reservations
from .scheduler import schedule_from_reservations


@dataclass(frozen=True)
class ConstructiveResult:
    method: str
    schedule: Schedule
    reservations: tuple[ReservationDecision, ...]
    conflicts: int
    safe_frontier_min: float
    safe_tasks: int


def priority_reservations(instance: Instance, train_order: list[str] | None = None) -> tuple[ReservationDecision, ...]:
    """Guaranteed finite-UB construction for the idealized fixed-chain model.

    A global train priority is imposed consistently on every shared resource.
    All cross-job resource edges therefore point from lower to higher train rank,
    so they cannot create a cross-train cycle.  This can be conservative, but it
    gives the B&B a certified finite incumbent immediately.
    """

    if train_order is None:
        train_order = [job.train_id for job in sorted(instance.jobs, key=lambda j: (j.release_min, j.train_id))]
    if set(train_order) != {job.train_id for job in instance.jobs} or len(train_order) != len(instance.jobs):
        raise ValueError("train_order must contain every train exactly once")
    rank = {train_id: index for index, train_id in enumerate(train_order)}

    by_resource: dict[int, list[TaskKey]] = defaultdict(list)
    for key in instance.task_keys():
        by_resource[instance.resource(key)].append(key)

    decisions: list[ReservationDecision] = []
    for resource_id, keys in by_resource.items():
        for i, left in enumerate(keys):
            for right in keys[i + 1 :]:
                if left.train_id == right.train_id:
                    continue
                if rank[left.train_id] < rank[right.train_id]:
                    first, second = left, right
                else:
                    first, second = right, left
                decisions.append(ReservationDecision(resource_id, first, second))
    return canonical_reservations(decisions)


def priority_upper_bound(instance: Instance, train_order: list[str] | None = None) -> ConstructiveResult:
    decisions = priority_reservations(instance, train_order)
    schedule = schedule_from_reservations(instance, decisions)
    if not schedule.feasible_precedence:
        raise RuntimeError("global train-priority construction unexpectedly created a cycle")
    conflicts = all_conflicts(instance, schedule)
    if conflicts:
        raise RuntimeError(
            "global train-priority construction should be resource-feasible but still has conflicts: "
            + conflicts[0].label()
        )
    frontier, safe = safe_frontier(instance, schedule, conflicts)
    return ConstructiveResult("GLOBAL_TRAIN_PRIORITY", schedule, decisions, 0, frontier, safe)


def greedy_conflict_upper_bound(instance: Instance, *, max_steps: int = 100_000) -> ConstructiveResult | None:
    """Greedy repair: resolve the earliest conflict using the lower-LB child.

    This is not guaranteed to find a feasible schedule without backtracking, so
    the exact B&B uses :func:`priority_upper_bound` as the guaranteed incumbent.
    The greedy result is used only when it improves that incumbent.
    """

    reservations: tuple[ReservationDecision, ...] = ()
    for _ in range(max_steps):
        schedule = schedule_from_reservations(instance, reservations)
        if not schedule.feasible_precedence:
            return None
        conflicts = all_conflicts(instance, schedule)
        if not conflicts:
            frontier, safe = safe_frontier(instance, schedule, conflicts)
            return ConstructiveResult("GREEDY_EARLIEST_CONFLICT", schedule, reservations, 0, frontier, safe)
        conflict = conflicts[0]
        candidates = []
        for first, second in ((conflict.left.key, conflict.right.key), (conflict.right.key, conflict.left.key)):
            decision = ReservationDecision(conflict.resource_id, first, second)
            child_res = canonical_reservations(reservations + (decision,))
            child_schedule = schedule_from_reservations(instance, child_res)
            if child_schedule.feasible_precedence:
                child_conflicts = all_conflicts(instance, child_schedule)
                frontier, safe = safe_frontier(instance, child_schedule, child_conflicts)
                candidates.append((child_schedule.objective_total_travel, -safe, -frontier, decision.label(), child_res))
        if not candidates:
            return None
        candidates.sort()
        reservations = candidates[0][-1]
    return None
