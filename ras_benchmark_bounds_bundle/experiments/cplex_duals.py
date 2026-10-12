"""CPLEX marginals = Lagrange multipliers (Prof. Zhou, Oct 8: "ask CPLEX to give you the marginal").

The LP relaxation of the space-time arc formulation (solver/python/lp_spacetime.py) is solved by CPLEX; the dual value
(marginal) of every capacity row "sum of arcs holding r at minute t <= tracks_r" is read from its solution file. Its
negative, lambda(r, t) >= 0, is a Lagrange multiplier of that row. The kernel then prices every train by its own DP
(solver/cpp/siding_lr.cpp --mode price) at these multipliers:

    L(lambda) = sum_k  min over train k's timetables of [cost_k + sum of lambda(r, t) over the (r, t) it holds]
                - sum_{r,t} tracks_r * lambda(r, t)

Hypothesis: L(lambda_CPLEX) = LP value. The LP dual is an optimal multiplier of the Lagrangian dual (Geoffrion 1974),
so the DP with the solver's marginals reaches the LP bound in one pricing, while the kernel's subgradient (3,000 steps,
results/comparison/full.json "lr") approaches it from below. A marginal also reads directly: lambda(r, t) minutes of
total delay saved per extra unit of capacity of resource r at minute t; the resources with large sum_t lambda are
the bottleneck.

    python -m experiments.cplex_duals [--sets toy,bseg,ideal] [--only key]   ->  results/formulations/duals.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "formulations"


def cplex_lp(h, work: Path):
    """Solve the (continuous) model by CPLEX; objective and the dual of every row."""
    from solver.python.mip_cumflow import CPLEX
    h.writeModel(str(work / "lp.mps"))
    log = subprocess.run([str(CPLEX), "-c", f"read {work / 'lp.mps'}", "set threads 2", "optimize",
                          f"write {work / 'lp.sol'}", "quit"], capture_output=True, text=True).stdout
    (work / "cplex_lp.log").write_text(log)
    root = ET.parse(work / "lp.sol").getroot()
    obj = float(root.find("header").attrib["objectiveValue"])
    duals = {}
    for c in root.find("linearConstraints"):
        d = float(c.attrib.get("dual", 0.0))
        if d:
            duals[int(c.attrib["index"])] = d
    return obj, duals


def price(model, U, lam: dict, work: Path, tag: str) -> float:
    """The kernel's L(lambda) at the multipliers lam[(r, t)] (window of value <= U: --ub U + 1)."""
    from solver.python.siding_kernel import build
    from solver.python.siding_model import write_instance
    inst = write_instance(model, work / "inst.txt")
    f = work / f"duals_{tag}.txt"
    f.write_text("".join(f"L {r} {t} {v:.12g}\n" for (r, t), v in lam.items() if v > 0))
    out = subprocess.run([str(build()), str(inst), "--mode", "price", "--ub", str(U + 1), "--duals", str(f)],
                         capture_output=True, text=True).stdout
    m = re.search(r"LAGR (\S+)", out)
    return float(m.group(1)) if m else float("nan")


def worker(key: str) -> dict:
    from experiments.formulation_compare import model_of
    from solver.python.lp_spacetime import build
    from solver.python.siding_kernel import greedy_ub
    from solver.python.siding_model import write_instance
    model = model_of(key)
    work = Path(tempfile.mkdtemp(prefix="duals_"))
    U = 9 if key.startswith("unit") else greedy_ub(write_instance(model, work / "i.txt"), work / "dp.csv")
    b = build(model, U)
    c0, T = b["cap_row0"], b["T"]
    obj, duals = cplex_lp(b["h"], work)
    lam = {}
    for row, d in duals.items():
        if row >= c0:
            r, t = divmod(row - c0, T)
            lam[(r, t)] = -d                    # <= row of a minimisation: dual <= 0
    # HiGHS: the same LP, its own duals
    h = b["h"]
    h.run()
    hobj = h.getInfo().objective_function_value
    rd = h.getSolution().row_dual
    lam_h = {}
    for row in range(c0, c0 + len(model.resources) * T):
        if rd[row] < -1e-9:
            lam_h[divmod(row - c0, T)] = -rd[row]
    by_res = {}
    for (r, t), v in lam.items():
        by_res[model.resources[r].name] = by_res.get(model.resources[r].name, 0.0) + v
    top = sorted(by_res.items(), key=lambda x: -x[1])[:5]
    L_c, L_h = price(model, U, lam, work, "cplex"), price(model, U, lam_h, work, "highs")
    shutil.rmtree(work, ignore_errors=True)
    return {"instance": key, "U": U, "lp_cplex": obj, "lp_highs": hobj,
            "L_cplex_marginals": L_c, "L_highs_duals": L_h,
            "priced_cells": len(lam), "priced_cells_highs": len(lam_h),
            "negative_check": min(lam.values(), default=0.0),
            "top_resources": [(n, round(v, 3)) for n, v in top]}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        print(json.dumps(worker(sys.argv[2])))
        return 0
    from experiments.formulation_compare import instances
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="unit,toy,bseg,ideal")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / "duals.json"
    rows = json.loads(f.read_text()) if f.exists() else []
    full = {r["instance"]: r for r in json.loads((PACKAGE / "results/comparison/full.json").read_text())}
    for _, key in instances(a.sets.split(",")):
        if a.only and a.only not in key:
            continue
        p = subprocess.run([sys.executable, "-m", "experiments.cplex_duals", "--worker", key], cwd=PACKAGE,
                           capture_output=True, text=True)
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        row = json.loads(lines[-1]) if lines else {"instance": key, "error": p.stderr[-300:]}
        lr = full.get(key, {}).get("lr", {})
        row["L_subgradient"] = lr.get("L")
        row["optimum"] = (full.get(key, {}).get("cplex") or {}).get("value")
        print(json.dumps(row), flush=True)
        rows = [x for x in rows if x["instance"] != key] + [row]
        f.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
