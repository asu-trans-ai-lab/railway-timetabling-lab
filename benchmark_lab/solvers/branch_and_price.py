"""
branch_and_price.py — B6: branch-and-price with pricing at EVERY node.

Branching rule: DEPARTURE-WINDOW branching (railway-native, pricing-compatible, complete):
a node holds per-train windows [lo_k, hi_k]; branching on train k at threshold theta creates
children with windows [lo, theta] and [theta+1, hi]. The pricing tdsp seeds only in-window
departures, so forbidden columns can never be regenerated; existing columns are filtered by
their recorded departure. Windows are integer and shrink strictly -> finite tree.

Node bound: column generation to convergence (exact pricing) => a TRUE lower bound for the node's
restricted problem; node LP integral => incumbent. Best-first on node bound; global LB = min over
open nodes (monotone); fathom on bound/infeasibility. Emits the common record + bnb_stats row.

Usage:  python branch_and_price.py <native_dir> [...] [--nodes 200] [--time 300] [--records path]
"""
from __future__ import annotations
import os, sys, time, json, heapq, argparse
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, LAB); sys.path.insert(0, HERE)
from record_schema import RunRecord, BnbStats, append_record
from priority_heuristics import load, build_adj, tdsp as tdsp_hard
from individual_column_generation import tdsp_priced, solve_master

EPS = 1e-7


BIGC = 1e5


class Pool:
    def __init__(self, ntrains=0):
        self.cols = []       # dict(train, cost, occ, dep, dummy)
        self.seen = set()
        for k in range(ntrains):     # phase-1 artificial column per train: keeps every master
            self.cols.append(dict(train=k, cost=BIGC, occ=[], dep=None, dummy=True))
            # feasible; exact pricing drives it out; a converged node still using one is
            # fathomed by bound (its LP value >= BIGC).

    def add(self, k, cost, occ, dep):
        key = (k, tuple(occ))
        if key in self.seen:
            return False
        self.seen.add(key)
        self.cols.append(dict(train=k, cost=cost, occ=occ, dep=dep, dummy=False))
        return True


def node_bound(pool, trains, adj, cfg, windows, max_cg_iters=60):
    """CG to convergence under departure windows. Returns (lp, y_full, allowed_idx) or None."""
    T = cfg["T"]
    nk = len(trains)
    for it in range(max_cg_iters):
        allowed = [j for j, c in enumerate(pool.cols)
                   if c["dummy"] or windows[c["train"]][0] <= c["dep"] <= windows[c["train"]][1]]
        have = {pool.cols[j]["train"] for j in allowed if not pool.cols[j]["dummy"]}
        # ensure every train has a column in-window (zero-dual pricing)
        miss_added = False
        for k in range(nk):
            if k not in have:
                r = tdsp_priced(trains[k], adj, cfg, None, windows[k][0], windows[k][1])
                if r is None:
                    return None                       # window infeasible -> fathom
                if pool.add(k, r[1], r[3], r[4]):
                    miss_added = True
        if miss_added:
            continue
        sub = [pool.cols[j] for j in allowed]
        remap = {i: pool.cols[j]["train"] for i, j in enumerate(allowed)}
        ms = solve_master(sub, nk, {})
        if ms is None:
            return None
        lp, y, lam, pi = ms
        lam_arr = {}
        for (lid, b), v in lam.items():
            lam_arr.setdefault(lid, np.zeros(T))[b] += v
        pref = {lid: np.concatenate(([0.0], np.cumsum(a))) for lid, a in lam_arr.items()}
        added = 0
        for k in range(nk):
            r = tdsp_priced(trains[k], adj, cfg, pref, windows[k][0], windows[k][1])
            if r is None:
                continue
            if r[0] - pi[k] < -EPS and pool.add(k, r[1], r[3], r[4]):
                added += 1
        if added == 0:
            return lp, y, allowed, True
    return lp, y, allowed, False                       # iter-capped: NOT a valid node bound


def node_status(y, sub):
    """Classify the node LP: ('integral', value) | ('branch', k, theta) | ('stalled', None).
    'stalled' = fractional but no train separable by departure (identical deps, different paths) —
    departure-window branching cannot split it; reported honestly, not fathomed as proven."""
    val = 0.0; integral = True
    cand = None
    bytrain = {}
    for j, c in enumerate(sub):
        bytrain.setdefault(c["train"], []).append((y[j], c))
    for k, entries in bytrain.items():
        fr = [(w, c) for (w, c) in entries if w > EPS]
        val += sum(w * c["cost"] for (w, c) in fr)
        if any(c.get("dummy") for (_, c) in fr):
            integral = False
        if any(w < 1 - 1e-6 for (w, _) in fr):
            integral = False
            real = [(w, c) for (w, c) in fr if not c.get("dummy")]
            deps = sorted({c["dep"] for (_, c) in real})
            if len(deps) > 1 and cand is None:
                cand = (k, deps[len(deps) // 2 - 1])
    if integral:
        return ("integral", val)
    if cand is not None:
        return ("branch", cand[0], cand[1])
    return ("stalled", None)


def run(d, instance_id, node_budget, time_limit, records, cg_iters=60):
    links, trains, mow, cfg = load(d)
    adj = build_adj(links)
    nk = len(trains)
    t0 = time.time()
    pool = Pool(nk)                                    # includes phase-1 dummies
    for k, tr in enumerate(trains):                    # free-flow init
        r = tdsp_priced(tr, adj, cfg, None)
        pool.add(k, r[1], r[3], r[4])
    root_win = {k: (trains[k]["entry"], trains[k]["entry"] + cfg["slack"]) for k in range(nk)}
    rb = node_bound(pool, trains, adj, cfg, root_win, cg_iters)
    if rb is None:
        raise RuntimeError("root infeasible")
    root_lp = rb[0]; root_conv = rb[3]
    UB, incumbent = float("inf"), None

    def rmp_incumbent():
        real = [c for c in pool.cols if not c["dummy"]]
        if {c["train"] for c in real} != set(range(nk)):
            return float("nan")
        iv, _ = solve_master(real, nk, {}, integer=True, time_limit=20)
        return iv

    iv = rmp_incumbent()
    if not np.isnan(iv):
        UB = iv
    if not root_conv:
        root_lp = float("-inf")                        # unconverged root: claim nothing
    heap = [(root_lp, 0, 0, root_win)]
    nodes = proc = pruned_b = pruned_inf = stalled = 0; stalled_lps = []
    best_traj, inc_traj = [], [(round(time.time() - t0, 2), UB)]
    LB = root_lp; ttf = float("nan"); depth_max = 0; nid = 0
    while heap and proc < node_budget and (time.time() - t0) < time_limit:
        bound, depth, _, windows = heapq.heappop(heap)
        LB = bound
        best_traj.append((round(time.time() - t0, 2), round(bound, 3)))
        if bound >= UB - 1e-6:
            pruned_b += 1
            LB = UB
            break                                      # best-first: everything else is >= bound
        nb = node_bound(pool, trains, adj, cfg, windows, cg_iters)
        proc += 1
        if nb is None:
            pruned_inf += 1; continue
        lp, y, allowed, conv = nb
        if not conv:
            stalled += 1; stalled_lps.append(bound); continue   # discarded subtree: parent bound kept
        if lp >= UB - 1e-6:
            pruned_b += 1; continue
        sub = [pool.cols[j] for j in allowed]
        st = node_status(y, sub)
        if st[0] == "integral":                        # integral node LP -> incumbent, fathom
            if st[1] < UB:
                UB = st[1]; incumbent = windows
                inc_traj.append((round(time.time() - t0, 2), UB))
                if np.isnan(ttf):
                    ttf = time.time() - t0
            continue
        if st[0] == "stalled":                         # departure-inseparable fractional node:
            stalled += 1; stalled_lps.append(lp)       # cannot split -> its subtree stays unresolved
            continue
        _, frac_k, theta = st
        lo, hi = windows[frac_k]
        theta = min(max(theta, lo), hi - 1)
        for w in ((lo, theta), (theta + 1, hi)):
            cw = dict(windows); cw[frac_k] = w
            nid += 1; nodes += 1
            heapq.heappush(heap, (lp, depth + 1, nid, cw))
            depth_max = max(depth_max, depth + 1)
        if proc % 25 == 0:                             # periodic incumbent from the enriched pool
            iv = rmp_incumbent()
            if not np.isnan(iv) and iv < UB:
                UB = iv
                inc_traj.append((round(time.time() - t0, 2), UB))
                if np.isnan(ttf):
                    ttf = time.time() - t0
    blocking = [v for v in stalled_lps if v < UB - 1e-6]   # unresolved subtrees below UB
    if not heap and proc < node_budget and not blocking:
        LB = UB                                        # tree exhausted, nothing unresolved -> proven
    if blocking:
        LB = min(LB, min(blocking))                    # unresolved subtrees cap the valid global LB
    wall = time.time() - t0
    proven = abs(UB - LB) < 1e-6 and not blocking
    gap = (UB - LB) / max(1.0, abs(UB)) if UB < float("inf") else float("nan")
    print(f"{instance_id}: B&P LB={LB:.2f} UB={UB:.1f} gap={100*gap:.1f}% proven={proven} "
          f"root_lp={root_lp:.2f} nodes={proc} cols={len(pool.cols)} wall={wall:.1f}s")
    append_record(records, RunRecord(
        instance_id=instance_id, method="B6_branch_and_price", objective=UB,
        best_lower_bound=LB, best_upper_bound=UB, feasible=UB < float("inf"),
        optimality_proven=proven, time_to_first_feasible=ttf, total_runtime=wall,
        nodes=proc, columns=len(pool.cols), hard_conflicts=0,
        config=json.dumps({"nodes": node_budget, "time": time_limit}),
        notes=f"departure-window branching; root LP {root_lp:.2f}; stalled nodes {stalled}"))
    append_record(os.path.join(LAB, "results", "bnb_stats.csv"), BnbStats(
        instance_id=instance_id, method="B6_branch_and_price",
        root_lower_bound=root_lp, initial_upper_bound=inc_traj[0][1],
        nodes_generated=nodes, nodes_processed=proc,
        nodes_pruned_bound=pruned_b, nodes_pruned_infeasible=pruned_inf,
        max_tree_depth=depth_max, time_to_first_feasible=ttf,
        time_to_optimum=wall if proven else float("nan"), final_gap=gap,
        best_bound_trajectory=json.dumps(best_traj[-50:]),
        incumbent_trajectory=json.dumps(inc_traj[-50:])))
    return LB, UB


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--nodes", type=int, default=400)
    ap.add_argument("--time", type=float, default=300)
    ap.add_argument("--cg-iters", type=int, default=60)
    ap.add_argument("--records", default=os.path.join(LAB, "results", "records.csv"))
    a = ap.parse_args()
    for d in a.dirs:
        run(d, os.path.basename(d.rstrip("/\\")), a.nodes, a.time, a.records, a.cg_iters)
