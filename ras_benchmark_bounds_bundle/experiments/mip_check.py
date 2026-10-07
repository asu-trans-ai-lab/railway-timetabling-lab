"""MIP check (meeting of Oct 4): for every small instance, the cumulative-flow MIP (HiGHS) gives the reference
optimum, and E1 (block-pair CP-SAT) and E3 (DP + Lagrangian + B&B) are compared with it on the same Model. Every
timetable goes through the validator and the physical check.

Instances: the toy 10-20-10 corridor with per-train speeds -- every third train is fast (running times halved on
R12 / R23 / R34) -- at whole-segment resolution and with 10-minute cells (cells capped by the shortest running time).

    python -m experiments.mip_check [--ns 3,4,5,6] [--mip-seconds 300] [--seconds 120]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from adapters.control_cells import cellify
from adapters.toy_corridor import C0, corridor_model, physical_check
from solver.python.mip_cumflow import solve_isolated as mip_solve
from solver.python.siding_kernel import RESULT_BB, build, greedy_ub
from solver.python.siding_model import write_instance
from solver.python.siding_validate import read_schedule, validate

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "mip_check"


def mixed_model(n: int, cells: int | None, interval: int = 15, h: int = 10):
    trains = [(f"{'E' if k % 2 == 0 else 'W'}{k // 2 + 1}{'f' if k % 3 == 1 else ''}", 1 if k % 2 == 0 else -1,
               k * interval) for k in range(n)]
    m = corridor_model(f"mixed_N{n}", C0, trains, h)
    for t in m.trains:
        if t.train_id.endswith("f"):
            t.path = [(r, p if m.resources[r].siding else max(1, p // 2), s) for r, p, s in t.path]
    return cellify(m, cells)


def check(model, sched, value):
    errors, total, _ = validate(model, sched)
    errors += physical_check(model, sched)
    if errors:
        return "FAIL: " + "; ".join(errors[:2])
    return "PASS" if total == value else f"FAIL: value {total} != {value}"


def run_e3(model, seconds):
    work = Path(tempfile.mkdtemp())
    inst = write_instance(model, work / "inst.txt")
    ub0 = greedy_ub(inst, work / "greedy.csv")
    t0 = time.time()
    log = subprocess.run([str(build()), str(inst), "--mode", "bb", "--ub", str(ub0 + 1), "--rule", "cell", "--plunge", "20",
                          "--time-cap", str(seconds), "--out", str(work / "bb.csv")], capture_output=True, text=True).stdout
    m = RESULT_BB.search(log)
    ub, nodes = int(m.group(3)), int(m.group(5))
    path = work / "bb.csv" if (work / "bb.csv").exists() else work / "greedy.csv"
    ub = min(ub, ub0)
    return {"value": ub, "nodes": nodes, "seconds": round(time.time() - t0, 2), "lagrangian_lb": int(m.group(2)),
            "check": check(model, read_schedule(path), ub)}, ub0


def run_e1(model, seconds, workers=4):
    from ortools.sat.python import cp_model
    from solver.python.e1_blockpair_chain import build as cp_build, hint_from, schedule_from
    t0 = time.time()
    horizon = max(t.release for t in model.trains) + sum(sum(p for _, p, _ in t.path) + model.headway + 1
                                                        for t in model.trains)
    ma, Sa, Aa, info_a, _ = cp_build(model, None, None, symmetry=False, horizon=horizon)
    sa = cp_model.CpSolver()
    sa.parameters.max_time_in_seconds = 0.2 * seconds
    sa.parameters.num_workers = workers
    sa.solve(ma)
    inc = {}
    for tid, i, name, e, x in schedule_from(model, sa, Sa, Aa, info_a):
        inc.setdefault(tid, []).append((i, name, e, x))
    _, ub0, _ = validate(model, inc)
    m, S, A, info, _ = cp_build(model, int(ub0), hint_from(model, inc), symmetry=False)
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = max(1.0, seconds - (time.time() - t0))
    s.parameters.num_workers = workers
    st = s.solve(m)
    sched = {}
    for tid, i, name, e, x in schedule_from(model, s, S, A, info):
        sched.setdefault(tid, []).append((i, name, e, x))
    v = int(round(s.objective_value))
    return {"value": v, "status": s.status_name(st), "seconds": round(time.time() - t0, 2), "check": check(model, sched, v)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", default="3,4,5,6")
    ap.add_argument("--mip-seconds", type=float, default=300)
    ap.add_argument("--seconds", type=float, default=120)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for n in [int(x) for x in args.ns.split(",")]:
        for res, cells in (("whole", None), ("cells10", 10)):
            model = mixed_model(n, cells)
            e3, ub0 = run_e3(model, args.seconds)
            free = [sum(p for _, p, _ in t.path) for t in model.trains]
            T = max(t.release for t in model.trains) + max(free) + (ub0 - sum(free)) + model.headway + 1
            mip = mip_solve(model, T, seconds=args.mip_seconds)
            mip_row = {k: mip.get(k) for k in ("value", "bound", "status", "seconds", "vars", "rows")}
            mip_row["check"] = check(model, mip["schedule"], mip["value"]) if "schedule" in mip else "no solution"
            e1 = run_e1(model, args.seconds)
            row = {"instance": f"mixed N={n}", "res": res, "fast": [t.train_id for t in model.trains if t.train_id.endswith("f")],
                   "TT0": sum(free), "horizon": T, "mip": mip_row, "e1": e1, "e3": e3,
                   "e1_matches": e1["value"] == mip_row["value"], "e3_matches": e3["value"] == mip_row["value"]}
            rows.append(row)
            print(json.dumps(row), flush=True)
    (OUT / "mixed_speeds.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
