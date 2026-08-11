"""make_instance.py — BNSF Southern Transcon, Belen-Clovis (Clovis Subdivision) native instance.

Extracts the 236.6-mi Belen(10130) -> Clovis(10154) corridor from the RAS 2026 PUBLIC v5.2.2
L3 national network, aggregates the ~101 physical links into O(30) controlling blocks, and
writes the native FastTrain files (input_node.csv, input_link.csv).

Data handling per the ANL status report's known gaps:
  * L3 'capacity' column is a uniform 700000 placeholder -> IGNORED; block capacity = min
    member track count (fwd/rev union; the L3 export populates 'tracks' on one direction only).
  * No observed run times -> posted free_speed is used (populated and varied on this path,
    including the 28-39 mph Abo Canyon / Mountainair slow zone).
  * Speeds are direction-symmetric in L3 (no grade data): speed_ft = speed_tf.

Aggregation boundaries: track-count changes + named yards (Belen/Vaughn/Clovis) + speed-class
jumps >= 10 mph, then blocks > 12 mi split to ~10-mi targets. Sub-0.5-mi same-capacity slivers
merge into a neighbor; a cap-1 pinch NEVER merges into a cap-2 block.

Usage:
    python make_instance.py                  # extract from RAS L3 (writes path_manifest.csv too)
    python make_instance.py --from-manifest  # rebuild from committed path_manifest.csv (no RAS data)
"""
from __future__ import annotations
import csv, heapq, os, sys, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
RAS_L3 = r"C:\source_codes\0_source_code_new\RAS\RAS_2026_PUBLIC\RAS_2026_PUBLIC\datasets\l3"
BELEN, VAUGHN, CLOVIS = "10130", "10348", "10154"
MANIFEST = os.path.join(HERE, "path_manifest.csv")


def load_path_from_l3():
    adj = {}                                   # from -> list[(to, length, link_id, speed, tracks)]
    rev_tracks = {}                            # (from,to) -> tracks (to heal one-sided export)
    with open(os.path.join(RAS_L3, "link.csv"), newline="") as f:
        for r in csv.DictReader(f):
            if r["railroad_id"] not in ("BNSF", "-1"):
                continue
            a, b = r["from_node_id"], r["to_node_id"]
            L = float(r["length"])
            v = float(r["free_speed"]) if r["free_speed"] else 0.0
            tk = r["tracks"].strip()
            adj.setdefault(a, []).append((b, L, r["link_id"], v, tk))
            if tk:
                rev_tracks[(a, b)] = tk
    # Dijkstra Belen -> Clovis over length
    dist, prev = {BELEN: 0.0}, {}
    pq = [(0.0, BELEN)]
    while pq:
        d, n = heapq.heappop(pq)
        if n == CLOVIS:
            break
        if d > dist.get(n, 1e18):
            continue
        for (m, L, lid, v, tk) in adj.get(n, []):
            nd = d + L
            if nd < dist.get(m, 1e18):
                dist[m] = nd
                prev[m] = (n, L, lid, v, tk)
                heapq.heappush(pq, (nd, m))
    assert CLOVIS in dist, "no BNSF path Belen->Clovis in L3"
    # backtrace
    path, n = [], CLOVIS
    while n != BELEN:
        pn, L, lid, v, tk = prev[n]
        path.append(dict(l3_link_id=lid, from_node_id=pn, to_node_id=n, length=L,
                         free_speed=v, tracks=tk))
        n = pn
    path.reverse()
    # heal tracks from the reverse link where blank
    for p in path:
        if not p["tracks"]:
            p["tracks"] = rev_tracks.get((p["to_node_id"], p["from_node_id"]), "1")
    # frozen regression checks
    total = sum(p["length"] for p in path)
    on_path = {p["from_node_id"] for p in path} | {CLOVIS}
    assert abs(total - 236.6) < 2.0, f"path length {total:.1f} != ~236.6 mi"
    assert VAUGHN in on_path, "Vaughn not on path"
    assert 90 <= len(path) <= 115, f"unexpected link count {len(path)}"
    # milepost + manifest
    mp = 0.0
    with open(MANIFEST, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["seq", "l3_link_id", "from_node_id", "to_node_id",
                    "mp_start", "mp_end", "length", "tracks", "free_speed"])
        for i, p in enumerate(path):
            p["mp_start"] = mp
            mp += p["length"]
            p["mp_end"] = mp
            w.writerow([i, p["l3_link_id"], p["from_node_id"], p["to_node_id"],
                        f"{p['mp_start']:.3f}", f"{p['mp_end']:.3f}", f"{p['length']:.4f}",
                        p["tracks"], p["free_speed"]])
    print(f"path: {len(path)} links, {mp:.1f} mi, manifest written")
    return path


def load_path_from_manifest():
    path = []
    with open(MANIFEST, newline="") as f:
        for r in csv.DictReader(f):
            path.append(dict(l3_link_id=r["l3_link_id"], from_node_id=r["from_node_id"],
                             to_node_id=r["to_node_id"], length=float(r["length"]),
                             free_speed=float(r["free_speed"]), tracks=r["tracks"],
                             mp_start=float(r["mp_start"]), mp_end=float(r["mp_end"])))
    return path


def aggregate(path):
    yards = {BELEN, VAUGHN, CLOVIS}
    cap = [max(1, min(2, int(float(p["tracks"] or 1)))) for p in path]

    # boundary before link i (i.e. between link i-1 and i)?
    def boundary(i):
        if cap[i] != cap[i - 1]:
            return True
        if path[i]["from_node_id"] in yards:
            return True
        if abs(path[i]["free_speed"] - path[i - 1]["free_speed"]) >= 10:
            return True
        return False

    runs = [[0]]
    for i in range(1, len(path)):
        (runs.append([i]) if boundary(i) else runs[-1].append(i))
    # split runs > 12 mi at ~10 mi
    blocks = []
    for run in runs:
        cur, curlen = [], 0.0
        runlen = sum(path[i]["length"] for i in run)
        target = runlen / max(1, round(runlen / 10.0)) if runlen > 12 else runlen + 1
        for i in run:
            cur.append(i)
            curlen += path[i]["length"]
            if curlen >= target - 1e-9 and i != run[-1]:
                blocks.append(cur)
                cur, curlen = [], 0.0
        if cur:
            blocks.append(cur)
    # merge <0.5-mi slivers into a same-capacity neighbor (never across capacities)
    merged = True
    while merged:
        merged = False
        for k, blk in enumerate(blocks):
            L = sum(path[i]["length"] for i in blk)
            c = min(cap[i] for i in blk)
            if L >= 0.5:
                continue
            for nb in (k - 1, k + 1):
                if 0 <= nb < len(blocks) and min(cap[i] for i in blocks[nb]) == c:
                    blocks[nb] = blocks[nb] + blk if nb < k else blk + blocks[nb]
                    blocks[nb].sort()
                    del blocks[k]
                    merged = True
                    break
            if merged:
                break

    rows = []
    for k, blk in enumerate(blocks):
        L = sum(path[i]["length"] for i in blk)
        c = min(cap[i] for i in blk)
        # harmonic (length-weighted) mean speed: run time is additive in L/v
        v = L / sum(path[i]["length"] / path[i]["free_speed"] for i in blk)
        rows.append(dict(seq=k, length=L, cap=c, speed=v,
                         mp_start=path[blk[0]]["mp_start"], mp_end=path[blk[-1]]["mp_end"]))
    return rows


def write_native(rows):
    with open(os.path.join(HERE, "input_node.csv"), "w", newline="") as f:
        f.write("node_id\n")
        for n in range(1, len(rows) + 2):
            f.write(f"{n}\n")
    with open(os.path.join(HERE, "input_link.csv"), "w", newline="") as f:
        f.write("from,to,length,speed_ft,speed_tf,capacity,link_type,bidir\n")
        for r in rows:
            f.write(f"{r['seq'] + 1},{r['seq'] + 2},{r['length']:.2f},"
                    f"{r['speed']:.2f},{r['speed']:.2f},{r['cap']},4,1\n")
    # structural lint
    total = sum(r["length"] for r in rows)
    pinches = [r for r in rows if r["cap"] == 1]
    assert abs(total - 236.6) < 2.0
    assert all(20 <= r["speed"] <= 60 or r["length"] < 1 for r in rows), \
        [f"{r['speed']:.0f}" for r in rows]
    print(f"blocks: {len(rows)}  ({len(pinches)} single-track pinches)  total {total:.1f} mi")
    for r in rows:
        tag = " <- PINCH" if r["cap"] == 1 else ""
        print(f"  block {r['seq']+1:2d}  mp {r['mp_start']:6.1f}-{r['mp_end']:6.1f} "
              f"{r['length']:5.1f} mi  cap {r['cap']}  {r['speed']:4.1f} mph{tag}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-manifest", action="store_true")
    a = ap.parse_args()
    path = load_path_from_manifest() if a.from_manifest else load_path_from_l3()
    write_native(aggregate(path))
