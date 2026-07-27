"""
individual_column_generation.py — B5: individual train-path column generation with a REAL pricing loop
(the piece nt_spacetime.py lacked: it enumerated columns up front; here pricing responds to duals).

Master (restricted LP, scipy/HiGHS):
    min sum c_p y_p   s.t.  sum_{p in P_k} y_p = 1 (per train k),
                            sum_p a_{cell,p} y_p <= cap(cell) (per touched (link,bin) cell),  y >= 0.
Pricing per train k (exact, fasttrain-faithful tdsp over (node,time) with dual prices on cells):
    min_p  c_p + sum_cell lambda_cell * a_{cell,p}  -  pi_k ;   add column if < -eps.
At convergence with exact pricing the master LP value is the TRUE path-LP lower bound.
Then the integer restricted master (same columns, binaries) gives a valid upper bound
(price-and-branch comes in B6; this is CG + integer RMP only).

Usage:  python individual_column_generation.py <native_dir> [...] [--iters 40] [--records path]
"""
from __future__ import annotations
import os, sys, time, json, heapq, argparse
from collections import defaultdict
import numpy as np
from scipy import sparse
from scipy.optimize import linprog, milp, LinearConstraint, Bounds

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, LAB); sys.path.insert(0, HERE)
from record_schema import RunRecord, append_record
from priority_heuristics import load, tt_bins, build_adj, dispatch, tdsp as tdsp_hard


def tdsp_priced(tr, adj, cfg, price_pref, dep_lo=None, dep_hi=None, banned=None):
    """exact tdsp minimizing |arr-intended| + sum price over occupied cells.
    price_pref: {link_id: prefix array over T+1} (None -> zero prices).
    dep_lo/dep_hi: optional departure-window restriction (B6 branching constraint).
    Returns (cost_with_prices, deviation_cost, arr, occ, dep)."""
    T, HW, MW, SL = cfg["T"], cfg["headway"], cfg["maxwait"], cfg["slack"]
    o, d, e0, sm, intended = tr["o"], tr["dd"], tr["entry"], tr["smult"], tr["intended"]
    lo = e0 if dep_lo is None else max(e0, dep_lo)
    hi = min(T - 1, e0 + SL) if dep_hi is None else min(T - 1, e0 + SL, dep_hi)
    if lo > hi:
        return None
    INF = float("inf"); best = {}; pred = {}; pq = []
    for t in range(lo, hi + 1):
        best[(o, t)] = 0.0; heapq.heappush(pq, (0.0, o, t))
    arr = None
    while pq:
        c, n, t = heapq.heappop(pq)
        if c > best.get((n, t), INF):
            continue
        if n == d:
            arr = t; break
        for (m, lk, ab) in adj[n]:
            if banned and lk["id"] in banned:
                continue
            ttb = tt_bins(lk, ab, sm)
            mw = MW if lk["ltype"] == 4 else 0
            pre = price_pref.get(lk["id"]) if price_pref else None
            for s in range(mw + 1):
                arrive = t + s + ttb; hi = arrive + HW - 1
                if hi >= T:
                    break
                dual = (pre[arrive + HW] - pre[t]) if pre is not None else 0.0
                cc = c + dual + (abs(arrive - intended) if m == d else 0.0)
                if cc < best.get((m, arrive), INF):
                    best[(m, arrive)] = cc; pred[(m, arrive)] = (n, t, lk["id"])
                    heapq.heappush(pq, (cc, m, arrive))
    if arr is None:
        return None
    occ = []; cur = (d, arr)
    while cur in pred:
        pn, pt, lid = pred[cur]
        occ += [(lid, b) for b in range(pt, cur[1] + HW) if b < T]
        cur = (pn, pt)
    dep = cur[1]                                  # origin node time = departure
    segs = []; c2 = (d, arr)
    while c2 in pred:
        pn, pt, lid = pred[c2]
        segs.append((pn, lid, pt, c2[1])); c2 = (pn, pt)
    occ = sorted(set(occ))
    return best[(d, arr)], abs(arr - tr["intended"]), arr, occ, dep, segs[::-1]


def solve_master(cols, ntrains, caps, integer=False, time_limit=120):
    """cols: list of dict(train, cost, occ). Returns (obj, y, lam{cell}, pi[k]) or (obj, y) if integer."""
    cells = sorted({c for col in cols for c in col["occ"]})
    crow = {c: i for i, c in enumerate(cells)}
    nP, nC = len(cols), len(cells)
    cost = np.array([c["cost"] for c in cols], float)
    rows, cc, vv = [], [], []
    for j, col in enumerate(cols):                         # capacity rows
        for cell in col["occ"]:
            rows.append(crow[cell]); cc.append(j); vv.append(1.0)
    Aub = sparse.csr_matrix((vv, (rows, cc)), shape=(nC, nP))
    bub = np.array([caps.get(c, 1) for c in cells], float)
    er, ec, ev = [], [], []
    for j, col in enumerate(cols):                         # convexity rows
        er.append(col["train"]); ec.append(j); ev.append(1.0)
    Aeq = sparse.csr_matrix((ev, (er, ec)), shape=(ntrains, nP))
    beq = np.ones(ntrains)
    if integer:
        A = sparse.vstack([Aub, Aeq]).tocsc()
        lc = LinearConstraint(A, np.concatenate([np.full(nC, -np.inf), beq]),
                              np.concatenate([bub, beq]))
        r = milp(c=cost, constraints=lc, integrality=np.ones(nP), bounds=Bounds(0, 1),
                 options={"time_limit": time_limit})
        return (float(r.fun) if r.status == 0 else float("nan")), r
    r = linprog(cost, A_ub=Aub, b_ub=bub, A_eq=Aeq, b_eq=beq,
                bounds=[(0, None)] * nP, method="highs")
    if not r.success:
        return None
    lam = {cells[i]: max(0.0, -float(m)) for i, m in enumerate(np.asarray(r.ineqlin.marginals))}
    # scipy/HiGHS convention: eqlin.marginals ARE the textbook convexity duals pi (verified by strong
    # duality: sum(pi) - lam^T cap == primal objective). Negating them silently kills pricing.
    pi = np.asarray(r.eqlin.marginals)
    return float(r.fun), r.x, lam, pi


def run(d, instance_id, iters, records):
    links, trains, mow, cfg = load(d)
    adj = build_adj(links); T = cfg["T"]
    import math as _math
    caps = {}                                   # only non-default caps: cap>1 links, MOW zeros
    for lk in links:
        if lk["cap"] != 1:
            for b in range(T):
                caps[(lk["id"], b)] = lk["cap"]
    mow_cells = set()
    for (A, Bn, st, en) in mow:
        for lk in links:
            if {lk["a"], lk["b"]} == {A, Bn}:
                for b in range(int(st), min(T, _math.ceil(en))):
                    caps[(lk["id"], b)] = 0; mow_cells.add((lk["id"], b))
    t0 = time.time()
    # init columns: free-flow path per train + B0-fcfs dispatched feasible set
    cols, seen = [], set()

    def add_col(k, cost, occ, segs=None):
        key = (k, tuple(occ))                   # FULL occupancy key: a truncated key false-positively
        if key in seen:                         # deduped distinct columns -> premature "convergence"
            return False                        # with an INVALID (too-high) "LP bound"
        seen.add(key); cols.append(dict(train=k, cost=cost, occ=occ, segs=segs)); return True

    base_usage = {lk["id"]: np.zeros(T, dtype=np.int64) for lk in links}
    for (lid, b) in mow_cells:
        base_usage[lid][b] = 10**6                       # saturate MOW cells for the init dispatch
    for k, tr in enumerate(trains):
        r = tdsp_priced(tr, adj, cfg, None)
        add_col(k, r[1], r[3], r[5])
    # a feasible incumbent set via sequential dispatch (fcfs)
    order = sorted(range(len(trains)), key=lambda i: trains[i]["entry"])
    pre = {lid: np.zeros(T + 1) for lid in base_usage}     # zero-blocked prefixes
    usage = {lid: arr.copy() for lid, arr in base_usage.items()}
    capd = {lk["id"]: lk["cap"] for lk in links}
    import numpy as _np
    prefixes = {lid: _np.concatenate(([0], _np.cumsum((usage[lid] >= capd[lid]).astype(_np.int64))))
                for lid in usage}
    for ti in order:
        r = tdsp_hard(trains[ti], adj, cfg, prefixes)
        if r:
            arr, occ, _segs = r
            add_col(ti, abs(arr - trains[ti]["intended"]), sorted(set(occ)), _segs)
            for (lid, b) in set(occ):
                usage[lid][b] += 1
            for lid in {l for (l, _) in occ}:
                blk = (usage[lid] >= capd[lid]).astype(_np.int64)
                prefixes[lid] = _np.concatenate(([0], _np.cumsum(blk)))
    lp_obj = None; pricing_calls = 0; it = 0
    for it in range(1, iters + 1):
        ms = solve_master(cols, len(trains), caps)
        if ms is None:
            raise RuntimeError("master LP infeasible")
        lp_obj, y, lam, pi = ms
        # price: build per-link prefix arrays of lambda
        lam_arr = {}
        for (lid, b), v in lam.items():
            lam_arr.setdefault(lid, np.zeros(T))[b] += v
        price_pref = {lid: np.concatenate(([0.0], np.cumsum(a))) for lid, a in lam_arr.items()}
        added = 0
        for k, tr in enumerate(trains):
            pricing_calls += 1
            r = tdsp_priced(tr, adj, cfg, price_pref)
            if r is None:
                continue
            cost_pr, dev, arr, occ, dep, psegs = r
            rc = cost_pr - pi[k]
            if rc < -1e-7 and add_col(k, dev, occ, psegs):
                added += 1
        if added == 0:
            break
    int_obj, ires = solve_master(cols, len(trains), caps, integer=True)
    if not np.isnan(int_obj) and getattr(ires, "x", None) is not None:   # Gate 1: export selection
        sdir = os.path.join(LAB, "results", "schedules"); os.makedirs(sdir, exist_ok=True)
        import csv as _csv
        lkmap = {lk["id"]: lk for lk in links}
        with open(os.path.join(sdir, f"{instance_id}_B5.csv"), "w", newline="") as f:
            w = _csv.writer(f); w.writerow(["instance_id","method","train_id","seq","from_node","to_node","link_id","enter_time","leave_time"])
            for j, xv in enumerate(ires.x):
                if xv > 0.5 and cols[j].get("segs"):
                    k = cols[j]["train"]
                    for si, (fn, lid, en, lv) in enumerate(cols[j]["segs"]):
                        tn = lkmap[lid]["b"] if lkmap[lid]["a"] == fn else lkmap[lid]["a"]
                        w.writerow([instance_id, "B5_integer_rmp", trains[k]["id"], si, fn, tn, lid, en, lv])
    wall = time.time() - t0
    proven_lp = added == 0 if iters else False
    print(f"{instance_id}: LP LB={lp_obj:.2f} ({'converged' if proven_lp else 'iter cap'}, "
          f"{it} iters, {len(cols)} cols)  integer RMP UB={int_obj:.1f}  wall={wall:.1f}s")
    append_record(records, RunRecord(
        instance_id=instance_id, method="B5_individual_cg", objective=int_obj,
        best_lower_bound=lp_obj if proven_lp else float("-inf"),
        best_upper_bound=int_obj, feasible=not np.isnan(int_obj),
        optimality_proven=False, total_runtime=wall,
        columns=len(cols), pricing_calls=pricing_calls, hard_conflicts=0,
        config=json.dumps({"iters": iters}),
        notes=f"exact pricing {'converged' if proven_lp else 'NOT converged'}; "
              f"LP path bound {lp_obj:.2f}; integer RMP (no branching yet = B6)"))
    return lp_obj, int_obj


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--iters", type=int, default=40)
    ap.add_argument("--records", default=os.path.join(LAB, "results", "records.csv"))
    a = ap.parse_args()
    for d in a.dirs:
        run(d, os.path.basename(d.rstrip("/\\")), a.iters, a.records)
