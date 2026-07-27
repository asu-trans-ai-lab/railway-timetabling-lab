"""
cp_sat_timetabling.py — B4: CP-SAT job-shop baseline (OR-Tools), independent of our LR/DP/CG code.

Mapping (corridor families; the Experiment-A verifier):
  train k = job; each link = machine; traversal = (optional) interval with NoOverlap per link.
Faithful to fasttrain semantics: run time max(1,int(len*60/(v*smult)+1)); occupancy interval
[enter, leave + headway) where leave = enter + dwell + tau; dwell in [0, maxwait] ONLY inside
link_type-4 (siding) links; NO waiting at intermediate nodes (leave_s == enter_{s+1}); departure
chosen in the origin slack window; objective sum |arrival - intended|.

Scope v0: routes are station chains with parallel-link choice per segment (C1/C2 corridors, toy).
General network routing (RAS) needs path enumeration or arc-flow CP — later.

Usage:  python cp_sat_timetabling.py <native_dir> [...] [--time 120] [--records path]
"""
from __future__ import annotations
import os, sys, time, json, argparse
from collections import defaultdict, deque

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, LAB); sys.path.insert(0, HERE)
from record_schema import RunRecord, append_record
from priority_heuristics import load, tt_bins, build_adj
from ortools.sat.python import cp_model


def station_chain(adj, o, d):
    """BFS station path o -> d (nodes only; parallel links resolved per hop)."""
    par = {o: None}; dq = deque([o])
    while dq:
        u = dq.popleft()
        if u == d:
            break
        for (v, lk, ab) in adj[u]:
            if v not in par:
                par[v] = u; dq.append(v)
    if d not in par:
        return None
    path = [d]
    while par[path[-1]] is not None:
        path.append(par[path[-1]])
    return path[::-1]


def solve_instance(d, instance_id, time_limit, records):
    links, trains, mow, cfg = load(d)
    adj = build_adj(links); HW = cfg["headway"]
    t0 = time.time()
    m = cp_model.CpModel()
    # parallel candidates per undirected station pair
    par_links = defaultdict(list)
    for lk in links:
        par_links[frozenset((lk["a"], lk["b"]))].append(lk)
    link_ivs = defaultdict(list)      # link id -> intervals (NoOverlap machines)
    link_caps = {lk["id"]: lk["cap"] for lk in links}
    arr_devs = []
    HMAX = cfg["T"] - cfg["headway"]  # leave+HW must stay < T (reference horizon guard)
    import math as _math
    for (A, Bn, st, en) in mow:       # MOW: fixed blocking interval on the link machine
        for lk in links:
            if {lk["a"], lk["b"]} == {A, Bn}:
                lo, hi = int(st), min(cfg["T"], _math.ceil(en))
                if hi > lo:
                    link_ivs[lk["id"]].append(m.NewIntervalVar(lo, hi - lo, hi, f"mow_{lk['id']}_{lo}"))
    model_vars = {}; enters_all = []; leaves_all = []
    for k, tr in enumerate(trains):
        chain = station_chain(adj, tr["o"], tr["dd"])
        if not chain:
            raise RuntimeError(f"no route for train {tr['id']}")
        enters = [m.NewIntVar(0, HMAX, f"en_{k}_{i}") for i in range(len(chain) - 1)]
        leaves = [m.NewIntVar(0, HMAX, f"lv_{k}_{i}") for i in range(len(chain) - 1)]
        enters_all.append(enters); leaves_all.append(leaves)
        m.Add(enters[0] >= tr["entry"])
        m.Add(enters[0] <= tr["entry"] + cfg["slack"])
        for i in range(len(chain) - 1):
            u, v = chain[i], chain[i + 1]
            cands = [lk for lk in par_links[frozenset((u, v))]
                     if lk["a"] == u or lk["bidir"]]        # direction filter: one-way links forward only
            assert cands, (u, v)
            pres = []
            for lk in cands:
                ab = (lk["a"] == u)
                tau = tt_bins(lk, ab, tr["smult"])
                mw = cfg["maxwait"] if lk["ltype"] == 4 else 0
                b = m.NewBoolVar(f"use_{k}_{i}_{lk['id']}")
                model_vars[(k, i, lk["id"])] = b
                dwell = m.NewIntVar(0, mw, f"dw_{k}_{i}_{lk['id']}")
                m.Add(leaves[i] == enters[i] + dwell + tau).OnlyEnforceIf(b)
                # occupancy interval [enter, leave + HW) on this machine, present iff chosen
                size = m.NewIntVar(tau + HW, tau + mw + HW, f"sz_{k}_{i}_{lk['id']}")
                m.Add(size == dwell + tau + HW).OnlyEnforceIf(b)
                end = m.NewIntVar(0, HMAX + HW, f"end_{k}_{i}_{lk['id']}")
                iv = m.NewOptionalIntervalVar(enters[i], size, end, b, f"iv_{k}_{i}_{lk['id']}")
                link_ivs[lk["id"]].append(iv)
                pres.append(b)
            m.AddExactlyOne(pres)
            if i + 1 < len(chain) - 1:
                m.Add(enters[i + 1] == leaves[i])           # no waiting at intermediate stations
        dev = m.NewIntVar(0, HMAX, f"dev_{k}")
        diff = m.NewIntVar(-HMAX, HMAX, f"diff_{k}")
        m.Add(diff == leaves[-1] - int(tr["intended"]))
        m.AddAbsEquality(dev, diff)
        arr_devs.append(dev)
    for lid, ivs in link_ivs.items():
        if len(ivs) > 1:
            if link_caps.get(lid, 1) <= 1:
                m.AddNoOverlap(ivs)
            else:
                m.AddCumulative(ivs, [1] * len(ivs), link_caps[lid])
    m.Minimize(sum(arr_devs))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 4
    status = solver.Solve(m)
    wall = time.time() - t0
    ok = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    proven = status == cp_model.OPTIMAL
    obj = solver.ObjectiveValue() if ok else float("nan")
    lb = solver.BestObjectiveBound() if ok else float("-inf")
    print(f"{instance_id}: CP-SAT {solver.StatusName(status)}  obj={obj:.1f} LB={lb:.1f} "
          f"branches={solver.NumBranches()} wall={wall:.1f}s")
    if ok:                                            # Gate 1: export the timetable for INDEPENDENT validation
        sdir = os.path.join(LAB, "results", "schedules"); os.makedirs(sdir, exist_ok=True)
        import csv as _csv
        with open(os.path.join(sdir, f"{instance_id}_B4.csv"), "w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["instance_id", "method", "train_id", "seq", "from_node", "to_node",
                        "link_id", "enter_time", "leave_time"])
            for k, tr in enumerate(trains):
                chain = station_chain(adj, tr["o"], tr["dd"])
                for i in range(len(chain) - 1):
                    u, v = chain[i], chain[i + 1]
                    for lk in par_links[frozenset((u, v))]:
                        if lk["a"] != u and not lk["bidir"]:
                            continue
                        bvar = model_vars.get((k, i, lk["id"]))
                        if bvar is not None and solver.Value(bvar):
                            w.writerow([instance_id, "B4_cp_sat", tr["id"], i, u, v, lk["id"],
                                        solver.Value(enters_all[k][i]), solver.Value(leaves_all[k][i])])
    append_record(records, RunRecord(
        instance_id=instance_id, method="B4_cp_sat", objective=obj,
        best_lower_bound=lb, best_upper_bound=obj if ok else float("inf"),
        feasible=ok, optimality_proven=proven, total_runtime=wall,
        nodes=int(solver.NumBranches()), hard_conflicts=0,
        config=json.dumps({"time_limit": time_limit}),
        notes="OR-Tools CP-SAT job-shop; NoOverlap per link; corridor-chain routing"))
    return obj


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--time", type=float, default=120)
    ap.add_argument("--records", default=os.path.join(LAB, "results", "records.csv"))
    a = ap.parse_args()
    for d in a.dirs:
        solve_instance(d, os.path.basename(d.rstrip("/\\")), a.time, a.records)
