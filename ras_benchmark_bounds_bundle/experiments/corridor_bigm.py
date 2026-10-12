"""The full ANL corridors (adapters/anl_corridor.py, 10-88 trains) by the big-M MIP with one precedence variable per
shared run (solver/python/mip_formulations.py, form bigm_shared), CPLEX; window from our DP value (no CP-SAT input).
Compared with E1 (CP-SAT, results/corridors/<name>/row.json): until now the full corridors had no MIP result.

    python -m experiments.corridor_bigm [--forms bigm_shared] [--seconds 1800] [--jobs 3]
        ->  results/formulations/corridor_bigm.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "formulations"
NAMES = ["overland", "panhandle", "transcon_clovis", "prb_joint", "pocahontas", "hiline", "cn_icmain", "gulf",
         "moffat", "cpkc_midcon", "bnsf_columbia", "csx_bo", "csx_waterlevel", "ns_chicago", "ns_crescent",
         "ns_heartland", "up_sunset", "up_texas", "transcon_clovis_native_fullday"]


def worker(name: str, form: str, seconds: float) -> dict:
    from adapters.anl_corridor import DATA, corridor_model
    from solver.python.mip_formulations import solve
    from solver.python.siding_kernel import greedy_ub
    from solver.python.siding_model import write_instance
    from solver.python.siding_validate import validate
    base = DATA / name if (DATA / name).exists() else None
    model, info = corridor_model(name.replace("_native_fullday", ""), base=base) if base else corridor_model(name)
    work = Path(tempfile.mkdtemp(prefix="corrbm_"))
    U = greedy_ub(write_instance(model, work / "i.txt"), work / "dp.csv")
    shutil.rmtree(work, ignore_errors=True)
    TT0 = sum(sum(p for _, p, _ in t.path) for t in model.trains)
    r = solve(model, form, U, "cplex", seconds, threads=2)
    out = {"corridor": name, "form": form, "trains": len(model.trains), "TT0": TT0, "dp_value": U,
           **{k: r.get(k) for k in ("value", "bound", "proven", "seconds", "nodes", "pairs", "vars", "status", "lp")}}
    if "schedule" in r:
        errs, total, _ = validate(model, r["schedule"])
        out["check"] = "PASS" if not errs and total == r["value"] else f"FAIL {errs[:1]}"
    e1 = PACKAGE / "results" / "corridors" / name / "row.json"
    if e1.exists():
        row = json.loads(e1.read_text()).get("e1", {})
        out["e1_value"], out["e1_status"] = row.get("value"), row.get("status")
    return out


def run(args):
    name, form, seconds = args
    p = subprocess.run([sys.executable, "-m", "experiments.corridor_bigm", "--worker", name, form, str(seconds)],
                       cwd=PACKAGE, capture_output=True, text=True)
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
    r = json.loads(lines[-1]) if lines else {"corridor": name, "form": form, "error": p.stderr[-400:]}
    print(json.dumps({k: r.get(k) for k in ("corridor", "form", "trains", "value", "proven", "bound", "seconds",
                                             "e1_value", "e1_status", "check", "error")}), flush=True)
    return r


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        print(json.dumps(worker(sys.argv[2], sys.argv[3], float(sys.argv[4]))))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", default="bigm_shared")
    ap.add_argument("--seconds", type=float, default=1800)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    todo = [(n, f, a.seconds) for n in NAMES for f in a.forms.split(",") if not a.only or a.only in n]
    f = OUT / "corridor_bigm.json"
    rows = json.loads(f.read_text()) if f.exists() else []
    with ProcessPoolExecutor(a.jobs) as ex:
        for r in ex.map(run, todo):
            rows = [x for x in rows if (x["corridor"], x["form"]) != (r["corridor"], r["form"])] + [r]
            f.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
