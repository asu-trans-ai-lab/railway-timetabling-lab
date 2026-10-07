"""RAS D1-D3 with real speeds (the fixed-track model: routes fixed down to the track, capacity 1 per segment, H = 3,
waits at the origin and at wait points) solved by the cumulative-flow MIP, as an independent check of the CP-SAT optima
2220 / 4127 / 4056 (OBJ-E, total elapsed time).

The MIP reads the same instance file E2 / E3 read (adapters/fixed_track_adapter.to_chain_instance). Its horizon and the
per-train delay window come from the value U of a validated timetable (--window greedy: the greedy insertion,
Algorithm 2; --window best: the best validated timetable on record): every timetable of value <= U has each train's
delay <= U - TT0, so the window keeps every such timetable and the MIP still has to prove on its own that none is
better. The MIP timetable goes through the resource-chain validator and the independent fixed-track C++ validator.

    python -m experiments.ras_fixed_mip [--datasets D1,D2,D3] [--seconds 1200] [--solver cplex]
        ->  results/mip_check/ras_fixed.json
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from adapters.fixed_track_adapter import TT0, to_chain_instance, to_package_schedule, validate as validate_fixed
from solver.python.mip_cumflow import solve_isolated
from solver.python.siding_kernel import greedy_ub
from solver.python.siding_model import read_instance
from solver.python.siding_validate import validate

OUT = Path(__file__).resolve().parents[1] / "results" / "mip_check"
CPSAT = {"D1": 2220, "D2": 4127, "D3": 4056}       # E1 (CP-SAT) optima, results/fixed_track


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", default="D1,D2,D3")
    ap.add_argument("--seconds", type=float, default=1200)
    ap.add_argument("--solver", default="cplex")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--window", default="best", choices=["best", "greedy"])
    a = ap.parse_args()
    f = OUT / "ras_fixed.json"
    rows = json.loads(f.read_text()) if f.exists() else []
    for ds in a.datasets.split(","):
        work = Path(tempfile.mkdtemp(prefix=f"rasfix_{ds}_"))
        inst = to_chain_instance(ds, work / "inst.txt")
        model = read_instance(inst)
        greedy = greedy_ub(inst, work / "greedy.csv")
        free = [sum(p for _, p, _ in t.path) for t in model.trains]
        U = CPSAT[ds] if a.window == "best" else greedy
        slack = U - sum(free)                         # every train's delay in a timetable of value <= U
        T = max(t.release + fr for t, fr in zip(model.trains, free)) + slack + model.headway + 1
        r = solve_isolated(model, T, seconds=a.seconds, threads=a.threads, solver=a.solver, window_ub=U)
        row = {"dataset": ds, "TT0": TT0[ds], "greedy": greedy, "window_from": a.window, "window_U": U, "horizon": T, "cpsat": CPSAT[ds],
               **{k: r.get(k) for k in ("status", "value", "bound", "proven", "seconds", "vars", "rows", "solver")}}
        if "schedule" in r:
            errors, total, _ = validate(model, r["schedule"])
            csv = work / "mip.csv"
            csv.write_text("train_id,index,resource,entry,exit\n" + "".join(
                f"{tid},{i},{res},{e},{x}\n" for tid, legs in r["schedule"].items() for i, res, e, x in legs))
            fixed_value, fixed_msg = validate_fixed(ds, to_package_schedule(csv, ds, work / "mip_pkg.csv"))
            row["check"] = "PASS" if not errors and total == r["value"] else "FAIL: " + "; ".join(errors[:2])
            row["fixed_track_validator"] = fixed_msg if fixed_value is None else f"{fixed_msg} {fixed_value}"
            row["equals_cpsat"] = r["value"] == CPSAT[ds]
        print(json.dumps(row), flush=True)
        rows = [x for x in rows if x["dataset"] != ds] + [row]
        OUT.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
