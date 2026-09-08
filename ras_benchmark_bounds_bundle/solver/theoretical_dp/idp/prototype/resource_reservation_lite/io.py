from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from dataclasses import asdict, is_dataclass

from .model import Instance, JobSpec, TaskSpec


def _clean(value):
    if is_dataclass(value):
        return _clean(asdict(value))
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def load_instance(path: str | Path, *, time_step_min: float | None = None, safety_headway_min: float | None = None) -> Instance:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    jobs = []
    for raw_job in payload["jobs"]:
        tasks = tuple(
            TaskSpec(
                resource_id=int(raw_task["resource_id"]),
                duration_min=float(raw_task["duration_min"]),
                name=str(raw_task.get("name", "")),
            )
            for raw_task in raw_job["tasks"]
        )
        jobs.append(
            JobSpec(
                train_id=str(raw_job["train_id"]),
                release_min=float(raw_job.get("release_min", 0.0)),
                tasks=tasks,
                train_type=str(raw_job.get("train_type", "HOM")),
            )
        )
    return Instance(
        name=str(payload.get("name", Path(path).stem)),
        jobs=tuple(jobs),
        time_step_min=float(payload.get("time_step_min", 1.0) if time_step_min is None else time_step_min),
        safety_headway_min=float(payload.get("safety_headway_min", 3.0) if safety_headway_min is None else safety_headway_min),
        metadata=dict(payload.get("metadata", {})),
    )


def write_instance(path: str | Path, instance: Instance) -> None:
    payload = {
        "name": instance.name,
        "time_step_min": instance.time_step_min,
        "safety_headway_min": instance.safety_headway_min,
        "metadata": instance.metadata,
        "jobs": [
            {
                "train_id": job.train_id,
                "release_min": job.release_min,
                "train_type": job.train_type,
                "tasks": [asdict(task) for task in job.tasks],
            }
            for job in instance.jobs
        ],
    }
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_run(output_dir: str | Path, instance: Instance, summary) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    free_run = instance.free_run_total
    delay_lb = None if not math.isfinite(summary.global_lb) else max(0.0, summary.global_lb - free_run)
    delay_ub = None if summary.best_schedule is None else summary.best_schedule.total_delay
    delay_abs_gap = None if delay_lb is None or delay_ub is None else max(0.0, delay_ub - delay_lb)
    delay_rel_gap = None if delay_abs_gap is None or delay_ub <= 1e-12 else delay_abs_gap / delay_ub
    best_conflicts = None
    if summary.best_schedule is not None:
        from .conflicts import all_conflicts
        best_conflicts = len(all_conflicts(instance, summary.best_schedule))
    metrics = {
        "instance": instance.name,
        "train_count": len(instance.jobs),
        "task_count": instance.total_task_count,
        "resource_count": len({instance.resource(key) for key in instance.task_keys()}),
        "time_step_min": instance.time_step_min,
        "requested_headway_min": instance.safety_headway_min,
        "effective_headway_min": instance.effective_headway_min,
        "status": summary.status,
        "root_lb": summary.root_lb,
        "global_lb": summary.global_lb,
        "initial_ub": summary.initial_ub,
        "incumbent_ub": summary.incumbent_ub,
        "absolute_gap": summary.absolute_gap,
        "relative_gap": summary.relative_gap,
        "free_run_total": free_run,
        "delay_lb": delay_lb,
        "total_delay": delay_ub,
        "delay_absolute_gap": delay_abs_gap,
        "delay_relative_gap": delay_rel_gap,
        "best_schedule_conflicts": best_conflicts,
        "nodes_generated": summary.nodes_generated,
        "nodes_expanded": summary.nodes_expanded,
        "nodes_pruned_bound": summary.nodes_pruned_bound,
        "nodes_pruned_cycle": summary.nodes_pruned_cycle,
        "feasible_leaves": summary.feasible_leaves,
        "first_ub_seconds": summary.first_ub_seconds,
        "elapsed_seconds": summary.elapsed_seconds,
    }
    (output / "metrics.json").write_text(json.dumps(_clean(metrics), indent=2, sort_keys=True) + "\n")
    (output / "bb_trace.json").write_text(json.dumps(_clean(summary.trace), indent=2, sort_keys=True) + "\n")
    if summary.trace:
        trace_fields = sorted({key for row in summary.trace for key in row})
        with (output / "bb_trace.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=trace_fields)
            writer.writeheader()
            for row in summary.trace:
                writer.writerow({key: _clean(row.get(key)) for key in trace_fields})
    tree_lines = []
    for node in summary.nodes:
        indent = "  " * node.depth
        branch = "ROOT" if node.branch_decision is None else node.branch_decision.label()
        selected = "" if not node.conflicts else f" | next={node.conflicts[0].label()}"
        tree_lines.append(
            f"{indent}N{node.node_id} parent={node.parent_id} {branch} | "
            f"LB={node.node_lb:g} | status={node.status} | conflicts={len(node.conflicts)} | "
            f"safe={node.safe_tasks}/{instance.total_task_count}@{node.safe_frontier_min:g}{selected}"
        )
    (output / "bb_tree.txt").write_text("\n".join(tree_lines) + "\n", encoding="utf-8")
    (output / "reservations.json").write_text(json.dumps([
        {
            "resource_id": d.resource_id,
            "first_train": d.first.train_id,
            "first_task": d.first.task_index,
            "second_train": d.second.train_id,
            "second_task": d.second.task_index,
            "label": d.label(),
        }
        for d in summary.best_reservations
    ], indent=2, sort_keys=True) + "\n")

    if summary.best_schedule is not None:
        fields = ["train_id", "task_index", "resource_id", "start_min", "end_min"]
        with (output / "schedule.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(summary.best_schedule.rows())
        schedule_payload = {
            "objective_total_travel": summary.best_schedule.objective_total_travel,
            "free_run_total": summary.best_schedule.free_run_total,
            "total_delay": summary.best_schedule.total_delay,
            "completion_by_train": summary.best_schedule.completion_by_train,
            "travel_time_by_train": summary.best_schedule.travel_time_by_train,
            "tasks": summary.best_schedule.rows(),
        }
        (output / "schedule.json").write_text(json.dumps(_clean(schedule_payload), indent=2, sort_keys=True) + "\n")
    return output
