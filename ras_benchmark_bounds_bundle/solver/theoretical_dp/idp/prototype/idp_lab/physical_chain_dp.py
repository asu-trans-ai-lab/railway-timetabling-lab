from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import math
from typing import Iterable, Mapping

from ..resource_reservation_lite.model import Instance, JobSpec, TaskKey

EPS = 1e-9


@dataclass(frozen=True)
class CalendarBlock:
    resource_id: int
    start_min: float
    end_min: float
    owner: str = "EXTERNAL"

    def __post_init__(self) -> None:
        if self.resource_id < 0:
            raise ValueError("resource_id must be nonnegative")
        if self.end_min <= self.start_min:
            raise ValueError("calendar block must have positive duration")


@dataclass
class ResourceCalendar:
    """External resource reservations seen by one conditional train DP.

    Blocks store the *physical* occupation interval. The headway is applied by
    :meth:`can_place`, so the rule is symmetric: a new task must either finish
    plus H before the stored block starts, or start after the stored block ends
    plus H.
    """

    blocks: dict[int, list[CalendarBlock]] = field(default_factory=dict)

    @classmethod
    def from_blocks(cls, blocks: Iterable[CalendarBlock]) -> "ResourceCalendar":
        cal = cls()
        for block in blocks:
            cal.add(block)
        return cal

    def add(self, block: CalendarBlock) -> None:
        self.blocks.setdefault(block.resource_id, []).append(block)
        self.blocks[block.resource_id].sort(key=lambda x: (x.start_min, x.end_min, x.owner))

    def can_place(self, resource_id: int, start_min: float, duration_min: float, headway_min: float) -> bool:
        end_min = start_min + duration_min
        for block in self.blocks.get(resource_id, ()):  # physical block + symmetric headway
            before = end_min + headway_min <= block.start_min + EPS
            after = start_min + EPS >= block.end_min + headway_min
            if not (before or after):
                return False
        return True


@dataclass(frozen=True)
class DPAction:
    state_task_index: int
    state_time_min: float
    action: str
    resource_id: int | None
    next_time_min: float


@dataclass
class PhysicalDPResult:
    feasible: bool
    train_id: str
    objective_travel_min: float
    total_wait_min: float
    completion_min: float
    task_starts: list[float]
    task_ends: list[float]
    actions: list[DPAction]
    states_evaluated: int
    horizon_min: float
    reason: str = ""


def _slots(value: float, dt: float) -> int:
    return int(math.ceil(value / dt - EPS))


def solve_physical_chain_dp(
    instance: Instance,
    train_id: str,
    *,
    calendar: ResourceCalendar | None = None,
    horizon_min: float | None = None,
    slot_prices: Mapping[tuple[int, int], float] | None = None,
    include_headway_in_price: bool = True,
) -> PhysicalDPResult:
    """Solve one train's fixed physical chain with an explicit time-state DP.

    State: ``(task_index, time_slot)``.
    Actions: WAIT one time step, or OCCUPY the next physical resource if the
    external calendar permits it.

    The base elapsed-time cost is exactly the train's travel time. Optional
    nonnegative ``slot_prices[(resource_id, slot)]`` turn the same recursion into
    a pricing/Lagrangian DP. Prices are charged on the protected occupation
    interval when ``include_headway_in_price`` is true.
    """

    job = instance.job(train_id)
    calendar = calendar or ResourceCalendar()
    slot_prices = slot_prices or {}
    dt = instance.time_step_min
    h = instance.effective_headway_min
    release = instance.snap_up(job.release_min)
    release_slot = _slots(release, dt)

    free_run = sum(instance.snapped_duration(task) for task in job.tasks)
    if horizon_min is None:
        # Pedagogical default, not a model constraint: enough room for long waits.
        latest_block = max(
            (block.end_min + h for blocks in calendar.blocks.values() for block in blocks),
            default=release,
        )
        horizon_min = max(release + free_run + 20 * dt + 5 * h, latest_block + free_run + 10 * dt)
    horizon_min = instance.snap_up(horizon_min)
    horizon_slot = _slots(horizon_min, dt)

    # durations in integer slots because Instance snaps all physical data to dt.
    dur_slots = [_slots(instance.snapped_duration(task), dt) for task in job.tasks]
    h_slots = _slots(h, dt)

    policy: dict[tuple[int, int], tuple[str, int]] = {}
    visited = 0

    @lru_cache(maxsize=None)
    def value(k: int, tslot: int) -> float:
        nonlocal visited
        visited += 1
        if k == len(job.tasks):
            return 0.0
        if tslot > horizon_slot:
            return math.inf

        best = math.inf
        best_action: tuple[str, int] | None = None

        # WAIT: elapsed time is part of train travel time.
        if tslot + 1 <= horizon_slot:
            candidate = dt + value(k, tslot + 1)
            if candidate < best - EPS:
                best = candidate
                best_action = ("WAIT", tslot + 1)

        task = job.tasks[k]
        dslot = dur_slots[k]
        start = tslot * dt
        duration = dslot * dt
        if tslot + dslot <= horizon_slot and calendar.can_place(task.resource_id, start, duration, h):
            price_end = tslot + dslot + (h_slots if include_headway_in_price else 0)
            price = 0.0
            for s in range(tslot, min(price_end, horizon_slot + 1)):
                price += float(slot_prices.get((task.resource_id, s), 0.0))
            candidate = duration + price + value(k + 1, tslot + dslot)
            if candidate < best - EPS:
                best = candidate
                best_action = ("OCCUPY", tslot + dslot)

        if best_action is not None:
            policy[(k, tslot)] = best_action
        return best

    objective = value(0, release_slot)
    if not math.isfinite(objective):
        return PhysicalDPResult(
            feasible=False,
            train_id=train_id,
            objective_travel_min=math.inf,
            total_wait_min=math.inf,
            completion_min=math.inf,
            task_starts=[],
            task_ends=[],
            actions=[],
            states_evaluated=visited,
            horizon_min=horizon_min,
            reason="no feasible chain schedule within the DP horizon",
        )

    actions: list[DPAction] = []
    starts: list[float] = []
    ends: list[float] = []
    k, tslot = 0, release_slot
    while k < len(job.tasks):
        chosen = policy.get((k, tslot))
        if chosen is None:
            raise RuntimeError(f"missing DP policy at state {(k, tslot)}")
        action, next_slot = chosen
        if action == "WAIT":
            actions.append(DPAction(k, tslot * dt, action, None, next_slot * dt))
            tslot = next_slot
            continue
        task = job.tasks[k]
        start = tslot * dt
        end = next_slot * dt
        starts.append(start)
        ends.append(end)
        actions.append(DPAction(k, start, action, task.resource_id, end))
        k += 1
        tslot = next_slot

    completion = ends[-1]
    travel = completion - release
    total_wait = travel - free_run
    # objective includes slot prices; travel is kept separately for diagnostics.
    return PhysicalDPResult(
        feasible=True,
        train_id=train_id,
        objective_travel_min=objective,
        total_wait_min=max(0.0, total_wait),
        completion_min=completion,
        task_starts=starts,
        task_ends=ends,
        actions=actions,
        states_evaluated=visited,
        horizon_min=horizon_min,
    )
