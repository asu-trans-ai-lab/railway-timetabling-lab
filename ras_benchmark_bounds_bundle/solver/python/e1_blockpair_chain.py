"""E1 on a resource-chain model (solver/python/siding_model.py) -- the block-pair CP-SAT formulation of
solver/python/e1_blockpair.py rebuilt on chains with station tracks (standing holds the track), capacity = tracks.
Used for the toy corridor and its control-cell versions (adapters/toy_corridor.py).

  * Blocks. A train's chain is cut after every leg on which it may stand and at the origin; inside a block it runs
    without stopping, so one start variable S_j fixes every entry of the block. A standing leg ends the block.
  * Single track. Every pair of blocks of different trains that share single-track resources gets the exact set of
    start differences that make two holds [entry, exit + H) overlap; one Boolean per allowed interval.
  * Two tracks. Cumulative with capacity 2 over interval variables (a standing leg's interval has variable size).
  * Windows. Train k arrives by r_k + UB - sum_{j != k} free_j (or by a horizon when there is no UB yet).
  * Symmetry (optional). Identical trains held outside the network keep release order on every block.
"""
from __future__ import annotations

from ortools.sat.python import cp_model


def blocks_of(train):
    """[(first leg, last leg)]: a new block after every leg that allows standing."""
    out, start = [], 0
    for i, (_, _, stand) in enumerate(train.path):
        if stand or i == len(train.path) - 1:
            out.append((start, i))
            start = i + 1
    return out


def build(model, ub: int | None, hint: dict | None, symmetry: bool = False, horizon: int | None = None):
    """ub: windows from the incumbent; ub None: only the horizon (every train done by `horizon`), no objective bound."""
    H = model.headway
    m = cp_model.CpModel()
    free = [sum(p for _, p, _ in t.path) for t in model.trains]
    slack_of = {k: (ub - sum(free)) if ub is not None else (horizon - t.release - free[k])
                for k, t in enumerate(model.trains)}
    S, A, info, dom = {}, {}, {}, {}
    single, variable = {}, {}
    for k, t in enumerate(model.trains):
        bl = blocks_of(t)
        info[k] = bl
        base = t.release
        for j, (a, b) in enumerate(bl):
            before = sum(p for _, p, _ in t.path[:a])
            dom[k, j] = (base + before, base + before + slack_of[k])
            S[k, j] = m.NewIntVar(*dom[k, j], f"S{k}_{j}")
        A[k] = m.NewIntVar(t.release + free[k], t.release + free[k] + slack_of[k], f"A{k}")
        for j, (a, b) in enumerate(bl):
            off = 0
            for i in range(a, b + 1):
                r, p, stand = t.path[i]
                tracks = model.resources[r].tracks
                entry = S[k, j] + off
                if i == b:                                   # the block's last leg: exit is the next start / arrival
                    exit_ = S[k, j + 1] if j + 1 < len(bl) else A[k]
                    if stand:
                        m.Add(exit_ >= entry + p)
                    else:
                        m.Add(exit_ == entry + p)
                    variable_size = bool(stand) and int(stand) != 2      # a pocket: it stands clear after p
                else:
                    exit_ = entry + p
                    variable_size = False
                if i == 0 and not t.terminal_origin:         # held on its first resource from release
                    start, variable_size = t.release, True
                else:
                    start = entry
                if tracks == 1 and not variable_size:
                    single.setdefault(r, []).append((k, j, off, p + H))
                else:
                    end = m.NewIntVar(0, 10 ** 6, f"e{k}_{i}")
                    m.Add(end == (entry + p if (i == b and int(stand) == 2) else exit_) + H)
                    size = m.NewIntVar(0, 10 ** 6, f"z{k}_{i}")
                    st = m.NewIntVar(0, 10 ** 6, f"st{k}_{i}")
                    m.Add(st == start)
                    variable.setdefault(r, []).append(m.NewIntervalVar(st, size, end, f"iv{k}_{i}"))
                off += p
    for r, ivs in variable.items():                          # single track with variable holds: NoOverlap
        if model.resources[r].tracks == 1:
            allv = list(ivs)
            for (k, j, off, length) in single.get(r, []):
                st = m.NewIntVar(0, 10 ** 6, f"fs{r}_{k}_{j}_{off}")
                m.Add(st == S[k, j] + off)
                allv.append(m.NewFixedSizeIntervalVar(st, length, f"fiv{r}_{k}_{j}_{off}"))
            m.AddNoOverlap(allv)
            single.pop(r, None)
    for r, ivs in variable.items():                          # two tracks: cumulative
        if model.resources[r].tracks >= 2:
            m.AddCumulative(ivs, [1] * len(ivs), model.resources[r].tracks)
    by_block = {}
    for r, holds in single.items():
        for (k, j, off, length) in holds:
            by_block.setdefault((k, j), []).append((r, off, length))
    keys = sorted(by_block)
    pairs = 0
    for x in range(len(keys)):
        for y in range(x + 1, len(keys)):
            (k1, j1), (k2, j2) = keys[x], keys[y]
            if k1 == k2:
                continue
            res1 = {}
            for r, off, length in by_block[(k1, j1)]:
                res1.setdefault(r, []).append((off, length))
            forbidden = []
            for r, off2, len2 in by_block[(k2, j2)]:
                for off1, len1 in res1.get(r, []):
                    forbidden.append((off1 - off2 - len2 + 1, off1 + len1 - off2 - 1))
            if not forbidden:
                continue
            a_lo, a_hi = dom[k1, j1]
            b_lo, b_hi = dom[k2, j2]
            d_lo, d_hi = b_lo - a_hi, b_hi - a_lo
            forbidden.sort()
            merged = []
            for lo, hi in forbidden:
                if merged and lo <= merged[-1][1] + 1:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
                else:
                    merged.append((lo, hi))
            allowed, cur = [], d_lo
            for lo, hi in merged:
                if lo > cur:
                    allowed.append((cur, min(lo - 1, d_hi)))
                cur = max(cur, hi + 1)
            if cur <= d_hi:
                allowed.append((cur, d_hi))
            allowed = [(lo, hi) for lo, hi in allowed if lo <= hi]
            if len(allowed) == 1 and allowed[0] == (d_lo, d_hi):
                continue                                     # the windows already keep them apart
            diff = S[k2, j2] - S[k1, j1]
            if not allowed:
                m.Add(diff >= d_hi + 1)                      # infeasible: no schedule within the windows
                continue
            if len(allowed) == 1:
                m.Add(diff >= allowed[0][0]); m.Add(diff <= allowed[0][1])
                continue
            lits = []
            for lo, hi in allowed:
                z = m.NewBoolVar("")
                m.Add(diff >= lo).OnlyEnforceIf(z)
                m.Add(diff <= hi).OnlyEnforceIf(z)
                lits.append(z)
            m.AddExactlyOne(lits)
            pairs += 1
    if symmetry:
        groups = {}
        for k, t in enumerate(model.trains):
            groups.setdefault((t.direction, t.terminal_origin, tuple(t.path)), []).append(k)
        for ks in groups.values():
            ks.sort(key=lambda k: model.trains[k].release)
            for k, l in zip(ks, ks[1:]):
                if not model.trains[k].terminal_origin:
                    continue
                for j in range(len(info[k])):
                    m.Add(S[k, j] <= S[l, j])
                m.Add(A[k] <= A[l])
    objective = sum(A[k] - t.release for k, t in enumerate(model.trains))
    if ub is not None:
        m.Add(objective <= ub)
    m.Minimize(objective)
    if hint:
        for (k, j), v in hint["S"].items():
            m.AddHint(S[k, j], v)
        for k, v in hint["A"].items():
            m.AddHint(A[k], v)
    return m, S, A, info, pairs


def hint_from(model, schedule):
    hS, hA = {}, {}
    for k, t in enumerate(model.trains):
        legs = schedule[t.train_id]
        for j, (a, b) in enumerate(blocks_of(t)):
            hS[k, j] = legs[a][2]
        hA[k] = legs[-1][3]
    return {"S": hS, "A": hA}


def schedule_from(model, solver, S, A, info):
    rows = []
    for k, t in enumerate(model.trains):
        bl = info[k]
        for j, (a, b) in enumerate(bl):
            s = solver.Value(S[k, j])
            off = 0
            for i in range(a, b + 1):
                r, p, _ = t.path[i]
                entry = s + off
                exit_ = (solver.Value(S[k, j + 1]) if j + 1 < len(bl) else solver.Value(A[k])) if i == b else entry + p
                rows.append((t.train_id, i, model.resources[r].name, entry, exit_))
                off += p
    return rows
