"""make_trains.py — traffic scenarios for the Belen-Clovis Transcon instance.

Freight-only (the Clovis Sub carries no Amtrak — the Southwest Chief runs via Raton Pass).
Three classes per corridor_kb evidence (BNSF_S_TRANSCON, ~90+ trains/day Belen-Clovis):
    Z  intermodal  speed_mult 1.25   ~45%   (the "hot" trains — priority via speed)
    M  manifest    speed_mult 1.00   ~40%
    G  grain/bulk  speed_mult 0.85   ~15%
Entries are NON-UNIFORM by design (the 8-step memo requires realistic peaks): bunched
intermodal fleets + a midday manifest/grain band, directions offset a half-cycle so meets
land on the mp 95.5-103.6 single-track pinch west of Vaughn.

intended_arrival = entry + class free-running time (solver formula reproduced exactly), so
the free-flow baseline deviation is 0 and objective = pure conflict-induced delay.

Usage:
    python make_trains.py                      # starter: 32 trains, T=720 (this directory)
    python make_trains.py --scenario fullday   # 88 trains, T=1800 -> ../transcon_clovis_native_fullday/
"""
from __future__ import annotations
import csv, os, shutil, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
CLASSES = {"Z": 1.25, "M": 1.00, "G": 0.85}


def read_links():
    links = []
    with open(os.path.join(HERE, "input_link.csv"), newline="") as f:
        for r in csv.DictReader(f):
            links.append((float(r["length"]), float(r["speed_ft"])))
    return links


def freerun(links, m):
    # fasttrain: max(1, int(L*60/(v*mult)+1)) per block
    return sum(max(1, int(L * 60.0 / (v * m) + 1.0)) for L, v in links)


def starter_entries():
    """16 trains/direction: Z-fleet (bunched), midday M/G band, second Z-fleet.

    Combined offered rate ~3.0 trains/h — peaked but below the ~4.6/h throughput
    ceiling of the mp 95.5-103.6 single-track pinch (13-min exclusive occupancy),
    so a feasible packing exists and the solvers fight over delay, not existence."""
    plan = []
    plan += [("Z", t) for t in (0, 18, 36, 54)]                       # early intermodal fleet
    band = [("M", 90), ("M", 130), ("G", 170), ("M", 210), ("M", 250),
            ("G", 290), ("M", 330), ("M", 370), ("G", 410)]           # manifest/grain band
    plan += band
    plan += [("Z", t) for t in (430, 448, 466)]                       # evening intermodal fleet
    return plan                                                        # 16 per direction


def fullday_entries():
    """44 trains/direction over 24 h: three Z fleets + two M/G bands (Z 20, M 17, G 7)."""
    plan = []
    for base in (0, 480, 960):                                        # three intermodal fleets
        n = 7 if base < 960 else 6
        plan += [("Z", base + 13 * k) for k in range(n)]
    for base, nm, ng in ((150, 9, 4), (640, 8, 3)):                   # two manifest/grain bands
        seq = ["M"] * nm + ["G"] * ng
        order = [seq[i::3] for i in range(3)]                         # interleave G among M
        flat = [c for grp in zip(*[iter(sum(order, []))] * 1) for c in grp]
        plan += [(c, base + 28 * k) for k, c in enumerate(sum(order, []))]
    return sorted(plan, key=lambda x: x[1])                           # 44 per direction


def build(scenario):
    links = read_links()
    n_nodes = len(links) + 1
    fr = {c: freerun(links, m) for c, m in CLASSES.items()}
    print("free-running minutes:", {c: fr[c] for c in "ZMG"})

    if scenario == "starter":
        out, T, plan = HERE, 960, starter_entries()
    else:
        out = os.path.normpath(os.path.join(HERE, "..", "transcon_clovis_native_fullday"))
        os.makedirs(os.path.join(out, "internal_timetable"), exist_ok=True)
        os.makedirs(os.path.join(out, "summary_log"), exist_ok=True)
        for fn in ("input_node.csv", "input_link.csv", "input_MOW.csv", "path_manifest.csv"):
            shutil.copy(os.path.join(HERE, fn), os.path.join(out, fn))
        T, plan = 1800, fullday_entries()

    rows, counts = [], {"Z": 0, "M": 0, "G": 0}
    for d, (o, dest, tag, off) in enumerate([(1, n_nodes, "EB", 0), (n_nodes, 1, "WB", 15)]):
        seq = 0
        for c, t in plan:
            seq += 1
            counts[c] += 1
            e = t + off
            rows.append(f"{tag}{seq:02d}{c},{o},{dest},{d+1},0,0,0,{CLASSES[c]},{e},0,0,0,{e + fr[c]}")

    hdr = ("train_id,origin,dest,dir,TOB,length,hazmat,speed_mult,entry,"
           "cost_stop,cost_early,cost_run,intended_arrival")
    with open(os.path.join(out, "input_train_info.csv"), "w", newline="") as f:
        f.write(hdr + "\n" + "\n".join(rows) + "\n")
    with open(os.path.join(out, "FTSettings.ini"), "w") as f:
        f.write(f"""[optimization]
OptimizationHorizon={T}
MinuteDivision=1
MonotoneRouting=1
[lagrangian]
MaxNumberOfLRIterations=15
MinimumStepSize=0.01
NumberOfIterationsWithMemory=5
MaxTrainWaitingTime=120
MaxSlackTimeAtDeparture=120
SafetyHeadway=2
""")
    print(f"{scenario}: wrote {len(rows)} trains (Z {counts['Z']}, M {counts['M']}, "
          f"G {counts['G']}) horizon {T} -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=["starter", "fullday"], default="starter")
    build(ap.parse_args().scenario)
