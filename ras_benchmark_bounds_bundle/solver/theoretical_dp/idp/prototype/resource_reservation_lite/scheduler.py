from __future__ import annotations

from collections import defaultdict, deque
import math

from .model import Instance, ReservationDecision, Schedule, TaskKey, TaskTiming

SOURCE = TaskKey("__SOURCE__", -1)
EPS = 1e-9


class ReservationModelError(ValueError):
    pass


def _validate_reservations(instance: Instance, reservations: tuple[ReservationDecision, ...]) -> None:
    keys = set(instance.task_keys())
    for decision in reservations:
        if decision.first not in keys or decision.second not in keys:
            raise ReservationModelError(f"reservation references unknown task: {decision}")
        if instance.resource(decision.first) != decision.resource_id:
            raise ReservationModelError(f"first task is not on resource {decision.resource_id}: {decision}")
        if instance.resource(decision.second) != decision.resource_id:
            raise ReservationModelError(f"second task is not on resource {decision.resource_id}: {decision}")


def schedule_from_reservations(
    instance: Instance,
    reservations: tuple[ReservationDecision, ...] = (),
) -> Schedule:
    """Forward DP / longest-path schedule under fixed reservation decisions.

    Each train chain contributes deterministic task-order arcs.  Each reservation
    contributes one cross-train arc requiring the second task to start after the
    first task's processing time plus the protected headway.  If the resulting
    graph is acyclic, a topological longest-path pass gives the componentwise
    earliest schedule; with nonnegative waiting cost this is also the node lower
    bound for the partial resource-order decisions.
    """

    reservations = tuple(reservations)
    _validate_reservations(instance, reservations)
    keys = instance.task_keys()
    nodes = (SOURCE,) + keys
    outgoing: dict[TaskKey, list[tuple[TaskKey, float, str]]] = defaultdict(list)
    indegree = {node: 0 for node in nodes}

    def add_edge(u: TaskKey, v: TaskKey, lag: float, label: str) -> None:
        outgoing[u].append((v, lag, label))
        indegree[v] += 1

    # Job release and internal task order. Waiting is implicit because every
    # inequality is a lower bound on the successor start time.
    for job in instance.jobs:
        first = TaskKey(job.train_id, 0)
        add_edge(SOURCE, first, instance.snap_up(job.release_min), f"release:{job.train_id}")
        for index in range(len(job.tasks) - 1):
            current = TaskKey(job.train_id, index)
            nxt = TaskKey(job.train_id, index + 1)
            add_edge(current, nxt, instance.duration(current), f"job:{job.train_id}")

    # Resource-reservation disjunction choices already fixed at this B&B node.
    for decision in reservations:
        add_edge(
            decision.first,
            decision.second,
            instance.duration(decision.first) + instance.effective_headway_min,
            decision.label(),
        )

    queue = deque(sorted((node for node in nodes if indegree[node] == 0)))
    topo: list[TaskKey] = []
    distance = {node: -math.inf for node in nodes}
    distance[SOURCE] = 0.0

    while queue:
        node = queue.popleft()
        topo.append(node)
        base = distance[node]
        for nxt, lag, _ in sorted(outgoing.get(node, ()), key=lambda row: row[0]):
            if math.isfinite(base):
                distance[nxt] = max(distance[nxt], base + lag)
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                # deterministic topological processing for reproducible traces
                queue.append(nxt)
                queue = deque(sorted(queue))

    if len(topo) != len(nodes):
        return Schedule(
            feasible_precedence=False,
            free_run_total=instance.free_run_total,
            reason="reservation decisions create a cyclic task/resource order",
        )

    timings: dict[TaskKey, TaskTiming] = {}
    for key in keys:
        start = distance[key]
        if not math.isfinite(start):
            return Schedule(
                feasible_precedence=False,
                free_run_total=instance.free_run_total,
                reason=f"task {key.label()} is unreachable from its release state",
            )
        end = start + instance.duration(key)
        timings[key] = TaskTiming(key, instance.resource(key), start, end)

    completion: dict[str, float] = {}
    travel: dict[str, float] = {}
    for job in instance.jobs:
        final = TaskKey(job.train_id, len(job.tasks) - 1)
        completion[job.train_id] = timings[final].end_min
        travel[job.train_id] = completion[job.train_id] - instance.snap_up(job.release_min)

    objective = sum(travel.values())
    free_run = instance.free_run_total
    delay = objective - free_run
    if delay < -1e-7:
        raise AssertionError("earliest schedule is below the free-run baseline")

    return Schedule(
        feasible_precedence=True,
        timings=timings,
        completion_by_train=completion,
        travel_time_by_train=travel,
        objective_total_travel=objective,
        free_run_total=free_run,
        total_delay=max(0.0, delay),
        topological_order=tuple(node for node in topo if node != SOURCE),
    )
