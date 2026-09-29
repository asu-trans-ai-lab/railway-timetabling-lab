"""E2 and E3 on an S01 instance (the S01 data is not in this repository: pass its directory), with the strongest
fixed-track configurations; every UB through the S01 validator (validator/s01/).

    python -m experiments.run_s01 --instance /path/to/S01/instances/n8 --engine e3 [--seconds 1800]

Reference (30 min, 2026-09-28; OBJ-E [LB, UB]; E1 = block-pair CP-SAT proves all three):
    N = 8   E1 6828   E2 [6701, 6994]    E3 6828 proven (1,047 s)
    N = 12  E1 10295  E2 [10058, 10796]  E3 [10160, 10653]
    N = 20  E1 17272  E2 [16772, 18239]  E3 [16815, 17998]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adapters.s01_adapter import model_of, stretches, tt0, validate_s01, write_s01_schedule
from solver.python.siding_model import write_instance
from solver.python.siding_validate import read_schedule

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "s01"


def legs_of(model, schedule_csv: Path) -> dict[int, tuple]:
    idx = {r.name: i for i, r in enumerate(model.resources)}
    s = read_schedule(schedule_csv)
    return {k: tuple((idx[n], e, x) for _, n, e, x in s[t.train_id]) for k, t in enumerate(model.trains)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", required=True, help="an S01 instance directory (params.tsv, resources.tsv, trains.tsv)")
    ap.add_argument("--engine", choices=["e2", "e3"], required=True)
    ap.add_argument("--seconds", type=float, default=1800)
    ap.add_argument("--milp", default="highs", choices=["highs", "cplex"])
    args = ap.parse_args()
    inst_dir = Path(args.instance)
    work = OUT / f"{args.engine}_{inst_dir.name}"
    work.mkdir(parents=True, exist_ok=True)
    model = model_of(inst_dir)
    inst = write_instance(model, work / "instance.txt")
    T0 = tt0(inst_dir)
    if args.engine == "e3":
        from solver.python.e3_bb import run_single
        res = run_single(inst, work, args.seconds)
        d, report = validate_s01(inst_dir, write_s01_schedule(model, legs_of(model, Path(res["schedule"])),
                                                              work / "best.tsv"))
    else:
        from solver.python.e2_colgen import independent
        idx = {r.name: i for i, r in enumerate(model.resources)}

        def check(_model, schedule):
            legs = {k: tuple((idx[nm], e, x) for _, nm, e, x in schedule[t.train_id])
                    for k, t in enumerate(model.trains)}
            dd, rep = validate_s01(inst_dir, write_s01_schedule(model, legs, work / "check.tsv"))
            return ("PASS" if dd is not None else rep), (None if dd is None else dd + T0)

        r = independent(model, inst, args.seconds, False, inst_dir.name, log=lambda s: print(s, flush=True),
                        milp_solver=args.milp, check=check, meet=True, heur_every=5)
        res = {k: v for k, v in r.items() if k != "schedule"}
        d, report = validate_s01(inst_dir, write_s01_schedule(model, r["schedule"], work / "best.tsv"))
    res.update({"engine": args.engine.upper(), "instance": str(inst_dir), "tt0": T0,
                "validated_objE": None if d is None else d + T0, "validator": "PASS" if d is not None else report,
                "bottleneck": stretches(inst_dir)[0]})
    if res.get("lb") is not None and res.get("ub") is not None:
        res["gap_obj_e_pct"] = round(100 * (res["ub"] - res["lb"]) / res["ub"], 2)
        res["gap_obj_d_pct"] = round(100 * (res["ub"] - res["lb"]) / max(1, res["ub"] - T0), 2)
    (work / "result.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res.get(k) for k in ("engine", "instance", "lb", "ub", "gap_obj_e_pct", "gap_obj_d_pct",
                                               "validator")}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
