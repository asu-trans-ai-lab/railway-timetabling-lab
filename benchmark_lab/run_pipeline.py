"""
run_pipeline.py — run the full solver ladder over the size ladder, one clean records file.

Per instance: B0 heuristics, B2 time-indexed MILP (UB-only restricted model), B4 CP-SAT,
B5 individual CG (+ LP bound), B6 branch-and-price. B1/B3 wrapped via fasttrain where present.
All rows -> results/pipeline_records.csv; report via make_report.py.

Usage: python run_pipeline.py [--fresh]
"""
from __future__ import annotations
import os, sys, subprocess, json, time

HERE = os.path.dirname(os.path.abspath(__file__))
TS = os.path.normpath(os.path.join(HERE, ".."))
REC = os.path.join(HERE, "results", "pipeline_records.csv")
PY = sys.executable

INSTANCES = [
    os.path.join(TS, "FTT_handoff", "data", "toy_native"),
    os.path.join(HERE, "results", "instances", "C1_L4_n4_seed1"),
    os.path.join(HERE, "results", "instances", "C1_L4_n4_seed2"),
    os.path.join(HERE, "results", "instances", "C1_L6_n6_seed3"),
    os.path.join(HERE, "results", "instances", "C1_L8_n8_seed11"),
    os.path.join(HERE, "results", "instances", "C1_L10_n10_seed12"),
    os.path.join(HERE, "results", "instances", "C1_L12_n12_seed13"),
]

STEPS = [  # (label, cmd builder)
    ("B0", lambda d: [PY, os.path.join(HERE, "solvers", "priority_heuristics.py"), d,
                      "--rand-n", "5", "--records", REC]),
    ("B2", lambda d: [PY, os.path.join(HERE, "solvers", "time_indexed_milp.py"), d,
                      "--records", REC]),
    ("B4", lambda d: [PY, os.path.join(HERE, "solvers", "cp_sat_timetabling.py"), d,
                      "--time", "180", "--records", REC]),
    ("B5", lambda d: [PY, os.path.join(HERE, "solvers", "individual_column_generation.py"), d,
                      "--iters", "60", "--records", REC]),
    ("B6", lambda d: [PY, os.path.join(HERE, "solvers", "branch_and_price.py"), d,
                      "--nodes", "200", "--time", "150", "--records", REC]),
]
CAPS = {"B0": 600, "B2": 300, "B4": 220, "B5": 400, "B6": 300}


def main(fresh=True):
    if fresh and os.path.exists(REC):
        os.remove(REC)
    t00 = time.time()
    for d in INSTANCES:
        iid = os.path.basename(d)
        if not os.path.isdir(d):
            print(f"[skip missing] {iid}"); continue
        for label, mk in STEPS:
            t0 = time.time()
            try:
                p = subprocess.run(mk(d), capture_output=True, text=True, timeout=CAPS[label])
                tail = (p.stdout + p.stderr).strip().splitlines()
                print(f"[{iid} {label} {time.time()-t0:5.1f}s] " + (tail[-1] if tail else "(no output)"))
            except subprocess.TimeoutExpired:
                print(f"[{iid} {label}] TIMEOUT {CAPS[label]}s (no record row)")
            except Exception as e:
                print(f"[{iid} {label}] ERROR {e}")
    print(f"pipeline done in {time.time()-t00:.0f}s -> {REC}")


if __name__ == "__main__":
    main()
