from __future__ import annotations

import math

from ..resource_reservation_lite.model import Instance, Schedule, TaskKey


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * q
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi:
        return values[lo]
    return values[lo] * (hi - pos) + values[hi] * (pos - lo)


def delay_by_train(instance: Instance, schedule: Schedule) -> dict[str, float]:
    out: dict[str, float] = {}
    for job in instance.jobs:
        free = sum(instance.snapped_duration(task) for task in job.tasks)
        out[job.train_id] = max(0.0, schedule.travel_time_by_train[job.train_id] - free)
    return out


def schedule_metrics(instance: Instance, schedule: Schedule) -> dict[str, object]:
    delays = delay_by_train(instance, schedule)
    vals = list(delays.values())
    avg = sum(vals) / len(vals) if vals else 0.0
    mx = max(vals, default=0.0)
    return {
        "objective_total_travel": schedule.objective_total_travel,
        "free_run_total": schedule.free_run_total,
        "total_delay": schedule.total_delay,
        "average_train_delay": avg,
        "max_train_delay": mx,
        "p95_train_delay": percentile(vals, 0.95),
        "max_to_average_delay_ratio": None if avg <= 1e-12 else mx / avg,
        "delay_by_train": delays,
    }
