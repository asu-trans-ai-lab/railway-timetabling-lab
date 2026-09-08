from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .branch_and_bound import ReservationBranchAndBound
from .io import write_instance, write_run
from .ras_bridge import build_fixed_chain_from_ras
from .search import beam_search


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the resource-reservation prototype on a frozen route extracted from RAS")
    parser.add_argument("--bundle-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--dataset", default="RAS_data-set_1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--time-step", type=float, default=1.0)
    parser.add_argument("--headway", type=float, default=3.0)
    parser.add_argument("--limit-trains", type=int)
    parser.add_argument("--max-nodes", type=int, default=200)
    parser.add_argument("--beam-width", type=int, default=8)
    args = parser.parse_args()

    root = args.bundle_root.resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    args.output.mkdir(parents=True, exist_ok=True)
    instance = build_fixed_chain_from_ras(
        root,
        args.dataset,
        work_root=args.output / "bridge_work",
        time_step_min=args.time_step,
        safety_headway_min=args.headway,
        limit_trains=args.limit_trains,
    )
    write_instance(args.output / "fixed_chain_instance.json", instance)
    summary = ReservationBranchAndBound(instance, max_nodes=args.max_nodes).solve()
    write_run(args.output, instance, summary)
    beam = beam_search(instance, args.beam_width)
    (args.output / "beam_search.json").write_text(json.dumps({
        "width": beam.width,
        "found_feasible": beam.schedule is not None,
        "objective": None if beam.schedule is None else beam.schedule.objective_total_travel,
        "delay": None if beam.schedule is None else beam.schedule.total_delay,
        "nodes_evaluated": beam.nodes_evaluated,
        "levels": beam.levels,
    }, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "instance": instance.name,
        "trains": len(instance.jobs),
        "tasks": instance.total_task_count,
        "status": summary.status,
        "root_lb": summary.root_lb,
        "initial_ub": summary.initial_ub,
        "best_ub": summary.incumbent_ub,
        "global_lb": summary.global_lb,
        "nodes": summary.nodes_generated,
        "beam_ub": None if beam.schedule is None else beam.schedule.objective_total_travel,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
