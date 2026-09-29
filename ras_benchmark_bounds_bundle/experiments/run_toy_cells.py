"""The three engines on the toy corridor (10-20-10) at three resource resolutions, and the bottleneck before / after
comparison: E2 / E3 without phase-time, and with phase-time rows on the identified bottleneck (the block of largest
demand sum(run + H)).

    python -m experiments.run_toy_cells e1  --ns 2,3,5,8 [--seconds 300]
    python -m experiments.run_toy_cells e23 --ns 2,3,5,8 [--seconds 300] [--jobs 6]

E3 variants: plain (cell branching), phase (phase-time on the bottleneck + phase-interval branching); both with dives
every 20 nodes. Every timetable passes the resource-chain validator and a physical check on the parent stretches.
Reference (10-min cells, 300 s): E3 N = 5 111,687 nodes open -> 57 nodes proven 340; N = 10 proven 866 in 70 s.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from adapters.control_cells import identify_bottleneck
from adapters.toy_corridor import RESOLUTIONS, physical_check, toy_model
from solver.python.siding_kernel import RESULT_BB, build, greedy_ub
from solver.python.siding_model import write_instance
from solver.python.siding_validate import read_schedule, validate

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "toy_cells"
NS = (2, 3, 5, 8, 10, 15, 20)


def check(model, schedule, value) -> str:
    errors, total, _ = validate(model, schedule)
    errors += physical_check(model, schedule)
    if errors:
        return "FAIL: " + "; ".join(errors[:2])
    return "PASS" if abs(total - value) < 1e-6 else f"FAIL: value {total} != {value}"


def run_e1(n: int, res: str, seconds: float, workers: int = 4) -> dict:
    from ortools.sat.python import cp_model
    from solver.python.e1_blockpair_chain import build as cp_build, hint_from, schedule_from
    model = toy_model(n, cells=RESOLUTIONS[res])
    t0 = time.time()
    horizon = max(t.release for t in model.trains) + sum(sum(p for _, p, _ in t.path) + model.headway + 1
                                                        for t in model.trains)
    ma, Sa, Aa, info_a, _ = cp_build(model, None, None, symmetry=True, horizon=horizon)
    sa = cp_model.CpSolver()
    sa.parameters.max_time_in_seconds = 0.2 * seconds
    sa.parameters.num_workers = workers
    sa.solve(ma)
    inc = {}
    for tid, i, name, e, x in schedule_from(model, sa, Sa, Aa, info_a):
        inc.setdefault(tid, []).append((i, name, e, x))
    _, ub0, _ = validate(model, inc)
    m, S, A, info, pairs = cp_build(model, int(ub0), hint_from(model, inc), symmetry=True)
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = max(1.0, seconds - (time.time() - t0))
    s.parameters.num_workers = workers
    st = s.solve(m)
    sched = {}
    for tid, i, name, e, x in schedule_from(model, s, S, A, info):
        sched.setdefault(tid, []).append((i, name, e, x))
    ub = int(round(s.objective_value))
    lb = ub if st == cp_model.OPTIMAL else int(s.best_objective_bound)
    return {"engine": "E1", "n": n, "res": res, "lb": lb, "ub": ub, "status": s.status_name(st),
            "seconds": round(time.time() - t0, 2), "check": check(model, sched, ub)}


def run_e3(n: int, res: str, variant: str, seconds: float, extra: tuple = ("--plunge", "20")) -> dict:
    model = toy_model(n, cells=RESOLUTIONS[res])
    if variant == "phase":
        model = identify_bottleneck(model)
    work = Path(tempfile.mkdtemp())
    inst = write_instance(model, work / "inst.txt")
    ub0 = greedy_ub(inst, work / "greedy.csv")
    opts = (["--rule", "cell"] if variant == "plain" else ["--phase", "--rule", "interval"]) + list(extra)
    t0 = time.time()
    log = subprocess.run([str(build()), str(inst), "--mode", "bb", "--ub", str(ub0 + 1), *opts, "--time-cap",
                          str(seconds), "--out", str(work / "bb.csv")], capture_output=True, text=True).stdout
    m = RESULT_BB.search(log)
    status, lb, ub, root, nodes = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
    if (work / "bb.csv").exists():
        verdict = check(model, read_schedule(work / "bb.csv"), ub)
    else:                                              # the tree found nothing better: the greedy schedule is the UB
        ub = min(ub, ub0)
        verdict = check(model, read_schedule(work / "greedy.csv"), ub)
    return {"engine": "E3", "variant": variant, "n": n, "res": res, "lb": lb, "ub": ub, "root": root,
            "status": status, "nodes": nodes, "seconds": round(time.time() - t0, 2), "check": verdict}


def run_e2(n: int, res: str, variant: str, seconds: float) -> dict:
    from solver.python.e2_colgen import independent, write_legs
    model = toy_model(n, cells=RESOLUTIONS[res])
    phase = variant == "phase"
    if phase:
        model = identify_bottleneck(model)
    work = Path(tempfile.mkdtemp())
    inst = write_instance(model, work / "inst.txt")
    t0 = time.time()
    r = independent(model, inst, seconds, phase, f"toy_N{n}_{res}", log=None, milp_solver="highs")
    ub, lb = r.get("ub"), r.get("lb")
    verdict = "no schedule"
    if ub is not None:
        verdict = check(model, read_schedule(write_legs(model, r["schedule"], work / "e2.csv")), ub)
    return {"engine": "E2", "variant": variant, "n": n, "res": res, "lb": lb, "ub": ub,
            "status": "PROVEN" if lb is not None and ub is not None and lb >= ub else "OPEN",
            "seconds": round(time.time() - t0, 2), "check": verdict}


def _job(args):
    kind, n, res, variant, seconds = args
    return run_e3(n, res, variant, seconds) if kind == "E3" else run_e2(n, res, variant, seconds)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=["e1", "e23"])
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--ns", default=",".join(map(str, NS)))
    args = ap.parse_args()
    ns = [int(x) for x in args.ns.split(",")]
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    if args.which == "e1":
        for res in RESOLUTIONS:
            for n in ns:
                rows.append(run_e1(n, res, args.seconds))
                print(json.dumps(rows[-1]), flush=True)
    else:
        jobs = [(k, n, res, v, args.seconds) for res in RESOLUTIONS for n in ns for k in ("E3", "E2")
                for v in ("plain", "phase")]
        with ProcessPoolExecutor(args.jobs) as pool:
            for r in pool.map(_job, jobs):
                print(json.dumps(r), flush=True)
                rows.append(r)
    (OUT / f"{args.which}.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
