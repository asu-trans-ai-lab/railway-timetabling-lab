"""E3: branch and bound + DP (solver/cpp/siding_lr.cpp --mode bb), in one process or split over parallel processes.

Node bound: the Lagrangian relaxation of the track capacities and the meet rows (an opposing pair keeps one order along
a single-track run), each train priced by its own DP. Branching: block pairs (the exact start-difference decisions E1
uses), then over-used cells. Primal side: greedy insertion, node heuristics, conflict-free nodes, dives.

Strongest configuration (fixed-track D1-D3): STRONGEST, 8 processes, 30 min: D1 2220 proven; D2 / D3 OBJ-E gap
15.88 / 12.18 %. Optional phase-time rows on an identified bottleneck: add "--phase" (the instance must carry blocks).

Parallel: 1. greedy UB. 2. one process runs the tree until `split_nodes` nodes are open and writes them. 3. the open
nodes, sorted by bound, are dealt round-robin to `workers` processes, each running the same tree on its share; they
share the incumbent value through a file. 4. global UB = the least worker UB; global LB = the least worker LB.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from solver.python.siding_kernel import RESULT_BB, build, greedy_ub

STRONGEST = ["--meet", "--rule", "block", "--node-heuristic", "1", "--plunge", "20", "--k-dive", "5"]


def run_single(inst: Path, work: Path, seconds: float, tree: list[str] = STRONGEST) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    greedy = greedy_ub(inst, work / "greedy.csv")
    log = subprocess.run([str(build()), str(inst), "--mode", "bb", "--ub", str(greedy + 1), *tree, "--time-cap",
                          str(seconds), "--out", str(work / "best.csv")], capture_output=True, text=True).stdout
    (work / "bb.log").write_text(log)
    m = RESULT_BB.search(log)
    status, lb, ub, root, nodes = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
    out = work / "best.csv" if (work / "best.csv").exists() else work / "greedy.csv"
    ub = min(ub, greedy)
    return {"greedy": greedy, "root": root, "lb": ub if status == "PROVEN" else min(lb, ub), "ub": ub,
            "status": status, "nodes": nodes, "proven": status == "PROVEN", "schedule": str(out),
            "seconds": round(time.time() - t0, 1)}


def run_parallel(inst: Path, work: Path, seconds: float, workers: int = 8, split_nodes: int = 64,
                 split_time: float = 300, tree: list[str] = STRONGEST, log=print) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    binary = build()
    t0 = time.time()
    greedy = greedy_ub(inst, work / "greedy.csv")
    shared = work / "shared_ub.txt"
    shared.write_text(f"{greedy}\n")
    split = work / "open_nodes.txt"
    s_log = subprocess.run([str(binary), str(inst), "--mode", "bb", "--ub", str(greedy + 1), *tree,
                            "--split", str(split_nodes), str(split), "--time-cap", str(split_time),
                            "--shared-ub", str(shared), "--out", str(work / "best_split.csv")],
                           capture_output=True, text=True).stdout
    (work / "split.log").write_text(s_log)
    m = RESULT_BB.search(s_log)
    status, lb_s, ub_s, root = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    log(f"  split: {status} [{lb_s}, {ub_s}] root {root}, {m.group(5)} nodes, {time.time() - t0:.0f} s")
    results = [{"worker": "split", "status": status, "lb": lb_s, "ub": ub_s, "out": work / "best_split.csv"}]
    if status != "SPLIT":                            # the tree closed (or ran out of time) before splitting
        lb = ub_s if status == "PROVEN" else lb_s
    else:
        nodes = [ln for ln in split.read_text().splitlines() if ln.strip()]
        nodes.sort(key=lambda ln: int(ln.split()[0]))
        shares = [nodes[w::workers] for w in range(workers)]
        left = max(10.0, seconds - (time.time() - t0))
        procs = []
        for w, share in enumerate(shares):
            if not share:
                continue
            f = work / f"share_{w}.txt"
            f.write_text("\n".join(share) + "\n")
            cmd = [str(binary), str(inst), "--mode", "bb", "--ub", str(ub_s + 1), *tree, "--nodes-in", str(f),
                   "--time-cap", str(left), "--shared-ub", str(shared), "--out", str(work / f"best_{w}.csv")]
            procs.append((w, subprocess.Popen(cmd, stdout=open(work / f"worker_{w}.log", "w"), text=True)))
        for w, p in procs:
            p.wait()
            m = RESULT_BB.search((work / f"worker_{w}.log").read_text())
            results.append({"worker": w, "status": m.group(1), "lb": int(m.group(2)), "ub": int(m.group(3)),
                            "nodes": int(m.group(5)), "out": work / f"best_{w}.csv"})
            log(f"  worker {w}: {m.group(1)} [{m.group(2)}, {m.group(3)}], {m.group(5)} nodes")
        lb = min(r["lb"] for r in results[1:])
    ub = min(min(r["ub"] for r in results), greedy)
    lb = min(lb, ub)
    return {"greedy": greedy, "root": root, "lb": lb, "ub": ub, "results": results, "proven": lb == ub,
            "seconds": round(time.time() - t0, 1)}
