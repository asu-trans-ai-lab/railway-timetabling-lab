"""extract_corridor.py — generalized RAS-L3 corridor -> native FastTrain instance builder.

Generalizes transcon_clovis_native/make_instance.py + make_trains.py so any scouted corridor
(see corridor_scout.py) becomes a benchmark instance in one command:

  python extract_corridor.py --name prb_joint_native --railroad BNSF,UP \\
      --src 12345 --dst 67890 --expect-miles 110 \\
      --trains-per-dir 12 --span 420 --mix Z:0.10,M:0.20,G:0.70

Pipeline: stream L3 -> Dijkstra src->dst (node ids, from the scout) -> heal 'tracks' via
fwd∪rev -> optional --trim MP_A MP_B slice -> aggregate to controlling blocks (track-count /
yard / >=10mph speed boundaries; >12-mi runs split ~10 mi; cap-1 pinches never merged away)
-> write input_node/link.csv, path_manifest.csv, FTSettings.ini, input_MOW.csv, and a peaked
freight scenario in input_train_info.csv.

Traffic realism guard: the offered combined rate is checked against the tightest cap-1
pinch's throughput ceiling (exclusive occupancy of the slowest class + headway); if offered
> 70% of ceiling the span is auto-stretched (the Belen-Clovis lesson: above the ceiling no
dispatch rule can route everything).

Known-gap handling (ANL_STATUS_REPORT): placeholder capacity ignored, posted free_speed used,
speeds direction-symmetric, capacity = min member track count clamped to [1,3].
"""
from __future__ import annotations
import csv, heapq, os, argparse

RAS_L3 = r"C:\source_codes\0_source_code_new\RAS\RAS_2026_PUBLIC\RAS_2026_PUBLIC\datasets\l3"
HERE = os.path.dirname(os.path.abspath(__file__))
CLASSES = {"Z": 1.25, "M": 1.00, "G": 0.85}
HEADWAY = 2


# ---------------------------------------------------------------- extraction
def dijkstra_path(rrs, src, dst):
    adj, rev_tracks, names = {}, {}, {}
    with open(os.path.join(RAS_L3, "node.csv"), newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            if r.get("name"):
                names[r["node_id"]] = r["name"]
    with open(os.path.join(RAS_L3, "link.csv"), newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            if r["railroad_id"] not in rrs:
                continue
            a, b = r["from_node_id"], r["to_node_id"]
            v = float(r["free_speed"]) if r["free_speed"] else 0.0
            tk = r["tracks"].strip()
            adj.setdefault(a, []).append((b, float(r["length"]), r["link_id"], v, tk))
            if tk:
                rev_tracks[(a, b)] = tk
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
    assert dst in dist, f"no path {src}->{dst} on {sorted(rrs)}"
    path, n = [], dst
    while n != src:
        pn, L, lid, v, tk = prev[n]
        if not tk:
            tk = rev_tracks.get((n, pn), "1")
        path.append(dict(l3_link_id=lid, from_node_id=pn, to_node_id=n, length=L,
                         free_speed=max(v, 5.0), tracks=tk))
        n = pn
    path.reverse()
    mp = 0.0
    for p in path:
        p["mp_start"] = mp
        mp += p["length"]
        p["mp_end"] = mp
        p["name"] = names.get(p["from_node_id"], "")
    return path


def aggregate(path, yards):
    cap = [max(1, min(3, int(float(p["tracks"] or 1)))) for p in path]

    def boundary(i):
        return (cap[i] != cap[i - 1] or path[i]["from_node_id"] in yards
                or abs(path[i]["free_speed"] - path[i - 1]["free_speed"]) >= 10)

    runs = [[0]]
    for i in range(1, len(path)):
        (runs.append([i]) if boundary(i) else runs[-1].append(i))
    blocks = []
    for run in runs:
        cur, curlen = [], 0.0
        runlen = sum(path[i]["length"] for i in run)
        target = runlen / max(1, round(runlen / 10.0)) if runlen > 12 else runlen + 1
        for i in run:
            cur.append(i)
            curlen += path[i]["length"]
            if curlen >= target - 1e-9 and i != run[-1]:
                blocks.append(cur); cur, curlen = [], 0.0
        if cur:
            blocks.append(cur)
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
                    blocks[nb] = sorted(blocks[nb] + blk); del blocks[k]; merged = True; break
            if merged:
                break
    rows = []
    for k, blk in enumerate(blocks):
        L = sum(path[i]["length"] for i in blk)
        v = L / sum(path[i]["length"] / path[i]["free_speed"] for i in blk)
        # quantize to the 2-dp precision written to input_link.csv, so freerun/intended
        # computed here match the solver's integer run times bit-for-bit (audit: baseline=0)
        rows.append(dict(seq=k, length=round(L, 2), cap=min(cap[i] for i in blk),
                         speed=round(v, 2),
                         mp_start=path[blk[0]]["mp_start"], mp_end=path[blk[-1]]["mp_end"]))
    return rows


# ---------------------------------------------------------------- traffic
def tau(L, v, m):
    return max(1, int(L * 60.0 / (v * m) + 1.0))


def build_traffic(rows, n_per_dir, span, mix):
    freerun = {c: sum(tau(r["length"], r["speed"], m) for r in rows) for c, m in CLASSES.items()}
    # pinch throughput ceiling (combined, trains/h) from slowest class on tightest cap-1 block
    pinch_occ = [tau(r["length"], r["speed"], CLASSES["G"]) + HEADWAY for r in rows if r["cap"] == 1]
    ceiling = 60.0 / max(pinch_occ) if pinch_occ else 60.0 / (HEADWAY + 1) * min(r["cap"] for r in rows)
    offered = 2.0 * n_per_dir / (span / 60.0)
    if pinch_occ and offered > 0.70 * ceiling:
        span = int(2.0 * n_per_dir / (0.70 * ceiling) * 60.0)
        print(f"  [guard] offered {offered:.1f}/h > 70% of pinch ceiling {ceiling:.1f}/h "
              f"-> span stretched to {span} min")
    # class sequence by largest remainder, then peaked slots: Z bunched fleets, M/G band
    seq = []
    quota = {c: mix[c] * n_per_dir for c in "ZMG"}
    for k in range(n_per_dir):
        c = max(quota, key=lambda x: quota[x])
        quota[c] -= 1
        seq.append(c)
    zs = [i for i, c in enumerate(seq) if c == "Z"]
    base = span / max(1, n_per_dir - 1)
    entries = []
    for i, c in enumerate(seq):
        t = i * base
        if c == "Z" and i > 0 and seq[i - 1] == "Z":
            t = entries[-1][1] + max(10, 0.55 * base)      # bunch consecutive Z into a fleet
        entries.append((c, int(t)))
    return entries, freerun, span, ceiling, offered


def write_instance(out, rows, entries, freerun, horizon):
    os.makedirs(os.path.join(out, "internal_timetable"), exist_ok=True)
    os.makedirs(os.path.join(out, "summary_log"), exist_ok=True)
    with open(os.path.join(out, "input_node.csv"), "w", newline="") as f:
        f.write("node_id\n" + "".join(f"{n}\n" for n in range(1, len(rows) + 2)))
    with open(os.path.join(out, "input_link.csv"), "w", newline="") as f:
        f.write("from,to,length,speed_ft,speed_tf,capacity,link_type,bidir\n")
        for r in rows:
            f.write(f"{r['seq']+1},{r['seq']+2},{r['length']:.2f},{r['speed']:.2f},"
                    f"{r['speed']:.2f},{r['cap']},4,1\n")
    with open(os.path.join(out, "input_MOW.csv"), "w") as f:
        f.write("from,to,start,end\n")
    n_nodes = len(rows) + 1
    lines = []
    for d, (o, dd, tag, off) in enumerate([(1, n_nodes, "EB", 0), (n_nodes, 1, "WB", 17)]):
        for k, (c, t) in enumerate(entries):
            e = t + off
            lines.append(f"{tag}{k+1:02d}{c},{o},{dd},{d+1},0,0,0,{CLASSES[c]},{e},0,0,0,{e + freerun[c]}")
    with open(os.path.join(out, "input_train_info.csv"), "w", newline="") as f:
        f.write("train_id,origin,dest,dir,TOB,length,hazmat,speed_mult,entry,"
                "cost_stop,cost_early,cost_run,intended_arrival\n" + "\n".join(lines) + "\n")
    with open(os.path.join(out, "FTSettings.ini"), "w") as f:
        f.write(f"[optimization]\nOptimizationHorizon={horizon}\nMinuteDivision=1\nMonotoneRouting=1\n[lagrangian]\n"
                f"MaxNumberOfLRIterations=15\nMinimumStepSize=0.01\nNumberOfIterationsWithMemory=5\n"
                f"MaxTrainWaitingTime=120\nMaxSlackTimeAtDeparture=120\nSafetyHeadway={HEADWAY}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--railroad", required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--expect-miles", type=float, default=None)
    ap.add_argument("--trim", nargs=2, type=float, default=None, metavar=("MP_A", "MP_B"))
    ap.add_argument("--trains-per-dir", type=int, default=12)
    ap.add_argument("--span", type=int, default=480)
    ap.add_argument("--mix", default="Z:0.45,M:0.40,G:0.15")
    a = ap.parse_args()
    mix = {k: float(v) for k, v in (p.split(":") for p in a.mix.split(","))}
    rrs = set(a.railroad.split(",")) | {"-1"}

    path = dijkstra_path(rrs, a.src, a.dst)
    if a.trim:
        path = [p for p in path if p["mp_end"] > a.trim[0] and p["mp_start"] < a.trim[1]]
        base = path[0]["mp_start"]
        for p in path:
            p["mp_start"] -= base; p["mp_end"] -= base
    total = sum(p["length"] for p in path)
    if a.expect_miles:
        assert abs(total - a.expect_miles) < 0.15 * a.expect_miles, \
            f"path {total:.1f} mi vs expected {a.expect_miles}"
    yards = {a.src, a.dst} | {p["from_node_id"] for p in path if p["name"]}
    rows = aggregate(path, yards)

    out = os.path.join(HERE, a.name)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "path_manifest.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["seq", "l3_link_id", "from_node_id", "to_node_id", "mp_start", "mp_end",
                    "length", "tracks", "free_speed", "name"])
        for i, p in enumerate(path):
            w.writerow([i, p["l3_link_id"], p["from_node_id"], p["to_node_id"],
                        f"{p['mp_start']:.3f}", f"{p['mp_end']:.3f}", f"{p['length']:.4f}",
                        p["tracks"], p["free_speed"], p["name"]])
    entries, freerun, span, ceiling, offered = build_traffic(rows, a.trains_per_dir, a.span, mix)
    horizon = ((max(e for _, e in entries) + 17 + 120 + freerun["G"] + 240) // 60 + 1) * 60
    write_instance(out, rows, entries, freerun, horizon)

    pin = [r for r in rows if r["cap"] == 1]
    print(f"{a.name}: {total:.1f} mi -> {len(rows)} blocks ({len(pin)} pinches), "
          f"{2*a.trains_per_dir} trains, span {span} min, horizon {horizon}")
    print(f"  freerun Z/M/G = {freerun['Z']}/{freerun['M']}/{freerun['G']} min; "
          f"pinch ceiling {ceiling:.1f}/h, offered {min(offered, 0.7*ceiling if pin else offered):.1f}/h")
    for r in rows:
        tag = " <- PINCH" if r["cap"] == 1 else ""
        print(f"    block {r['seq']+1:2d}  mp {r['mp_start']:6.1f}-{r['mp_end']:6.1f} "
              f"{r['length']:5.1f} mi  cap {r['cap']}  {r['speed']:4.1f} mph{tag}")


if __name__ == "__main__":
    main()
