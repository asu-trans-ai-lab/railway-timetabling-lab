"""The cumulative-flow MIP on toy instances whose optimum E1 and E3 have already proven: it must reproduce them.

    python -m experiments.mip_known      ->  results/mip_check/known.json
"""
import json
from pathlib import Path

from adapters.toy_corridor import physical_check, toy_model
from solver.python.mip_cumflow import solve_isolated
from solver.python.siding_validate import validate

OUT = Path(__file__).resolve().parents[1] / "results" / "mip_check"
KNOWN = [(3, 10, 174), (5, 10, 340), (5, 5, 340), (5, None, 363)]

rows = []
for n, cells, z in KNOWN:
    m = toy_model(n, cells=cells)
    r = solve_isolated(m, max(t.release for t in m.trains) + 160, seconds=300)
    errors, total, _ = validate(m, r["schedule"])
    errors += physical_check(m, r["schedule"])
    rows.append({"instance": f"toy N={n}", "res": {10: "10-min cells", 5: "5-min cells", None: "whole"}[cells],
                 "known": z, "mip": r["value"], "status": r["status"], "seconds": r["seconds"], "vars": r["vars"],
                 "check": "PASS" if not errors and total == r["value"] else "FAIL"})
    print(rows[-1], flush=True)
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "known.json").write_text(json.dumps(rows, indent=1))
