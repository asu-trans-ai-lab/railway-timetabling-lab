"""blocking_layer.py — simplified blocking/service derivation for a corridor instance.

Implements the memo's chain at corridor granularity:
    commodity demand (public L3 demand.csv, cars/70-day, block_type)
      -> corridor screenline assignment (two single-source Dijkstras from the anchors)
      -> cars/70d by direction x block_type
      -> cars-per-train (Wenlin s1_block_to_train class constants)
      -> DERIVED trains/day/direction/class.

Screenline rule: yard y is "beyond A" iff dist(B,y) >= dist(A,y) + d_AB*(1-tol) (its path
to B runs through the corridor); symmetric for B; "on-corridor" iff
dist(A,y)+dist(B,y) <= d_AB*(1+tol). A demand o->d is assigned to the corridor if o is
beyond-A/on and d is beyond-B/on (direction A->B), or the mirror (B->A).

Outputs (into the instance dir):
  service_derivation.csv  direction,block_type,class,cars_70d,cars_per_train,trains_per_70d,trains_per_day
  block_summary.csv       top corridor-crossing OD flows (the simplified blocking evidence)

Usage: python blocking_layer.py <instance_dir> [...]
"""
from __future__ import annotations
import csv, heapq, os, sys

RAS_L3 = r"C:\source_codes\0_source_code_new\RAS\RAS_2026_PUBLIC\RAS_2026_PUBLIC\datasets\l3"
TOL = 0.02
# class map + cars/train: externally supported (Wenlin s1_block_to_train defaults)
CPT = {"Intermodal": ("Z", 150), "Merchandise": ("M", 100), "Automobile": ("M", 90),
       "Grain": ("G", 120), "Coal": ("G", 120)}
INSTANCE_RR = {  # railroad filter used at extraction time (extract_corridor.py args)
    "transcon_clovis_native": "BNSF", "transcon_clovis_native_fullday": "BNSF",
    "hiline_native": "BNSF", "gulf_native": "BNSF", "panhandle_native": "BNSF",
    "prb_joint_native": "BNSF,UP", "overland_native": "UP", "moffat_native": "UP",
    "cpkc_midcon_native": "KCS,CPRS", "cn_icmain_native": "CN", "pocahontas_native": "NS",
}


def sssp(adj, src):
    dist, pq = {src: 0.0}, [(0.0, src)]
    while pq:
        d, n = heapq.heappop(pq)
        if d > dist.get(n, 1e18):
            continue
        for m, L in adj.get(n, []):
            nd = d + L
            if nd < dist.get(m, 1e18):
                dist[m] = nd
                heapq.heappush(pq, (nd, m))
    return dist


def derive(d):
    name = os.path.basename(os.path.normpath(d))
    rrs = set(INSTANCE_RR[name].split(",")) | {"-1"}
    man = list(csv.DictReader(open(os.path.join(d, "path_manifest.csv"))))
    A, B = man[0]["from_node_id"], man[-1]["to_node_id"]
    # undirected distance graph over the same railroad subnetwork used at extraction
    adj = {}
    with open(os.path.join(RAS_L3, "link.csv"), newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            if r["railroad_id"] not in rrs:
                continue
            a, b, L = r["from_node_id"], r["to_node_id"], float(r["length"])
            adj.setdefault(a, []).append((b, L))
            adj.setdefault(b, []).append((a, L))
    dA, dB = sssp(adj, A), sssp(adj, B)
    dAB = dA.get(B)
    assert dAB, f"{name}: anchors not connected"

    def side(y):
        a, b = dA.get(y), dB.get(y)
        if a is None or b is None:
            return None                        # yard not on this railroad subnetwork
        if a + b <= dAB * (1 + TOL) + 5:
            return "on"
        if b >= a + dAB * (1 - TOL) - 5:
            return "A"                         # beyond A: reaches B through the corridor
        if a >= b + dAB * (1 - TOL) - 5:
            return "B"
        return None                            # bypasses the corridor

    agg, ods = {}, []
    with open(os.path.join(RAS_L3, "demand.csv"), newline="") as f:
        for r in csv.DictReader(f):
            so, sd_ = side(r["origin_yard_id"]), side(r["dest_yard_id"])
            if so is None or sd_ is None or so == sd_ == "on" or {so, sd_} == {"on"}:
                pass
            direc = None
            if so in ("A", "on") and sd_ == "B" or (so == "A" and sd_ == "on"):
                direc = "EB"                   # A -> B orientation
            elif so in ("B", "on") and sd_ == "A" or (so == "B" and sd_ == "on"):
                direc = "WB"
            if direc is None:
                continue
            v, bt = float(r["volume"]), r["block_type"]
            k = (direc, bt)
            agg[k] = agg.get(k, 0.0) + v
            ods.append((v, direc, bt, r["origin_yard_id"], r["dest_yard_id"]))

    with open(os.path.join(d, "service_derivation.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["direction", "block_type", "class", "cars_70d", "cars_per_train",
                    "trains_per_70d", "trains_per_day"])
        tot = {"EB": 0.0, "WB": 0.0}
        for (direc, bt), cars in sorted(agg.items()):
            cls, cpt = CPT[bt]
            t70 = cars / cpt
            w.writerow([direc, bt, cls, int(cars), cpt, f"{t70:.1f}", f"{t70/70:.2f}"])
            tot[direc] += t70 / 70
        for direc in ("EB", "WB"):
            w.writerow([direc, "TOTAL", "", "", "", "", f"{tot[direc]:.2f}"])
    ods.sort(reverse=True)
    with open(os.path.join(d, "block_summary.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "cars_70d", "direction", "block_type", "origin_yard_id", "dest_yard_id"])
        for i, (v, direc, bt, o, dd) in enumerate(ods[:25]):
            w.writerow([i + 1, int(v), direc, bt, o, dd])
    print(f"{name}: screenline {A}<->{B} d_AB={dAB:.0f} mi; derived {tot['EB']:.1f} EB + "
          f"{tot['WB']:.1f} WB trains/day from {len(ods)} OD flows")
    return tot


if __name__ == "__main__":
    for d in sys.argv[1:]:
        derive(d)
