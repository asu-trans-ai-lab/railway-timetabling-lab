from __future__ import annotations

import json
import math
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from branch_and_bound import ConflictBB
from dp import DPConfig, NetworkDP, load_dataset
from dp.headway_model import HEADWAY_MODEL_SEGMENT_CLEARANCE_V1


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "RAS123_ROOT_SMOKE.json"


def config(trains) -> DPConfig:
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


def finite_or_none(value):
    return float(value) if value is not None and math.isfinite(value) else None


def run() -> list[dict[str, object]]:
    rows = []
    with TemporaryDirectory(prefix="ras123_root_smoke_") as temporary:
        executable = ROOT / "dp" / "build" / "network_dp"
        for index in (1, 2, 3):
            name = f"RAS_data-set_{index}"
            arcs, trains, mow = load_dataset(ROOT / "dataset" / name)
            dp = NetworkDP(
                ROOT / "dp",
                Path(temporary) / name,
                executable=executable,
            )
            started = time.perf_counter()
            result = ConflictBB(
                dp,
                arcs=arcs,
                trains=trains,
                mow=mow,
                config=config(trains),
                max_nodes=3,
                max_depth=1,
            ).solve()
            root = result.nodes[0]
            row = {
                "dataset": name,
                "train_count": len(trains),
                "arc_count": len(arcs),
                "mow_count": len(mow),
                "dp_returned_all_trains": len(root.paths) == len(trains),
                "all_train_ids_match": set(root.paths) == {train.train_id for train in trains},
                "root_status": root.status,
                "bb_status": result.status,
                "root_lb": finite_or_none(result.root_lb),
                "incumbent_ub": finite_or_none(result.incumbent_ub),
                "root_conflicts": root.validator_conflicts,
                "nodes_generated": len(result.nodes),
                "root_children": len(root.children),
                "validator_structural_errors": root.validator_report.get("errors", []),
                "elapsed_seconds": time.perf_counter() - started,
                "node_budget": 3,
                "depth_budget": 1,
                "headway_model": result.headway_model,
                "safety_headway": result.safety_headway,
            }
            assert row["dp_returned_all_trains"]
            assert row["all_train_ids_match"]
            assert not row["validator_structural_errors"]
            assert row["nodes_generated"] == 3
            assert row["root_children"] == 2
            rows.append(row)
            print(json.dumps(row, sort_keys=True), flush=True)
    OUTPUT.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    return rows


if __name__ == "__main__":
    run()
