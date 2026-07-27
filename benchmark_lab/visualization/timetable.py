"""
timetable.py — View A (time-space timetable) + View B (resource-time occupancy) for a native instance.

Schedule source: the B0 dispatcher (rule of choice) — on C1_L4_n4_seed1 the class rule IS the proven
optimum (29), so the figure shows an optimal timetable. Positions = BFS hop distance from node 1.

Usage:  python timetable.py <native_dir> [--rule class] [--out prefix]
"""
from __future__ import annotations
import os, sys, argparse
from collections import defaultdict
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, LAB); sys.path.insert(0, os.path.join(LAB, "solvers"))
from priority_heuristics import load, build_adj, tdsp, free_flow


def schedule(d, rule="class"):
    links, trains, mow, cfg = load(d)
    adj = build_adj(links)
    n = len(trains)
    ff = {i: (free_flow(t, adj, cfg) or (10**9, []))[0] for i, t in enumerate(trains)}
    orders = {
        "fcfs": sorted(range(n), key=lambda i: trains[i]["entry"]),
        "class": sorted(range(n), key=lambda i: (-trains[i]["smult"], trains[i]["entry"])),
        "edd": sorted(range(n), key=lambda i: trains[i]["intended"]),
    }
    order = orders.get(rule, orders["fcfs"])
    T = cfg["T"]
    usage = {lk["id"]: np.zeros(T, dtype=np.int64) for lk in links}
    capd = {lk["id"]: lk["cap"] for lk in links}
    pre = {lid: np.concatenate(([0], np.cumsum((usage[lid] >= capd[lid]).astype(np.int64))))
           for lid in usage}
    scheds = {}
    for ti in order:
        r = tdsp(trains[ti], adj, cfg, pre)
        if not r:
            continue
        arr, occ, _segs = r
        scheds[ti] = (arr, sorted(set(occ)))
        for (lid, b) in set(occ):
            usage[lid][b] += 1
        for lid in {l for (l, _) in occ}:
            blk = (usage[lid] >= capd[lid]).astype(np.int64)
            pre[lid] = np.concatenate(([0], np.cumsum(blk)))
    return links, trains, cfg, scheds


def draw(d, rule, outpref):
    links, trains, cfg, scheds = schedule(d, rule)
    # node positions: BFS hops from node 1 over links
    adjn = defaultdict(set)
    for lk in links:
        adjn[lk["a"]].add(lk["b"]); adjn[lk["b"]].add(lk["a"])
    pos = {1: 0}; frontier = [1]
    while frontier:
        u = frontier.pop(0)
        for v in adjn[u]:
            if v not in pos:
                pos[v] = pos[u] + 1; frontier.append(v)
    lpos = {lk["id"]: (pos[lk["a"]], pos[lk["b"]]) for lk in links}
    obj = sum(abs(scheds[i][0] - trains[i]["intended"]) for i in scheds)
    tmax = max(b for i in scheds for (_, b) in scheds[i][1]) + 5

    # ---- View A: time-space
    fig, ax = plt.subplots(figsize=(10, 5))
    cmap = plt.get_cmap("tab10")
    for i in scheds:
        # per-link traversal segments: (enter=min bin, exit=max bin-headway+1)
        by = defaultdict(list)
        for (lid, b) in scheds[i][1]:
            by[lid].append(b)
        segs = sorted(((min(bs), max(bs) - cfg["headway"] + 1, lid) for lid, bs in by.items()))
        xs, ys = [], []
        for (t0, t1, lid) in segs:
            a, b2 = lpos[lid]
            lo, hi = (a, b2) if trains[i]["o"] == 1 or pos.get(trains[i]["o"], 0) < pos.get(trains[i]["dd"], 0) else (b2, a)
            # orient by actual travel direction along this link for this train
        # simpler: plot node-arrival polyline from segments in time order
        cur = pos.get(trains[i]["o"], 0)
        xs = [min(s[0] for s in segs)]; ys = [cur]
        for (t0, t1, lid) in segs:
            a, b2 = lpos[lid]
            nxt = b2 if abs(a - cur) < 1e-9 else a
            xs += [t0, t1]; ys += [cur, nxt]; cur = nxt
        ax.plot(xs, ys, "-o", ms=2, lw=1.4, color=cmap(i % 10),
                label=f"{trains[i]['id']} (dev {abs(scheds[i][0]-trains[i]['intended']):.0f})")
    ax.set_xlabel("time (min)"); ax.set_ylabel("station (hops from node 1)")
    ax.set_title(f"View A — time-space timetable | {os.path.basename(d)} | rule={rule} | total dev={obj:.0f}")
    ax.legend(fontsize=7, ncol=2); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(outpref + "_viewA.png", dpi=140); plt.close(fig)

    # ---- View B: resource-time occupancy
    fig, ax = plt.subplots(figsize=(10, 3 + 0.25 * len(links)))
    for row, lk in enumerate(links):
        for i in scheds:
            bs = [b for (lid, b) in scheds[i][1] if lid == lk["id"]]
            if bs:
                ax.barh(row, max(bs) - min(bs) + 1, left=min(bs), height=0.72,
                        color=plt.get_cmap("tab10")(i % 10), alpha=.8)
        ax.text(-2, row, f"L{lk['id']} {lk['a']}-{lk['b']}" + (" (sdg)" if lk["ltype"] == 4 else ""),
                ha="right", va="center", fontsize=7)
    ax.set_xlim(0, tmax); ax.set_yticks([]); ax.set_xlabel("time (min)")
    ax.set_title(f"View B — resource-time occupancy (headway included) | {os.path.basename(d)}")
    fig.tight_layout(); fig.savefig(outpref + "_viewB.png", dpi=140); plt.close(fig)
    print(f"wrote {outpref}_viewA.png / _viewB.png  (schedule dev={obj:.0f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--rule", default="class")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = a.out or os.path.join(LAB, "results", os.path.basename(a.dir.rstrip("/\\")))
    draw(a.dir, a.rule, out)
