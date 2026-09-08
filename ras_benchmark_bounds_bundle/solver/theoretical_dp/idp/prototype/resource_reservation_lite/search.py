from __future__ import annotations

from dataclasses import dataclass

from .conflicts import all_conflicts, safe_frontier
from .model import Instance, ReservationDecision, Schedule, canonical_reservations
from .scheduler import schedule_from_reservations


@dataclass(frozen=True)
class BeamResult:
    width: int
    schedule: Schedule | None
    reservations: tuple[ReservationDecision, ...]
    nodes_evaluated: int
    levels: int


def beam_search(instance: Instance, width: int = 8, *, max_levels: int = 10_000) -> BeamResult:
    if width < 1:
        raise ValueError("beam width must be >= 1")
    frontier: list[tuple[tuple[ReservationDecision, ...], Schedule]] = [
        ((), schedule_from_reservations(instance, ()))
    ]
    nodes = 1
    for level in range(max_levels + 1):
        feasible = []
        next_frontier = []
        for reservations, schedule in frontier:
            if not schedule.feasible_precedence:
                continue
            conflicts = all_conflicts(instance, schedule)
            if not conflicts:
                feasible.append((schedule.objective_total_travel, reservations, schedule))
                continue
            conflict = conflicts[0]
            for first, second in ((conflict.left.key, conflict.right.key), (conflict.right.key, conflict.left.key)):
                decision = ReservationDecision(conflict.resource_id, first, second)
                child_res = canonical_reservations(reservations + (decision,))
                if child_res == reservations:
                    continue
                child_schedule = schedule_from_reservations(instance, child_res)
                nodes += 1
                if not child_schedule.feasible_precedence:
                    continue
                child_conflicts = all_conflicts(instance, child_schedule)
                frontier_min, safe = safe_frontier(instance, child_schedule, child_conflicts)
                next_frontier.append((
                    child_schedule.objective_total_travel,
                    -safe,
                    -frontier_min,
                    len(child_conflicts),
                    child_res,
                    child_schedule,
                ))
        if feasible:
            feasible.sort(key=lambda row: (row[0], len(row[1])))
            _, reservations, schedule = feasible[0]
            return BeamResult(width, schedule, reservations, nodes, level)
        if not next_frontier:
            return BeamResult(width, None, (), nodes, level)
        next_frontier.sort(key=lambda row: row[:4])
        frontier = [(row[4], row[5]) for row in next_frontier[:width]]
    return BeamResult(width, None, (), nodes, max_levels)
