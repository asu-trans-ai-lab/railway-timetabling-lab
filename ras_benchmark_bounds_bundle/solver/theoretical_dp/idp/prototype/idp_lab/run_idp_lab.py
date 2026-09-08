from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path

from .conflict_history import extract_conflict_history
from .global_resource_dp import solve_global_resource_dp
from .lagrangian import run_lagrangian_relaxation
from .metrics import schedule_metrics
from .physical_chain_dp import CalendarBlock, ResourceCalendar, solve_physical_chain_dp
from .search_strategies import compare_first_ub_strategies
from ..resource_reservation_lite.branch_and_bound import ReservationBranchAndBound
from ..resource_reservation_lite.io import load_instance, write_run

ROOT = Path(__file__).resolve().parent
CASE_DIR = ROOT / "cases"


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({k for row in rows for k in row})
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)


def run_case(case_path: Path, out: Path, *, lr_iterations: int = 80) -> dict[str, object]:
    inst = load_instance(case_path)
    out.mkdir(parents=True, exist_ok=True)

    # IDP-0: conditional DP. For multi-train cases, expose A's physical chain
    # against an empty calendar; case 02 also gets an explicit B-after-A calendar test.
    first_train = inst.jobs[0].train_id
    idp0 = solve_physical_chain_dp(inst, first_train)
    idp0_payload = {
        **{k: v for k, v in asdict(idp0).items() if k != "actions"},
        "actions": [asdict(a) for a in idp0.actions],
    }
    (out / "idp0_physical_chain_dp.json").write_text(json.dumps(idp0_payload, indent=2, sort_keys=True) + "\n")

    # IDP-1: explicit global resource-state DP on tiny cases.
    idp1 = solve_global_resource_dp(inst)
    idp1_payload = {
        **{k: v for k, v in asdict(idp1).items() if k != "dispatches"},
        "dispatches": [asdict(d) for d in idp1.dispatches],
    }
    (out / "idp1_global_resource_dp.json").write_text(json.dumps(idp1_payload, indent=2, sort_keys=True) + "\n")

    # IDP-2: exact reservation B&B.
    bb = ReservationBranchAndBound(inst, max_nodes=100_000).solve()
    write_run(out / "idp2_reservation_bb", inst, bb)
    metrics = schedule_metrics(inst, bb.best_schedule) if bb.best_schedule is not None else {}
    (out / "idp2_equity_metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    history = extract_conflict_history(bb)
    (out / "idp2_conflict_history.json").write_text(json.dumps(history, indent=2, sort_keys=True) + "\n")
    _write_csv(out / "idp2_conflict_history.csv", history)

    # IDP-3: first-UB behavior of DFS, best-first and beam.
    strategies = compare_first_ub_strategies(inst)
    (out / "idp3_search_strategies.json").write_text(json.dumps(strategies, indent=2, sort_keys=True) + "\n")
    _write_csv(out / "idp3_search_strategies.csv", strategies)

    # IDP-4: Lagrangian/subgradient + train pricing DP.
    lr = run_lagrangian_relaxation(inst, iterations=lr_iterations)
    lr_rows = [asdict(row) for row in lr.iterations]
    (out / "idp4_lagrangian.json").write_text(json.dumps({
        "best_dual_bound": lr.best_dual_bound,
        "upper_bound": lr.upper_bound,
        "horizon_min": lr.horizon_min,
        "iterations": lr_rows,
    }, indent=2, sort_keys=True) + "\n")
    _write_csv(out / "idp4_lagrangian.csv", lr_rows)

    summary = {
        "instance": inst.name,
        "trains": len(inst.jobs),
        "tasks": inst.total_task_count,
        "resources": len({inst.resource(k) for k in inst.task_keys()}),
        "idp0_first_train_travel": idp0.completion_min - inst.snap_up(inst.job(first_train).release_min) if idp0.feasible else None,
        "idp1_objective": idp1.objective_total_travel if idp1.feasible else None,
        "idp1_states": idp1.states_evaluated,
        "idp2_status": bb.status,
        "idp2_lb": bb.global_lb,
        "idp2_ub": bb.incumbent_ub,
        "idp2_nodes": bb.nodes_generated,
        "idp4_best_dual": lr.best_dual_bound,
        "idp4_ub": lr.upper_bound,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, help="single JSON case; otherwise run all idp cases")
    parser.add_argument("--output", type=Path, default=ROOT / "results")
    parser.add_argument("--lr-iterations", type=int, default=80)
    args = parser.parse_args()
    cases = [args.case] if args.case else sorted(CASE_DIR.glob("*.json"))
    summaries = []
    for case in cases:
        summaries.append(run_case(case, args.output / case.stem, lr_iterations=args.lr_iterations))
    (args.output / "summary.json").write_text(json.dumps(summaries, indent=2, sort_keys=True) + "\n")
    _write_csv(args.output / "summary.csv", summaries)
    print(json.dumps(summaries, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
