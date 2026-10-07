"""Prof. Zhou's test for the parallel B&B: fix the global upper bound (no updating rule at all) and check that the serial
search and the parallel search explore the same tree, i.e. the same number of nodes.

With --freeze-ub the kernel prunes with the given --ub only; incumbents are recorded but never tighten the bound or
the time windows, so a node's evaluation depends only on the node (its restrictions and the parent's multipliers).
Dives are switched off (--plunge 0) because a node evaluated inside a dive uses a different iteration budget.
Parallel = the serial split until 64 open nodes, then the open nodes dealt over 8 workers, each run to the end.
Reported per run: wall-clock seconds and total CPU seconds of all kernel processes (user + system, from the children's
resource usage), so the parallel run's total work can be compared with the serial run's.

    python -m experiments.check_fixed_ub [--workers 8]
"""
from __future__ import annotations

import argparse
import json
import re
import resource
import subprocess
import tempfile
import time
from pathlib import Path

from adapters.control_cells import identify_bottleneck
from adapters.toy_corridor import toy_model
from solver.python.e3_bb import read_open, write_share
from solver.python.siding_kernel import build
from solver.python.siding_model import write_instance

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "determinism"
RES = re.compile(r"status (\S+) LB (\d+) UB (\d+) root -?\d+ examined (\d+) created (\d+) pruned (\d+).* found (-?\d+)")
TREE = ["--phase", "--rule", "interval"]
ZSTAR = {5: 340, 8: 635}


def cpu() -> float:
    u = resource.getrusage(resource.RUSAGE_CHILDREN)
    return u.ru_utime + u.ru_stime


def run(cmd):
    out = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, check=True).stdout
    m = RES.search(out)
    return {"status": m.group(1), "lb": int(m.group(2)), "ub": int(m.group(3)), "examined": int(m.group(4)),
            "created": int(m.group(5)), "pruned": int(m.group(6)), "found": int(m.group(7))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    binary = build()
    rows = []
    for n in (5, 8):
        work = Path(tempfile.mkdtemp(prefix=f"fixedub_{n}_"))
        inst = write_instance(identify_bottleneck(toy_model(n, cells=10)), work / "toy.txt")
        ub = ZSTAR[n]                                       # prune LB >= z*: the tree proves nothing below z* exists
        base = [binary, inst, "--mode", "bb", "--ub", ub, *TREE, "--freeze-ub", "--time-cap", 300]
        t0, c0 = time.time(), cpu()
        serial = run(base)
        serial["seconds"] = round(time.time() - t0, 2)
        serial["cpu_seconds"] = round(cpu() - c0, 2)
        t0, c0 = time.time(), cpu()
        split = run(base + ["--split", 64, work / "open.txt"])
        duals: dict = {}
        pool = read_open(work / "open.txt", "s", duals)
        procs = []
        for w in range(min(args.workers, len(pool))):
            share = work / f"share_{w}.txt"
            write_share(share, pool[w::args.workers], duals)
            procs.append(subprocess.Popen([str(c) for c in base + ["--nodes-in", share]], stdout=subprocess.PIPE, text=True))
        workers = []
        for p in procs:
            out, _ = p.communicate()
            m = RES.search(out)
            workers.append({"status": m.group(1), "examined": int(m.group(4)), "created": int(m.group(5)),
                            "found": int(m.group(7))})
        par = {"examined": split["examined"] + sum(w["examined"] for w in workers),
               "created": split["created"] + sum(w["created"] - len(pool[i::args.workers]) for i, w in enumerate(workers)),
               "found": min([split["found"]] + [w["found"] for w in workers]),
               "all_closed": all(w["status"] == "PROVEN" for w in workers),
               "seconds": round(time.time() - t0, 2), "cpu_seconds": round(cpu() - c0, 2),
               "split_examined": split["examined"], "pool": len(pool)}
        row = {"instance": f"toy N={n}, 10-min cells, phase-time", "fixed_ub": ub, "serial": serial, "parallel": par,
               "same_nodes": serial["examined"] == par["examined"]}
        rows.append(row)
        print(json.dumps(row), flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fixed_ub.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
