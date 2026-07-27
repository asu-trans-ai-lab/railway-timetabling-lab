"""
Meng & Zhou (2014) Fast-Train FAITHFUL reproduction (aligned to the original C++ source in
.../FastTrain/FastTrain/: FastTrain.cpp, ShortestPath.cpp, Timetable.cpp, Network.h).

Exact conventions (verified against the C++):
  * time step = 1 min (MinuteDivision=1), horizon = 1440 steps.
  * link run time  tt = max(1, int( length*60/(speed_dir*mult) + 1.0 ))   [their +1 truncation].
  * OBJECTIVE = total absolute ARRIVAL DEVIATION  sum_f |arrival_f - intended_f|.
    (run/stop costs in the CSV are READ but commented out in their SP; not used.)
  * per-train SP cost = sum of Lagrangian resource prices along the path  +  |arrival-intended|
    added only on the destination arc.  NO run/stop cost.
  * free departure slack: origin label 0 for t in [entry, entry+1+1200]  (depart >= entry, free).
  * dwell allowed ONLY on siding links (link_type==4), up to MaxTrainWaitingTime=120; other links 0.
  * headway=3: a traversal occupies link cells [enter, arrive+headway); priced window [enter, arrive+headway-1].
  * LR: dualize per-(link,time) capacity; step = max(0.01, 1/(k+1)); price += step*(usage-cap), clamp>=0;
        relax-and-cut: zero a price unused for >5 iters (cap>0); cap==0 (MOW) -> price=MAX(=1e6).
        LB = max_k [ sum_f min_cost_f  -  sum_{cells, price<=1e4} price ].
  * UB = priority-rule (rank by deviation ratio asc) sequential schedule on residual capacity;
        UB = sum_f |arrival - intended|.

Reproduction target (their summary.xlsx): LB ~ 1923, best UB (all paths) ~ 3471.

Run:  python mz_lr.py --iters 40
"""
from __future__ import annotations
import os, argparse, heapq, math
from collections import defaultdict
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_MZ_TAIL = os.path.join("Meng_Zhou_RAS_network_timetabling", "Fast-train_for_train_timetabling-master",
                        "Fast-train_for_train_timetabling-master", "PublicReleaseFastTrain", "Public")
# first existing candidate: handoff-local native data, then the Meng-Zhou tree at either folder depth
ROOT = next((p for p in (os.path.normpath(os.path.join(_HERE, rel)) for rel in
             (os.path.join("..", "data", "RAS_set3_native"),
              os.path.join("..", _MZ_TAIL), os.path.join("..", "..", _MZ_TAIL),
              os.path.join("..", "..", "..", _MZ_TAIL)))
             if os.path.isdir(p)), _HERE)
SLACK = 1200       # free departure slack (min)
MAXWAIT = 120      # siding dwell cap
HEADWAY = 3
MAXP = 1e6         # MAX_SPLABEL (blocked cell price)


def _col(df, prefix):
    for c in df.columns:
        if c.strip().lower().startswith(prefix.lower()):
            return c
    raise KeyError(prefix)


def load(d=ROOT):
    L = pd.read_csv(os.path.join(d, "input_link.csv"))
    c = {k: _col(L, k) for k in ["from_node", "to_node", "length", "speed_limit_in_mph_FT",
                                 "speed_limit_in_mph_TF", "link_capacity", "link_type", "bidirectional"]}
    links = [dict(id=i, a=int(r[c["from_node"]]), b=int(r[c["to_node"]]), length=float(r[c["length"]]),
                  vFT=float(r[c["speed_limit_in_mph_FT"]]), vTF=float(r[c["speed_limit_in_mph_TF"]]),
                  cap=max(1, int(float(r[c["link_capacity"]]))), ltype=int(r[c["link_type"]]),
                  bidir=int(r[c["bidirectional"]]) == 1) for i, r in L.iterrows()]
    T = pd.read_csv(os.path.join(d, "input_train_info.csv"))
    tc = {k: _col(T, k) for k in ["train", "origin", "destination", "speed multiplier",
                                  "entry time", "intended arrival"]}
    trains = [dict(id=str(r[tc["train"]]), o=int(r[tc["origin"]]), dd=int(r[tc["destination"]]),
                   smult=float(r[tc["speed multiplier"]]), entry=int(round(float(r[tc["entry time"]]))),
                   intended=float(r[tc["intended arrival"]])) for _, r in T.iterrows()]
    mow = []
    mp = os.path.join(d, "input_MOW.csv")
    if os.path.exists(mp) and os.path.getsize(mp) > 5:
        for _, r in pd.read_csv(mp).iterrows():
            mow.append((int(r["A_node_id"]), int(r["B_node_id"]),
                        float(r["start_time_in_min"]), float(r["end_time_in_min"])))
    return links, trains, mow


def tt_bins(link, a_to_b, sm):
    v = (link["vFT"] if a_to_b else link["vTF"]) * max(sm, 1e-6)
    if v <= 0 or link["length"] <= 0:
        return 1
    return max(1, int(link["length"] * 60.0 / v + 1.0))      # their max(1,int(a+1))


def build_adj(links):
    adj = defaultdict(list)
    for lk in links:
        adj[lk["a"]].append((lk["b"], lk, True))
        if lk["bidir"]:
            adj[lk["b"]].append((lk["a"], lk, False))
    return adj


def tdsp(tr, adj, links, T, price_pref, price, cap_arr, link_row, residual=None):
    """time-dependent SP: min  sum price along path + |arrival-intended|.
    price_pref[lr] = prefix sums of price[lr] (len T+1) for O(1) window sums.
    Returns (cost, arrival, occ=[(lid,bin)...]) or None."""
    o, d = tr["o"], tr["dd"]; e0 = tr["entry"]; intended = tr["intended"]; sm = tr["smult"]
    INF = float("inf"); best = {}; pred = {}; pq = []
    for t in range(e0, min(T, e0 + 1 + SLACK)):
        best[(o, t)] = 0.0; heapq.heappush(pq, (0.0, o, t))
    arr = None
    while pq:
        cterm = heapq.heappop(pq); c, n, t = cterm
        if c > best.get((n, t), INF):
            continue
        if n == d:
            arr = t; break
        for (m, lk, ab) in adj[n]:
            lr = link_row[lk["id"]]; ttb = tt_bins(lk, ab, sm)
            mw = MAXWAIT if lk["ltype"] == 4 else 0
            for s in range(0, mw + 1):
                arrive = t + s + ttb
                hi = arrive + HEADWAY - 1
                if hi >= T:
                    break
                if residual is not None:                          # UB: hard residual capacity
                    if any(residual.get((lk["id"], b), cap_arr[lr, b]) <= 0 for b in range(t, arrive + HEADWAY)):
                        continue
                dual = price_pref[lr][hi + 1] - price_pref[lr][t]   # sum price[t..hi]
                cc = c + dual + (abs(arrive - intended) if m == d else 0.0)
                if cc < best.get((m, arrive), INF):
                    best[(m, arrive)] = cc; pred[(m, arrive)] = (n, t, lk["id"])
                    heapq.heappush(pq, (cc, m, arrive))
    if arr is None:
        return None
    cost = best[(d, arr)]; occ = []; cur = (d, arr)
    while cur in pred:
        pn, pt, lid = pred[cur]
        for b in range(pt, cur[1] + HEADWAY):
            if b < T:
                occ.append((lid, b))
        cur = (pn, pt)
    return cost, arr, occ


def prefix(price):
    return np.concatenate([np.zeros((price.shape[0], 1)), np.cumsum(price, axis=1)], axis=1)


def solve(d=ROOT, iters=40, horizon=1440, verbose=True):
    links, trains, mow = load(d); adj = build_adj(links)
    nL = len(links); T = horizon; lrow = {lk["id"]: i for i, lk in enumerate(links)}
    cap = np.ones((nL, T))
    npl = defaultdict(list)
    for lk in links:
        npl[(lk["a"], lk["b"])].append(lk["id"]); npl[(lk["b"], lk["a"])].append(lk["id"])
    for (a, b, st, en) in mow:
        for lid in npl.get((a, b), []):
            cap[lrow[lid], int(st):int(math.ceil(en))] = 0.0
    price = np.where(cap <= 0, MAXP, 0.0)
    last_use = np.zeros((nL, T), int)
    best_lb, best_ub = -1e18, 1e18

    # free-flow (iter-0) deviation = LB baseline
    pf = prefix(price)
    ff_dev = 0.0; ff_arr = {}
    for tr in trains:
        r = tdsp(tr, adj, links, T, pf, price, cap, lrow)
        ff_dev += r[0] if r else 1e6; ff_arr[tr["id"]] = r[1] if r else None
    if verbose:
        print(f"\n{'='*68}\n  MENG-ZHOU FAST-TRAIN (faithful)  links={nL} trains={len(trains)} "
              f"horizon={T} headway={HEADWAY}")
        print(f"  free-flow total arrival-deviation (iter-0 LB) = {ff_dev:.0f}   (target LB~1923)")
        print(f"{'='*68}\n  {'iter':>4} {'LB':>9} {'UB':>9} {'gap%':>7} {'maxviol':>8} {'step':>7}")

    for k in range(iters):
        pf = prefix(price)
        usage = np.zeros((nL, T)); trip = 0.0
        for tr in trains:
            r = tdsp(tr, adj, links, T, pf, price, cap, lrow)
            if r is None:
                trip += MAXP; continue
            cost, a, occ = r; trip += cost
            seen = set()
            for (lid, b) in occ:
                if (lid, b) not in seen:
                    seen.add((lid, b)); usage[lrow[lid], b] += 1
        lb = trip - float(price[price <= 1e4].sum())
        best_lb = max(best_lb, lb)
        viol = usage - cap; maxviol = float(viol.max())
        ub = priority_ub(trains, adj, links, T, cap, lrow, ff_arr)
        best_ub = min(best_ub, ub)
        # update last_use where used
        used = usage > 0; last_use[used] = k
        # subgradient price update (harmonic step, relax-and-cut)
        step = max(0.01, 1.0 / (k + 1.0))
        price = price + step * viol
        price = np.maximum(price, 0.0)
        stale = (cap > 0) & ((k - last_use) > 5)
        price[stale] = 0.0
        price[cap <= 0] = MAXP
        if verbose:
            gap = 100 * (best_ub - best_lb) / best_ub if best_ub < 1e17 else float("inf")
            print(f"  {k:>4} {best_lb:>9.0f} {best_ub:>9.0f} {gap:>7.1f} {maxviol:>8.0f} {step:>7.3f}")
    if verbose:
        print(f"\n  BEST  LB={best_lb:.0f}  UB={best_ub:.0f}  gap={100*(best_ub-best_lb)/best_ub:.1f}% "
              f"(target LB~1923, UB~3471)")
    return dict(lb=best_lb, ub=best_ub, ff=ff_dev, nL=nL, nt=len(trains))


def priority_ub(trains, adj, links, T, cap, lrow, ff_arr):
    nL = len(links)
    zpref = np.concatenate([np.zeros((nL, 1)), np.zeros((nL, T))], axis=1)  # price=0 for UB SP
    zero = np.zeros((nL, T))
    order = sorted(trains, key=lambda t: -(abs((ff_arr[t["id"]] or 0) - t["intended"])))
    residual = {}; total = 0.0
    for tr in order:
        r = tdsp(tr, adj, links, T, zpref, zero, cap, lrow, residual=residual)
        if r is None:
            total += 1e6; continue
        cost, a, occ = r
        total += abs(a - tr["intended"])
        for (lid, b) in occ:
            residual[(lid, b)] = residual.get((lid, b), cap[lrow[lid], b]) - 1
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=ROOT)
    ap.add_argument("--iters", type=int, default=40)
    ap.add_argument("--horizon", type=int, default=1440)
    args = ap.parse_args()
    solve(args.dir, iters=args.iters, horizon=args.horizon)
