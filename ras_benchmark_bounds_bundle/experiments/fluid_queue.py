"""Hypothetical congestion cases for the Fluid Queue layer (Prof. Zhou, Oct 8: "create hypothetical cases ... three
hours, one hour is a congestion ... this will show the benefit for really larger scale").

Corridor: A 10 min (no capacity limit) | B 20 min, s tracks | C 10 min (no limit); headway H = 3, so B serves
mu = s / 23 trains per minute. Trains alternate east / west; releases follow a 3-hour profile: 0.8 mu in hour 1,
1.5 mu in hour 2 (the congestion hour), 0.8 mu in hour 3. Scale s = 1, 2, 4, ... multiplies the trains and the
capacity together, so the congestion pattern is the same at every scale.

Fluid Queue (the framework's layer: Q_after = max(0, Q_before + A - Cq)): the release profile as an inflow rate,
service at rate mu; per minute (the deterministic fluid queue) and per 30-minute interval (Cq = 30 mu, the framework's
aggregation). Total waiting = the area under Q. Microseconds, no optimisation.

Compared with the exact optimum (cumulative-flow MIP, CPLEX, window from the DP value), the DP timetable and the
Lagrangian bound (kernel --mode lb), at every scale: the fluid estimate's error, and the time each method needs.

    python -m experiments.fluid_queue [--scales 1,2,4,8,16] [--seconds 600]  ->  results/formulations/fluid.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "formulations"
H, PB = 3, 20


def releases(s: int) -> list[int]:
    mu = s / (PB + H)
    rate = lambda t: (1.5 if 60 <= t < 120 else 0.8) * mu       # noqa: E731
    out, cum, k = [], 0.0, 0
    for t in range(180):
        cum += rate(t)
        while cum >= k + 0.5:
            out.append(t)
            k += 1
    return out


def model(s: int):
    from adapters.toy_corridor import corridor_model
    corridor = [("A", 10, 999, False), ("B", PB, s, False), ("C", 10, 999, False)]
    trains = [(f"{'E' if k % 2 == 0 else 'W'}{k + 1}", 1 if k % 2 == 0 else -1, r) for k, r in enumerate(releases(s))]
    return corridor_model(f"fluid_s{s}", corridor, trains, H)


def fluid(s: int, step: int = 1) -> tuple[float, float]:
    """Total waiting (train-minutes) of a fluid queue at B, and the seconds it took. Inflow: the release profile as a
    rate (0.8 / 1.5 / 0.8 mu, shifted by the 10 minutes on A), per interval of `step` minutes; service mu per minute.
    step = 1: the deterministic fluid queue; step = 30: the framework's interval form
    Q_n = max(0, Q_{n-1} + A_n - Cq),  Cq = 30 mu, waiting = the trapezoid area of Q."""
    t0 = time.time()
    mu = s / (PB + H)
    inflow = [0.0] * 600
    for t in range(180):
        inflow[t + 10] = (1.5 if 60 <= t < 120 else 0.8) * mu
    Q, total = 0.0, 0.0
    for n in range(0, 600, step):
        A = sum(inflow[n:n + step])
        q = max(0.0, Q + A - mu * step)
        total += (Q + q) / 2 * step
        Q = q
    return total, time.time() - t0


def run(s: int, seconds: float) -> dict:
    from solver.python.mip_cumflow import solve_isolated
    from solver.python.mip_formulations import horizon
    from solver.python.siding_kernel import build, greedy_ub
    from solver.python.siding_model import write_instance
    from solver.python.siding_validate import validate
    m = model(s)
    work = Path(tempfile.mkdtemp(prefix="fluid_"))
    inst = write_instance(m, work / "inst.txt")
    TT0 = sum(sum(p for _, p, _ in t.path) for t in m.trains)
    t0 = time.time()
    U = greedy_ub(inst, work / "dp.csv")
    dp_s = time.time() - t0
    fq, fq_s = fluid(s)
    fq30, _ = fluid(s, 30)
    t0 = time.time()
    lb_out = subprocess.run([str(build()), str(inst), "--mode", "lb", "--ub", str(U + 1), "--iters", "3000"],
                            capture_output=True, text=True).stdout
    lm = re.search(r"RESULT mode lb .* L (\S+) LB (\d+)", lb_out)
    lr_s = time.time() - t0
    mip = solve_isolated(m, horizon(m, U), seconds=seconds, threads=2, solver="cplex", window_ub=U)
    shutil.rmtree(work, ignore_errors=True)
    row = {"scale": s, "trains": len(m.trains), "TT0": TT0, "fluid_wait": round(fq, 1), "fluid30_wait": round(fq30, 1),
           "fluid_seconds": round(fq_s, 4),
           "dp_value": U, "dp_wait": U - TT0, "dp_seconds": round(dp_s, 2),
           "lr_L": float(lm.group(1)) if lm else None, "lr_seconds": round(lr_s, 2),
           "mip_value": mip.get("value"), "mip_proven": mip.get("proven"), "mip_bound": mip.get("bound"),
           "mip_seconds": mip.get("seconds"), "mip_vars": mip.get("vars"), "mip_status": mip.get("status")}
    if mip.get("value") is not None:
        row["opt_wait"] = mip["value"] - TT0
        errs, total, _ = validate(m, mip["schedule"])
        row["mip_check"] = "PASS" if not errs and total == mip["value"] else f"FAIL {errs[:1]}"
        row["fluid_error_pct"] = round(100 * (fq - row["opt_wait"]) / max(1, row["opt_wait"]), 1)
        row["fluid30_error_pct"] = round(100 * (fq30 - row["opt_wait"]) / max(1, row["opt_wait"]), 1)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", default="1,2,4,8,16")
    ap.add_argument("--seconds", type=float, default=600)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in [int(x) for x in a.scales.split(",")]:
        row = run(s, a.seconds)
        print(json.dumps(row), flush=True)
        rows.append(row)
        (OUT / "fluid.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
