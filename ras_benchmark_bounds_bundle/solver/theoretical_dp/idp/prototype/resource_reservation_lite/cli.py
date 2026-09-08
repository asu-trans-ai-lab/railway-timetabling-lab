from __future__ import annotations

import argparse
import json
from pathlib import Path

from .branch_and_bound import ReservationBranchAndBound
from .io import load_instance, write_run
from .search import beam_search


def main() -> None:
    parser = argparse.ArgumentParser(description="Idealized single-track resource-reservation B&B prototype")
    parser.add_argument("--instance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--time-step", type=float)
    parser.add_argument("--headway", type=float)
    parser.add_argument("--max-nodes", type=int, default=100000)
    parser.add_argument("--no-greedy-ub", action="store_true")
    parser.add_argument("--beam-width", type=int, action="append", default=[])
    args = parser.parse_args()

    instance = load_instance(args.instance, time_step_min=args.time_step, safety_headway_min=args.headway)
    summary = ReservationBranchAndBound(
        instance,
        max_nodes=args.max_nodes,
        use_greedy_ub=not args.no_greedy_ub,
    ).solve()
    write_run(args.output, instance, summary)

    beam_rows = []
    for width in args.beam_width:
        result = beam_search(instance, width)
        beam_rows.append({
            "width": width,
            "found_feasible": result.schedule is not None,
            "objective": None if result.schedule is None else result.schedule.objective_total_travel,
            "delay": None if result.schedule is None else result.schedule.total_delay,
            "nodes_evaluated": result.nodes_evaluated,
            "levels": result.levels,
        })
    if beam_rows:
        (args.output / "beam_search.json").write_text(json.dumps(beam_rows, indent=2, sort_keys=True) + "\n")

    print(json.dumps({
        "instance": instance.name,
        "status": summary.status,
        "root_lb": summary.root_lb,
        "UB": summary.incumbent_ub,
        "gap": summary.absolute_gap,
        "delay": None if summary.best_schedule is None else summary.best_schedule.total_delay,
        "nodes": summary.nodes_generated,
        "output": str(args.output),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
