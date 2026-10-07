"""One and two bottleneck segments (Prof. Zhou, Oct 4): what does the phase-time DP add, and does E3 still reach the MIP
optimum? A phase pattern belongs to a segment (a single-track block), not to a conflict.

Corridors (west -> east; name, free running minutes, tracks, may stand):
  K = 1   A 10 min double | B 20 min single | C 10 min double
  K = 2   A 10 min double | B 20 min single | S 1 min station with a siding | C 20 min single | D 10 min double
The single-track segments are cut into 10-minute cells (so each is a phase block of >= 2 resources); waits at the origin
and on the siding of S. Trains alternate E / W, released 10 minutes apart; equal speeds, or every third train fast
(running times halved on the line).

For each instance: the MIP optimum; E3 with the phase-time rows on the K segments (--phase --rule phase) and E3 without
them (--rule cell, Lagrangian bound of the capacity rows only): root bound, final value, nodes, seconds.

    python -m experiments.bottleneck_segments [--ns 4,6,8] [--seconds 120]  ->  results/bottleneck_segments/rows.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

from adapters.control_cells import cellify, identify_bottleneck
from adapters.toy_corridor import corridor_model, physical_check
from solver.python.mip_cumflow import solve_isolated
from solver.python.siding_kernel import RESULT_BB, build, greedy_ub
from solver.python.siding_model import write_instance
from solver.python.siding_validate import read_schedule, validate

OUT = Path(__file__).resolve().parents[1] / "results" / "bottleneck_segments"
CORRIDORS = {1: [("A", 10, 2, False), ("B", 20, 1, False), ("C", 10, 2, False)],
             2: [("A", 10, 2, False), ("B", 20, 1, False), ("S", 1, 2, True), ("C", 20, 1, False), ("D", 10, 2, False)]}
TREES = {"phase": ["--phase", "--rule", "phase", "--plunge", "20"], "no_phase": ["--rule", "cell", "--plunge", "20"]}


def instance(K: int, n: int, mixed: bool):
    trains = [(f"{'E' if k % 2 == 0 else 'W'}{k // 2 + 1}{'f' if mixed and k % 3 == 1 else ''}", 1 if k % 2 == 0 else -1,
               k * 10) for k in range(n)]
    m = corridor_model(f"K{K}_N{n}{'_mixed' if mixed else ''}", CORRIDORS[K], trains, 3)
    for t in m.trains:
        if t.train_id.endswith("f"):
            t.path = [(r, p if m.resources[r].siding else max(1, p // 2), s) for r, p, s in t.path]
    return identify_bottleneck(cellify(m, 10), keep=K)


def run_e3(model, tree, seconds):
    work = Path(tempfile.mkdtemp(prefix="bseg_"))
    inst = write_instance(model, work / "inst.txt")
    ub0 = greedy_ub(inst, work / "greedy.csv")
    t0 = time.time()
    log = subprocess.run([str(build()), str(inst), "--mode", "bb", "--ub", str(ub0 + 1), *tree, "--time-cap",
                          str(seconds), "--out", str(work / "bb.csv")], capture_output=True, text=True).stdout
    m = RESULT_BB.search(log)
    status, lb, ub, root, nodes = m.group(1), int(m.group(2)), min(int(m.group(3)), ub0), int(m.group(4)), int(m.group(5))
    sched = read_schedule(work / "bb.csv" if (work / "bb.csv").exists() else work / "greedy.csv")
    errors, total, _ = validate(model, sched)
    errors += physical_check(model, sched)
    return {"status": status, "root_lb": root, "lb": lb, "value": ub, "nodes": nodes,
            "seconds": round(time.time() - t0, 1), "check": "PASS" if not errors and total == ub else "FAIL"}, ub0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", default="4,6,8")
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--mip-seconds", type=float, default=600)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for K in (1, 2):
        for mixed in (False, True):
            for n in [int(x) for x in a.ns.split(",")]:
                model = instance(K, n, mixed)
                row = {"K": K, "N": n, "speeds": "mixed" if mixed else "equal", "blocks": len(model.blocks),
                       "TT0": sum(sum(p for _, p, _ in t.path) for t in model.trains)}
                ub0 = None
                for name, tree in TREES.items():
                    row[name], ub0 = run_e3(model, tree, a.seconds)
                free = [sum(p for _, p, _ in t.path) for t in model.trains]
                T = max(t.release for t in model.trains) + max(free) + (ub0 - sum(free)) + model.headway + 1
                mip = solve_isolated(model, T, seconds=a.mip_seconds, threads=4)
                row["mip"] = {k: mip.get(k) for k in ("value", "bound", "status", "seconds")}
                rows.append(row)
                print(json.dumps(row), flush=True)
                (OUT / "rows.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
