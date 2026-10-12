"""Space-time prism and minimum average speed (Prof. Zhou, Oct 8: "the minimum speed ... prism ... sensitivity
analysis; the MIP solver will not solve larger instances, but small tricks give good solutions").

Every event of train k (entry of leg i) lies in its prism [earliest_ki, earliest_ki + slack_k]. Two sources of slack_k:
  objective budget   slack_k = U - TT0 for a validated timetable value U. Exact: every timetable of value <= U keeps
                     each train's delay <= U - TT0 (all delays >= 0), so the optimum stays inside.
  minimum speed      a train must keep an average speed of at least rho * its free-running speed, origin to
                     destination including waits: elapsed_k <= free_k / rho, i.e. delay_k <= free_k (1 / rho - 1).
                     This is a service rule, not a valid reduction: it removes timetables, and the optimum of the
                     restricted model is >= the true optimum (or the model is infeasible).
rho* of an instance = min_k free_k / elapsed_k in an optimal timetable: for rho <= rho* that timetable survives, so the
restricted optimum equals the true one.

For each rho the cumulative-flow MIP (CPLEX) is built with slack_k = min(U - TT0, floor(free_k (1/rho - 1))) and
reports the free binaries, its value, bound and time. The unrestricted LP bound still bounds the true optimum from
below, so  LB(unrestricted LP) <= z* <= value(restricted MIP)  holds for every rho.

    python -m experiments.prism_speed [--cases ideal|D2|cells10,...] [--rhos none,0.9,...] [--seconds 600]
        ->  results/formulations/prism.json
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "formulations"
CASES = ["ideal|D2|whole", "ideal|D2|cells10", "ideal|D3|whole", "ideal|D3|cells10"]
RHOS = ["none", "0.9", "0.8", "0.7", "0.6", "0.5", "0.4"]


def worker(key: str, rho: str, seconds: float) -> dict:
    from experiments.formulation_compare import model_of
    from experiments.full_comparison import verdict
    from solver.python.mip_cumflow import solve
    from solver.python.mip_formulations import horizon
    from solver.python.siding_kernel import greedy_ub
    from solver.python.siding_model import write_instance
    model = model_of(key)
    work = Path(tempfile.mkdtemp(prefix="prism_"))
    U = greedy_ub(write_instance(model, work / "i.txt"), work / "dp.csv")
    free = [sum(p for _, p, _ in t.path) for t in model.trains]
    budget = U - sum(free)
    if rho == "none":
        slack = None
    else:
        r = float(rho)
        slack = [min(budget, math.floor(f * (1 / r - 1) + 1e-9)) for f in free]
    out = solve(model, horizon(model, U), seconds, 2, solver="cplex", window_ub=U, train_slack=slack)
    shutil.rmtree(work, ignore_errors=True)
    out.update(instance=key, rho=rho, U=U, TT0=sum(free))
    if "schedule" in out:
        sched = out.pop("schedule")
        out["check"] = verdict(model, sched, out["value"])
        ratios = []
        for t, f in zip(model.trains, free):
            legs = sched[t.train_id]
            ratios.append(f / max(1, legs[-1][3] - t.release))
        out["rho_star"] = round(min(ratios), 4)            # the slowest train's average speed / free speed
    return out


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        print(json.dumps(worker(sys.argv[2], sys.argv[3], float(sys.argv[4]))))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--rhos", default=",".join(RHOS))
    ap.add_argument("--seconds", type=float, default=600)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / "prism.json"
    rows = json.loads(f.read_text()) if f.exists() else []
    for key in a.cases.split(","):
        for rho in a.rhos.split(","):
            p = subprocess.run([sys.executable, "-m", "experiments.prism_speed", "--worker", key, rho, str(a.seconds)],
                               cwd=PACKAGE, capture_output=True, text=True)
            lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
            row = json.loads(lines[-1]) if lines else {"instance": key, "rho": rho, "error": p.stderr[-300:]}
            print(json.dumps({k: row.get(k) for k in ("instance", "rho", "free_vars", "value", "proven", "bound",
                                                       "seconds", "rho_star", "status", "check", "error")}), flush=True)
            rows = [x for x in rows if (x.get("instance"), x.get("rho")) != (key, rho)] + [row]
            f.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
