"""RAS 2012 D1-D3, ideal case (meeting of Oct 4): every train at the same speed, every track single.

Corridor: the main line from node 0 to node 39 (108 miles), all of it single track, including the double-track
section. The five sidings of the network are kept as passing loops: the main section beside a siding becomes a
resource of 2 tracks where a train may stand (it holds one of them). The single-track stretches between loops are
cut into control cells of `cell_minutes` (every train runs every cell; opposing trains never share a stretch).
Running time: length / 80 mph for every train (speed multiplier 1), rounded up to whole minutes. Releases and
origin / destination nodes are those of the RAS input; an intermediate node is placed at the nearest loop boundary.
H = 3 (as in the fixed-track model); cost = running + origin waiting + standing (alpha = beta = 1).

    ideal_model(dataset)  ->  Model   (solver/python/siding_model.py: read by E1, E2, E3 and the MIP)
"""
from __future__ import annotations

import csv
import heapq
import math
from collections import defaultdict
from pathlib import Path

from adapters.control_cells import cellify
from solver.python.siding_model import Model, Resource, Train

DATA = Path(__file__).resolve().parents[1] / "data"
SPEED = 80.0                                   # mph, main track
HEADWAY = 3


def _rows(path):
    return list(csv.DictReader(open(path)))


def corridor(d: int):
    """[(x_from, x_to, is_loop)] along the main line, west to east, and the node x positions."""
    base = DATA / f"RAS_data-set_{d}"
    arcs = _rows(base / "input_rail_arc.csv")
    x = {r["node_id"]: float(r["location_x"]) for r in _rows(base / "input_rail_node.csv")}
    g = defaultdict(list)
    for a in arcs:
        if a["track_type"] in ("0", "1", "2", "SW", "C"):
            g[a["A_node_id"]].append((a["B_node_id"], float(a["length"])))
            g[a["B_node_id"]].append((a["A_node_id"], float(a["length"])))
    dist, prev, pq = {"0": 0.0}, {}, [(0.0, "0")]
    while pq:                                  # the shortest main-line path 0 -> 39
        du, u = heapq.heappop(pq)
        if du > dist[u]:
            continue
        for v, w in g[u]:
            if du + w < dist.get(v, 1e18):
                dist[v], prev[v] = du + w, u
                heapq.heappush(pq, (du + w, v))
    loops = sorted((min(x[a["A_node_id"]], x[a["B_node_id"]]), max(x[a["A_node_id"]], x[a["B_node_id"]]))
                   for a in arcs if a["track_type"] == "S")
    # a loop spans the main section between the two switch nodes around its siding: widen to main-line node positions
    path_x = []
    u = "39"
    while u != "0":
        path_x.append(x[u])
        u = prev[u]
    path_x = sorted(set(path_x + [0.0]))
    spans = []
    for a, b in loops:
        lo = max(p for p in path_x if p <= a + 1e-9)
        hi = min(p for p in path_x if p >= b - 1e-9)
        if hi - lo < 1e-9:
            lo = max(p for p in path_x if p < a - 1e-9)
        spans.append((lo, hi))
    cuts = sorted(set([0.0, max(path_x)] + [v for s in spans for v in s]))
    sections = [(cuts[i], cuts[i + 1], any(abs(cuts[i] - lo) < 1e-9 and abs(cuts[i + 1] - hi) < 1e-9 for lo, hi in spans))
                for i in range(len(cuts) - 1)]
    return sections, x


def ideal_model(dataset: str, cell_minutes: int | None = 10) -> Model:
    d = int(str(dataset)[1:])
    sections, x = corridor(d)
    resources = []
    for i, (a, b, loop) in enumerate(sections):
        name = f"L{i}" if loop else f"T{i}"
        resources.append(Resource(name, str(i), str(i + 1), 2 if loop else 1, loop, frozenset({str(i), str(i + 1)})))
    minutes = [max(1, math.ceil(60.0 * (b - a) / SPEED)) for a, b, _ in sections]
    bounds = [s[0] for s in sections] + [sections[-1][1]]

    def boundary(node):                        # index of the section boundary nearest the node
        return min(range(len(bounds)), key=lambda j: abs(bounds[j] - x[node]))

    trains = []
    for r in _rows(DATA / f"RAS_data-set_{d}" / "input_train_info.csv"):
        east = r["direction"] == "EASTBOUND"
        o, dd = boundary(r["origin_node_id"]), boundary(r["destination_node_id"])
        seq = list(range(o, dd)) if east else list(range(o - 1, dd - 1, -1))
        trains.append(Train(r["train_header"], int(float(r["entry_time"])), str(o), str(dd), 1 if east else -1, True,
                            [(i, minutes[i], resources[i].siding) for i in seq]))
    trains.sort(key=lambda t: (t.release, t.train_id))
    model = Model(f"RAS_D{d}_ideal", HEADWAY, 1, 1, resources, trains, [])
    return cellify(model, cell_minutes)
