"""The MIP (HiGHS, CPLEX) against every algorithm of the three engines, instance by instance (Prof. Zhou, Oct 8):

  dp        Algorithm 2: sequential insertion by the train DP (kernel --mode ub, no search)        -> upper bound
  lr        Algorithm 3: DP + Lagrangian relaxation of the capacity rows (kernel --mode lb)        -> lower bound
  lr_phase  Algorithm 3 + 4: with the phase-time rows of the model's single-track blocks           -> lower bound
  bb        Algorithm 5: E3 branch and bound, strongest tree (block pairs + meet rows)             -> UB, LB
  bb_phase  Algorithm 5 with phase-time rows and phase branching (models with blocks)             -> UB, LB
  e2        E2: LP column generation priced by the train DP + MILP over the generated trips        -> UB, LB
  e1        E1: block-pair CP-SAT                                                                  -> UB (certificate)
  highs / cplex  the cumulative-flow MIP (solver/python/mip_cumflow.py), window from the dp value  -> optimum

Every method runs in its own process (OR-Tools and highspy cannot share one). Every timetable goes through the
resource-chain validator (and the stretch check on cell models). For the RAS sets the HiGHS / CPLEX rows of
results/mip_check/solver_compare.json are reused (window from a validated timetable value, see that file).

    python -m experiments.full_comparison [--sets toy,mixed,bseg,corr,ideal,real] [--jobs 3]
        ->  results/comparison/full.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "comparison"
CORRIDORS = ["overland", "panhandle", "transcon_clovis", "prb_joint", "pocahontas", "hiline", "cn_icmain", "gulf",
             "moffat", "cpkc_midcon", "bnsf_columbia", "csx_bo", "csx_waterlevel", "ns_chicago", "ns_crescent",
             "ns_heartland", "up_sunset", "up_texas"]
CAPS = {"toy": 120, "mixed": 120, "bseg": 120, "corr": 120, "ideal": 300, "real": 300}
LB_RE = re.compile(r"RESULT mode lb .* L (\S+) LB (\d+) UB (\d+)")


def instances(sets):
    if "toy" in sets:
        for n, res in ((3, 10), (5, 10), (5, 5), (5, 0)):
            yield "toy", f"toy|{n}|{res}"
    if "mixed" in sets:
        for n in (3, 4, 5, 6):
            yield "mixed", f"mixed|{n}|0|15"
        for n in (3, 4, 5):
            yield "mixed", f"mixed|{n}|10|15"
        for n in (3, 4, 5):
            yield "mixed", f"mixed|{n}|0|0"
        for n in (3, 4, 5):
            yield "mixed", f"mixed|{n}|10|0"
    if "bseg" in sets:
        for K in (1, 2):
            for mixed in (0, 1):
                for n in (4, 6, 8):
                    yield "bseg", f"bseg|{K}|{n}|{mixed}"
    if "corr" in sets:
        for c in CORRIDORS:
            for k in (1, 2, 3):
                yield "corr", f"corr|{c}|{k}"
    if "ideal" in sets:
        for d in ("D1", "D2", "D3"):
            for res in ("whole", "cells10", "cells5"):
                yield "ideal", f"ideal|{d}|{res}"
    if "real" in sets:
        for d in ("D1", "D2", "D3"):
            yield "real", f"real|{d}"


def model_of(key: str):
    kind, *a = key.split("|")
    if kind == "toy":
        from adapters.toy_corridor import toy_model
        return toy_model(int(a[0]), cells=int(a[1]) or None)
    if kind == "mixed":
        from experiments.mip_check import mixed_model
        return mixed_model(int(a[0]), int(a[1]) or None, interval=int(a[2]))
    if kind == "bseg":
        from experiments.bottleneck_segments import instance
        return instance(int(a[0]), int(a[1]), bool(int(a[2])))
    if kind == "corr":
        from adapters.anl_corridor import corridor_model
        from experiments.corridor_small_mip import subset
        return subset(corridor_model(a[0])[0], int(a[1]))
    if kind == "ideal":
        from adapters.ras_ideal import ideal_model
        return ideal_model(a[0], {"whole": None, "cells10": 10, "cells5": 5}[a[1]])
    if kind == "real":
        from adapters.fixed_track_adapter import to_chain_instance
        from solver.python.siding_model import read_instance
        work = Path(tempfile.mkdtemp(prefix="cmp_real_"))
        return read_instance(to_chain_instance(a[0], work / "inst.txt"))
    raise ValueError(key)


def verdict(model, sched, value):
    from adapters.toy_corridor import physical_check
    from solver.python.siding_validate import validate
    errors, total, _ = validate(model, sched)
    if any("#" in r.name for r in model.resources):
        errors += physical_check(model, sched)
    return "PASS" if not errors and total == value else "FAIL: " + "; ".join(errors[:2])


def worker(method: str, key: str, seconds: float) -> dict:
    """One method on one instance, in this (fresh) process."""
    from solver.python.siding_kernel import build, greedy_ub
    from solver.python.siding_model import write_instance
    from solver.python.siding_validate import read_schedule
    model = model_of(key)
    work = Path(tempfile.mkdtemp(prefix="cmp_"))
    inst = write_instance(model, work / "inst.txt")
    t0 = time.time()
    if method == "dp":
        v = greedy_ub(inst, work / "dp.csv")
        return {"ub": v, "seconds": round(time.time() - t0, 2), "check": verdict(model, read_schedule(work / "dp.csv"), v)}
    greedy = greedy_ub(inst, work / "greedy.csv")
    if method in ("lr", "lr_phase"):
        if method == "lr_phase" and not model.blocks:
            return {"skipped": "no single-track block"}
        cmd = [str(build()), str(inst), "--mode", "lb", "--ub", str(greedy + 1), "--iters", "3000",
               "--lb-time-cap", str(seconds)] + (["--phase"] if method == "lr_phase" else [])
        m = LB_RE.search(subprocess.run(cmd, capture_output=True, text=True).stdout)
        return {"L": float(m.group(1)), "lb": min(math.ceil(float(m.group(1)) - 1e-6), greedy),
                "seconds": round(time.time() - t0, 2)}
    if method in ("bb", "bb_phase"):
        from solver.python.e3_bb import STRONGEST, run_single
        if method == "bb_phase" and not model.blocks:
            return {"skipped": "no single-track block"}
        tree = STRONGEST if method == "bb" else ["--phase", "--rule", "phase", "--plunge", "20"]
        r = run_single(inst, work / method, seconds, tree)
        return {"ub": r["ub"], "lb": r["lb"], "root": r["root"], "status": r["status"], "nodes": r["nodes"],
                "seconds": r["seconds"], "check": verdict(model, read_schedule(Path(r["schedule"])), r["ub"])}
    if method == "e2":
        from solver.python.e2_colgen import independent, write_legs
        r = independent(model, inst, seconds, False, key, log=lambda *_: None, milp_solver="highs")
        ub, lb = r.get("ub"), r.get("lb")
        chk = "no schedule" if ub is None else verdict(model, read_schedule(write_legs(model, r["schedule"],
                                                                                       work / "e2.csv")), ub)
        return {"ub": ub, "lb": lb, "lp": r.get("stage2_lp", r.get("stage1_lp")), "seconds": round(time.time() - t0, 2),
                "check": chk}
    if method == "e1":
        from experiments.mip_check import run_e1
        r = run_e1(model, seconds)
        return {"ub": r["value"], "status": r["status"], "seconds": r["seconds"], "check": r["check"]}
    if method in ("highs", "cplex"):
        from solver.python.mip_cumflow import solve_isolated
        free = [sum(p for _, p, _ in t.path) for t in model.trains]
        T = max(t.release + f for t, f in zip(model.trains, free)) + (greedy - sum(free)) + model.headway + 1
        r = solve_isolated(model, T, seconds=max(seconds, 600), threads=2, solver=method, window_ub=greedy)
        out = {k: r.get(k) for k in ("value", "bound", "status", "proven", "seconds", "vars")}
        out["window_from"] = f"dp {greedy}"
        if "schedule" in r:
            out["check"] = verdict(model, r["schedule"], r["value"])
        return out
    raise ValueError(method)


def run_method(method, key, seconds):
    p = subprocess.run([sys.executable, "-m", "experiments.full_comparison", "--worker", method, key, str(seconds)],
                       cwd=PACKAGE, capture_output=True, text=True)
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
    return json.loads(lines[-1]) if lines else {"error": (p.stderr.strip().splitlines() or ["?"])[-1][:200]}


def reused_mip(key):
    kind, *a = key.split("|")
    case = f"RAS_{a[0]}_ideal_{a[1]}" if kind == "ideal" else f"RAS_{a[0]}_real"
    rows = json.loads((PACKAGE / "results" / "mip_check" / "solver_compare.json").read_text())
    out = {}
    for r in rows:
        if r["case"] == case:
            out[r["solver"]] = {k: r.get(k) for k in ("value", "bound", "status", "proven", "seconds", "check")}
            out[r["solver"]]["window_from"] = f"validated value {r['window_U']}" + (" + MIP start" if r.get("start") else "")
    return out


def job(args):
    setname, key = args
    cap = CAPS[setname]
    model = model_of(key)
    row = {"set": setname, "instance": key, "trains": len(model.trains), "resources": len(model.resources),
           "blocks": len(model.blocks), "TT0": sum(sum(p for _, p, _ in t.path) for t in model.trains)}
    methods = ["dp", "lr", "lr_phase", "bb", "bb_phase", "e2"]
    methods += ["e1"] if setname != "real" else []
    if setname in ("ideal", "real"):
        row.update(reused_mip(key))
    else:
        methods += ["highs", "cplex"]
    for m in methods:
        row[m] = run_method(m, key, cap)
    print(json.dumps(row), flush=True)
    return row


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        print(json.dumps(worker(sys.argv[2], sys.argv[3], float(sys.argv[4]))))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="toy,mixed,bseg,corr,ideal,real")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    todo = [x for x in instances(a.sets.split(",")) if not a.only or a.only in x[1]]
    f = OUT / "full.json"
    OUT.mkdir(parents=True, exist_ok=True)
    rows = json.loads(f.read_text()) if f.exists() else []
    with ProcessPoolExecutor(a.jobs) as ex:
        for row in ex.map(job, todo):
            rows = [x for x in rows if x["instance"] != row["instance"]] + [row]
            f.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
