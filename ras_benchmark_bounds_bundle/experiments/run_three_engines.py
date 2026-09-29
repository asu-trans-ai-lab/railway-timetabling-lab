"""The three engines on the fixed-track RAS model (D1-D3), each in its strongest configuration, every UB through the
independent validator (validator/fixed_track/).

    python -m experiments.run_three_engines e1 --dataset D2 [--seconds 600] [--workers 3]
    python -m experiments.run_three_engines e2 --dataset D2 [--seconds 1800] [--milp highs|cplex] [--heur-every 5]
    python -m experiments.run_three_engines e3 --dataset D2 [--seconds 1800] [--workers 8]   (--workers 0: one process)

Reference (30 min per engine, 2026-09-28; OBJ-E [LB, UB]):
    E1  D1 2220 / D2 4127 / D3 4056, all proven (CP-SAT OPTIMAL + brute-force certificate)
    E2  D1 2220 proven; D2 [3710, 4801] 22.72 %; D3 [3816, 4566] 16.43 %   (meet cuts + heuristic every 5, CPLEX MILP)
    E3  D1 2220 proven; D2 [3873, 4604] 15.88 %; D3 [3879, 4417] 12.18 %   (8 processes)
Results: results/three_engines/<engine>/<dataset>/result.json and the validated schedule.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from adapters.fixed_track_adapter import DATA, TT0, to_chain_instance, to_package_schedule, validate

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "three_engines"


def gaps(lb, ub, tt0):
    if lb is None or ub is None:
        return {}
    return {"gap_obj_e_pct": round(100 * (ub - lb) / ub, 2),
            "gap_obj_d_pct": round(100 * (ub - lb) / max(1, ub - tt0), 2)}


def run_e1(dataset: str, seconds: float, workers: int, work: Path) -> dict:
    from solver.python.e1_blockpair import solve
    return solve(DATA / dataset, DATA / "donors" / f"{dataset}_donor.csv", work,
                 lambda sched: validate(dataset, Path(sched)), seconds=seconds, workers=workers)


def run_e2(dataset: str, seconds: float, milp: str, heur_every: int, meet: bool, work: Path) -> dict:
    from solver.python.e2_colgen import independent, write_legs
    from solver.python.siding_model import read_instance
    inst = to_chain_instance(dataset, work / "instance.txt")
    model = read_instance(inst)

    def check(_model, schedule):                  # {train: [(i, name, entry, exit)]} -> (message, OBJ-E)
        csv_path = work / "check.csv"
        with open(csv_path, "w") as f:
            f.write("train_id,index,resource,entry,exit\n")
            for tid, legs in schedule.items():
                for i, name, e, x in legs:
                    f.write(f"{tid},{i},{name},{e},{x}\n")
        value, msg = validate(dataset, to_package_schedule(csv_path, dataset, work / "check_pkg.csv"))
        return msg, value

    r = independent(model, inst, seconds, False, dataset, log=lambda m: print(m, flush=True), milp_solver=milp,
                    check=check, meet=meet, heur_every=heur_every)
    res = {k: v for k, v in r.items() if k != "schedule"}
    res["engine"] = "E2 kernel + LP + MILP" + (" + meet cuts" if meet else "") + \
                    (f" + heuristic every {heur_every}" if heur_every else "")
    if r.get("ub") is not None:
        path = write_legs(model, r["schedule"], work / "schedule_engine.csv")
        value, msg = validate(dataset, to_package_schedule(path, dataset, work / "schedule.csv"))
        res.update({"validator": msg, "validated_value": value, "ub_e": r["ub"], "lb_e": r.get("lb")})
    return res


def run_e3(dataset: str, seconds: float, workers: int, work: Path) -> dict:
    from solver.python.e3_bb import STRONGEST, run_parallel, run_single
    inst = to_chain_instance(dataset, work / "instance.txt")
    if workers <= 0:
        res = run_single(inst, work, seconds)
        outs = [Path(res["schedule"])]
    else:
        res = run_parallel(inst, work, seconds, workers=workers, log=lambda s: print(s, flush=True))
        outs = [Path(r["out"]) for r in res["results"]] + [work / "greedy.csv"]
        res["results"] = [{k: (str(v) if k == "out" else v) for k, v in r.items()} for r in res["results"]]
    best = (None, "no schedule")
    for f in outs:                                # every process wrote its own best: keep the least validated one
        if f.exists():
            v, msg = validate(dataset, to_package_schedule(f, dataset, f.with_name(f.stem + "_pkg.csv")))
            if v is not None and (best[0] is None or v < best[0]):
                best = (v, msg)
    assert best[0] == res["ub"], (best, res["ub"])   # the reported UB is a validated schedule
    res.update({"engine": "E3 B&B + DP" + (f", {workers} processes" if workers > 0 else ""), "tree": " ".join(STRONGEST),
                "validated_value": best[0], "validator": best[1], "ub_e": res["ub"], "lb_e": res["lb"]})
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("engine", choices=["e1", "e2", "e3"])
    ap.add_argument("--dataset", required=True, choices=["D1", "D2", "D3"])
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--workers", type=int, default=None, help="E1: CP-SAT workers (3); E3: processes (8, 0 = one)")
    ap.add_argument("--milp", default="highs", choices=["highs", "cplex"], help="E2's MILP (cplex: set CPLEX_BIN)")
    ap.add_argument("--heur-every", type=int, default=5, help="E2: heuristic at the master duals every K iterations")
    ap.add_argument("--no-meet", action="store_true", help="E2: without meet cuts (ablation)")
    args = ap.parse_args()
    work = OUT / args.engine / args.dataset
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if args.engine == "e1":
        res = run_e1(args.dataset, args.seconds or 600, args.workers if args.workers is not None else 3, work)
    elif args.engine == "e2":
        res = run_e2(args.dataset, args.seconds or 1800, args.milp, args.heur_every, not args.no_meet, work)
    else:
        res = run_e3(args.dataset, args.seconds or 1800, args.workers if args.workers is not None else 8, work)
    res.update({"dataset": args.dataset, "TT0": TT0[args.dataset], "wall_seconds": round(time.time() - t0, 1)})
    res.update(gaps(res.get("lb_e"), res.get("ub_e"), TT0[args.dataset]))
    (work / "result.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: res.get(k) for k in ("engine", "dataset", "lb_e", "ub_e", "gap_obj_e_pct", "gap_obj_d_pct",
                                               "validator", "wall_seconds")}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
