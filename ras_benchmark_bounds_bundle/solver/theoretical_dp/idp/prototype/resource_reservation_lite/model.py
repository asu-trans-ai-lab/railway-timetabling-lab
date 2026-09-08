from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable

EPS = 1e-9


@dataclass(frozen=True, order=True)
class TaskKey:
    train_id: str
    task_index: int

    def label(self) -> str:
        return f"{self.train_id}:{self.task_index}"


@dataclass(frozen=True)
class TaskSpec:
    resource_id: int
    duration_min: float
    name: str = ""

    def __post_init__(self) -> None:
        if self.resource_id < 0:
            raise ValueError("resource_id must be nonnegative")
        if not math.isfinite(self.duration_min) or self.duration_min <= 0:
            raise ValueError("duration_min must be finite and positive")


@dataclass(frozen=True)
class JobSpec:
    train_id: str
    release_min: float
    tasks: tuple[TaskSpec, ...]
    train_type: str = "HOM"

    def __post_init__(self) -> None:
        if not self.train_id:
            raise ValueError("train_id is required")
        if not math.isfinite(self.release_min) or self.release_min < 0:
            raise ValueError("release_min must be finite and nonnegative")
        if not self.tasks:
            raise ValueError("each train/job needs at least one task")


@dataclass(frozen=True)
class Instance:
    name: str
    jobs: tuple[JobSpec, ...]
    time_step_min: float = 1.0
    safety_headway_min: float = 3.0
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("instance name is required")
        if not self.jobs:
            raise ValueError("at least one train/job is required")
        if len({job.train_id for job in self.jobs}) != len(self.jobs):
            raise ValueError("train IDs must be unique")
        for value, label, strict in (
            (self.time_step_min, "time_step_min", True),
            (self.safety_headway_min, "safety_headway_min", False),
        ):
            if not math.isfinite(value) or value < 0 or (strict and value <= 0):
                raise ValueError(f"{label} has invalid value {value}")

    @property
    def effective_headway_min(self) -> float:
        if self.safety_headway_min <= EPS:
            return 0.0
        return self.snap_up(self.safety_headway_min)

    def snap_up(self, value: float) -> float:
        if value <= EPS:
            return 0.0
        return math.ceil(value / self.time_step_min - EPS) * self.time_step_min

    def snapped_duration(self, task: TaskSpec) -> float:
        return self.snap_up(task.duration_min)

    def task_keys(self) -> tuple[TaskKey, ...]:
        return tuple(
            TaskKey(job.train_id, index)
            for job in self.jobs
            for index, _ in enumerate(job.tasks)
        )

    def task_spec(self, key: TaskKey) -> TaskSpec:
        job = self.job(key.train_id)
        if key.task_index < 0 or key.task_index >= len(job.tasks):
            raise KeyError(key)
        return job.tasks[key.task_index]

    def job(self, train_id: str) -> JobSpec:
        for job in self.jobs:
            if job.train_id == train_id:
                return job
        raise KeyError(train_id)

    def duration(self, key: TaskKey) -> float:
        return self.snapped_duration(self.task_spec(key))

    def resource(self, key: TaskKey) -> int:
        return self.task_spec(key).resource_id

    @property
    def total_task_count(self) -> int:
        return sum(len(job.tasks) for job in self.jobs)

    @property
    def free_run_total(self) -> float:
        return sum(self.snapped_duration(task) for job in self.jobs for task in job.tasks)


@dataclass(frozen=True, order=True)
class ReservationDecision:
    """One mutually exclusive resource-order decision.

    ``first`` reserves the contested resource-time service before ``second``.
    This is intentionally named a reservation rather than a generic precedence
    variable, even though its computational representation is a difference
    constraint in the forward DP/longest-path subproblem.
    """

    resource_id: int
    first: TaskKey
    second: TaskKey

    def __post_init__(self) -> None:
        if self.first == self.second:
            raise ValueError("reservation requires two distinct tasks")
        if self.first.train_id == self.second.train_id:
            raise ValueError("resource conflicts are only branched across trains/jobs")

    def label(self) -> str:
        return f"R{self.resource_id}: {self.first.label()} BEFORE {self.second.label()}"


@dataclass(frozen=True)
class TaskTiming:
    key: TaskKey
    resource_id: int
    start_min: float
    end_min: float

    def protected_end(self, headway_min: float) -> float:
        return self.end_min + headway_min


@dataclass(frozen=True)
class Conflict:
    resource_id: int
    left: TaskTiming
    right: TaskTiming

    @property
    def time_min(self) -> float:
        return min(self.left.start_min, self.right.start_min)

    def label(self) -> str:
        return (
            f"R{self.resource_id}: {self.left.key.label()} "
            f"[{self.left.start_min:g},{self.left.end_min:g}) vs "
            f"{self.right.key.label()} [{self.right.start_min:g},{self.right.end_min:g})"
        )


@dataclass
class Schedule:
    feasible_precedence: bool
    timings: dict[TaskKey, TaskTiming] = field(default_factory=dict)
    completion_by_train: dict[str, float] = field(default_factory=dict)
    travel_time_by_train: dict[str, float] = field(default_factory=dict)
    objective_total_travel: float = math.inf
    free_run_total: float = math.inf
    total_delay: float = math.inf
    topological_order: tuple[TaskKey, ...] = ()
    reason: str = ""

    def rows(self) -> list[dict[str, object]]:
        return [
            {
                "train_id": timing.key.train_id,
                "task_index": timing.key.task_index,
                "resource_id": timing.resource_id,
                "start_min": timing.start_min,
                "end_min": timing.end_min,
            }
            for timing in sorted(
                self.timings.values(),
                key=lambda x: (x.start_min, x.key.train_id, x.key.task_index),
            )
        ]


def canonical_reservations(values: Iterable[ReservationDecision]) -> tuple[ReservationDecision, ...]:
    return tuple(sorted(set(values)))
