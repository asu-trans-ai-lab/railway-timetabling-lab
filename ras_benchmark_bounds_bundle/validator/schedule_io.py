from __future__ import annotations

import json
from pathlib import Path

from solver.python.dp_interface import DPResult


def path_to_dict(result: DPResult) -> dict[str, object]:
    return {
        "train_id": result.train_id,
        "feasible": result.feasible,
        "generalized_cost": result.generalized_cost,
        "physical_cost": result.physical_cost,
        "lambda_cost": result.lambda_cost,
        "physical_dual_cost": result.physical_dual_cost,
        "origin_wait_cost": result.origin_wait_cost,
        "running_cost": result.running_cost,
        "siding_wait_cost": result.siding_wait_cost,
        "early_arrival_cost": result.early_arrival_cost,
        "late_arrival_cost": result.late_arrival_cost,
        "departure_min": result.departure_min,
        "arrival_min": result.arrival_min,
        "arc_ids": list(result.arc_ids),
        "node_ids": list(result.node_ids),
        "ab_flags": list(result.ab_flags),
        "track_types": list(result.track_types),
        "waits": list(result.waits),
        "legs": [list(leg) for leg in result.legs],
        "resources": sorted(result.resources),
        "lambda_cells": [list(cell) for cell in sorted(result.lambda_cells)],
        "diagnostics": dict(result.diagnostics),
        "reason": result.reason,
    }


def dict_to_path(row: dict[str, object]) -> DPResult:
    return DPResult(
        train_id=str(row["train_id"]),
        feasible=bool(row["feasible"]),
        generalized_cost=float(row["generalized_cost"]),
        physical_cost=float(row["physical_cost"]),
        lambda_cost=float(row["lambda_cost"]),
        physical_dual_cost=float(row.get("physical_dual_cost", 0.0)),
        origin_wait_cost=float(row["origin_wait_cost"]),
        running_cost=float(row["running_cost"]),
        siding_wait_cost=float(row["siding_wait_cost"]),
        early_arrival_cost=float(row["early_arrival_cost"]),
        late_arrival_cost=float(row["late_arrival_cost"]),
        departure_min=None if row["departure_min"] is None else float(row["departure_min"]),
        arrival_min=None if row["arrival_min"] is None else float(row["arrival_min"]),
        arc_ids=tuple(int(value) for value in row["arc_ids"]),
        node_ids=tuple(int(value) for value in row["node_ids"]),
        ab_flags=tuple(bool(value) for value in row["ab_flags"]),
        track_types=tuple(str(value) for value in row["track_types"]),
        waits=tuple(float(value) for value in row["waits"]),
        legs=tuple((int(leg[0]), float(leg[1]), float(leg[2])) for leg in row["legs"]),
        resources=frozenset(int(value) for value in row["resources"]),
        lambda_cells=frozenset((int(cell[0]), int(cell[1])) for cell in row["lambda_cells"]),
        diagnostics={str(key): str(value) for key, value in row.get("diagnostics", {}).items()},
        reason=str(row.get("reason", "")),
    )


def write_schedule(path: Path, *, dataset: str, candidate_source: str,
                   config: dict[str, object], paths: dict[str, DPResult]) -> None:
    payload = {
        "dataset": dataset,
        "candidate_source": candidate_source,
        "config": config,
        "paths": {train_id: path_to_dict(result) for train_id, result in sorted(paths.items())},
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_schedule(path: Path) -> tuple[dict[str, object], dict[str, DPResult]]:
    payload = json.loads(path.read_text())
    paths = {train_id: dict_to_path(row) for train_id, row in payload["paths"].items()}
    return payload, paths
