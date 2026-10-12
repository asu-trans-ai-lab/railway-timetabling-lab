"""Three integer formulations of the same timetabling problem (solver/python/mip_formulations.py: space-time arc,
cumulative flow, big-M precedence), each solved by HiGHS and CPLEX, on the instances of experiments/full_comparison.py
(Prof. Zhou, Oct 8).

Hypotheses checked:
  H1 equivalence   the three formulations have the same optimum on every instance (and their timetables pass the
                   validator) -- they describe one feasible set and one objective.
  H2 relaxation    the LP relaxation of the arc and cumulative formulations is the same (a change of variables,
                   x -> cumulative sums, plus the same capacity rows) and far above the big-M LP, which only knows
                   TT0 (any z in (0, 1) switches the disjunctions off).
  H3 size vs time  the time-indexed models grow with the time window (U - TT0) and the time resolution, the big-M model
                   with the number of conflicting pairs; which one solves faster depends on that trade-off.

Same protocol for every run: window from the DP value U of the instance (our own sequential DP, no other engine), the
same cap per set, 2 threads, no MIP start. Each run in its own process.

    python -m experiments.formulation_compare [--sets unit,toy,mixed,bseg,corr,ideal] [--jobs 3] [--only key]
        ->  results/formulations/compare.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "formulations"
CAPS = {"unit": 60, "toy": 300, "mixed": 300, "bseg": 300, "corr": 300, "ideal": 900, "real": 1800}
SOLVERS = ["highs", "cplex"]


def instances(sets):
    from experiments.full_comparison import instances as base
    if "unit" in sets:
        yield "unit", "unit|2"
    yield from base([s for s in sets if s != "unit"])


def model_of(key):
    if key.startswith("unit"):
        from solver.python.mip_formulations import unit_model
        return unit_model()
    from experiments.full_comparison import model_of as base
    return base(key)


def worker(form, solver, key, seconds):
    from experiments.full_comparison import verdict
    from solver.python.mip_formulations import solve
    from solver.python.siding_kernel import greedy_ub
    from solver.python.siding_model import write_instance
    model = model_of(key)
    work = Path(tempfile.mkdtemp(prefix="form_"))
    U = 9 if key.startswith("unit") else greedy_ub(write_instance(model, work / "inst.txt"), work / "dp.csv")
    r = solve(model, form, U, solver, seconds, threads=2)
    shutil.rmtree(work, ignore_errors=True)
    if "schedule" in r:
        r["check"] = verdict(model, r.pop("schedule"), r["value"])
    return r


def run(form, solver, key, seconds):
    p = subprocess.run([sys.executable, "-m", "experiments.formulation_compare", "--worker", form, solver, key,
                        str(seconds)], cwd=PACKAGE, capture_output=True, text=True)
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
    return json.loads(lines[-1]) if lines else {"error": (p.stderr.strip().splitlines() or ["?"])[-1][:300]}


def job(args):
    from solver.python.mip_formulations import FORMS
    setname, key, forms, solvers = args
    model = model_of(key)
    row = {"set": setname, "instance": key, "trains": len(model.trains), "resources": len(model.resources),
           "TT0": sum(sum(p for _, p, _ in t.path) for t in model.trains)}
    for form in (forms or FORMS):
        for solver in solvers:
            row[f"{form}_{solver}"] = run(form, solver, key, CAPS[setname])
    print(json.dumps({"instance": key, **{k: (v.get("value"), v.get("proven"), v.get("seconds"))
                                          for k, v in row.items() if isinstance(v, dict)}}), flush=True)
    return row


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        print(json.dumps(worker(sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5]))))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="unit,toy,mixed,bseg,corr,ideal")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--forms", default="")
    ap.add_argument("--solvers", default="highs,cplex")
    ap.add_argument("--out", default="compare.json")
    a = ap.parse_args()
    forms = a.forms.split(",") if a.forms else None
    todo = [(s_, k, forms, a.solvers.split(",")) for s_, k in instances(a.sets.split(","))
            if not a.only or a.only in k]
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / a.out
    rows = json.loads(f.read_text()) if f.exists() else []
    with ProcessPoolExecutor(a.jobs) as ex:
        for row in ex.map(job, todo):
            rows = [x for x in rows if x["instance"] != row["instance"]] + [row]
            f.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
