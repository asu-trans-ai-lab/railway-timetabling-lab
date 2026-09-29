"""An independent check of a schedule on a resource-chain model (solver/python/siding_model.py).

Written from the model's rules, not from solver/cpp/siding_lr.cpp. A schedule is rows (train_id, index, resource,
entry, exit). Checked:
  1 every train appears and its resources are exactly its path, in order
  2 entry of the first >= release; entry of each next = exit of the previous
  3 exit - entry >= its minutes; more only where the leg may stand (may_stand 1 or 2, or a siding resource)
  4 on every resource, at no minute more than `tracks` holds: [entry, exit + H), a pocket leg [entry, entry + p + H),
    from the release on the first leg when the origin is inside the territory -- by a sweep
  5 the cost: running + alpha * (first entry - release) + beta * standing, recomputed
  6 the horizon: every train has arrived by it (when one is given)
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from solver.python.siding_model import Model


def read_schedule(path: Path) -> dict[str, list[tuple[int, str, int, int]]]:
    rows = defaultdict(list)
    for r in csv.DictReader(open(path)):
        rows[r["train_id"]].append((int(r["index"]), r["resource"], int(r["entry"]), int(r["exit"])))
    return {k: sorted(v) for k, v in rows.items()}


def validate(model: Model, schedule: dict[str, list[tuple[int, str, int, int]]], *, horizon: int | None = None,
             expect_objective: int | None = None) -> tuple[list[str], int, dict]:
    errors: list[str] = []
    held = defaultdict(list)                       # resource -> [(start, end, train)]
    total, parts = 0, {}
    for t in model.trains:
        legs = schedule.get(t.train_id)
        if not legs:
            errors.append(f"check1 {t.train_id}: not scheduled")
            continue
        names = [model.resources[i].name for i, _, _ in t.path]
        if [name for _, name, _, _ in legs] != names or [i for i, _, _, _ in legs] != list(range(len(names))):
            errors.append(f"check1 {t.train_id}: resources are not its path")
            continue
        run = stand = 0
        for n, ((index, name, entry, exit_), (r, p, may_stand)) in enumerate(zip(legs, t.path)):
            if n == 0 and entry < t.release:
                errors.append(f"check2 {t.train_id}: departs {entry} before release {t.release}")
            if n > 0 and entry != legs[n - 1][3]:
                errors.append(f"check2 {t.train_id}: {name} entered at {entry}, previous exit {legs[n - 1][3]}")
            if exit_ - entry < p:
                errors.append(f"check3 {t.train_id}: {name} in {exit_ - entry} < run {p}")
            if exit_ - entry > p and not (int(may_stand) in (1, 2) or model.resources[r].siding):
                errors.append(f"check3 {t.train_id}: stands {exit_ - entry - p} after {name}, not allowed there")
            run += p
            stand += max(0, exit_ - entry - p)
            start = t.release if (n == 0 and not t.terminal_origin) else entry
            end = entry + p if int(may_stand) == 2 else exit_
            held[r].append((start, end + model.headway, t.train_id))
        origin = legs[0][2] - t.release
        cost = run + model.alpha * origin + model.beta * stand
        parts[t.train_id] = {"running": run, "origin_wait": origin, "standing": stand, "cost": cost,
                             "departure": legs[0][2], "arrival": legs[-1][3]}
        total += cost
        if horizon is not None and legs[-1][3] > horizon:
            errors.append(f"check6 {t.train_id}: arrives at {legs[-1][3]}, after the horizon {horizon}")
    for r, spans in held.items():
        events = sorted([(s, 1, k) for s, e, k in spans] + [(e, -1, k) for s, e, k in spans], key=lambda x: (x[0], x[1]))
        active, on = 0, set()
        for time, delta, k in events:
            active += delta
            (on.add if delta > 0 else on.discard)(k)
            if active > model.resources[r].tracks:
                errors.append(f"check4 {model.resources[r].name}: {active} trains at {time} "
                              f"(tracks {model.resources[r].tracks}): {', '.join(sorted(on))}")
    if expect_objective is not None and expect_objective != total:
        errors.append(f"check5 objective: reported {expect_objective}, recomputed {total}")
    return errors, total, parts
