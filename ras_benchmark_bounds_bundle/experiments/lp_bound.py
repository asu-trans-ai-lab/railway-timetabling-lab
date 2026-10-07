"""Lower bounds side by side (Prof. Zhou, Oct 4: a bound comes from the LP of the space-time formulation or from the
Lagrangian relaxation): the space-time arc LP (solver/python/lp_spacetime.py), the kernel's subgradient Lagrangian
bound of the same capacity rows (--mode lb, no phase or meet rows), and the optimum (MIP / CP-SAT, on record).

Both bounds are valid for every objective with alpha, beta >= 0; the LP value is the best Lagrangian bound, the
subgradient approaches it from below. On cell models neither has the single-track stretch rows, so both are weaker there.

    python -m experiments.lp_bound [--seconds 900]  ->  results/mip_check/lp_bound.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

from adapters.fixed_track_adapter import to_chain_instance
from adapters.ras_ideal import ideal_model
from adapters.toy_corridor import toy_model
from experiments.mip_check import mixed_model
from solver.python.lp_spacetime import solve_lp
from solver.python.siding_kernel import build
from solver.python.siding_model import read_instance, write_instance

OUT = Path(__file__).resolve().parents[1] / "results" / "mip_check"
LB_RE = re.compile(r"RESULT mode lb .* L (\S+) LB (\d+) UB (\d+) LB0 (\S+) iterations (\d+) seconds (\S+)")


def kernel_lr(model, ub, iters, seconds):
    work = Path(tempfile.mkdtemp(prefix="lr_"))
    inst = write_instance(model, work / "inst.txt")
    out = subprocess.run([str(build()), str(inst), "--mode", "lb", "--ub", str(ub), "--iters", str(iters),
                          "--lb-time-cap", str(seconds)], capture_output=True, text=True).stdout
    m = LB_RE.search(out)
    return {"L": float(m.group(1)), "lb": int(m.group(2)), "iterations": int(m.group(5)), "seconds": float(m.group(6))}


def cases():
    """(name, model, optimum or best value on record, certified?)"""
    yield "toy N=5 whole", toy_model(5), 363, True
    yield "toy N=5 cells10", toy_model(5, cells=10), 340, True
    for n, v in ((4, 223), (5, 290), (6, 387)):
        yield f"mixed N={n} whole", mixed_model(n, None), v, True
    for d, v, cert in (("D1", 1124, True), ("D2", 1759, True), ("D3", 2012, True)):
        yield f"RAS {d} ideal", ideal_model(d), v, cert
    for d, v in (("D1", 2220), ("D2", 4127), ("D3", 4056)):
        work = Path(tempfile.mkdtemp(prefix=f"fx_{d}_"))
        yield f"RAS {d} real speeds", read_instance(to_chain_instance(d, work / "inst.txt")), v, True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=900)
    ap.add_argument("--iters", type=int, default=3000)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    f = OUT / "lp_bound.json"
    rows = json.loads(f.read_text()) if f.exists() else []
    for name, model, best, certified in cases():
        if a.only and a.only not in name:
            continue
        tt0 = sum(sum(p for _, p, _ in t.path) for t in model.trains)
        lp = solve_lp(model, best, seconds=a.seconds, threads=4)
        lr = kernel_lr(model, best, a.iters, a.seconds)
        row = {"instance": name, "TT0": tt0, "best": best, "best_certified": certified, "lp": lp, "lagrangian": lr}
        if "bound" in lp:
            row["lp_gap_pct"] = round(100 * (best - lp["bound"]) / best, 2)
        row["lr_gap_pct"] = round(100 * (best - lr["L"]) / best, 2)
        print(json.dumps(row), flush=True)
        rows = [x for x in rows if x["instance"] != name] + [row]
        OUT.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
