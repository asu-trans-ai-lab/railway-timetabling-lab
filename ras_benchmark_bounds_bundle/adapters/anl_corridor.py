"""The ANL corridors (RAS L3 extracts, FTT_handoff/data/<name>_native, CORRIDOR_COMPARISON.md) as a resource-chain
Model, read by E1 / E3 / the MIP and checked by the same validator.

    link (from, to, length mi, speed_ft / speed_tf mph, capacity)  ->  a resource with `capacity` tracks; no train
                                                                       stands on a link (as in S01)
    wait points, about every 20 miles                              ->  the interior node nearest each multiple of 20
                                                                       miles; a train may wait there standing clear
                                                                       of the line (holding no link): the leg that
                                                                       ends at the node is a pocket (may_stand 2)
    origin                                                         ->  a train may wait at its origin, holding nothing
    train (origin, dest, dir, speed_mult, entry)                     ->  its own running time per link:
                                                                       ceil(60 * length / (speed * speed_mult)), >= 1
H = SafetyHeadway of the instance; cost = running + origin waiting + standing (alpha = beta = 1), so
OBJ-E = total elapsed minutes and OBJ-D = OBJ-E - TT0 = total delay.
"""
from __future__ import annotations

import configparser
import csv
import math
from pathlib import Path

from solver.python.siding_model import Model, Resource, Train

DATA = Path(__file__).resolve().parents[2] / "FTT_handoff" / "data"
L3 = ["overland", "panhandle", "transcon_clovis", "prb_joint", "pocahontas", "hiline", "cn_icmain", "gulf", "moffat",
      "cpkc_midcon"]                              # the ten corridors from the RAS L3 network
EXTRA = ["harrod_bnsf"]                           # Harrod (2011), literature-based


def _rows(p):
    return list(csv.DictReader(open(p)))


def corridor_model(name: str) -> tuple[Model, dict]:
    base = DATA / f"{name}_native" if (DATA / f"{name}_native").exists() else DATA / name
    ini = configparser.ConfigParser()
    ini.read(base / "FTSettings.ini")
    H = int(ini["lagrangian"].get("SafetyHeadway", "2")) if "lagrangian" in ini else 2
    links = _rows(base / "input_link.csv")
    nodes = [int(links[0]["from"])] + [int(l["to"]) for l in links]
    for a, b in zip(links, links[1:]):
        assert a["to"] == b["from"], f"{name}: links are not a chain"
    miles_at, x = {nodes[0]: 0.0}, 0.0
    for l in links:
        x += float(l["length"])
        miles_at[int(l["to"])] = x
    interior = nodes[1:-1]
    berths = sorted({min(interior, key=lambda n: abs(miles_at[n] - 20.0 * k)) for k in range(1, int(x // 20) + 1)}
                    if interior else set(), key=lambda n: miles_at[n])
    resources = [Resource(f"L{l['from']}-{l['to']}", l["from"], l["to"], int(l["capacity"]), False,
                          frozenset({l["from"], l["to"]})) for l in links]
    pos = {n: i for i, n in enumerate(nodes)}
    berth_set = set(berths)

    def legs(origin, dest, east, mult):
        lo, hi = min(pos[origin], pos[dest]), max(pos[origin], pos[dest])
        out = []
        for r, l in enumerate(links):
            a, b = pos[int(l["from"])], pos[int(l["to"])]
            if lo <= a and b <= hi:
                speed = float(l["speed_ft"] if east else l["speed_tf"]) * mult
                end_node = int(l["to"]) if east else int(l["from"])
                out.append((r, max(1, math.ceil(60.0 * float(l["length"]) / speed)), end_node))
        out = out if east else out[::-1]
        return [(r, p, 2 if (i < len(out) - 1 and n in berth_set) else 0) for i, (r, p, n) in enumerate(out)]

    trains = []
    for t in _rows(base / "input_train_info.csv"):
        east = int(t["dir"]) == 1
        path = legs(int(t["origin"]), int(t["dest"]), east, float(t["speed_mult"]))
        trains.append(Train(t["train_id"], int(float(t["entry"])), t["origin"], t["dest"], 1 if east else -1, True, path))
    trains.sort(key=lambda t: (t.release, t.train_id))
    miles = sum(float(l["length"]) for l in links)
    single = sum(float(l["length"]) for l in links if int(l["capacity"]) == 1)
    info = {"corridor": name, "miles": round(miles, 1), "links": len(links), "single_track_pct": round(100 * single / miles),
            "wait_points": len(berths), "wait_point_miles": [round(miles_at[n], 1) for n in berths],
            "trains": len(trains), "H": H,
            "horizon": int(ini["optimization"]["OptimizationHorizon"])}
    return Model(name, H, 1, 1, resources, trains, []), info
