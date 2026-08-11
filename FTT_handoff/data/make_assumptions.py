"""make_assumptions.py — build INPUT_ASSUMPTIONS.md, the explicit input-assumption ledger.

One row per corridor: assumed service level (from the scenario file) vs demand-derived
service (blocking_layer.py), class mixes, and the evidence class for the frequency
assumption. Implements the auditability memo's requirement that no train count is an
undocumented "convenient computational" choice.

Usage: python make_assumptions.py           (reads every instance dir with a service_derivation.csv)
"""
from __future__ import annotations
import csv, os

HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE = {  # frequency evidence class per corridor (beyond the demand derivation)
    "transcon_clovis_native": "corridor_kb: '~90+ trains/day Belen-Clovis' (trains.com-sourced); Harrod (2011) class timings",
    "transcon_clovis_native_fullday": "same as starter; 88 trains ~ the 90+/day headline (deliberate stress case)",
    "hiline_native": "synthetic: no published count found; rate set to ~70% of single-track meet capacity",
    "prb_joint_native": "corridor_kb: Joint Line triple-track coal corridor; mix set coal-heavy (G 75%)",
    "gulf_native": "synthetic: low-density secondary main; deliberate OPEN stress case at ~45% ceiling",
    "panhandle_native": "corridor_kb: Transcon 'almost entirely double-tracked'; volume scaled from Belen-Clovis",
    "overland_native": "corridor_kb: 'high-capacity double/triple-track intermodal spine'; intermodal-heavy mix",
    "moffat_native": "synthetic: mountain single track, sparse service; deliberate OPEN stress case",
    "cpkc_midcon_native": "synthetic: single-line KC-Gulf spine; deliberate OPEN stress case",
    "cn_icmain_native": "corridor_kb: IC main Chicago-New Orleans spine; mixed traffic",
    "pocahontas_native": "corridor_kb: ex-N&W coal network; mix set coal-heavy",
}


def mixstr(m, tot):
    return "/".join(f"{c}:{m.get(c,0)/tot:.0%}" for c in "ZMG") if tot else "-"


rows = []
for name in sorted(EVIDENCE):
    d = os.path.join(HERE, name)
    sd = os.path.join(d, "service_derivation.csv")
    if not os.path.isdir(d):
        continue
    trains = list(csv.DictReader(open(os.path.join(d, "input_train_info.csv"))))
    amix = {}
    for t in trains:
        m = float(t["speed_mult"])
        c = "Z" if m > 1.05 else ("G" if m < 0.95 else "M")
        amix[c] = amix.get(c, 0) + 1
    span = max(float(t["entry"]) for t in trains) - min(float(t["entry"]) for t in trains) or 1
    aday = len(trains) / (span / 1440.0)
    dday, dmix = None, {}
    if os.path.exists(sd):
        for r in csv.DictReader(open(sd)):
            if r["block_type"] == "TOTAL":
                dday = (dday or 0.0) + float(r["trains_per_day"])
            else:
                dmix[r["class"]] = dmix.get(r["class"], 0.0) + float(r["trains_per_70d"])
    rows.append((name, len(trains), f"{aday:.0f}", mixstr(amix, len(trains)),
                 f"{dday:.1f}" if dday is not None else "-",
                 mixstr(dmix, sum(dmix.values())), EVIDENCE[name]))

with open(os.path.join(HERE, "INPUT_ASSUMPTIONS.md"), "w", encoding="utf-8") as f:
    f.write("""# Input-assumption ledger — corridor service levels

Per the auditability memo: every train-frequency assumption with its evidence. "Derived"
columns come from the simplified blocking chain (`blocking_layer.py`: public L3 demand ->
corridor screenline -> cars-per-train). **Known limitation** (stated in every audit): the
public L3 demand release is volume-sampled (~3.33M cars/70d nationally), so derived
absolute trains/day are a FLOOR; the usable derived evidence is the class mix and the
directional balance. Absolute levels lean on the corridor knowledge base and published
sources listed per row. Cars-per-train constants {IM 150, manifest 100, auto 90,
grain/coal 120} are externally supported (Wenlin `s1_block_to_train.py`).

| instance | trains | assumed/day* | assumed mix | derived/day | derived mix | frequency evidence |
|---|---|---|---|---|---|---|
""")
    for r in rows:
        f.write("| " + " | ".join(str(x) for x in r) + " |\n")
    f.write("""
*assumed/day = scenario trains normalized by entry span; scenarios are deliberately
peaked, so this is the within-window rate, not a calendar-day average.

Maintained by `make_assumptions.py`; Wenlin: add rows/columns from your transit-time and
service-schedule estimates (Week 2 of the work plan) rather than editing by hand.
""")
print(f"INPUT_ASSUMPTIONS.md: {len(rows)} corridors")
