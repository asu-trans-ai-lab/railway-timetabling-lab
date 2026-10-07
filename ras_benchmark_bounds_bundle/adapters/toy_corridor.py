"""The toy three-segment corridor (10-20-10) as a resource-chain model, at three resource resolutions.

Corridor (west -> east): R12 single track 10 min | S2 station, main + siding, 1 min | R23 single track 20 min |
S3 station 1 min | R34 single track 10 min. A train may wait only at its origin or on the siding of S2 / S3 (standing
holds one of the station's two tracks). Trains alternate E, W, E, ... released every `interval` minutes (h = 10).
Resolutions: whole segments; control cells of 10 min; cells of 5 min (adapters/control_cells.py).
"""
from __future__ import annotations

from adapters.control_cells import cellify
from solver.python.siding_model import Model, Resource, Train

# (name, running minutes, tracks, may stand)
C0 = [("R12", 10, 1, False), ("S2", 1, 2, True), ("R23", 20, 1, False), ("S3", 1, 2, True), ("R34", 10, 1, False)]
RESOLUTIONS = {"whole": None, "cells10": 10, "cells5": 5}


def corridor_model(name: str, corridor, trains, headway: int, alpha: int = 1, beta: int = 1) -> Model:
    """Terminals at both ends, the fixed chain of resources; trains = [(train_id, direction +1/-1, release)]."""
    names = [r[0] for r in corridor]
    resources = [Resource(nm, str(i), str(i + 1), tracks, stand, frozenset({str(i), str(i + 1)}))
                 for i, (nm, _, tracks, stand) in enumerate(corridor)]
    blocks, run = [], []
    for i, res in enumerate(resources + [None]):
        if res is not None and res.tracks == 1:
            run.append(i)
            continue
        if len(run) >= 2:
            blocks.append(run)
        run = []
    out = []
    for tid, d, release in trains:
        seq = range(len(names)) if d > 0 else range(len(names) - 1, -1, -1)
        t = Train(tid, release, "0" if d > 0 else str(len(names)), str(len(names)) if d > 0 else "0", d, True,
                  [(i, corridor[i][1], corridor[i][3]) for i in seq])
        on = {i for i, _, _ in t.path}
        t.through = [b for b, blk in enumerate(blocks) if all(i in on for i in blk)]
        out.append(t)
    return Model(name, headway, alpha, beta, resources, out, blocks)


def toy_model(n: int, interval: int = 15, h: int = 10, cells: int | None = None) -> Model:
    trains = [(f"{'E' if k % 2 == 0 else 'W'}{k // 2 + 1}", 1 if k % 2 == 0 else -1, k * interval) for k in range(n)]
    return cellify(corridor_model(f"toy_N{n}", C0, trains, h), cells)


def physical_check(model: Model, schedule) -> list[str]:
    """Independent of the cell split: on every single-track stretch (a parent resource), two opposing trains never
    overlap -- the stretch is held from the first cell's entry to the last cell's exit + H (to the arrival + H when
    the train then stands clear at a wait point, holding nothing)."""
    parent = lambda name: name.split("#")[0]             # noqa: E731
    single = {parent(r.name) for r in model.resources if r.tracks == 1 and not r.siding}
    spans = {}
    for t in model.trains:
        for i, name, e, x in schedule[t.train_id]:
            if int(t.path[i][2]) == 2:                    # a pocket: clear of the line on arrival
                x = e + t.path[i][1]
            p = parent(name)
            if p in single:
                a, b = spans.get((t.train_id, p), (e, x))
                spans[(t.train_id, p)] = (min(a, e), max(b, x))
    direction = {t.train_id: t.direction for t in model.trains}
    errors, keys = [], list(spans)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            (ta, pa), (tb, pb) = keys[i], keys[j]
            if pa != pb or direction[ta] == direction[tb]:
                continue
            (a1, b1), (a2, b2) = spans[keys[i]], spans[keys[j]]
            if a1 < b2 + model.headway and a2 < b1 + model.headway:
                errors.append(f"{ta} and {tb} both on single-track {pa}")
    return errors
