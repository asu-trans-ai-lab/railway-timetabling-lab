"""corridor_scout.py — probe the RAS 2026 PUBLIC L3 network for extractable corridor slices.

Read-only reconnaissance used to pick anchor yards for new native instances (see
transcon_clovis_native/make_instance.py for the extraction that follows a scout).

Usage:
  python corridor_scout.py --list PATTERN [--railroad RR]     # find yards by name substring
  python corridor_scout.py --railroad BNSF --from BELEN --to CLOVIS   # scout a path
Options: --railroad accepts a comma list (\"-1\" = shared/unknown always included).
Anchors match on node name substring (case-insensitive); yard nodes preferred, ambiguity listed.

Report: total miles / link count, track-profile runs (tracks x miles), single-track share,
pinch inventory (cap-1 runs with mileposts), speed stats, named yards passed in order.
"""
from __future__ import annotations
import csv, heapq, argparse, sys

RAS_L3 = r"C:\source_codes\0_source_code_new\RAS\RAS_2026_PUBLIC\RAS_2026_PUBLIC\datasets\l3"


def load_nodes():
    nodes = {}
    with open(RAS_L3 + r"\node.csv", newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            nodes[r["node_id"]] = r
    return nodes


def find(nodes, pat, rr=None):
    pat = pat.upper()
    out = []
    for nid, r in nodes.items():
        if pat in (r.get("name") or "").upper():
            if rr and r.get("railroad_id") not in rr and r.get("node_type") == "yard":
                pass  # keep — interchange yards may carry another RR id
            out.append((nid, r.get("name"), r.get("node_type"), r.get("railroad_id")))
    return out


def build_graph(rrs):
    adj, rev_tracks = {}, {}
    with open(RAS_L3 + r"\link.csv", newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            if r["railroad_id"] not in rrs:
                continue
            a, b = r["from_node_id"], r["to_node_id"]
            v = float(r["free_speed"]) if r["free_speed"] else 0.0
            tk = r["tracks"].strip()
            adj.setdefault(a, []).append((b, float(r["length"]), r["link_id"], v, tk))
            if tk:
                rev_tracks[(a, b)] = tk
    return adj, rev_tracks


def scout(adj, rev_tracks, nodes, src, dst):
    dist, prev, pq = {src: 0.0}, {}, [(0.0, src)]
    while pq:
        d, n = heapq.heappop(pq)
        if n == dst:
            break
        if d > dist.get(n, 1e18):
            continue
        for (m, L, lid, v, tk) in adj.get(n, []):
            nd = d + L
            if nd < dist.get(m, 1e18):
                dist[m] = nd
                prev[m] = (n, L, lid, v, tk)
                heapq.heappush(pq, (nd, m))
    if dst not in dist:
        return None
    path, n = [], dst
    while n != src:
        pn, L, lid, v, tk = prev[n]
        if not tk:
            tk = rev_tracks.get((n, pn), "1")
        path.append((pn, n, L, v, tk))
        n = pn
    path.reverse()
    return path


def report(path, nodes):
    total = sum(p[2] for p in path)
    cap = [max(1, min(3, int(float(p[4] or 1)))) for p in path]
    speeds = sorted(p[3] for p in path)
    print(f"  path: {len(path)} links, {total:.1f} mi, "
          f"speed min/med/max = {speeds[0]:.0f}/{speeds[len(speeds)//2]:.0f}/{speeds[-1]:.0f} mph")
    # track-profile runs
    runs, mp = [], 0.0
    for c, p in zip(cap, path):
        if runs and runs[-1][0] == c:
            runs[-1][2] += p[2]
        else:
            runs.append([c, mp, p[2]])
        mp += p[2]
    single = sum(r[2] for r in runs if r[0] == 1)
    print(f"  track profile ({len(runs)} runs, single-track {single:.1f} mi = {100*single/total:.0f}%):")
    for c, s, L in runs:
        tag = "  <- PINCH" if c == 1 and L <= 15 else ""
        print(f"    {c}-track  mp {s:7.1f}-{s+L:7.1f}  {L:6.1f} mi{tag}")
    named = [(p[0], nodes[p[0]].get("name")) for p in path if nodes.get(p[0], {}).get("name")]
    if named:
        print("  yards passed:", " -> ".join(f"{nm}({nid})" for nid, nm in named[:14]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--railroad", default="BNSF")
    ap.add_argument("--list")
    ap.add_argument("--from", dest="src")
    ap.add_argument("--to", dest="dst")
    a = ap.parse_args()
    rrs = set(a.railroad.split(",")) | {"-1"}
    nodes = load_nodes()
    if a.list:
        for nid, nm, ty, rr in sorted(find(nodes, a.list), key=lambda x: x[1] or ""):
            print(f"  {nid:>8}  {ty:12} {rr:8} {nm}")
        sys.exit(0)
    def pick(pat):
        c = find(nodes, pat)
        yards = [x for x in c if x[2] == "yard"] or c
        if not yards:
            print(f"no node matching '{pat}'"); sys.exit(1)
        if len(yards) > 1:
            print(f"  (ambiguous '{pat}': {[(y[0], y[1]) for y in yards[:6]]} — using first)")
        return yards[0][0]
    src, dst = pick(a.src), pick(a.dst)
    print(f"scout {a.src}({src}) -> {a.dst}({dst}) on {sorted(rrs)}")
    adj, rev = build_graph(rrs)
    p = scout(adj, rev, nodes, src, dst)
    print("  NO PATH" if p is None else "", end="")
    if p:
        report(p, nodes)
