"""RAS D1-D3, ideal case, at three resource resolutions: whole single-track stretches, 10-minute cells, 5-minute cells.

Cells are cut by running minutes (adapters/control_cells.cellify): a stretch of p minutes becomes m = ceil(p / c) cells
and every train's time on the stretch is split over the m cells. With equal speeds this is the same as cutting by
distance. In every resolution opposing trains never share a single-track stretch (the stretch rows of the MIP and the
physical check); cells let following trains in the same direction be closer.

Per case: E1 (CP-SAT), E3 (DP + Lagrangian + B&B) and the MIP (window from the better engine value), every timetable
validated.

    python -m experiments.ras_ideal_cells [--datasets D1,D2,D3] [--seconds 300] [--mip-seconds 900]
        ->  results/mip_check/ras_ideal_cells.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adapters.ras_ideal import ideal_model
from experiments.mip_check import check, run_e1, run_e3
from solver.python.mip_cumflow import solve_isolated

OUT = Path(__file__).resolve().parents[1] / "results" / "mip_check"
RESOLUTIONS = {"whole": None, "cells10": 10, "cells5": 5}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", default="D1,D2,D3")
    ap.add_argument("--resolutions", default="whole,cells10,cells5")
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--mip-seconds", type=float, default=900)
    ap.add_argument("--solver", default="cplex")
    a = ap.parse_args()
    f = OUT / "ras_ideal_cells.json"
    rows = json.loads(f.read_text()) if f.exists() else []
    for ds in a.datasets.split(","):
        for res in a.resolutions.split(","):
            model = ideal_model(ds, RESOLUTIONS[res])
            tt0 = sum(sum(p for _, p, _ in t.path) for t in model.trains)
            e3, _ = run_e3(model, a.seconds)
            e1 = run_e1(model, a.seconds)
            U = min(v for v in (e1.get("value"), e3["value"]) if v is not None)
            free = [sum(p for _, p, _ in t.path) for t in model.trains]
            T = max(t.release + fr for t, fr in zip(model.trains, free)) + (U - tt0) + model.headway + 1
            mip = solve_isolated(model, T, seconds=a.mip_seconds, threads=6, solver=a.solver, window_ub=U)
            mrow = {x: mip.get(x) for x in ("value", "bound", "status", "proven", "seconds", "vars")}
            if "schedule" in mip:
                mrow["check"] = check(model, mip["schedule"], mip["value"])
            row = {"instance": f"RAS {ds} ideal", "res": res, "resources": len(model.resources), "TT0": tt0,
                   "e1": e1, "e3": e3, "mip": mrow}
            print(json.dumps(row), flush=True)
            rows = [x for x in rows if (x["instance"], x["res"]) != (row["instance"], res)] + [row]
            OUT.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
