from __future__ import annotations

import csv
import json
import math
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

from adapters.ras_adapter import dataset_path, load_ras_dataset
from solver.python.branch_and_bound import ConflictBB
from solver.python.dp_interface import BranchRestrictions, DPConfig, NetworkDP
from solver.python.headway_model import HEADWAY_MODEL_SEGMENT_CLEARANCE_V1
from solver.python.lagrangian import evaluate_at_lambda
from validator.schedule_io import write_schedule
from validator.validate_schedule import certified_upper_bound
from visualization.plot_space_time import plot_space_time


ROOT = Path(__file__).resolve().parents[1]
MINI_MANIFEST = ROOT / "data" / "mini_cases" / "manifest.json"


def clean(value):
    if is_dataclass(value):
        return clean(asdict(value))
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [clean(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(clean(value), indent=2, sort_keys=True) + "\n")


def mini_cases() -> list[dict[str, object]]:
    return json.loads(MINI_MANIFEST.read_text())["cases"]


def config_for(dataset: str, trains) -> DPConfig:
    if dataset.startswith("case"):
        manifest = json.loads(MINI_MANIFEST.read_text())
        case = next(item for item in manifest["cases"] if item["name"] == dataset)
        return DPConfig(
            **manifest["common_config"],
            departure_slack=case["departure_slack"],
            max_wait=case["max_wait"],
            safety_headway=manifest["safety_headway"],
            headway_model=manifest["headway_model"],
        )
    horizon = max(
        360.0,
        max(train.entry_min for train in trains) + 360.0,
        max((train.terminal_want or 0.0) for train in trains) + 120.0,
    )
    return DPConfig(
        time_step=1.0,
        horizon=horizon,
        departure_slack=20.0,
        departure_step=5.0,
        max_wait=10.0,
        wait_step=5.0,
        safety_headway=3.0,
        headway_model=HEADWAY_MODEL_SEGMENT_CLEARANCE_V1,
    )


def validation_kwargs(config: DPConfig, arcs, trains, mow) -> dict[str, object]:
    return {
        "arcs": arcs,
        "trains": trains,
        "mow": mow,
        "restrictions": BranchRestrictions(),
        "horizon": config.horizon,
        "safety_headway": config.safety_headway,
        "time_step": config.time_step,
        "wait_step": config.wait_step,
        "origin_wait_cost": config.origin_wait_cost,
        "running_cost": config.running_cost,
        "siding_wait_cost": config.siding_wait_cost,
        "early_cost": config.early_cost,
        "late_cost": config.late_cost,
        "headway_model": config.headway_model,
    }


def write_schedule_csv(path: Path, paths, arcs) -> None:
    fields = [
        "train_id", "leg_index", "arc_id", "from_node", "to_node",
        "direction", "track_type", "entry_min", "exit_min",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for train_id, result in sorted(paths.items()):
            for index, ((arc_id, entry, exit_), ab) in enumerate(zip(result.legs, result.ab_flags)):
                arc = arcs[arc_id]
                writer.writerow({
                    "train_id": train_id,
                    "leg_index": index,
                    "arc_id": arc_id,
                    "from_node": arc.a if ab else arc.b,
                    "to_node": arc.b if ab else arc.a,
                    "direction": "AB" if ab else "BA",
                    "track_type": arc.track_type,
                    "entry_min": entry,
                    "exit_min": exit_,
                })


def run_pipeline(dataset: str, output: Path, *, max_nodes: int, max_depth: int,
                 make_plot: bool = True) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    arcs, trains, mow = load_ras_dataset(dataset)
    config = config_for(dataset, trains)
    dp = NetworkDP(ROOT / "solver" / "cpp", output / "dp_calls")

    started = time.perf_counter()
    lr = evaluate_at_lambda(
        dp,
        node_id="root",
        iteration=0,
        arcs=arcs,
        trains=trains,
        mow=mow,
        lambdas={},
        restrictions=BranchRestrictions(),
        config=config,
    )
    result = ConflictBB(
        dp,
        arcs=arcs,
        trains=trains,
        mow=mow,
        config=config,
        max_nodes=max_nodes,
        max_depth=max_depth,
    ).solve()
    elapsed = time.perf_counter() - started

    candidate_source = "incumbent" if result.best_schedule else "root_relaxation"
    candidate = result.best_schedule or result.nodes[0].paths
    write_schedule(
        output / "schedule.json",
        dataset=dataset,
        candidate_source=candidate_source,
        config=asdict(config),
        paths=candidate,
    )
    write_schedule_csv(output / "schedule.csv", candidate, arcs)
    certified_value, validator_report = certified_upper_bound(
        candidate, **validation_kwargs(config, arcs, trains, mow)
    )
    write_json(output / "validator_report.json", validator_report)
    write_json(output / "bb_trace.json", result.trace)

    metrics = {
        "dataset": dataset_path(dataset).name,
        "train_count": len(trains),
        "arc_count": len(arcs),
        "mow_count": len(mow),
        "solver_status": result.status,
        "root_lb": result.root_lb,
        "global_lb": result.global_lb,
        "incumbent_ub": result.incumbent_ub,
        "certified_output_ub": certified_value,
        "absolute_gap": None if result.incumbent_ub is None else result.incumbent_ub - result.global_lb,
        "nodes_generated": len(result.nodes),
        "root_conflicts": result.nodes[0].validator_conflicts,
        "candidate_source": candidate_source,
        "validator_status": validator_report["status"],
        "validator_conflicts": len(validator_report.get("conflicts") or []),
        "lr_zero_dual": None if lr is None else lr.dual_value,
        "elapsed_seconds": elapsed,
        "headway_model": config.headway_model,
        "safety_headway": config.safety_headway,
        "node_budget": max_nodes,
        "depth_budget": max_depth,
    }
    write_json(output / "metrics.json", metrics)
    if make_plot:
        plot_space_time(
            dataset,
            output / "schedule.csv",
            output / "space_time_diagram.png",
            output / "validator_report.json",
        )
    return metrics
