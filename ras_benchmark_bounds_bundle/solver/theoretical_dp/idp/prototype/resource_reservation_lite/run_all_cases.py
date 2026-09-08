from __future__ import annotations

import argparse
import json
from pathlib import Path

from .branch_and_bound import ReservationBranchAndBound
from .io import load_instance, write_run
from .search import beam_search

ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT.parent / "results" / "idealized")
    parser.add_argument("--time-step", type=float, default=None)
    parser.add_argument("--headway", type=float, default=None)
    parser.add_argument("--max-nodes", type=int, default=100000)
    args = parser.parse_args()
    rows = []
    for path in sorted((ROOT / "cases").glob("case*.json")):
        instance = load_instance(path, time_step_min=args.time_step, safety_headway_min=args.headway)
        summary = ReservationBranchAndBound(instance, max_nodes=args.max_nodes).solve()
        out = args.output / instance.name
        write_run(out, instance, summary)
        beam8 = beam_search(instance, 8)
        row = {
            "instance": instance.name,
            "status": summary.status,
            "trains": len(instance.jobs),
            "tasks": instance.total_task_count,
            "root_lb": summary.root_lb,
            "UB": summary.incumbent_ub,
            "delay": summary.best_schedule.total_delay if summary.best_schedule else None,
            "nodes": summary.nodes_generated,
            "beam8_UB": beam8.schedule.objective_total_travel if beam8.schedule else None,
            "beam8_nodes": beam8.nodes_evaluated,
        }
        rows.append(row)
        print(json.dumps(row, sort_keys=True))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
