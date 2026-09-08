from __future__ import annotations

from collections import defaultdict

from .model import Conflict, Instance, Schedule, TaskTiming

EPS = 1e-9


def protected_overlap(left: TaskTiming, right: TaskTiming, headway_min: float) -> bool:
    if left.key.train_id == right.key.train_id:
        return False
    return (
        left.start_min < right.protected_end(headway_min) - EPS
        and right.start_min < left.protected_end(headway_min) - EPS
    )


def all_conflicts(instance: Instance, schedule: Schedule) -> list[Conflict]:
    if not schedule.feasible_precedence:
        return []
    grouped: dict[int, list[TaskTiming]] = defaultdict(list)
    for timing in schedule.timings.values():
        grouped[timing.resource_id].append(timing)

    conflicts: list[Conflict] = []
    h = instance.effective_headway_min
    for resource_id, timings in grouped.items():
        ordered = sorted(timings, key=lambda t: (t.start_min, t.end_min, t.key))
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                # Once the later start is outside the earlier protected support,
                # still do not break globally because the later task can have a
                # very long duration and the symmetric support is what matters.
                if protected_overlap(left, right, h):
                    conflicts.append(Conflict(resource_id, left, right))
    return sorted(
        conflicts,
        key=lambda c: (
            c.time_min,
            c.resource_id,
            c.left.key.train_id,
            c.left.key.task_index,
            c.right.key.train_id,
            c.right.key.task_index,
        ),
    )


def earliest_conflict(instance: Instance, schedule: Schedule) -> Conflict | None:
    conflicts = all_conflicts(instance, schedule)
    return conflicts[0] if conflicts else None


def safe_frontier(instance: Instance, schedule: Schedule, conflicts: list[Conflict] | None = None) -> tuple[float, int]:
    if not schedule.feasible_precedence:
        return 0.0, 0
    conflicts = all_conflicts(instance, schedule) if conflicts is None else conflicts
    if not conflicts:
        frontier = max((timing.end_min for timing in schedule.timings.values()), default=0.0)
        return frontier, instance.total_task_count
    frontier = min(conflict.time_min for conflict in conflicts)
    safe = sum(
        1
        for timing in schedule.timings.values()
        if timing.protected_end(instance.effective_headway_min) <= frontier + EPS
    )
    return frontier, safe
