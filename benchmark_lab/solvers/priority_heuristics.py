"""
priority_heuristics.py — B0: fast feasible-schedule generators (valid primal upper bounds).

Sequential insertion under a priority order: each train gets the residual-capacity time-dependent
shortest path (identical semantics to fasttrain/mz_lr: run time max(1,int(len*60/(v*smult)+1)),
occupancy [enter, exit+headway), dwell only on link_type-4 links inside the link, free departure
slack at the origin). Committed occupancies become hard reservations for later trains.

Rules (the directive's B0 set): fcfs (release), edd (intended arrival), minslack, class priority
(faster class first), conflict-first (most free-flow overlap first), random best-of-N. The serial
resource schedule lives in each instance's manifest (generator). B0_best = min over rules.

Usage:  python priority_heuristics.py <native_dir> [<native_dir> ...] [--rand-n 10] [--records path]
"""
from __future__ import annotations
import os, sys, csv, json, time, heapq, random, argparse
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, LAB)
from record_schema import RunRecord, append_record


# ---------------------------------------------------------------- native reading (positional)
def read_ini(path):
    kv, sec = {}, ""
    for ln in open(path):
        t = ln.strip()
        if not t or t[0] in ";#":
            continue
        if t[0] == "[":
            sec = t[1:t.find("]")].lower(); continue
        if "=" in t:
            k, v = t.split("=", 1); kv[sec + "." + k.strip().lower()] = v.strip()
    return kv


def load(d):
    links = []
    with open(os.path.join(d, "input_link.csv")) as f:
        for r in list(csv.reader(f))[1:]:
            if len(r) >= 8:
                links.append(dict(id=len(links), a=int(r[0]), b=int(r[1]), length=float(r[2]),
                                  vFT=float(r[3]), vTF=float(r[4]), cap=max(1, int(float(r[5]))),
                                  ltype=int(r[6]), bidir=int(r[7]) == 1))
    trains = []
    with open(os.path.join(d, "input_train_info.csv")) as f:
        for r in list(csv.reader(f))[1:]:
            if len(r) >= 13:
                trains.append(dict(id=r[0].strip(), o=int(r[1]), dd=int(r[2]), smult=float(r[7]),
                                   entry=int(round(float(r[8]))), intended=float(r[12])))
    mow = []
    mp = os.path.join(d, "input_MOW.csv")
    if os.path.exists(mp):
        with open(mp) as f:
            for r in list(csv.reader(f))[1:]:
                if len(r) >= 4 and r[0].strip():
                    mow.append((int(r[0]), int(r[1]), float(r[2]), float(r[3])))
    kv = read_ini(os.path.join(d, "FTSettings.ini"))
    cfg = dict(horizon=int(kv.get("optimization.optimizationhorizon", 1440)),
               minute=max(1, int(kv.get("optimization.minutedivision", 1))),
               maxwait=int(kv.get("lagrangian.maxtrainwaitingtime", 120)),
               slack=int(kv.get("lagrangian.maxslacktimeatdeparture", 1200)),
               headway=int(kv.get("lagrangian.safetyheadway", 3)))
    cfg["T"] = cfg["horizon"] * cfg["minute"]
    return links, trains, mow, cfg


def tt_bins(link, ab, sm):
    v = (link["vFT"] if ab else link["vTF"]) * max(sm, 1e-6)
    if v <= 0 or link["length"] <= 0:
        return 1
    return max(1, int(link["length"] * 60.0 / v + 1.0))


def build_adj(links):
    adj = defaultdict(list)
    for lk in links:
        adj[lk["a"]].append((lk["b"], lk, True))
        if lk["bidir"]:
            adj[lk["b"]].append((lk["a"], lk, False))
    return adj


def free_flow(tr, adj, cfg):
    """unconstrained tdsp (no reservations): free-flow arrival + occupancy footprint."""
    return tdsp(tr, adj, cfg, None)


def tdsp(tr, adj, cfg, blockedpre):
    """residual-capacity shortest path minimizing |arrival-intended|; returns (arr, occ) or None.
    blockedpre: None (free flow) or {link_id: prefix array} where prefix[b] = #blocked bins in [0,b);
    a traversal window [t, arrive+HW) is feasible iff it contains zero blocked bins (O(1) check)."""
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
            pre = blockedpre.get(lk["id"]) if blockedpre is not None else None
            for s in range(mw + 1):
                arrive = t + s + ttb; hi = arrive + HW - 1
                if hi >= T:
                    break
                if pre is not None and pre[arrive + HW] - pre[t] > 0:
                    continue
                cc = c + (abs(arrive - intended) if m == d else 0.0)
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
    return arr, occ


# ---------------------------------------------------------------- B0 dispatcher
def _prefix(blocked):
    import numpy as np
    return {lid: np.concatenate(([0], np.cumsum(arr))) for lid, arr in blocked.items()}


def dispatch(order, trains, adj, cfg, links, base_usage):
    import numpy as np
    T = cfg["T"]
    usage = {lid: arr.copy() for lid, arr in base_usage.items()}
    cap = {lk["id"]: lk["cap"] for lk in links}
    blocked = {lid: (usage[lid] >= cap[lid]).astype(np.int64) for lid in usage}
    pre = _prefix(blocked)
    total = 0.0; ok = True
    for ti in order:
        tr = trains[ti]
        r = tdsp(tr, adj, cfg, pre)
        if r is None:
            ok = False; total += 1e6; continue
        arr, occ = r
        total += abs(arr - tr["intended"])
        touched = set()
        for (lid, b) in occ:
            usage[lid][b] += 1; touched.add(lid)
        for lid in touched:                       # refresh prefixes only for changed links
            blk = (usage[lid] >= cap[lid]).astype(np.int64)
            pre[lid] = np.concatenate(([0], np.cumsum(blk)))
    return total, ok


def run_instance(d, instance_id, rand_n, records):
    import numpy as np
    links, trains, mow, cfg = load(d)
    adj = build_adj(links)
    # base usage arrays; MOW saturates capacity in its window
    base_usage = {lk["id"]: np.zeros(cfg["T"], dtype=np.int64) for lk in links}
    for (A, B, st, en) in mow:
        for lk in links:
            if {lk["a"], lk["b"]} == {A, B}:
                import math as _math
                base_usage[lk["id"]][int(st):min(cfg["T"], _math.ceil(en))] = lk["cap"]  # exclusive end (ref)
    n = len(trains)
    ff, ffocc = {}, {}
    for i, tr in enumerate(trains):
        r = free_flow(tr, adj, cfg)
        ff[i] = r[0] if r else 10**9
        ffocc[i] = set(r[1]) if r else set()
    overlap = [sum(len(ffocc[i] & ffocc[j]) for j in range(n) if j != i) for i in range(n)]
    rules = {
        "fcfs":     sorted(range(n), key=lambda i: trains[i]["entry"]),
        "edd":      sorted(range(n), key=lambda i: trains[i]["intended"]),
        "minslack": sorted(range(n), key=lambda i: trains[i]["intended"] - ff[i]),
        "class":    sorted(range(n), key=lambda i: (-trains[i]["smult"], trains[i]["entry"])),
        "conflict": sorted(range(n), key=lambda i: (-overlap[i], trains[i]["entry"])),
    }
    results = {}
    for name, order in rules.items():
        t0 = time.time()
        obj, ok = dispatch(order, trains, adj, cfg, links, base_usage)
        results[name] = (obj, ok, time.time() - t0)
    rng = random.Random(0)
    t0 = time.time(); best_r = (float("inf"), False)
    for _ in range(rand_n):
        order = list(range(n)); rng.shuffle(order)
        obj, ok = dispatch(order, trains, adj, cfg, links, base_usage)
        if obj < best_r[0]:
            best_r = (obj, ok)
    results[f"rand_best_of_{rand_n}"] = (best_r[0], best_r[1], time.time() - t0)
    best = min(v[0] for v in results.values())
    for name, (obj, ok, wall) in results.items():
        append_record(records, RunRecord(
            instance_id=instance_id, method=f"B0_{name}",
            objective=obj if ok else float("nan"),
            best_upper_bound=obj if ok else float("inf"),
            feasible=ok, total_runtime=wall, hard_conflicts=0,
            notes="sequential residual-capacity insertion" + (" (BEST rule)" if obj == best else "")))
    print(f"{instance_id}: " + "  ".join(f"{k}={v[0]:.0f}" for k, v in results.items()) +
          f"   -> B0_best={best:.0f}")
    return best


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--rand-n", type=int, default=10)
    ap.add_argument("--records", default=os.path.join(LAB, "results", "records.csv"))
    a = ap.parse_args()
    for d in a.dirs:
        run_instance(d, os.path.basename(d.rstrip("/\\")), a.rand_n, a.records)
