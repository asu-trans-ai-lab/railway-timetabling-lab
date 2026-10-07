"""The ten ANL corridors (and Harrod) with the updated engines: E3 (DP + Lagrangian + B&B, one process) and E1
(block-pair CP-SAT). Reported per corridor: size, whether every train is scheduled, the validator verdict, total
elapsed (OBJ-E), total delay (OBJ-D = OBJ-E - TT0) and runtime. No bounds or gaps are reported (meeting of Oct 4).

    python -m experiments.run_corridors [--seconds 300] [--jobs 3]   ->  results/corridors/<name>/{e1,e3}.csv, summary.json
    python -m experiments.run_corridors --cells 10                   ->  results/corridors/<name>_cells10/...
        --cells m: every single-track link longer than m minutes (for its slowest train) is cut into cells; each
        train's running time on the link is split over the cells (adapters/control_cells.cellify), every train runs
        every cell, and opposing trains never share a single-track link (the stretch check of the validator).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from adapters.control_cells import cellify
from adapters.anl_corridor import EXTRA, L3, corridor_model
from adapters.toy_corridor import physical_check
from solver.python.e3_bb import STRONGEST
from solver.python.siding_kernel import RESULT_BB, build, greedy_ub
from solver.python.siding_model import write_instance
from solver.python.siding_validate import read_schedule, validate

OUT = Path(__file__).resolve().parents[1] / "results" / "corridors"


def verdict(model, sched):
    errors, total, _ = validate(model, sched)
    if any("#" in r.name for r in model.resources):  # the stretch check is for cell models; a pocket stands clear
        errors += physical_check(model, sched)
    missing = [t.train_id for t in model.trains if t.train_id not in sched]
    return ("PASS" if not errors and not missing else "FAIL: " + "; ".join((errors + missing)[:2])), total


def write_csv(sched, path):
    lines = ["train_id,index,resource,entry,exit"]
    for tid, legs in sched.items():
        lines += [f"{tid},{i},{name},{e},{x}" for i, name, e, x in legs]
    path.write_text("\n".join(lines) + "\n")


def e3(model, work, seconds):
    inst = write_instance(model, work / "inst.txt")
    t0 = time.time()
    ub0 = greedy_ub(inst, work / "greedy.csv")
    log = subprocess.run([str(build()), str(inst), "--mode", "bb", "--ub", str(ub0 + 1), *STRONGEST,   # block pairs + meet rows
                          "--time-cap", str(seconds), "--out", str(work / "bb.csv")], capture_output=True, text=True).stdout
    nodes = int(RESULT_BB.search(log).group(5)) if RESULT_BB.search(log) else 0
    src = work / "bb.csv" if (work / "bb.csv").exists() else work / "greedy.csv"
    sched = read_schedule(src)
    v, total = verdict(model, sched)
    shutil.copy(src, work / "e3.csv")
    return {"value": total, "greedy": ub0, "nodes": nodes, "seconds": round(time.time() - t0, 1), "check": v}


def e1(model, work, seconds, workers=2):
    from ortools.sat.python import cp_model
    from solver.python.e1_blockpair_chain import build as cp_build, hint_from, schedule_from
    t0 = time.time()
    hint = read_schedule(work / "e3.csv") if (work / "e3.csv").exists() else None
    m, S, A, info, _ = cp_build(model, None, hint_from(model, hint) if hint else None, symmetry=False,
                                horizon=max(t.release for t in model.trains)
                                + sum(sum(p for _, p, _ in t.path) + model.headway + 1 for t in model.trains))
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = seconds
    s.parameters.num_workers = workers
    st = s.solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"value": None, "status": s.status_name(st), "seconds": round(time.time() - t0, 1), "check": "no schedule"}
    sched = {}
    for tid, i, name, e, x in schedule_from(model, s, S, A, info):
        sched.setdefault(tid, []).append((i, name, e, x))
    v, total = verdict(model, sched)
    write_csv(sched, work / "e1.csv")
    return {"value": total, "status": s.status_name(st), "seconds": round(time.time() - t0, 1), "check": v}


def job(args):
    name, seconds, cells = args
    model, info = corridor_model(name)
    if cells:
        model = cellify(model, cells)
        info = {**info, "cells": cells, "cell_resources": sum("#" in r.name for r in model.resources)}
    work = OUT / (f"{name}_cells{cells}" if cells else name)
    work.mkdir(parents=True, exist_ok=True)
    tt0 = sum(sum(p for _, p, _ in t.path) for t in model.trains)
    r3 = e3(model, work, seconds)
    r1 = e1(model, work, seconds)
    for r in (r3, r1):
        r["delay"] = None if r["value"] is None else r["value"] - tt0
    row = {**info, "TT0": tt0, "resources": len(model.resources), "e3": r3, "e1": r1}
    (work / "row.json").write_text(json.dumps(row, indent=1))
    print(json.dumps(row), flush=True)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--summary", default="summary.json")
    ap.add_argument("--cells", type=int, default=0)
    args = ap.parse_args()
    names = args.only.split(",") if args.only else L3 + EXTRA
    with ProcessPoolExecutor(args.jobs) as ex:
        rows = list(ex.map(job, [(n, args.seconds, args.cells) for n in names]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / args.summary).write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
