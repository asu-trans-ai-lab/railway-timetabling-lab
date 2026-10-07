"""S01 N = 8 with E3 in one process and with 8 workers: value, status, wall-clock seconds and total CPU seconds of all
kernel processes (Prof. Zhou, Oct 4: show that the parallel search lowers the time, and what it costs in CPU).

    python -m experiments.s01_timing --instance /path/to/S01/instances/n8 [--seconds 1800]
        ->  results/determinism/s01_timing.json
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from adapters.s01_adapter import model_of
from solver.python.e3_bb import run_parallel, run_single
from solver.python.siding_model import write_instance

OUT = Path(__file__).resolve().parents[1] / "results" / "determinism"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", required=True)
    ap.add_argument("--seconds", type=float, default=1800)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="s01_timing_"))
    inst = write_instance(model_of(Path(a.instance)), work / "instance.txt")
    rows = []
    for name, fn in (("serial", lambda: run_single(inst, work / "serial", a.seconds)),
                     (f"{a.workers} workers", lambda: run_parallel(inst, work / "parallel", a.seconds, a.workers,
                                                                   log=lambda *_: None))):
        r = fn()
        row = {"run": name, "ub": r["ub"], "lb": r["lb"], "status": "PROVEN" if r["proven"] else "TIME_CAP",
               "seconds": r["seconds"], "cpu_seconds": r["cpu_seconds"]}
        rows.append(row)
        print(json.dumps(row), flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "s01_timing.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
