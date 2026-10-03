"""Bottleneck lower bounds on the fixed-track model: how high does an identity-complete bottleneck relaxation get?

Relaxation R(S): every train keeps its whole route (release, running times, waiting only where allowed), but conflicts
between trains are enforced only on the segment set S (the "bottleneck"); conflicts elsewhere are dropped. Every
feasible timetable is feasible for R(S), so min R(S) <= z*: a valid lower bound. The sequencing on S is
identity-complete -- each pair of rigid blocks gets its exact allowed start differences (the block pairs of E1), no
first-in-first-out order. S = the K segments of largest occupancy demand sum(run + H); K = all is the full model.

Also, for the single busiest segment, the pure projection (one capacity-1 resource, each train ready at its release +
free running time to the segment, occupancy run + H, objective = sum of entry delays): the single-bottleneck bound of
an identity-complete phase-time DP.

    python -m experiments.bottleneck_bound --dataset D2 [--ks 1,2,3,5,10,20,40] [--seconds 120] [--workers 2]
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from ortools.sat.python import cp_model

from adapters.fixed_track_adapter import DATA, TT0, to_chain_instance
from solver.python.e1_blockpair import allowed_ranges, identical_groups, load_instance, make_blocks
from solver.python.siding_kernel import greedy_ub

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "bottleneck_bound"


def demand(inst) -> dict[int, int]:
    H, out = inst["headway"], {}
    for t in inst["trains"]:
        for seg, p in zip(t["chain"], t["run"]):
            out[seg] = out.get(seg, 0) + p + H
    return out


def forbidden_on(ba, bb, H, S):
    """Start differences s_b - s_a for which the two blocks overlap on a segment of S (closed intervals, merged)."""
    ta = {seg: (o, p) for seg, o, p, _ in ba["tasks"] if seg in S}
    iv = []
    for seg, ob, pb, _ in bb["tasks"]:
        if seg in ta:
            oa, pa = ta[seg]
            iv.append((oa - ob - pb - H + 1, oa + pa + H - ob - 1))
    iv.sort()
    merged = []
    for lo, hi in iv:
        if merged and lo <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [tuple(x) for x in merged]


def relaxation(inst, S: set, ub: int, seconds: float, workers: int) -> dict:
    H = inst["headway"]
    blocks, per_train = make_blocks(inst)
    T0 = sum(sum(t["run"]) for t in inst["trains"])
    max_delay = ub - T0                                    # windows: valid for every timetable with OBJ-E <= ub
    m = cp_model.CpModel()
    s = [m.new_int_var(b["earliest"], b["earliest"] + max_delay, f"s{i}") for i, b in enumerate(blocks)]
    for ids in per_train:
        for a, b in zip(ids, ids[1:]):
            m.add(s[b] >= s[a] + blocks[a]["length"])
    fixed = set()
    for group in identical_groups(inst):                   # identical trains keep release order (swap argument)
        for i, j in zip(group, group[1:]):
            for a, b in zip(per_train[i], per_train[j]):
                forb = forbidden_on(blocks[a], blocks[b], H, S)
                m.add(s[b] - s[a] >= (forb[-1][1] + 1 if forb else 0))
                fixed.add((min(a, b), max(a, b)))
    pairs = 0
    for a in range(len(blocks)):
        for b in range(a + 1, len(blocks)):
            if blocks[a]["train"] == blocks[b]["train"] or (a, b) in fixed:
                continue
            forb = forbidden_on(blocks[a], blocks[b], H, S)
            if not forb:
                continue
            lo = blocks[b]["earliest"] - (blocks[a]["earliest"] + max_delay)
            hi = blocks[b]["earliest"] + max_delay - blocks[a]["earliest"]
            alts = allowed_ranges(forb, lo, hi)
            if not alts:
                return {"status": "INFEASIBLE_WINDOWS"}
            pairs += 1
            if len(alts) == 1:
                if alts[0][0] > lo:
                    m.add(s[b] - s[a] >= alts[0][0])
                if alts[0][1] < hi:
                    m.add(s[b] - s[a] <= alts[0][1])
                continue
            lits = []
            for r0, r1 in alts:
                x = m.new_bool_var("")
                if r0 > lo:
                    m.add(s[b] - s[a] >= r0).only_enforce_if(x)
                if r1 < hi:
                    m.add(s[b] - s[a] <= r1).only_enforce_if(x)
                lits.append(x)
            m.add_exactly_one(lits)
    obj = sum(s[ids[-1]] + blocks[ids[-1]]["length"] - inst["trains"][k]["release"] for k, ids in enumerate(per_train))
    m.add(obj <= ub)
    m.minimize(obj)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_workers = workers
    t0 = time.time()
    st = solver.solve(m)
    lb = int(solver.best_objective_bound + 1e-6) if st in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None
    return {"status": solver.status_name(st), "lb": lb, "pairs": pairs, "seconds": round(time.time() - t0, 1),
            "value": int(solver.objective_value) if st in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None}


def projection(inst, seg: int, seconds: float) -> dict:
    """One capacity-1 resource: min sum(entry - ready), occupancy [entry, entry + run + H), every order allowed."""
    H = inst["headway"]
    jobs = []
    for t in inst["trains"]:
        before = 0
        for s_, p in zip(t["chain"], t["run"]):
            if s_ == seg:
                jobs.append((t["release"] + before, p))
            before += p
    m = cp_model.CpModel()
    horizon = max(r for r, _ in jobs) + sum(p + H for _, p in jobs) + 1
    xs, ivs = [], []
    for r, p in jobs:
        x = m.new_int_var(r, horizon, "")
        ivs.append(m.new_fixed_size_interval_var(x, p + H, ""))
        xs.append((x, r))
    m.add_no_overlap(ivs)
    m.minimize(sum(x - r for x, r in xs))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_workers = 1
    st = solver.solve(m)
    return {"segment": seg, "trains": len(jobs), "status": solver.status_name(st),
            "delay_lb": int(solver.best_objective_bound + 1e-6)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--ks", default="1,2,3,5,10,20,40")
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()
    inst = load_instance(DATA / args.dataset)
    work = Path(tempfile.mkdtemp())
    ub = greedy_ub(to_chain_instance(args.dataset, work / "inst.txt"), work / "greedy.csv")   # E3's own first UB
    dem = demand(inst)
    ranked = sorted(dem, key=lambda seg: -dem[seg])
    T0 = TT0[args.dataset]
    proj = projection(inst, ranked[0], args.seconds)
    rows = {"dataset": args.dataset, "TT0": T0, "greedy_ub": ub, "segments": len(ranked),
            "projection_top_segment": {**proj, "objE_lb": T0 + proj["delay_lb"]}, "relaxations": []}
    print(json.dumps(rows["projection_top_segment"]), flush=True)
    for k in [int(x) for x in args.ks.split(",")]:
        k = min(k, len(ranked))
        r = relaxation(inst, set(ranked[:k]), ub, args.seconds, args.workers)
        r.update({"K": k, "segments": ranked[:k], "demand_share": round(sum(dem[x] for x in ranked[:k]) / sum(dem.values()), 3)})
        rows["relaxations"].append(r)
        print(json.dumps({x: r.get(x) for x in ("K", "status", "lb", "value", "pairs", "seconds", "demand_share")}),
              flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{args.dataset}.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
