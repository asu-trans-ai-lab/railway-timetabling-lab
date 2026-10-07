"""E3: branch and bound + DP (solver/cpp/siding_lr.cpp --mode bb), in one process or split over parallel processes.

Node bound: the Lagrangian relaxation of the track capacities and the meet rows (an opposing pair keeps one order along
a single-track run), each train priced by its own DP. Branching: block pairs (the exact start-difference decisions E1
uses), then over-used cells. Primal side: greedy insertion, node heuristics, conflict-free nodes, dives.

Strongest configuration (fixed-track D1-D3): STRONGEST, 8 processes, 30 min: D1 2220 proven; D2 / D3 OBJ-E gap
15.88 / 12.18 %. Optional phase-time rows on an identified bottleneck: add "--phase" (the instance must carry blocks).

Parallel: 1. greedy UB. 2. one process runs the tree until `split_nodes` nodes are open and writes them. 3. the open
nodes, sorted by bound, are dealt round-robin to `workers` processes, each running the same tree on its share; they
share the incumbent value through a file. 4. Rounds: when a round ends, every unfinished worker writes its open nodes
(--dump-open); they are pooled, nodes the best UB prunes are dropped, and the rest are dealt again over all workers,
which restart with the best global UB (a worker's time windows come from the UB it starts with, so a share that held
the optimum but started from a weak UB could search a huge tree a serial run never sees -- found 2026-10-01 on the toy
corridor, N = 5). No search is lost between rounds. The first round gets min(30 s, 10 % of the budget), each next one
twice the last. 5. Global UB = the least UB found; global LB = min(global UB, least bound of the pooled open nodes).
Every node is a subtree of the root, so the LB is valid after any round, and a run that closes returns the serial
certificate.
"""
from __future__ import annotations

import resource
import subprocess
import time
from pathlib import Path

from solver.python.siding_kernel import RESULT_BB, build, greedy_ub, run_kernel

def child_cpu() -> float:
    """CPU seconds (user + system) of all finished child processes: the kernel runs of this driver."""
    u = resource.getrusage(resource.RUSAGE_CHILDREN)
    return u.ru_utime + u.ru_stime


STRONGEST = ["--meet", "--rule", "block", "--node-heuristic", "1", "--plunge", "20", "--k-dive", "5"]


def run_single(inst: Path, work: Path, seconds: float, tree: list[str] = STRONGEST) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    t0, c0 = time.time(), child_cpu()
    greedy = greedy_ub(inst, work / "greedy.csv")
    log, m = run_kernel([inst, "--mode", "bb", "--ub", greedy + 1, *tree, "--time-cap", seconds,
                         "--out", work / "best.csv"], RESULT_BB)
    (work / "bb.log").write_text(log)
    status, lb, ub, root, nodes = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
    out = work / "best.csv" if (work / "best.csv").exists() else work / "greedy.csv"
    ub = min(ub, greedy)
    return {"greedy": greedy, "root": root, "lb": ub if status == "PROVEN" else min(lb, ub), "ub": ub,
            "status": status, "nodes": nodes, "proven": status == "PROVEN", "schedule": str(out),
            "seconds": round(time.time() - t0, 1), "cpu_seconds": round(child_cpu() - c0, 1)}


def read_open(path: Path, tag: str, duals: dict) -> list[tuple[int, str, str | None]]:
    """An open-node file (--split / --dump-open): node lines "key depth nr restrictions... [w id]" and warm-start lines
    "W id ...". Returns (key, node text without the w tag, dual key or None); dual lines go into `duals` under tag:id."""
    nodes = []
    for ln in path.read_text().splitlines():
        parts = ln.split()
        if not parts:
            continue
        if parts[0] == "W":
            duals[f"{tag}:{parts[1]}"] = " ".join(parts[2:])
            continue
        dual = None
        if len(parts) >= 2 and parts[-2] == "w":
            dual, parts = f"{tag}:{parts[-1]}", parts[:-2]
        nodes.append((int(parts[0]), " ".join(parts), dual))
    return nodes


def write_share(path: Path, nodes: list, duals: dict) -> None:
    """A worker's share: each warm start written once, before the nodes that name it."""
    ids, lines = {}, []
    for _, text, dual in nodes:
        if dual is not None and dual in duals and dual not in ids:
            ids[dual] = len(ids)
            lines.append(f"W {ids[dual]} {duals[dual]}")
    for _, text, dual in nodes:
        lines.append(text + (f" w {ids[dual]}" if dual in ids else ""))
    path.write_text("\n".join(lines) + "\n")


def run_parallel(inst: Path, work: Path, seconds: float, workers: int = 8, split_nodes: int = 64,
                 split_time: float = 300, tree: list[str] = STRONGEST, log=print) -> dict:
    if workers < 1:
        raise ValueError("run_parallel needs workers >= 1 (use run_single for one process)")
    work.mkdir(parents=True, exist_ok=True)
    binary = build()
    t0, c0 = time.time(), child_cpu()
    greedy = greedy_ub(inst, work / "greedy.csv")
    shared = work / "shared_ub.txt"
    shared.write_text(f"{greedy}\n")
    split = work / "open_nodes.txt"
    s_log, m = run_kernel([inst, "--mode", "bb", "--ub", greedy + 1, *tree, "--split", split_nodes, split,
                           "--time-cap", split_time, "--shared-ub", shared, "--out", work / "best_split.csv"], RESULT_BB)
    (work / "split.log").write_text(s_log)
    status, lb_s, ub_s, root = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    log(f"  split: {status} [{lb_s}, {ub_s}] root {root}, {m.group(5)} nodes, {time.time() - t0:.0f} s")
    results = [{"worker": "split", "status": status, "lb": lb_s, "ub": ub_s, "out": work / "best_split.csv"}]
    if status != "SPLIT":                            # the tree closed (or ran out of time) before splitting
        lb = ub_s if status == "PROVEN" else lb_s
    else:
        duals: dict[str, str] = {}
        pool = read_open(split, "s", duals)
        best = min(ub_s, greedy)
        rnd, slice_ = 0, max(5.0, min(30.0, 0.1 * seconds))
        while pool:
            left = seconds - (time.time() - t0)
            if left < 1.0:
                break
            cap = min(slice_, left)
            pool = [x for x in pool if x[0] < best]      # nodes the best UB already prunes are closed
            if not pool:
                break
            pool.sort(key=lambda x: x[0])
            used = {x[2] for x in pool}
            duals = {k: v for k, v in duals.items() if k in used}
            shared.write_text(f"{best}\n")
            procs = []
            for w in range(min(workers, len(pool))):
                share = work / f"share_{w}_r{rnd}.txt"
                write_share(share, pool[w::workers], duals)
                cmd = [str(binary), str(inst), "--mode", "bb", "--ub", str(best + 1), *tree, "--nodes-in", str(share),
                       "--time-cap", str(cap), "--shared-ub", str(shared), "--dump-open",
                       str(work / f"open_{w}_r{rnd}.txt"), "--out", str(work / f"best_{w}_r{rnd}.csv")]
                procs.append((w, subprocess.Popen(cmd, stdout=open(work / f"worker_{w}_r{rnd}.log", "w"), text=True)))
            pool = []
            for w, p in procs:
                logf = work / f"worker_{w}_r{rnd}.log"
                if p.wait() != 0:
                    raise RuntimeError(f"worker {w} failed ({p.returncode}); see {logf}")
                m = RESULT_BB.search(logf.read_text())
                if m is None:
                    raise RuntimeError(f"worker {w} wrote no RESULT line; see {logf}")
                st, lb_w, ub_w = m.group(1), int(m.group(2)), int(m.group(3))
                results.append({"worker": w, "round": rnd, "status": st, "lb": lb_w, "ub": ub_w,
                                "nodes": int(m.group(5)), "out": work / f"best_{w}_r{rnd}.csv"})
                log(f"  round {rnd} worker {w}: {st} [{lb_w}, {ub_w}], {m.group(5)} nodes")
                best = min(best, ub_w)
                dumped = work / f"open_{w}_r{rnd}.txt"
                if st != "PROVEN" and dumped.exists():           # its unfinished nodes go back into the pool
                    pool += read_open(dumped, f"{w}r{rnd}", duals)
                for used in (dumped, work / f"share_{w}_r{rnd}.txt"):   # node files with duals are large: the pool holds them now
                    used.unlink(missing_ok=True)
            rnd += 1
            slice_ *= 2
        pool = [x for x in pool if x[0] < best]
        lb = min([best] + [x[0] for x in pool])
        results.append({"worker": "rounds", "status": "summary", "lb": lb, "ub": best, "rounds": rnd,
                        "open_nodes": len(pool), "out": work / "none"})
    ub = min(min(r["ub"] for r in results), greedy)
    lb = min(lb, ub)
    return {"greedy": greedy, "root": root, "lb": lb, "ub": ub, "results": results, "proven": lb == ub,
            "seconds": round(time.time() - t0, 1), "cpu_seconds": round(child_cpu() - c0, 1)}
