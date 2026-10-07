"""Are the B&B branching rules of E3 complete? (Prof. Zhou, Oct 4: the old --branch phase kept release order within a
direction, so it missed timetables where a fast train passes a slow one, and its value was not a bound.)

Each rule of solver/cpp/siding_lr.cpp is run on the same instances and compared with the MIP optimum:
  cell    the earliest over-used cell; one child per train holding it
  phase   --phase --rule phase: which direction holds the busiest block at the busiest minute
  block   the STRONGEST tree of E3 (block-pair start differences, meet rows)
A complete rule never closes the tree above the optimum, and its bound never exceeds the optimum.

Instances: the mixed-speed toy corridor (every third train fast) with releases 15 min apart, and the same corridor
with every train released at minute 0 -- a fast train tied with a slow one in the same direction, the case a FIFO
rule gets wrong.

    python -m experiments.branch_rules [--seconds 60]   ->  results/mip_check/branch_rules.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

from experiments.mip_check import check, mixed_model
from solver.python.e3_bb import STRONGEST
from solver.python.mip_cumflow import solve_isolated
from solver.python.siding_kernel import RESULT_BB, build, greedy_ub
from solver.python.siding_model import write_instance
from solver.python.siding_validate import read_schedule

OUT = Path(__file__).resolve().parents[1] / "results" / "mip_check"
RULES = {"cell": ["--rule", "cell", "--plunge", "20"],
         "phase": ["--phase", "--rule", "phase", "--plunge", "20"],
         "block": STRONGEST}


def run_rule(model, tree, seconds):
    work = Path(tempfile.mkdtemp(prefix="rule_"))
    inst = write_instance(model, work / "inst.txt")
    ub0 = greedy_ub(inst, work / "greedy.csv")
    t0 = time.time()
    log = subprocess.run([str(build()), str(inst), "--mode", "bb", "--ub", str(ub0 + 1), *tree, "--time-cap",
                          str(seconds), "--out", str(work / "bb.csv")], capture_output=True, text=True).stdout
    m = RESULT_BB.search(log)
    status, lb, ub, nodes = m.group(1), int(m.group(2)), min(int(m.group(3)), ub0), int(m.group(5))
    path = work / "bb.csv" if (work / "bb.csv").exists() else work / "greedy.csv"
    return {"status": status, "lb": lb, "value": ub, "nodes": nodes, "seconds": round(time.time() - t0, 1),
            "check": check(model, read_schedule(path), ub)}, ub0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--mip-seconds", type=float, default=600)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    cases = [(f"mixed N={n}", "whole", n, None, 15) for n in (3, 4, 5, 6)] + \
            [(f"mixed N={n}", "cells10", n, 10, 15) for n in (3, 4, 5)] + \
            [(f"tied N={n}", "whole", n, None, 0) for n in (3, 4, 5)] + \
            [(f"tied N={n}", "cells10", n, 10, 0) for n in (3, 4, 5)]
    f = OUT / "branch_rules.json"
    rows = json.loads(f.read_text()) if f.exists() else []
    for name, res, n, cells, interval in cases:
        if a.only and a.only not in f"{name} {res}":
            continue
        model = mixed_model(n, cells, interval=interval)
        out = {"instance": name, "res": res, "fast": [t.train_id for t in model.trains if t.train_id.endswith("f")]}
        ub0 = None
        for rule, tree in RULES.items():
            out[rule], ub0 = run_rule(model, tree, a.seconds)
        free = [sum(p for _, p, _ in t.path) for t in model.trains]
        T = max(t.release for t in model.trains) + max(free) + (ub0 - sum(free)) + model.headway + 1
        mip = solve_isolated(model, T, seconds=a.mip_seconds, threads=8)
        out["mip"] = {k: mip.get(k) for k in ("value", "bound", "status", "seconds")}
        opt = mip.get("value") if "optimal" in str(mip.get("status", "")).lower() else None
        for rule in RULES:
            r = out[rule]
            r["closed_at_optimum"] = None if opt is None or r["status"] != "PROVEN" else r["value"] == opt
            r["bound_le_optimum"] = None if opt is None else r["lb"] <= opt
        rows = [x for x in rows if (x["instance"], x["res"]) != (name, res)] + [out]
        print(json.dumps(out), flush=True)
        OUT.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
