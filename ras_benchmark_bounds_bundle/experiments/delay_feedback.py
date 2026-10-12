"""Delay feedback to the blocking solver, iterated until the corridor delays are gone (Oct 10).

The timetable's delay is the feedback: after each blocking run every corridor's day is timetabled (E1), and the
average delay per train, d_c (minutes), raises that corridor's "equivalent miles"

    W_c(k+1) = W_c(k) + eta * d_c(k) * MILES_PER_MINUTE         (never lowered; a corridor without delay keeps W)

W_c is spread over the corridor's L3 links in proportion to their length and added to `length` in a copy of
link.csv, in both directions (the solver and the validator require a link and its reverse to have one length). The blocking solver reads `length` in every step (candidate blocks, block design, hub routing,
block routes), so the delay reaches the design, not only the routes; the solver itself is unchanged. This is the
subgradient update of a Lagrangian price on delay: where trains wait, the corridor gets dearer until they stop.

Every plan is checked by the official validator v2.0 with the ORIGINAL inputs (true lengths, official settings), so
the official cost, C6 (circuity on true miles) and C4 are what the competition would see.

    python -m experiments.delay_feedback [--eta 10] [--rounds 5] [--e1-seconds 30]  ->  results/feedback/delay_loop.json
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from experiments.blocking_feedback import (CORRIDORS, L3, OUT, WORK, day_model, delays, demand, dp_schedule,
                                           link_flows, manifest, max_wait, official_check, run_blocking)

MILES_PER_MINUTE = 1.0                   # 60 mph: one minute of waiting = one mile of detour


def corridor_delays(name: str, flow: dict, e1_seconds: float) -> dict:
    """The day's timetable (E1, DP if E1 is worse): total delay and the average delay per train by direction."""
    from experiments.mip_check import run_e1
    dem = demand(name, flow)
    model, info, _ = day_model(name, dem)
    if not model.trains:
        return {"corridor": name, "trains": 0}
    U, dp = dp_schedule(model)
    e1 = run_e1(model, e1_seconds)
    sched = e1["schedule"] if e1.get("check") == "PASS" and e1["value"] <= U else dp
    dl = delays(model, sched)
    by_dir = defaultdict(list)
    for t in model.trains:
        by_dir[t.direction].append(dl[t.train_id])
    return {"corridor": name, "trains": len(model.trains), "delay": sum(dl.values()), "max_delay": max(dl.values()),
            "wait_limit": max_wait(name), "single_track_pct": info["single_track_pct"],
            "per_train": {f"{d:+d}": sum(v) / len(v) for d, v in by_dir.items()},
            "trains_by_dir": {f"{d:+d}": len(v) for d, v in by_dir.items()}}


def all_delays(plan: Path, e1_seconds: float) -> list:
    rows = []
    for c in CORRIDORS:
        p = subprocess.run([sys.executable, "-m", "experiments.delay_feedback", "_corridor", str(plan), c,
                            str(e1_seconds)], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        rows.append(json.loads(lines[-1]) if lines else {"corridor": c, "error": p.stderr[-300:]})
    return rows


def delay_dataset(W: dict, name: str) -> Path:
    """L3 copy with length + W_cd on the corridor links (direction-specific link rows)."""
    dst = WORK / "datasets" / name
    dst.mkdir(parents=True, exist_ok=True)
    for f in L3.iterdir():
        if f.is_file() and f.name != "link.csv":
            shutil.copy(f, dst / f.name)
    with open(L3 / "link.csv", newline="") as f:
        rd = csv.DictReader(f)
        fields, links = list(rd.fieldnames), list(rd)
    by_pair = {(int(l["from_node_id"]), int(l["to_node_id"])): l for l in links}
    added = 0.0
    for c, w in W.items():
        if w <= 0:
            continue
        rows, _ = manifest(c)
        total = sum(float(r["length"]) for r in rows)
        for r in rows:
            u, v = (int(r["from_node_id"]), int(r["to_node_id"]))
            extra = w * float(r["length"]) / total
            for key in ((u, v), (v, u)):
                l = by_pair.get(key)
                if l is not None:
                    l["length"] = f"{float(l['length']) + extra:.6f}"
            added += extra
    with open(dst / "link.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        wr.writerows(links)
    return dst


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "_corridor":
        print(json.dumps(corridor_delays(sys.argv[3], link_flows(Path(sys.argv[2])), float(sys.argv[4]))))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--eta", type=float, default=10.0)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--e1-seconds", type=float, default=30)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    tag = a.tag or f"eta{a.eta:g}"
    out = OUT / f"delay_loop_{tag}.json"
    plan = WORK / "runs" / "base_l3" / "solution_result.json"
    W = defaultdict(float)
    hist = []
    for k in range(a.rounds + 1):
        t0 = time.time()
        rows = all_delays(plan, a.e1_seconds)
        off = official_check(plan)
        entry = {"round": k, "official": off, "W_miles": {c: round(w, 1) for c, w in W.items()},
                 "total_delay": sum(r.get("delay") or 0 for r in rows),
                 "corridor_delay": {r["corridor"]: r.get("delay") for r in rows if r.get("trains")},
                 "over_limit": [r["corridor"] for r in rows if r.get("trains") and r["max_delay"] > r["wait_limit"]],
                 "trains": {r["corridor"]: r.get("trains") for r in rows if r.get("trains")}}
        hist.append(entry)
        print(json.dumps({"round": k, "total_delay": entry["total_delay"], "cost": off["cost"],
                          "failed": off["failed"], "c4": off["c4_violations"],
                          "delays": {c: v for c, v in entry["corridor_delay"].items() if v},
                          "W": entry["W_miles"]}), flush=True)
        out.write_text(json.dumps(hist, indent=1))
        if k == a.rounds or entry["total_delay"] == 0:
            break
        for r in rows:                                    # the subgradient step on delay
            if r.get("trains") and r.get("delay"):
                W[r["corridor"]] += a.eta * r["delay"] / r["trains"] * MILES_PER_MINUTE
        ds = delay_dataset(W, f"delay_{tag}_r{k + 1}")
        plan = run_blocking(ds, f"delay_{tag}_r{k + 1}")
        if not plan.exists():
            hist.append({"round": k + 1, "error": "no solution_result.json"})
            out.write_text(json.dumps(hist, indent=1))
            break
        print(f"round {k + 1} blocking done in {time.time() - t0:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def repair_c6(plan: Path, out: Path) -> dict:
    """Blocks whose route is longer than max_circuitous_ratio x the TRUE shortest path (the solver planned on the
    equivalent miles) get their true shortest path back; the rest of the plan is kept. Returns counts."""
    import heapq
    import json as _json
    data = _json.load(open(plan))
    adj = defaultdict(list)
    length = {}
    with open(L3 / "link.csv", newline="") as f:
        for r in csv.DictReader(f):
            u, v, d, lid = int(r["from_node_id"]), int(r["to_node_id"]), float(r["length"]), int(r["link_id"])
            adj[u].append((v, d, lid))
            length[lid] = d
    rev = defaultdict(list)                               # the validator treats every link as two-way
    for u, lst in list(adj.items()):
        for v, d, lid in lst:
            rev[v].append((u, d, lid))
    for v, lst in rev.items():
        have = {w for w, _, _ in adj[v]}
        adj[v] += [(u, d, lid) for u, d, lid in lst if u not in have]
    ratio = 1.3
    with open(L3 / "setting.csv") as f:
        for r in csv.DictReader(f):
            if r["parameter"] == "max_circuitous_ratio":
                ratio = float(r["value"])

    def sp(o, t):
        dist, prev, pq = {o: 0.0}, {}, [(0.0, o)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == t:
                break
            if d > dist[u]:
                continue
            for v, w, lid in adj[u]:
                if d + w < dist.get(v, float("inf")):
                    dist[v], prev[v] = d + w, (u, lid)
                    heapq.heappush(pq, (d + w, v))
        nodes, links, x = [t], [], t
        while x != o:
            u, lid = prev[x]
            nodes.append(u)
            links.append(lid)
            x = u
        return dist[t], nodes[::-1], links[::-1]
    fixed = 0
    for r in data["outputs"]["3 Block Route"]:
        lids = [int(x) for x in str(r["physical_path_links"]).split(" -> ") if x.strip()]
        actual = sum(length.get(l, 0.0) for l in lids)
        d, nodes, links = sp(int(r["from_yard_id"]), int(r["to_yard_id"]))
        if actual > ratio * d + 1e-4:
            r["physical_path_nodes"] = " -> ".join(map(str, nodes))
            r["physical_path_links"] = " -> ".join(map(str, links))
            fixed += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps(data))
    return {"repaired_blocks": fixed}
