from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math

from ..resource_reservation_lite.model import Instance

EPS = 1e-9


@dataclass(frozen=True)
class GlobalDPDispatch:
    step: int
    train_id: str
    task_index: int
    resource_id: int
    start_min: float
    end_min: float


@dataclass
class GlobalDPResult:
    feasible: bool
    objective_total_travel: float
    free_run_total: float
    total_delay: float
    completion_by_train: dict[str, float]
    dispatches: list[GlobalDPDispatch]
    states_evaluated: int
    reason: str = ""


def _slots(value: float, dt: float) -> int:
    return int(math.ceil(value / dt - EPS))


def solve_global_resource_dp(instance: Instance, *, max_states: int = 2_000_000) -> GlobalDPResult:
    """Tiny exact resource-state DP for the idealized fixed-chain benchmark.

    State components:
      * progress vector: next task index of each train/job,
      * ready-time vector: earliest time each train can start its next task,
      * resource-availability vector: end of the last protected reservation.

    An action dispatches one train's next task at its earliest feasible time.
    The DP enumerates resource-order decisions and is intended only for small
    benchmark instances; its purpose is to expose the full state explicitly.
    """

    jobs = tuple(instance.jobs)
    train_ids = tuple(job.train_id for job in jobs)
    resources = sorted({instance.resource(key) for key in instance.task_keys()})
    rpos = {r: i for i, r in enumerate(resources)}
    dt = instance.time_step_min
    hslot = _slots(instance.effective_headway_min, dt)
    release_slots = tuple(_slots(instance.snap_up(job.release_min), dt) for job in jobs)
    durations = tuple(
        tuple(_slots(instance.snapped_duration(task), dt) for task in job.tasks)
        for job in jobs
    )

    start_progress = tuple(0 for _ in jobs)
    start_ready = release_slots
    start_resource = tuple(0 for _ in resources)
    policy: dict[tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]], int] = {}
    starts_at_policy: dict[tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]], int] = {}
    visited = 0

    @lru_cache(maxsize=None)
    def value(progress: tuple[int, ...], ready: tuple[int, ...], ravail: tuple[int, ...]) -> float:
        nonlocal visited
        visited += 1
        if visited > max_states:
            raise RuntimeError(f"global DP exceeded max_states={max_states}")
        if all(progress[i] == len(jobs[i].tasks) for i in range(len(jobs))):
            return 0.0

        best = math.inf
        best_i: int | None = None
        best_start: int | None = None
        for i, job in enumerate(jobs):
            k = progress[i]
            if k >= len(job.tasks):
                continue
            task = job.tasks[k]
            rp = rpos[task.resource_id]
            start = max(ready[i], ravail[rp])
            end = start + durations[i][k]

            new_progress = list(progress)
            new_progress[i] += 1
            new_ready = list(ready)
            new_ready[i] = end
            new_ravail = list(ravail)
            new_ravail[rp] = end + hslot

            immediate = 0.0
            if new_progress[i] == len(job.tasks):
                immediate = (end - release_slots[i]) * dt
            candidate = immediate + value(tuple(new_progress), tuple(new_ready), tuple(new_ravail))
            if candidate < best - EPS:
                best = candidate
                best_i = i
                best_start = start
            elif abs(candidate - best) <= EPS and best_i is not None:
                # deterministic tie break: earlier start, then train id
                if (start, train_ids[i]) < (best_start if best_start is not None else math.inf, train_ids[best_i]):
                    best_i = i
                    best_start = start

        if best_i is not None:
            state = (progress, ready, ravail)
            policy[state] = best_i
            starts_at_policy[state] = int(best_start or 0)
        return best

    try:
        objective = value(start_progress, start_ready, start_resource)
    except RuntimeError as exc:
        return GlobalDPResult(False, math.inf, instance.free_run_total, math.inf, {}, [], visited, str(exc))

    if not math.isfinite(objective):
        return GlobalDPResult(False, math.inf, instance.free_run_total, math.inf, {}, [], visited, "no feasible state")

    dispatches: list[GlobalDPDispatch] = []
    completion: dict[str, float] = {}
    progress, ready, ravail = start_progress, start_ready, start_resource
    step = 0
    while not all(progress[i] == len(jobs[i].tasks) for i in range(len(jobs))):
        state = (progress, ready, ravail)
        i = policy[state]
        k = progress[i]
        job = jobs[i]
        task = job.tasks[k]
        rp = rpos[task.resource_id]
        start = starts_at_policy[state]
        end = start + durations[i][k]
        dispatches.append(GlobalDPDispatch(step, job.train_id, k, task.resource_id, start * dt, end * dt))
        step += 1
        p = list(progress); p[i] += 1
        rd = list(ready); rd[i] = end
        rv = list(ravail); rv[rp] = end + hslot
        if p[i] == len(job.tasks):
            completion[job.train_id] = end * dt
        progress, ready, ravail = tuple(p), tuple(rd), tuple(rv)

    delay = objective - instance.free_run_total
    return GlobalDPResult(
        feasible=True,
        objective_total_travel=objective,
        free_run_total=instance.free_run_total,
        total_delay=max(0.0, delay),
        completion_by_train=completion,
        dispatches=dispatches,
        states_evaluated=visited,
    )
