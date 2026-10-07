"""Three ways to help the cumulative-flow MIP on a full-size case (RAS ideal D2 / D3), short runs to see which is worth
pursuing:
  base     HiGHS, 1-minute grid                          (the reference)
  cplex    the same model solved by CPLEX 22.2            (experiment 1: a commercial solver)
  grid2/5  2- and 5-minute grid: running times, H and releases rounded up, so the coarse timetable scaled back to minutes
           holds every resource at least as long as needed (experiment 2)
  window   HiGHS with UB = the DP engine's timetable: each train's delay <= UB - TT0, objective <= UB (experiment 3)

    python -m experiments.mip_variants [--datasets D2] [--seconds 300]  ->  results/mip_check/variants.json
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path

from adapters.ras_ideal import ideal_model
from solver.python.mip_cumflow import solve_isolated
from solver.python.siding_validate import validate

OUT = Path(__file__).resolve().parents[1] / "results" / "mip_check"


def coarse(model, g):
    up = lambda x: math.ceil(x / g)                       # noqa: E731
    trains = [replace(t, release=up(t.release), path=[(r, up(p), s) for r, p, s in t.path]) for t in model.trains]
    return replace(model, headway=up(model.headway), trains=trains)


def fine_value(model, sched, g):
    """the coarse timetable in minutes: (total elapsed, capacity / order errors)"""
    fine = {tid: [(i, n, e * g, x * g) for i, n, e, x in legs] for tid, legs in sched.items()}
    errors, _, _ = validate(model, fine)
    hard = [e for e in errors if not e.startswith("check3")]           # check3: a train runs slower than its time
    rel = {t.train_id: t.release for t in model.trains}
    return sum(legs[-1][3] - rel[tid] for tid, legs in fine.items()), hard


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", default="D2")
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--variants", default="base,cplex,grid2,grid5,window")
    a = ap.parse_args()
    prev = {r["instance"]: r for r in json.loads((OUT / "ras_ideal.json").read_text())}
    vf = OUT / "variants.json"
    rows = json.loads(vf.read_text()) if vf.exists() else []           # keep earlier runs; a rerun replaces its own row
    for ds in a.datasets.split(","):
        model = ideal_model(ds)
        p = prev[f"RAS {ds} ideal"]
        T, tt0, ub_dp = p["horizon"], p["TT0"], p["e3"]["value"]
        for v in a.variants.split(","):
            if v.startswith("grid"):
                g = int(v[4:])
                r = solve_isolated(coarse(model, g), math.ceil(T / g) + 2, seconds=a.seconds, threads=8)
                if "schedule" in r:
                    r["value_minutes"], hard = fine_value(model, r["schedule"], g)
                    r["check"] = "PASS" if not hard else "FAIL: " + "; ".join(hard[:2])
                    r["coarse_value"], r["coarse_bound"] = r["value"], r["bound"]
                    r["value"] = r["value_minutes"]
                    r["bound"] = None            # a bound of the coarse (rounded-up) model is not a bound in minutes
            else:
                kw = {"solver": "cplex"} if v == "cplex" else ({"window_ub": ub_dp} if v == "window" else {})
                r = solve_isolated(model, T, seconds=a.seconds, threads=8, **kw)
                if "schedule" in r:
                    errors, total, _ = validate(model, r["schedule"])
                    r["check"] = "PASS" if not errors and total == r["value"] else "FAIL: " + "; ".join(errors[:2])
            r.pop("schedule", None)
            row = {"instance": f"RAS {ds} ideal", "variant": v, "TT0": tt0, "horizon": T, "ub_dp": ub_dp,
                   "cpsat": p["e1"]["value"], **r}
            rows = [x for x in rows if (x["instance"], x["variant"]) != (row["instance"], v)] + [row]
            print(json.dumps(row), flush=True)
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / "variants.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
