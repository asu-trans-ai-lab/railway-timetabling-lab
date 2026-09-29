"""E1: block-pair CP-SAT on the fixed-track model (adapters/fixed_track_adapter.py). Proves D1-D3 optimal:
OBJ-E 2220 / 4127 / 4056 (independent brute-force certificate: OBJ-E <= 2219 / 4126 / 4055 infeasible).

Physical model (identical to validator/fixed_track/fixed_track_validate.cpp):
  * train i follows chain[k] with run_times[k]; start(k) >= end(k-1);
  * a positive wait before task k > 0 is legal only if wait_after[k-1] == 1 (origin waits are always legal);
  * every task occupies its segment on [start, end + H) with capacity 1;
  * objective OBJ-E = sum_i (end of last task - release_i).

A block is a maximal run of tasks with no legal wait inside, so one integer start fixes all its task times. For two
blocks of different trains that share segments, the forbidden start offsets Delta = s_b - s_a are computed exactly;
their complement gives the disjunctive alternatives (one Boolean each: a real meet / pass decision). Identical trains
keep release order in every block (a swap argument). Time windows: each train's delay <= UB - TT0 (UB from a
validated donor schedule).
"""
from __future__ import annotations

import csv
import time
from pathlib import Path
from types import SimpleNamespace

from ortools.sat.python import cp_model


def load_instance(path):
    path = Path(path)
    params = {r["key"]: r["value"] for r in csv.DictReader((path / "params.tsv").open(), delimiter="\t")}
    cap = {int(r["segment"]): int(r["capacity"])
           for r in csv.DictReader((path / "segments.tsv").open(), delimiter="\t")}
    trains = []
    for r in csv.DictReader((path / "trains.tsv").open(), delimiter="\t"):
        trains.append(dict(name=r["train"], release=int(r["release"]),
                           chain=[int(x) for x in r["chain"].split(",")],
                           run=[int(x) for x in r["run_times"].split(",")],
                           wait=[int(x) for x in r["wait_after"].split(",")]))
    assert all(c == 1 for c in cap.values()), "model assumes capacity 1"
    return dict(headway=int(params["headway"]), trains=trains, capacity=cap)


def load_schedule(path):
    """{train: [start of task k]}."""
    out = {}
    for r in csv.DictReader(Path(path).open()):
        out.setdefault(r.get("train_id", r.get("train")), {})[int(r["index"])] = int(r["start"])
    return {t: [d[k] for k in sorted(d)] for t, d in out.items()}


def make_blocks(inst):
    blocks, per_train = [], []
    for ti, tr in enumerate(inst["trains"]):
        ids, cur = [], []
        free = tr["release"]
        for k, (seg, dur) in enumerate(zip(tr["chain"], tr["run"])):
            if not cur:
                start_free, offset = free, 0
            cur.append((seg, offset, dur, k))
            offset += dur
            free += dur
            if k == len(tr["chain"]) - 1 or tr["wait"][k] == 1:
                ids.append(len(blocks))
                blocks.append(dict(train=ti, pos=len(ids) - 1, tasks=cur, length=offset,
                                   earliest=start_free, first_task=cur[0][3]))
                cur = []
        per_train.append(ids)
    return blocks, per_train


def forbidden_offsets(ba, bb, H):
    """Integer Delta = s_b - s_a that create a conflict, merged into sorted closed intervals."""
    ta = {seg: (o, p) for seg, o, p, _ in ba["tasks"]}
    iv = []
    for seg, ob, pb, _ in bb["tasks"]:
        if seg in ta:
            oa, pa = ta[seg]
            iv.append((oa - ob - pb - H + 1, oa + pa + H - ob - 1))    # overlap iff both holds intersect
    iv.sort()
    merged = []
    for lo, hi in iv:
        if merged and lo <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [tuple(x) for x in merged]


def allowed_ranges(forb, lo, hi):
    """Complement of forbidden intervals inside [lo, hi]."""
    out, cur = [], lo
    for fl, fh in forb:
        if fl > cur:
            out.append((cur, min(fl - 1, hi)))
        cur = max(cur, fh + 1)
        if cur > hi:
            break
    if cur <= hi:
        out.append((cur, hi))
    return [r for r in out if r[0] <= r[1]]


def identical_groups(inst):
    """Groups of trains with identical chain, run times and wait points (sorted by release, name)."""
    groups = {}
    for i, tr in enumerate(inst["trains"]):
        groups.setdefault((tuple(tr["chain"]), tuple(tr["run"]), tuple(tr["wait"])), []).append(i)
    return [sorted(g, key=lambda i: (inst["trains"][i]["release"], inst["trains"][i]["name"]))
            for g in groups.values() if len(g) > 1]


def tt0(inst):
    return sum(sum(t["run"]) for t in inst["trains"])


def write_schedule(path, inst, blocks, per_train, starts):
    rows = []
    for ti, tr in enumerate(inst["trains"]):
        for b in per_train[ti]:
            for seg, off, dur, k in blocks[b]["tasks"]:
                s = starts[b] + off
                rows.append((tr["name"], k, seg, s, s + dur))
    tmp = Path(str(path) + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["train_id", "index", "segment", "start", "end"])
        w.writerows(rows)
    tmp.replace(path)


def flow_of(inst, blocks, per_train, starts):
    return sum(starts[ids[-1]] + blocks[ids[-1]]["length"] - inst["trains"][ti]["release"]
               for ti, ids in enumerate(per_train))


def donor_block_starts(inst, blocks, per_train, sched):
    starts = [None] * len(blocks)
    for ti, tr in enumerate(inst["trains"]):
        st = sched[tr["name"]]
        for b in per_train[ti]:
            blk = blocks[b]
            s0 = st[blk["first_task"]]
            for seg, off, dur, k in blk["tasks"]:
                assert st[k] == s0 + off, "donor waits inside a rigid block"
            starts[b] = s0
    return starts


def build(inst, donor_starts, ub_e, args):
    """The CP-SAT model. args: sym, hint, nooverlap, obj_lb, delay_lb_others (see solve())."""
    H = inst["headway"]
    blocks, per_train = make_blocks(inst)
    T0 = tt0(inst)
    max_delay = ub_e - T0 - args.delay_lb_others
    m = cp_model.CpModel()
    s = [m.new_int_var(blk["earliest"], blk["earliest"] + max_delay, f"s{b}") for b, blk in enumerate(blocks)]
    for ids in per_train:
        for a, b in zip(ids, ids[1:]):
            m.add(s[b] >= s[a] + blocks[a]["length"])
    sym_fixed = set()
    if args.sym:
        for group in identical_groups(inst):
            for i, j in zip(group, group[1:]):
                for a, b in zip(per_train[i], per_train[j]):
                    forb = forbidden_offsets(blocks[a], blocks[b], H)
                    m.add(s[b] - s[a] >= forb[-1][1] + 1)
                    sym_fixed.add((min(a, b), max(a, b)))
    stats = dict(blocks=len(blocks), pairs=0, hard_pairs=0, bool_pairs=0, bools=0, sym_pairs=len(sym_fixed))
    for a in range(len(blocks)):
        for b in range(a + 1, len(blocks)):
            if blocks[a]["train"] == blocks[b]["train"] or (a, b) in sym_fixed:
                continue
            forb = forbidden_offsets(blocks[a], blocks[b], H)
            if not forb:
                continue
            stats["pairs"] += 1
            lo = blocks[b]["earliest"] - (blocks[a]["earliest"] + max_delay)
            hi = blocks[b]["earliest"] + max_delay - blocks[a]["earliest"]
            alts = allowed_ranges(forb, lo, hi)
            if not alts:
                raise RuntimeError(f"no allowed offset for blocks {a},{b}")
            if len(alts) == 1:
                stats["hard_pairs"] += 1
                if alts[0][0] > lo:
                    m.add(s[b] - s[a] >= alts[0][0])
                if alts[0][1] < hi:
                    m.add(s[b] - s[a] <= alts[0][1])
                continue
            stats["bool_pairs"] += 1
            lits = []
            for r0, r1 in alts:
                x = m.new_bool_var(f"x{a}_{b}_{r0}")
                if r0 > lo:
                    m.add(s[b] - s[a] >= r0).only_enforce_if(x)
                if r1 < hi:
                    m.add(s[b] - s[a] <= r1).only_enforce_if(x)
                lits.append(x)
                if donor_starts is not None and args.hint:
                    d = donor_starts[b] - donor_starts[a]
                    m.add_hint(x, r0 <= d <= r1)
            m.add_exactly_one(lits)
            stats["bools"] += len(lits)
    if args.nooverlap:
        by_seg = {}
        for b, blk in enumerate(blocks):
            for seg, off, dur, _ in blk["tasks"]:
                by_seg.setdefault(seg, []).append(m.new_fixed_size_interval_var(s[b] + off, dur + H, f"i{b}_{seg}"))
        for ivs in by_seg.values():
            if len(ivs) > 1:
                m.add_no_overlap(ivs)
    obj = sum(s[ids[-1]] + blocks[ids[-1]]["length"] - inst["trains"][ti]["release"] for ti, ids in enumerate(per_train))
    m.add(obj <= ub_e)
    if args.obj_lb is not None:
        m.add(obj >= args.obj_lb)
    m.minimize(obj)
    if donor_starts is not None and args.hint:
        for b, v in enumerate(donor_starts):
            m.add_hint(s[b], v)
    return m, s, blocks, per_train, stats


def solve(instance_dir: Path, donor_csv: Path, out: Path, validate, seconds: float = 600, workers: int = 3,
          seed: int = 0, sym: bool = True, hint: bool = True) -> dict:
    """E1 on one instance. validate(schedule_csv) -> (OBJ-E or None, message) is the independent validator; the donor's
    validated value is the UB that sets the time windows. Returns the result (OPTIMAL: lb_e = ub_e)."""
    t_wall = time.monotonic()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    inst = load_instance(instance_dir)
    donor_ub, msg = validate(donor_csv)
    if donor_ub is None:
        raise RuntimeError(f"donor rejected: {msg}")
    blocks0, per0 = make_blocks(inst)
    donor_starts = donor_block_starts(inst, blocks0, per0, load_schedule(donor_csv))
    assert flow_of(inst, blocks0, per0, donor_starts) == donor_ub
    args = SimpleNamespace(sym=sym, hint=hint, nooverlap=False, obj_lb=None, delay_lb_others=0)
    m, s, blocks, per_train, stats = build(inst, donor_starts, donor_ub, args)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(1.0, seconds - (time.monotonic() - t_wall))
    solver.parameters.num_workers = workers
    solver.parameters.random_seed = seed
    status = solver.solve(m)
    name = solver.status_name(status)
    lb = int(solver.best_objective_bound + 1e-6) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None
    ub_sched, ub = donor_csv, donor_ub
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        write_schedule(out / "schedule.csv", inst, blocks, per_train, [solver.value(v) for v in s])
        val, vmsg = validate(out / "schedule.csv")
        if val is None:
            raise RuntimeError(f"E1 schedule failed validation: {vmsg}")
        if val <= ub:
            ub_sched, ub = out / "schedule.csv", val
    if status == cp_model.OPTIMAL:
        lb = ub                                     # nothing better than the validated incumbent exists
    T0 = tt0(inst)
    return dict(engine="E1 block-pair CP-SAT", status=name, proven_optimal=status == cp_model.OPTIMAL, ub_e=ub,
                lb_e=lb, TT0=T0, gap_e_pct=None if lb is None else 100 * (ub - lb) / ub,
                gap_d_pct=None if lb is None else (100 * (ub - lb) / (ub - T0) if ub > T0 else 0.0),
                schedule=str(ub_sched), donor_ub_e=donor_ub, seconds=round(time.monotonic() - t_wall, 2),
                branches=solver.num_branches, workers=workers, **stats)
