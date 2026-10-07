"""The corridors with 1, 2, 3 trains per direction (Prof. Zhou, Oct 4: where the MIP can give the optimum, give it and
check the engines against it). Each case keeps the first k eastbound and the first k westbound trains (by release) of
a corridor in data/corridors, with its links, wait points (standing clear, holding nothing) and running times.

Per case: the cumulative-flow MIP (window from the greedy timetable), E1 (CP-SAT) and E3 (DP + Lagrangian + B&B), every
timetable through the validator; reported: the three values and whether E1 / E3 equal the MIP optimum.

    python -m experiments.corridor_small_mip [--ks 1,2,3] [--seconds 120] [--solver cplex]
        ->  results/corridors/small_mip.json
"""
from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import replace
from pathlib import Path

from adapters.anl_corridor import EXTRA, L3, corridor_model
from experiments.run_corridors import e1, e3
from solver.python.mip_cumflow import solve_isolated
from solver.python.siding_model import write_instance
from solver.python.siding_kernel import greedy_ub
from solver.python.siding_validate import validate

OUT = Path(__file__).resolve().parents[1] / "results" / "corridors"


def subset(model, k):
    east = sorted((t for t in model.trains if t.direction > 0), key=lambda t: t.release)[:k]
    west = sorted((t for t in model.trains if t.direction < 0), key=lambda t: t.release)[:k]
    keep = sorted(east + west, key=lambda t: t.release)
    return replace(model, name=f"{model.name}_k{k}", trains=keep)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ks", default="1,2,3")
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--mip-seconds", type=float, default=600)
    ap.add_argument("--solver", default="cplex")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    f = OUT / "small_mip.json"
    rows = json.loads(f.read_text()) if f.exists() else []
    names = a.only.split(",") if a.only else L3 + [n for n in EXTRA if n != "transcon_clovis_fullday"]
    for name in names:
        full, _ = corridor_model(name)
        for k in [int(x) for x in a.ks.split(",")]:
            model = subset(full, k)
            work = Path(tempfile.mkdtemp(prefix=f"small_{name}_{k}_"))
            tt0 = sum(sum(p for _, p, _ in t.path) for t in model.trains)
            r3 = e3(model, work, a.seconds)
            r1 = e1(model, work, a.seconds)
            greedy = greedy_ub(write_instance(model, work / "g.txt"), work / "g.csv")
            free = [sum(p for _, p, _ in t.path) for t in model.trains]
            T = max(t.release + fr for t, fr in zip(model.trains, free)) + (greedy - tt0) + model.headway + 1
            mip = solve_isolated(model, T, seconds=a.mip_seconds, threads=4, solver=a.solver, window_ub=greedy)
            mrow = {x: mip.get(x) for x in ("value", "bound", "status", "proven", "seconds", "vars")}
            if "schedule" in mip:
                errors, total, _ = validate(model, mip["schedule"])
                mrow["check"] = "PASS" if not errors and total == mip["value"] else "FAIL"
            opt = mip.get("value") if (mip.get("proven") or "optimal" in str(mip.get("status", "")).lower()) else None
            row = {"corridor": name, "k": k, "trains": len(model.trains), "TT0": tt0, "greedy": greedy, "mip": mrow,
                   "e1": r1, "e3": r3, "optimum": opt,
                   "e1_equals_optimum": None if opt is None else r1["value"] == opt,
                   "e3_equals_optimum": None if opt is None else r3["value"] == opt}
            print(json.dumps(row), flush=True)
            rows = [x for x in rows if (x["corridor"], x["k"]) != (name, k)] + [row]
            OUT.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
