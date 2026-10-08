"""RAS D1-D3: the same MIP (experiments/export_mip_models.py) solved by HiGHS and by CPLEX, for the comparison with the
engines (Prof. Zhou, Oct 7). Every MIP timetable goes through the resource-chain validator.

Window: the value U of a validated timetable (every train's delay <= U - TT0, objective <= U). With --start, the
validated CP-SAT timetable of the real-speed case (results/fixed_track/D?_a1_b1/best.csv) is the solver's MIP start;
the solver still has to prove that nothing better exists.

    python -m experiments.ras_solver_compare --solver highs --only ideal --seconds 3600
    python -m experiments.ras_solver_compare --solver cplex --only D2_real,D3_real --start --seconds 7200
        ->  results/mip_check/solver_compare.json
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from experiments.export_mip_models import cases, horizon
from solver.python.mip_cumflow import solve_isolated
from solver.python.siding_validate import validate

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "mip_check"


def start_of(path: Path) -> dict:
    """A package schedule (train_id,index,segment,start,end): every leg's entry and the last leg's exit."""
    legs = {}
    for r in csv.DictReader(open(path)):
        legs.setdefault(r["train_id"], []).append((int(r["index"]), int(r["start"]), int(r["end"])))
    out = {}
    for tid, ls in legs.items():
        ls.sort()
        out[tid] = [s for _, s, _ in ls] + [ls[-1][2]]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solver", required=True, choices=["highs", "cplex"])
    ap.add_argument("--only", default="")
    ap.add_argument("--seconds", type=float, default=3600)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--starts", default=str(PACKAGE / "results" / "fixed_track"))
    a = ap.parse_args()
    f = OUT / "solver_compare.json"
    rows = json.loads(f.read_text()) if f.exists() else []
    picks = [x for x in a.only.split(",") if x]
    for name, model, u in cases():
        if picks and not any(p in name for p in picks):
            continue
        kw = {"solver": a.solver, "window_ub": u}
        if a.start and name.endswith("_real"):
            kw["mip_start"] = start_of(Path(a.starts) / f"{name.split('_')[1]}_a1_b1" / "best.csv")
        r = solve_isolated(model, horizon(model, u), seconds=a.seconds, threads=a.threads, **kw)
        row = {"case": name, "solver": a.solver, "start": "mip_start" in kw, "window_U": u, "seconds_cap": a.seconds,
               **{k: r.get(k) for k in ("status", "value", "bound", "proven", "seconds", "vars", "rows")}}
        if "schedule" in r:
            errors, total, _ = validate(model, r["schedule"])
            row["check"] = "PASS" if not errors and total == r["value"] else "FAIL: " + "; ".join(errors[:2])
        print(json.dumps(row), flush=True)
        rows = json.loads(f.read_text()) if f.exists() else []        # the other solver's run may have written meanwhile
        rows = [x for x in rows if (x["case"], x["solver"]) != (name, a.solver)] + [row]
        OUT.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
