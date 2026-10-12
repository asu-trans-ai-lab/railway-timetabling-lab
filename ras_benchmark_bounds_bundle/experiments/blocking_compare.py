"""The RAS 2026 blocking-plan MIP (solver/python/blocking_mip.py) on the competition's practice instance (TWI sample:
8 yards, 5 stations, 15 links, 50 commodities), arc and chain formulations, HiGHS and CPLEX; every plan is checked and
costed by the official validator v2.0 (RAS_VALIDATOR, fast_validator_v2_0.py, shortest paths from the instance's own
links). The sample solution shipped with the instance is checked and costed the same way, as a reference plan.

Cases: the instance as given; "fixed x20" (block fixed cost 50,000: consolidation pays); "minvol x10" (minimum block
volumes 50 / 100 / 150, where C4 binds and some small direct-only commodities cannot be served at all), scored like
the competition by the stress score = cost + M x unserved car-miles.

    python -m experiments.blocking_compare [--instance data/blocking/twi_sample.json] [--seconds 600]
        ->  results/blocking/compare.json
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "blocking"
VALIDATOR = Path(os.environ.get("RAS_VALIDATOR", Path.home() / "Downloads/RAS_Version_A_blocking_solver_current_2026-09-25"
                                "/official_release_v2_1/scoring/fast_validator_v2_0.py"))


def validate(inputs: dict, outputs: dict) -> dict:
    spec = importlib.util.spec_from_file_location("ras_fast_validator", VALIDATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ok, res = mod.fast_validate_payload({"inputs": inputs, "outputs": outputs},
                                        od_distance_matrix_path=PACKAGE / "no_od_matrix.csv")   # links only
    failed = [k for k, v in res["checks"].items() if not v.get("pass")]
    return {"valid": bool(ok), "failed": failed, "cost": res.get("cost", {}).get("total"),
            "stress_score": res.get("stress_metrics", {}).get("stress_score"), "cost_parts": res.get("cost")}


def sample_plan(path: Path) -> dict:
    """The instance's own sample solution, in the v2.0 key names."""
    data = json.load(open(path))
    o = data["outputs"]
    get = lambda name: next(v for k, v in o.items() if k.rstrip(":").strip() == name)           # noqa: E731
    seqs = [{**r, "blocking_sequence": r.get("blocking_sequence", r.get("blocking"))} for r in get("2 Blocking Sequence")]
    design = get("1 Block Design")
    if design and not isinstance(design[0], dict):              # a list of candidate block ids (older format)
        cand = {b["block_id"]: b for b in data["inputs"]["blocks"]}
        design = [{"block_id": i, "from_yard_id": cand[i]["from_yard_id"], "to_yard_id": cand[i]["to_yard_id"],
                   "block_type": cand[i]["block_type"]} for i in design]
    return {"1 Block Design": design, "2 Blocking Sequence": seqs, "3 Block Route": get("3 Block Route")}


STRESS = {"minvol_x10"}                 # cases scored by the stress score (unserved demand allowed, penalised)


def variant(inst: dict, name: str) -> dict:
    inst = copy.deepcopy(inst)
    if name == "fixed_x20":
        inst["settings"]["block_fixed_cost"] = float(inst["settings"]["block_fixed_cost"]) * 20
    if name == "minvol_x10":
        for key in ("min_block_vol_short(<100mi)", "min_block_vol_med(100-500mi)", "min_block_vol_long(>500mi)"):
            inst["settings"][key] = float(inst["settings"][key]) * 10
    return inst


def worker(path, case, form, solver, seconds):
    from solver.python.blocking_mip import load, solve
    inst = variant(load(path), case)
    r = solve(inst, form, solver, seconds, unserved=case in STRESS)
    if "plan" in r:
        r["validator"] = validate(inst, r.pop("plan"))
    return r


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        print(json.dumps(worker(*sys.argv[2:6], float(sys.argv[6]))))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", default=str(PACKAGE / "data" / "blocking" / "twi_sample.json"))
    ap.add_argument("--seconds", type=float, default=600)
    ap.add_argument("--cases", default="given,fixed_x20,minvol_x10")
    a = ap.parse_args()
    from solver.python.blocking_mip import load
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in a.cases.split(","):
        inst = variant(load(a.instance), case)
        row = {"case": case, "sample_solution": validate(inst, sample_plan(Path(a.instance)))}
        for form in ("arc", "chain"):
            for solver in ("highs", "cplex"):
                p = subprocess.run([sys.executable, "-m", "experiments.blocking_compare", "--worker", a.instance,
                                    case, form, solver, str(a.seconds)], cwd=PACKAGE, capture_output=True, text=True)
                lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
                row[f"{form}_{solver}"] = json.loads(lines[-1]) if lines else {"error": p.stderr[-400:]}
                r = row[f"{form}_{solver}"]
                print(case, form, solver, {k: r.get(k) for k in ("value", "proven", "lp", "seconds", "nodes", "vars",
                                                                  "binaries", "served", "error")},
                      r.get("validator"), flush=True)
        rows.append(row)
        (OUT / "compare.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
