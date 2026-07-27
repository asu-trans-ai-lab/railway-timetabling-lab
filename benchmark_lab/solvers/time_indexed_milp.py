"""
time_indexed_milp.py — B2: time-indexed MILP on (k, i, j, t, t') transition variables (scipy/HiGHS).

Faithful to fasttrain semantics: run time max(1,int(len*60/(v*smult)+1)); a traversal entered at t with
dwell s (siding links only, inside the link) occupies cells [t, t+s+tau+headway); capacity 1 per
(link, bin); departure time chosen freely in the origin slack window; objective sum |arrival-intended|.
No waiting at intermediate nodes (matches the DP transition set).

Model reductions (RESTRICTIONS -> the MILP optimum is an upper bound on the true optimum; the
Experiment-A gate checks it EQUALS the B&B-proven optimum, which certifies both):
  * arcs only toward the destination (BFS hop-distance decreasing) — no reversals;
  * departure window capped at --pad bins after entry (default 60);
  * siding dwell capped at --smax (default 15).

Usage:  python time_indexed_milp.py <native_dir> [...] [--pad 60] [--smax 15] [--records path]
"""
from __future__ import annotations
import os, sys, time, json, argparse
from collections import defaultdict, deque
import numpy as np
from scipy import sparse
from scipy.optimize import milp, LinearConstraint, Bounds

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, LAB); sys.path.insert(0, HERE)
from record_schema import RunRecord, append_record
from priority_heuristics import load, tt_bins, build_adj


def hops_to(adj, dest):
    d = {dest: 0}; dq = deque([dest])
    while dq:
        u = dq.popleft()
        for (v, lk, ab) in adj[u]:
            if v not in d:
                d[v] = d[u] + 1; dq.append(v)
    # adj is outgoing; for corridor bidir graphs the reverse reachability is identical
    return d


def solve_instance(d, instance_id, pad, smax, records, time_limit=600):
    links, trains, mow, cfg = load(d)
    adj = build_adj(links); HW = cfg["headway"]
    import math as _math
    mow_blocked = set()
    for (A, Bn, st, en) in mow:
        for lk in links:
            if {lk["a"], lk["b"]} == {A, Bn}:
                for b in range(int(st), min(cfg["T"], _math.ceil(en))):
                    mow_blocked.add((lk["id"], b))
    linkcap = {lk["id"]: lk["cap"] for lk in links}
    t0 = time.time()
    cols = []          # (kind, payload): arc=(k, lid, occ_lo, occ_hi, to_node, arrive), src=(k,t0), snk=(k,t)
    cost = []
    conserv = defaultdict(list)   # (k, node, t) -> [(col, +1 out / -1 in)]
    capcells = defaultdict(list)  # (lid, b) -> [col]
    src_rows = defaultdict(list)  # k -> cols
    snk_rows = defaultdict(list)
    Tmax = 0
    for k, tr in enumerate(trains):
        hd = hops_to(adj, tr["dd"])
        # free-flow run length for window sizing
        ffrun = 0; n = tr["o"]
        while n != tr["dd"]:
            nxt = min(((m, lk, ab) for (m, lk, ab) in adj[n] if hd.get(m, 99) < hd.get(n, 99)),
                      key=lambda x: tt_bins(x[1], x[2], tr["smult"]))
            ffrun += tt_bins(nxt[1], nxt[2], tr["smult"]); n = nxt[0]
        lo, hi = tr["entry"], tr["entry"] + pad
        Tk = hi + ffrun + smax * 4 + HW + 2
        Tmax = max(Tmax, Tk)
        # source arcs: choose departure time
        for t in range(lo, hi + 1):
            c = len(cols); cols.append(("src", k)); cost.append(0.0)
            src_rows[k].append(c); conserv[(k, tr["o"], t)].append((c, -1))   # injects flow at (o,t)
        # traversal arcs, toward destination only
        reach_t = range(lo, Tk)
        for node in list(hd.keys()):
            for (m, lk, ab) in adj[node]:
                if hd.get(m, 99) >= hd.get(node, 99):
                    continue
                tau = tt_bins(lk, ab, tr["smult"])
                mw = min(smax, cfg["maxwait"]) if lk["ltype"] == 4 else 0
                for t in reach_t:
                    for s in range(mw + 1):
                        arrive = t + s + tau
                        if arrive + HW - 1 >= Tk:
                            break
                        if any((lk["id"], b) in mow_blocked for b in range(t, arrive + HW)):
                            continue                     # MOW zero-capacity window
                        c = len(cols); cols.append(("arc", k, lk["id"], t, arrive, m))
                        cost.append(0.0)
                        conserv[(k, node, t)].append((c, +1))
                        conserv[(k, m, arrive)].append((c, -1))
                        for b in range(t, arrive + HW):
                            capcells[(lk["id"], b)].append(c)
        # sink arcs at destination: cost |t - intended|
        for t in reach_t:
            c = len(cols); cols.append(("snk", k, t)); cost.append(abs(t - tr["intended"]))
            snk_rows[k].append(c); conserv[(k, tr["dd"], t)].append((c, +1))
    nV = len(cols)
    rows, ccols, vals, rhs_lo, rhs_hi = [], [], [], [], []
    rix = 0
    for key, ent in conserv.items():                      # flow conservation: out - in = 0
        for (c, sgn) in ent:
            rows.append(rix); ccols.append(c); vals.append(sgn)
        rhs_lo.append(0); rhs_hi.append(0); rix += 1
    for k in src_rows:                                    # one departure, one arrival per train
        for c in src_rows[k]:
            rows.append(rix); ccols.append(c); vals.append(1)
        rhs_lo.append(1); rhs_hi.append(1); rix += 1
        for c in snk_rows[k]:
            rows.append(rix); ccols.append(c); vals.append(1)
        rhs_lo.append(1); rhs_hi.append(1); rix += 1
    ncap = 0
    for (cell, cl) in capcells.items():
        if len(cl) < 2:
            continue
        for c in cl:
            rows.append(rix); ccols.append(c); vals.append(1)
        rhs_lo.append(-np.inf); rhs_hi.append(linkcap.get(cell[0], 1)); rix += 1; ncap += 1
    A = sparse.csc_matrix((vals, (rows, ccols)), shape=(rix, nV))
    lc = LinearConstraint(A, np.array(rhs_lo), np.array(rhs_hi))
    res = milp(c=np.array(cost), constraints=lc, integrality=np.ones(nV),
               bounds=Bounds(0, 1), options={"time_limit": time_limit, "disp": False})
    wall = time.time() - t0
    ok = res.status == 0
    obj = float(res.fun) if ok else float("nan")
    mipgap = float(res.mip_gap) if ok and hasattr(res, "mip_gap") else 0.0
    # RESTRICTED model (toward-dest arcs, pad, smax): its bound is NOT a valid instance LB.
    nodes = int(getattr(res, "mip_node_count", 0) or 0)
    print(f"{instance_id}: vars={nV} rows={rix} (cap {ncap})  ->  "
          f"obj={obj:.1f} proven={ok and mipgap <= 1e-9} nodes={nodes} wall={wall:.1f}s")
    append_record(records, RunRecord(
        instance_id=instance_id, method="B2_time_indexed_milp", objective=obj,
        best_lower_bound=float("-inf"), best_upper_bound=obj if ok else float("inf"),
        feasible=ok, optimality_proven=False,
        total_runtime=wall, nodes=nodes, hard_conflicts=0,
        config=json.dumps({"pad": pad, "smax": smax}),
        notes=f"HiGHS RESTRICTED model (UB only): toward-dest arcs, pad={pad}, smax={smax}; MOW enforced"))
    return obj


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--pad", type=int, default=60)
    ap.add_argument("--smax", type=int, default=15)
    ap.add_argument("--records", default=os.path.join(LAB, "results", "records.csv"))
    a = ap.parse_args()
    for d in a.dirs:
        solve_instance(d, os.path.basename(d.rstrip("/\\")), a.pad, a.smax, a.records)
