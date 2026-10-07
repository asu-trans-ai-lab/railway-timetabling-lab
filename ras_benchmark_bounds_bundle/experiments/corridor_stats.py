"""Key statistics of the corridor runs (Prof. Zhou, Oct 5): size, demand over capacity, average delay and average speed
per train, from the models (adapters/anl_corridor.py) and the best validated timetable of results/corridors/<name>/.

  demand        trains / release window (trains per hour, both directions)
  capacity      bottleneck link: tracks * 60 / (slowest train's running time + H)  (trains per hour)
  demand / cap  the offered rate over that ceiling (the check the August extraction used)
  delay / train sum (arrival - release - free running time) / trains, best validated timetable
  speed         total train-miles / total train-hours: free running, and in the timetable (release to arrival)

    python -m experiments.corridor_stats [--names a,b]   ->  results/corridors/stats.json
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from adapters.anl_corridor import DATA, EXTRA, L3, corridor_model

RES = Path(__file__).resolve().parents[1] / "results" / "corridors"


def stats(name: str) -> dict | None:
    row_f = RES / name / "row.json"
    if not row_f.exists():
        return None
    row = json.loads(row_f.read_text())
    model, info = corridor_model(name)
    base = DATA / f"{name}_native" if (DATA / f"{name}_native").exists() else DATA / name
    links = list(csv.DictReader(open(base / "input_link.csv")))
    miles = {i: float(l["length"]) for i, l in enumerate(links)}           # resource index = link index
    H = model.headway
    rel = [t.release for t in model.trains]
    window_h = max(1e-9, (max(rel) - min(rel)) / 60.0)
    demand = len(model.trains) / window_h
    worst = {}
    for t in model.trains:
        for r, p, _ in t.path:
            worst[r] = max(worst.get(r, 0), p)
    cap = {r: model.resources[r].tracks * 60.0 / (worst[r] + H) for r in worst}
    bott = min(cap, key=cap.get)
    ok = [(row[e]["value"], e) for e in ("e1", "e3") if row[e]["value"] is not None and row[e]["check"] == "PASS"]
    best = None if not ok else min(ok)
    tot = sum(float(l["length"]) for l in links)
    mix = {k: round(100 * sum(float(l["length"]) for l in links if f(int(l["capacity"]))) / tot)
           for k, f in (("single", lambda c: c == 1), ("double", lambda c: c == 2), ("triple_plus", lambda c: c >= 3))}
    east = sum(1 for t in model.trains if t.direction > 0)
    out = {"corridor": name, "miles": info["miles"], "single_track_pct": info["single_track_pct"], "track_mix": mix,
           "stations": len(links) + 1, "trains_east": east, "trains_west": len(model.trains) - east,
           "demand_east_per_h": round(east / window_h, 2), "demand_west_per_h": round((len(model.trains) - east) / window_h, 2),
           "wait_points": info["wait_points"], "trains": info["trains"], "H": H, "window_h": round(window_h, 1),
           "demand_per_h": round(demand, 2), "bottleneck_link": model.resources[bott].name,
           "bottleneck_tracks": model.resources[bott].tracks, "capacity_per_h": round(cap[bott], 2),
           "demand_over_capacity": round(demand / cap[bott], 2)}
    if best:
        sched = {}
        for r in csv.DictReader(open(RES / name / f"{best[1]}.csv")):
            sched.setdefault(r["train_id"], []).append(r)
        tm = th = fh = 0.0
        delay = 0
        ratios = []
        for t in model.trains:
            legs = sched[t.train_id]
            arr = max(int(l["exit"]) for l in legs)
            free = sum(p for _, p, _ in t.path)
            m = sum(miles[r] for r, _, _ in t.path)
            tm += m
            th += (arr - t.release) / 60.0
            fh += free / 60.0
            delay += arr - t.release - free
            ratios.append(free / max(1, arr - t.release))
        out.update(min_speed_ratio=round(min(ratios), 3), best_engine=best[1], delay_total=delay, delay_per_train=round(delay / len(model.trains), 1),
                   speed_free_mph=round(tm / fh, 1), speed_timetable_mph=round(tm / th, 1),
                   dp_delay_per_train=None if row["e3"]["value"] is None else round(row["e3"]["delay"] / len(model.trains), 1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", default="")
    a = ap.parse_args()
    names = a.names.split(",") if a.names else [p.name for p in sorted(RES.iterdir()) if (p / "row.json").exists()]
    rows = [s for s in (stats(n) for n in names) if s]
    (RES / "stats.json").write_text(json.dumps(rows, indent=1))
    for s in rows:
        print(json.dumps(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
