"""Resources as chains of control cells (Meng & Zhou 2014, Figs 5-6): a model-data change only.

A resource held as one piece over [entry, exit + H) blocks a follower for the whole traversal. Split it into cells: a
train holds one cell at a time, over [entry_c, exit_c + H), so a follower may enter a cell as soon as the leader has
left it and H has passed -- the cumulative-flow occupancy O_c(t) = W^A_c(t) - W^D_c(t) <= C_c, one cell at a time.
Engines and kernel are untouched: they read cells as resources.

    cellify(model, cell_minutes)          every single-track resource longer than cell_minutes -> a chain of cells that
                                          every train traverses in full (coarsened when a fast train is too short)
    check_cells(model)                    the invariant: on a split resource every train runs all its cells, in its
                                          direction's order, each for >= 1 minute
    identify_bottleneck(model, keep=1)    keep the phase blocks with the largest occupancy demand sum(run + H)

Opposing trains cannot swap between two cells of one single-track stretch: nobody may stand at a cell boundary and
H >= 1, so splitting never opens a passing place.
"""
from __future__ import annotations

import math
from dataclasses import replace

from solver.python.siding_model import Model


def _blocks(resources, stops=frozenset()):
    """Runs of >= 2 consecutive single-track resources; a run also ends at a boundary i | i+1 in `stops`, where some
    train may stop clear of the line (a wait point), since a train may not stand inside a phase block."""
    blocks, run = [], []
    for i, res in enumerate(list(resources) + [None]):
        if res is not None and res.tracks == 1 and not res.siding and not (run and run[-1] in stops):
            run.append(i)
            continue
        if len(run) >= 2:
            blocks.append(run)
        run = [i] if res is not None and res.tracks == 1 and not res.siding else []
    return blocks


def _split(p: int, m: int) -> list[int]:
    """p minutes over m cells, integers >= 1, as even as possible (larger parts first)."""
    base, extra = divmod(p, m)
    return [base + (1 if j < extra else 0) for j in range(m)]


def cellify(model: Model, cell_minutes: int | None) -> Model:
    """A copy of `model` with every single-track, no-standing resource cut into control cells of at most
    `cell_minutes` of running (by the slowest train on it), but never more cells than the shortest positive running
    time on that resource: every train must traverse every cell for at least one minute on the integer grid, so that
    opposing trains share the same physical cells (Meng & Zhou 2014, eq. 33). None or 0 keeps the model as it is."""
    if not cell_minutes:
        return model
    longest, shortest = {}, {}
    for t in model.trains:
        for r, p, _ in t.path:
            longest[r] = max(longest.get(r, 0), p)
            if p > 0:
                shortest[r] = min(shortest.get(r, p), p)
    new_res, cells_of = [], {}
    for r, res in enumerate(model.resources):
        m = 1
        if res.tracks == 1 and not res.siding and longest.get(r, 0) > cell_minutes:
            m = max(1, min(math.ceil(longest[r] / cell_minutes), shortest.get(r, 1)))
        ids = []
        for j in range(m):
            name = res.name if m == 1 else f"{res.name}#{j + 1}"
            ids.append(len(new_res))
            new_res.append(replace(res, name=name))
        cells_of[r] = ids                                  # west -> east
    trains = []
    for t in model.trains:
        path = []
        for r, p, stand in t.path:
            ids = cells_of[r]
            if len(ids) == 1:
                path.append((ids[0], p, stand))
                continue
            parts = _split(p, len(ids))
            assert min(parts) >= 1 and sum(parts) == p, (t.train_id, r, p, len(ids))
            order = ids if t.direction > 0 else ids[::-1]
            for j, (cid, q) in enumerate(zip(order, parts)):        # a stop at the link's end stays on its last cell
                path.append((cid, q, stand if j == len(order) - 1 else False))
        trains.append(replace(t, path=path, through=[]))
    stops = set()                                          # boundaries i | i+1 where some train may stand
    for t in trains:
        for r, _, stand in t.path:
            if int(stand):
                stops.add(r if t.direction > 0 else r - 1)
    blocks = _blocks(new_res, frozenset(stops))
    for t in trains:
        on = {r for r, _, _ in t.path}
        t.through = [b for b, blk in enumerate(blocks) if all(i in on for i in blk)]
    return Model(model.name + f"_cells{cell_minutes}", model.headway, model.alpha, model.beta, new_res, trains, blocks)


def check_cells(model: Model) -> list[str]:
    """Every resource cut into cells (names R#1..R#m) is traversed in full by every train that uses it: all m cells,
    in order 1..m eastbound and m..1 westbound, each for >= 1 minute. Returns the violations (empty = holds)."""
    cells_of = {}
    for i, r in enumerate(model.resources):
        if "#" in r.name:
            parent, _, j = r.name.rpartition("#")
            cells_of.setdefault(parent, {})[int(j)] = i
    errors = []
    for t in model.trains:
        for parent, cells in cells_of.items():
            ids = [cells[j] for j in sorted(cells)]
            seq = [(r, p) for r, p, _ in t.path if r in set(ids)]
            if not seq:
                continue
            want = ids if t.direction > 0 else ids[::-1]
            if [r for r, _ in seq] != want:
                errors.append(f"{t.train_id}: on {parent} runs {[model.resources[r].name for r, _ in seq]}")
            if any(p < 1 for _, p in seq):
                errors.append(f"{t.train_id}: a zero-minute cell on {parent}")
    return errors

def block_demand(model: Model) -> list[int]:
    """Per phase block: sum over the trains through it of (running time inside + H) -- its occupancy demand."""
    out = []
    for b, blk in enumerate(model.blocks):
        cells = set(blk)
        out.append(sum(sum(p for r, p, _ in t.path if r in cells) + model.headway
                       for t in model.trains if b in t.through))
    return out


def identify_bottleneck(model: Model, keep: int = 1) -> Model:
    """Keep only the `keep` phase blocks of largest occupancy demand: the phase layer acts on the identified bottleneck."""
    if not model.blocks:
        return model
    demand = block_demand(model)
    chosen = sorted(sorted(range(len(model.blocks)), key=lambda b: -demand[b])[:keep])
    blocks = [model.blocks[b] for b in chosen]
    trains = []
    for t in model.trains:
        on = {r for r, _, _ in t.path}
        trains.append(replace(t, through=[b for b, blk in enumerate(blocks) if all(i in on for i in blk)]))
    return Model(model.name + "_bottleneck", model.headway, model.alpha, model.beta, model.resources, trains, blocks)
