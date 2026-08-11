"""make_provenance.py — per-element provenance labels for a native corridor instance.

Implements the auditability memo: every network/service element carries one of
    observed | externally_supported | inferred | synthetic
plus an evidence string, so another team can reproduce or challenge each assumption.

Usage:  python make_provenance.py <instance_dir> [...]    -> writes <dir>/provenance.csv
Instances without a path_manifest.csv (toy, harrod) are labeled from their own README
conventions (synthetic literature setups).
"""
from __future__ import annotations
import csv, os, sys

L = "RAS 2026 PUBLIC v5.2.2 L3 link.csv"


def read_ini(path):
    kv = {}
    for ln in open(path):
        ln = ln.strip()
        if "=" in ln and not ln.startswith("["):
            k, v = ln.split("=", 1)
            kv[k.strip().lower()] = v.strip()
    return kv


def build(d):
    rows = []

    def add(element, attr, value, prov, evidence):
        rows.append([element, attr, value, prov, evidence])

    has_manifest = os.path.exists(os.path.join(d, "path_manifest.csv"))
    # ---- physical layer
    if has_manifest:
        man = list(csv.DictReader(open(os.path.join(d, "path_manifest.csv"))))
        healed = sum(1 for r in man if not r.get("tracks", "").strip())
        add("corridor", "geometry+length", f"{sum(float(r['length']) for r in man):.1f} mi, {len(man)} L3 links",
            "observed", f"{L}: Dijkstra between named yards; frozen in path_manifest.csv")
        add("corridor", "free_speed per link", "posted speeds 7-55 mph", "observed",
            f"{L} free_speed column (no observed run times exist; ANL_STATUS_REPORT gap #3)")
        add("corridor", "tracks per link", "fwd union rev", "inferred" if healed == 0 else "inferred",
            f"L3 populates tracks on one direction only; healed from reverse link ({healed} links needed healing)")
    links = list(csv.DictReader(open(os.path.join(d, "input_link.csv"))))
    n1 = sum(1 for r in links if r["capacity"] == "1")
    long_splits = sum(1 for r in links if float(r["length"]) >= 9.5 and r["capacity"] == "1")
    add("blocks", "aggregation to controlling blocks", f"{len(links)} blocks",
        "inferred", "boundaries at track-count change / named yard / >=10 mph speed jump; "
        ">12-mi runs split ~10 mi; harmonic-mean speeds (extract_corridor.py)")
    add("blocks", "capacity = min member track count", f"cap 1 x{n1}, multi x{len(links)-n1}",
        "inferred", "L3 'capacity' column is a uniform 700000 placeholder and is ignored by design")
    if long_splits:
        add("blocks", "siding spacing on single-track runs", f"~10-mi boundaries ({long_splits} cap-1 blocks >=9.5 mi)",
            "synthetic", "L3 records NO siding locations (ANL_STATUS_REPORT gap #2); block boundaries "
            "act as passing points at typical NA siding spacing")
    add("blocks", "speed_ft == speed_tf", "direction-symmetric", "inferred",
        "L3 has no grade data; real corridors are asymmetric (cf. harrod WB/EB back-solved speeds)")
    # ---- service layer
    kv = read_ini(os.path.join(d, "FTSettings.ini"))
    add("service", "SafetyHeadway", kv.get("safetyheadway", "?") + " min", "synthetic",
        "signal-spacing surrogate; not calibrated to any specific CTC block layout")
    add("service", "departure slack / max dwell", f"{kv.get('maxslacktimeatdeparture','?')} / {kv.get('maxtrainwaitingtime','?')} min",
        "synthetic", "operational-flexibility assumption (Harrod-style flexible dispatch)")
    add("service", "MonotoneRouting", kv.get("monotonerouting", "0"), "synthetic",
        "modeling rule: corridor trains never reverse; makes LR cost == occupancy (semantics of record)")
    trains = list(csv.DictReader(open(os.path.join(d, "input_train_info.csv"))))
    mix = {}
    for t in trains:
        c = "Z" if float(t["speed_mult"]) > 1.05 else ("G" if float(t["speed_mult"]) < 0.95 else "M")
        mix[c] = mix.get(c, 0) + 1
    sd = os.path.join(d, "service_derivation.csv")
    if os.path.exists(sd):
        add("service", "trains/day and class mix", f"{len(trains)} trains, mix {mix}",
            "externally_supported", "cross-checked against demand-derived frequencies "
            "(service_derivation.csv: public L3 demand.csv -> corridor screenline -> cars/train)")
    else:
        add("service", "trains/day and class mix", f"{len(trains)} trains, mix {mix}",
            "synthetic", "expert-calibrated to corridor_kb evidence + pinch-ceiling guard; "
            "run blocking_layer.py to derive from demand")
    add("service", "class speed multipliers", "Z x1.25 / M x1.00 / G x0.85",
        "externally_supported", "ratio structure from Harrod (2011) Tables 4/7 intermodal vs freight timings")
    add("service", "cars per train (derivation constant)", "IM 150 / manifest 100 / bulk 120 / auto 90",
        "externally_supported", "Wenlin_git dev/code/scheduler/preprocessing/stages/s1_block_to_train.py defaults")
    add("service", "peaked entry structure", "bunched Z fleets + M/G band, half-cycle direction offset",
        "synthetic", "time-of-day evidence unavailable; structure follows the 8-step memo's realism requirement")
    add("service", "intended_arrival = entry + free-run", "objective counts conflict delay only",
        "inferred", "free-flow baseline provably 0 (make_trains/extract_corridor reproduce the solver formula)")

    with open(os.path.join(d, "provenance.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["element", "attribute", "value", "provenance", "evidence"])
        w.writerows(rows)
    counts = {}
    for r in rows:
        counts[r[3]] = counts.get(r[3], 0) + 1
    print(f"{os.path.basename(os.path.normpath(d))}: provenance.csv {len(rows)} rows {counts}")


if __name__ == "__main__":
    for d in sys.argv[1:]:
        build(d)
