"""
group_supercolumn_cg.py — P1: hard-feasible GROUP supercolumn CG on native C1 instances.

Groups = connected components of the free-flow conflict graph, chunked to size <= 3 (freeze: disjoint
partition). A group SUPERCOLUMN is a jointly conflict-free schedule for all its trains (cost = sum of
deviations, occupancy = union of cells) — strong local interactions become primal-visible inside the
column, exactly the paper's thesis (jtv E3, here on real C1 corridors).

Pricing (v0, HONEST LABEL): per group, enumerate member ORDERS (<= 3! = 6); for each order schedule
members sequentially by exact priced+blocked tdsp (later members see earlier members' cells as hard
blocks). Exact for singletons; for pairs/triples it is order-exact but not full joint DP, so the
converged master value is reported as the RESTRICTED group LP (valid integer UB via the integer RMP;
LB claims only vs the individual-path LP measured on the same instance by B5).

Usage:  python group_supercolumn_cg.py <native_dir> [...] [--iters 40] [--records path]
"""
from __future__ import annotations
import os, sys, time, json, heapq, argparse
from itertools import permutations
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, LAB); sys.path.insert(0, HERE)
from record_schema import RunRecord, append_record
from priority_heuristics import load, tt_bins, build_adj
from individual_column_generation import solve_master

BIGC = 1e5
EPS = 1e-7


def tdsp_pb(tr, adj, cfg, price_pref, blocked):
    """exact tdsp: min |arr-intended| + dual-price load, with HARD blocked cells (set)."""
    T, HW, MW, SL = cfg["T"], cfg["headway"], cfg["maxwait"], cfg["slack"]
    o, d, e0, sm, intended = tr["o"], tr["dd"], tr["entry"], tr["smult"], tr["intended"]
    INF = float("inf"); best = {}; pred = {}; pq = []
    for t in range(e0, min(T, e0 + 1 + SL)):
        best[(o, t)] = 0.0; heapq.heappush(pq, (0.0, o, t))
    arr = None
    while pq:
        c, n, t = heapq.heappop(pq)
        if c > best.get((n, t), INF):
            continue
        if n == d:
            arr = t; break
        for (m, lk, ab) in adj[n]:
            ttb = tt_bins(lk, ab, sm)
            mw = MW if lk["ltype"] == 4 else 0
            pre = price_pref.get(lk["id"]) if price_pref else None
            for s in range(mw + 1):
                arrive = t + s + ttb
                if arrive + HW - 1 >= T:
                    break
                if blocked and any((lk["id"], b) in blocked for b in range(t, arrive + HW)):
                    continue
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
    return abs(arr - tr["intended"]), sorted(set(occ))


def price_group(members, trains, adj, cfg, pref):
    """best order-enumerated joint column: (total_dev, union_occ, priced_cost) or None."""
    bestcol = None
    for order in permutations(members):
        blocked = set(); dev_sum = 0.0; occ_all = []
        ok = True
        for ti in order:
            r = tdsp_pb(trains[ti], adj, cfg, pref, blocked)
            if r is None:
                ok = False; break
            dev, occ = r
            dev_sum += dev; occ_all += occ; blocked |= set(occ)
        if not ok:
            continue
        occ_all = sorted(set(occ_all))
        # priced cost of the joint column under current duals
        pc = dev_sum
        if pref:
            for (lid, b) in occ_all:
                arr = pref.get(lid)
                if arr is not None:
                    pc += arr[b + 1] - arr[b]
        if bestcol is None or pc < bestcol[2]:
            bestcol = (dev_sum, occ_all, pc)
    return bestcol


def conflict_groups(trains, adj, cfg, maxg=3):
    ff = {}
    for i, tr in enumerate(trains):
        r = tdsp_pb(tr, adj, cfg, None, None)
        ff[i] = set(r[1]) if r else set()
    n = len(trains); par = list(range(n))
    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x
    for i in range(n):
        for j in range(i + 1, n):
            if ff[i] & ff[j]:
                par[find(i)] = find(j)
    comp = {}
    for i in range(n):
        comp.setdefault(find(i), []).append(i)
    groups = []
    for c in comp.values():
        for i in range(0, len(c), maxg):
            groups.append(c[i:i + maxg])
    return groups


def run(d, instance_id, iters, records, maxg=3):
    links, trains, mow, cfg = load(d)
    adj = build_adj(links); T = cfg["T"]
    t0 = time.time()
    groups = conflict_groups(trains, adj, cfg, maxg)
    gsizes = sorted(len(g) for g in groups)
    cols, seen = [], set()
    for gi in range(len(groups)):                     # phase-1 dummy per group
        cols.append(dict(train=gi, cost=BIGC, occ=[], dummy=True))

    def add_col(gi, cost, occ):
        key = (gi, tuple(occ))
        if key in seen:
            return False
        seen.add(key); cols.append(dict(train=gi, cost=cost, occ=occ, dummy=False)); return True

    for gi, g in enumerate(groups):                   # init: entry-order joint column
        r = price_group(tuple(g), trains, adj, cfg, None)
        if r:
            add_col(gi, r[0], r[1])
    lp = None; calls = 0
    for it in range(1, iters + 1):
        ms = solve_master(cols, len(groups), {})
        if ms is None:
            raise RuntimeError("group master infeasible")
        lp, y, lam, pi = ms
        lam_arr = {}
        for (lid, b), v in lam.items():
            lam_arr.setdefault(lid, np.zeros(T))[b] += v
        pref = {lid: np.concatenate(([0.0], np.cumsum(a))) for lid, a in lam_arr.items()}
        added = 0
        for gi, g in enumerate(groups):
            calls += 1
            r = price_group(tuple(g), trains, adj, cfg, pref)
            if r and (r[2] - pi[gi] < -EPS) and add_col(gi, r[0], r[1]):
                added += 1
        if added == 0:
            break
    int_obj, _ = solve_master(cols, len(groups), {}, integer=True)
    if not np.isnan(int_obj) and int_obj >= BIGC / 2:
        int_obj = float("nan")                        # artificial column active => NOT a feasible timetable
    wall = time.time() - t0
    nreal = sum(1 for c in cols if not c.get("dummy"))
    dummy_active = lp is not None and lp >= BIGC / 2
    print(f"{instance_id}: groups={len(groups)} sizes={gsizes}  restricted group LP={lp:.2f}"
          f"{' (DUMMY ACTIVE)' if dummy_active else ''}  integer={int_obj:.1f}  "
          f"cols={nreal}  iters={it}  wall={wall:.1f}s")
    append_record(records, RunRecord(
        instance_id=instance_id, method="P1_group_supercolumn_cg",
        objective=int_obj, best_upper_bound=int_obj if not np.isnan(int_obj) else float("inf"),
        best_lower_bound=float("-inf"),           # order-enum pricer is not full joint DP: no LB claim
        feasible=not np.isnan(int_obj), optimality_proven=False,
        total_runtime=wall, columns=nreal, pricing_calls=calls, hard_conflicts=0,
        config=json.dumps({"iters": iters, "maxg": maxg}),
        notes=f"RESTRICTED group LP {lp:.2f} (order-enum pricing, groups {gsizes}); "
              f"UB valid (columns are jointly feasible)"))
    return lp, int_obj


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--iters", type=int, default=40)
    ap.add_argument("--maxg", type=int, default=3)
    ap.add_argument("--records", default=os.path.join(LAB, "results", "p1_records.csv"))
    a = ap.parse_args()
    for d in a.dirs:
        run(d, os.path.basename(d.rstrip("/\\")), a.iters, a.records, a.maxg)
