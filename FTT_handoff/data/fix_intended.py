"""fix_intended.py — recompute intended_arrival from the WRITTEN input_link.csv values.

One-off repair for instances built before extract_corridor.py quantized block speeds:
freeruns were computed from unrounded speeds, so boundary blocks' integer run times
differ by 1 min from what the solver derives from the 2-dp CSV (audit: baseline != 0).
Entries and class assignments are untouched; only intended_arrival is re-derived.

Usage: python fix_intended.py <instance_dir> [...]
"""
import csv, os, sys

CLASSES = {1.25: "Z", 1.0: "M", 0.85: "G"}

for d in sys.argv[1:]:
    links = [(float(r["length"]), float(r["speed_ft"]))
             for r in csv.DictReader(open(os.path.join(d, "input_link.csv")))]
    fr = {m: sum(max(1, int(L * 60.0 / (v * m) + 1.0)) for L, v in links)
          for m in (1.25, 1.0, 0.85)}
    p = os.path.join(d, "input_train_info.csv")
    rows = list(csv.DictReader(open(p)))
    hdr = list(rows[0].keys())
    changed = 0
    for r in rows:
        want = int(float(r["entry"])) + fr[float(r["speed_mult"])]
        if int(float(r["intended_arrival"])) != want:
            r["intended_arrival"] = str(want)
            changed += 1
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=hdr)
        w.writeheader()
        w.writerows(rows)
    print(f"{os.path.basename(os.path.normpath(d))}: freeruns {fr}  fixed {changed} intended arrivals")
