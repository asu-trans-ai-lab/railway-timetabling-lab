"""The MIP of every RAS D1-D3 case as a solver-independent model file, so CPLEX, HiGHS, Gurobi or GAMS can solve the
same formulation without this code (Prof. Zhou, Oct 7).

Cases: D1-D3 ideal (equal speeds, single-track main line) at whole / 10-minute / 5-minute resolution, and D1-D3 with real
speeds (the fixed-track model). Each model is the fixed-route cumulative-flow MIP of solver/python/mip_cumflow.py
(Meng & Zhou 2014, model P3 with one route per train, g = 0, h = H) with the time window of a validated timetable of
value U (every train's delay <= U - TT0, objective <= U; this keeps every timetable of value <= U). The objective
constant is stored as the model's offset: a solver's optimum is OBJ-E, total elapsed minutes, directly.

    python -m experiments.export_mip_models [--out results/mip_models]  ->  <case>.mps.gz, models.json
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import tempfile
from pathlib import Path

from adapters.fixed_track_adapter import to_chain_instance
from adapters.ras_ideal import ideal_model
from solver.python.mip_cumflow import solve_isolated
from solver.python.siding_model import read_instance

PACKAGE = Path(__file__).resolve().parents[1]
IDEAL = {("D1", "whole"): 1124, ("D1", "cells10"): 1124, ("D1", "cells5"): 1124,
         ("D2", "whole"): 1994, ("D2", "cells10"): 1759, ("D2", "cells5"): 1729,
         ("D3", "whole"): 2109, ("D3", "cells10"): 2012, ("D3", "cells5"): 1960}   # MIP-proven optima (CPLEX)
REAL = {"D1": 2220, "D2": 4127, "D3": 4056}                                        # CP-SAT optima
RES = {"whole": None, "cells10": 10, "cells5": 5}


def cases():
    for (d, res), u in IDEAL.items():
        yield f"RAS_{d}_ideal_{res}", ideal_model(d, RES[res]), u
    for d, u in REAL.items():
        work = Path(tempfile.mkdtemp(prefix=f"exp_{d}_"))
        yield f"RAS_{d}_real", read_instance(to_chain_instance(d, work / "inst.txt")), u


def horizon(model, u):
    free = [sum(p for _, p, _ in t.path) for t in model.trains]
    return max(t.release + f for t, f in zip(model.trains, free)) + (u - sum(free)) + model.headway + 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(PACKAGE / "results" / "mip_models"))
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    f = out / "models.json"
    rows = json.loads(f.read_text()) if f.exists() else []
    for name, model, u in cases():
        if a.only and a.only not in name:
            continue
        tmp = Path(tempfile.mkdtemp(prefix="mps_")) / f"{name}.mps"
        T = horizon(model, u)
        r = solve_isolated(model, T, export=str(tmp), window_ub=u)
        with open(tmp, "rb") as src, gzip.open(out / f"{name}.mps.gz", "wb", compresslevel=9) as dst:
            shutil.copyfileobj(src, dst)
        row = {"case": name, "file": f"{name}.mps.gz", "trains": len(model.trains), "resources": len(model.resources),
               "headway": model.headway, "horizon": T, "window_U": u, "variables": r["vars"], "rows": r["rows"],
               "objective_constant": r["constant"], "mps_mb": round(tmp.stat().st_size / 2 ** 20, 1),
               "gz_mb": round((out / f"{name}.mps.gz").stat().st_size / 2 ** 20, 1)}
        tmp.unlink()
        rows = [x for x in rows if x["case"] != name] + [row]
        f.write_text(json.dumps(rows, indent=1))
        print(json.dumps(row), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
