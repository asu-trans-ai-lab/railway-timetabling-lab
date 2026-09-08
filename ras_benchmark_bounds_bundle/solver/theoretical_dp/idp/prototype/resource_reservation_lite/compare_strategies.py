from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .branch_and_bound import ReservationBranchAndBound
from .constructive import greedy_conflict_upper_bound, priority_upper_bound
from .io import load_instance
from .search import beam_search

ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare UB construction and exact best-first B&B")
    parser.add_argument("--instance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--beam", type=int, nargs="*", default=[1, 2, 4, 8])
    parser.add_argument("--max-nodes", type=int, default=100000)
    args = parser.parse_args()

    instance = load_instance(args.instance)
    rows = []
    priority = priority_upper_bound(instance)
    rows.append({
        "method": "priority_UB",
        "feasible": True,
        "objective": priority.schedule.objective_total_travel,
        "delay": priority.schedule.total_delay,
        "nodes": 0,
        "status": "FEASIBLE",
    })
    greedy = greedy_conflict_upper_bound(instance)
    rows.append({
        "method": "greedy_earliest_conflict",
        "feasible": greedy is not None,
        "objective": None if greedy is None else greedy.schedule.objective_total_travel,
        "delay": None if greedy is None else greedy.schedule.total_delay,
        "nodes": None,
        "status": "FEASIBLE" if greedy is not None else "FAILED",
    })
    for width in args.beam:
        beam = beam_search(instance, width)
        rows.append({
            "method": f"beam_{width}",
            "feasible": beam.schedule is not None,
            "objective": None if beam.schedule is None else beam.schedule.objective_total_travel,
            "delay": None if beam.schedule is None else beam.schedule.total_delay,
            "nodes": beam.nodes_evaluated,
            "status": "FEASIBLE" if beam.schedule is not None else "FAILED",
        })
    exact = ReservationBranchAndBound(instance, max_nodes=args.max_nodes).solve()
    rows.append({
        "method": "best_first_exact_BB",
        "feasible": exact.best_schedule is not None,
        "objective": exact.incumbent_ub,
        "delay": None if exact.best_schedule is None else exact.best_schedule.total_delay,
        "nodes": exact.nodes_generated,
        "status": exact.status,
        "global_lb": exact.global_lb,
        "gap": exact.absolute_gap,
    })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    csv_path = args.output.with_suffix(".csv")
    fields = sorted({key for row in rows for key in row})
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(rows, sort_keys=True))


if __name__ == "__main__":
    main()
