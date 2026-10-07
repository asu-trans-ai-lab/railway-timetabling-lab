"""RAS D1-D3 ideal case (equal speeds, all single track; adapters/ras_ideal.py): the cumulative-flow MIP (HiGHS)
against E1 (CP-SAT) and E3 (DP + Lagrangian + B&B) on the same Model; every timetable validated.

    python -m experiments.ras_ideal_check [--datasets D1,D2,D3] [--mip-seconds 1800] [--seconds 600]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adapters.ras_ideal import ideal_model
from experiments.mip_check import check, run_e1, run_e3
from solver.python.mip_cumflow import solve_isolated as mip_solve

OUT = Path(__file__).resolve().parents[1] / "results" / "mip_check"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", default="D1,D2,D3")
    ap.add_argument("--mip-seconds", type=float, default=1800)
    ap.add_argument("--seconds", type=float, default=600)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    old = OUT / "ras_ideal.json"
    names = {f"RAS {d} ideal" for d in args.datasets.split(",")}
    rows = [r for r in (json.loads(old.read_text()) if old.exists() else []) if r["instance"] not in names]
    for ds in args.datasets.split(","):
        model = ideal_model(ds)
        free = [sum(p for _, p, _ in t.path) for t in model.trains]
        e3, ub0 = run_e3(model, args.seconds)
        e1 = run_e1(model, args.seconds)
        best = min(e3["value"], e1["value"])
        T = max(t.release for t in model.trains) + max(free) + (best - sum(free)) + model.headway + 1
        mip = mip_solve(model, T, seconds=args.mip_seconds, threads=8)
        mrow = {k: mip.get(k) for k in ("value", "bound", "status", "seconds", "vars", "rows")}
        mrow["check"] = check(model, mip["schedule"], mip["value"]) if "schedule" in mip else "no solution"
        row = {"instance": f"RAS {ds} ideal", "trains": len(model.trains), "resources": len(model.resources),
               "TT0": sum(free), "horizon": T, "greedy": ub0, "mip": mrow, "e1": e1, "e3": e3}
        rows.append(row)
        print(json.dumps(row), flush=True)
        (OUT / "ras_ideal.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
