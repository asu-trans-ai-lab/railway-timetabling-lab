"""
validate_schedule.py — Gate 1: INDEPENDENT timetable validator (no solver code paths).

Schedule contract (results/schedules/<instance>_<method>.csv):
    instance_id, method, train_id, seq, from_node, to_node, link_id, enter_time, leave_time
Semantics validated against the NATIVE instance files only:
  1 route continuity (first=origin, chained nodes, last=destination)
  2 travel time: leave-enter = dwell + tau(link,dir,smult); dwell=0 unless link_type 4; dwell<=maxwait
  3 no waiting at intermediate nodes: next.enter == prev.leave
  4 departure window: first enter in [entry, entry+slack]
  5 direction: one-way links traversed a->b only
  6 capacity: per (link,bin) occupancy [enter, leave+headway) count <= cap; MOW bins cap 0
  7 horizon: leave+headway-1 < T
  8 objective recompute: sum |arrival - intended| (== --expect-obj if given)

Usage: python validate_schedule.py <native_dir> <schedule.csv> [--expect-obj X]
Exit 0 = VALID, 1 = VIOLATIONS.
"""
from __future__ import annotations
import os, sys, csv, math, argparse
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "solvers"))
from priority_heuristics import load, tt_bins   # instance reading + the reference run-time formula only


def validate(native_dir, sched_csv, expect_obj=None):
    links, trains, mow, cfg = load(native_dir)
    HW, T = cfg["headway"], cfg["T"]
    lk_by_id = {lk["id"]: lk for lk in links}
    tr_by_id = {t["id"]: t for t in trains}
    rows = list(csv.DictReader(open(sched_csv)))
    errs = []
    bytrain = defaultdict(list)
    for r in rows:
        bytrain[r["train_id"]].append(r)
    usage = defaultdict(set)     # (link,bin) -> {train_id}: one physical train = one occupancy,
                                 # even when a folded (hold-and-reverse) trajectory covers a cell twice
    total_dev = 0.0
    if set(bytrain) != set(tr_by_id):
        errs.append(f"train set mismatch: schedule has {sorted(bytrain)} vs instance {sorted(tr_by_id)}")
    for tid, segs in bytrain.items():
        tr = tr_by_id.get(tid)
        if tr is None:
            continue
        segs = sorted(segs, key=lambda r: int(r["seq"]))
        prev_leave, prev_to = None, None
        for si, r in enumerate(segs):
            fn, tn = int(r["from_node"]), int(r["to_node"])
            lid = int(r["link_id"]); en, lv = int(r["enter_time"]), int(r["leave_time"])
            lk = lk_by_id.get(lid)
            if lk is None:
                errs.append(f"{tid} seq{si}: unknown link {lid}"); continue
            if {fn, tn} != {lk["a"], lk["b"]}:
                errs.append(f"{tid} seq{si}: link {lid} does not connect {fn}-{tn}")
            ab = (fn == lk["a"])
            if not ab and not lk["bidir"]:
                errs.append(f"{tid} seq{si}: one-way link {lid} traversed backward {fn}->{tn}")   # check 5
            tau = tt_bins(lk, ab, tr["smult"])
            dwell = (lv - en) - tau
            if dwell < 0:
                errs.append(f"{tid} seq{si}: leave-enter {lv-en} < tau {tau}")                    # check 2
            elif dwell > 0 and lk["ltype"] != 4:
                errs.append(f"{tid} seq{si}: dwell {dwell} on non-siding link {lid}")
            elif dwell > cfg["maxwait"]:
                errs.append(f"{tid} seq{si}: dwell {dwell} > maxwait {cfg['maxwait']}")
            if si == 0:
                if fn != tr["o"]:
                    errs.append(f"{tid}: starts at {fn}, origin is {tr['o']}")                    # check 1
                if not (tr["entry"] <= en <= tr["entry"] + cfg["slack"]):
                    errs.append(f"{tid}: departure {en} outside [{tr['entry']},{tr['entry']+cfg['slack']}]")
            else:
                if fn != prev_to:
                    errs.append(f"{tid} seq{si}: discontinuity {prev_to} -> {fn}")                # check 1
                if en != prev_leave:
                    errs.append(f"{tid} seq{si}: waits at node {fn} ({prev_leave}->{en})")        # check 3
            if lv + HW - 1 >= T:
                errs.append(f"{tid} seq{si}: occupancy exceeds horizon")                          # check 7
            for b in range(en, lv + HW):
                usage[(lid, b)].add(tid)
            if cfg.get("monotone"):                                                            # check 9
                tr = next((t for t in trains if t["id"] == tid), None)
                if tr and ((int(r["to_node"]) > int(r["from_node"])) != (tr["dd"] > tr["o"])):
                    errs.append(f"{tid} seq{si}: reverse move {r['from_node']}->{r['to_node']} "
                                f"(MonotoneRouting=1 forbids reversing)")
            prev_leave, prev_to = lv, tn
        if segs and prev_to != tr["dd"]:
            errs.append(f"{tid}: ends at {prev_to}, destination is {tr['dd']}")
        if segs:
            total_dev += abs(prev_leave - tr["intended"])
    # check 6: capacity incl. MOW
    mow_zero = set()
    for (A, B, st, en) in mow:
        for lk in links:
            if {lk["a"], lk["b"]} == {A, B}:
                for b in range(int(st), min(T, math.ceil(en))):
                    mow_zero.add((lk["id"], b))
    for (lid, b), trns in usage.items():
        u = len(trns)                                     # distinct trains, not schedule rows
        cap = 0 if (lid, b) in mow_zero else lk_by_id[lid]["cap"]
        if u > cap:
            errs.append(f"capacity violation link {lid} bin {b}: {u} > {cap}")
    if expect_obj is not None and abs(total_dev - expect_obj) > 1e-6:
        errs.append(f"objective mismatch: recomputed {total_dev} vs reported {expect_obj}")       # check 8
    return errs, total_dev


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("native_dir"); ap.add_argument("schedule_csv")
    ap.add_argument("--expect-obj", type=float, default=None)
    a = ap.parse_args()
    errs, dev = validate(a.native_dir, a.schedule_csv, a.expect_obj)
    if errs:
        print(f"INVALID ({len(errs)} violations, recomputed dev={dev:.1f}):")
        for e in errs[:20]:
            print("  -", e)
        sys.exit(1)
    print(f"VALID: all checks pass; recomputed objective = {dev:.1f}")
