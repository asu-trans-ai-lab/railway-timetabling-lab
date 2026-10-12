"""The space-time arc formulation (Prof. Zhou's IJTT' network) on the resource-chain Model the engines read: its LP
is a lower bound (solve_lp), and with binary arcs it is a MIP of the timetabling problem (build(..., integer=True),
used by solver/python/mip_formulations.py).

Every train has its own time-expanded network whose nodes are the states of the single-train DP
(solver/cpp/siding_lr.cpp, train_dp):
    F(i, t)   the head is at the end of leg i at minute t, still holding leg i's resource
    G(i, t)   after a pocket leg (a wait point), standing clear of the main track, holding nothing
Arcs (x >= 0, one unit of flow per train):
    depart at t >= R     source -> F(0, t + p0)          cost alpha (t - R) + p0   holds r0 [t, t + p0)
                                                                                   (and [R, t) if the origin is inside)
    stand (may stand)    F(i, t) -> F(i, t + 1)          cost beta                 holds r_i [t, t + 1)
    run on               F(i, t) -> F(i+1, t + p)        cost p                    holds r_i [t, t + H), r_i+1 [t, t + p)
    step clear (pocket)  F(i, t) -> G(i, t)              cost 0                    holds r_i [t, t + H)
    wait clear           G(i, t) -> G(i, t + 1)          cost beta                 holds nothing
    leave the pocket     G(i, t) -> F(i+1, t + p)        cost p                    holds r_i+1 [t, t + p)
    arrive               F(L-1, t) -> sink               cost 0                    holds r_L-1 [t, t + H)
Rows: flow conservation at every node, one departure per train, and the capacity of every resource in every minute:
sum of the arcs holding r at minute t <= tracks_r. With stretch=True also the single-track stretch rows of cell models
(as in mip_cumflow): two opposing trains are never both inside a stretch, a train being inside from its entry into the
first cell to the exit of the last cell + H. With x >= 0 the optimum is a valid lower bound. Each train's network is a
shortest-path polytope, so this LP bound equals the best Lagrangian bound max_lambda L(lambda) of the capacity rows
(Geoffrion 1974); the subgradient in the kernel approaches it from below.

Window: with a known timetable value U, every train's delay in a timetable of value <= U is at most U - TT0, so every
event lies in [earliest, earliest + U - TT0]; this keeps every timetable of value <= U.

    solve_lp(model, window_ub, seconds) -> {"bound", "status", "seconds", "vars", "rows", "nonzeros"}
"""
from __future__ import annotations

import time
from collections import defaultdict

import numpy as np


def stretches(model):
    """parent name -> cells: the single-track stretches cut into cells ("parent#j")."""
    out = defaultdict(list)
    for r, res in enumerate(model.resources):
        if "#" in res.name and res.tracks == 1 and not res.siding:
            out[res.name.split("#")[0]].append(r)
    return out


def build(model, window_ub: int, integer: bool = False, stretch: bool = False, max_slack: int | None = None) -> dict | None:
    """{"h", "decode" (x -> schedule, integer only), "vars", "rows", "nonzeros"}; None if U < TT0.
    max_slack: also cap each train's delay (a waiting limit such as MaxTrainWaitingTime; a service rule)."""
    import highspy
    H = model.headway
    free = [sum(p for _, p, _ in t.path) for t in model.trains]
    slack = window_ub - sum(free)
    if slack < 0:
        return None
    if max_slack is not None:
        slack = min(slack, max_slack)
    nres = len(model.resources)
    T = max(t.release + f for t, f in zip(model.trains, free)) + slack + H + 2
    cap_row = lambda r, t: n_node_rows + r * T + t          # noqa: E731
    # node rows: per train, F(i, t) and G(i, t) on each leg's window, plus the departure row
    node_index, n_node_rows = {}, 0
    windows = []
    for k, tr in enumerate(model.trains):
        e, w = tr.release, []
        for r, p, s in tr.path:
            e += p
            w.append((e, e + slack))                         # end of leg i: earliest .. latest
        windows.append(w)
        for i, (lo, hi) in enumerate(w):
            for t in range(lo, hi + 1):
                node_index[(k, "F", i, t)] = n_node_rows
                n_node_rows += 1
                if int(tr.path[i][2]) == 2 and i < len(tr.path) - 1:
                    node_index[(k, "G", i, t)] = n_node_rows
                    n_node_rows += 1
        node_index[(k, "src")] = n_node_rows
        n_node_rows += 1
    # stretch spans: (k, parent) -> (first leg, last leg) inside the stretch
    spans, spans_of = {}, defaultdict(list)
    if stretch:
        for parent, cells in stretches(model).items():
            cells = set(cells)
            for k, tr in enumerate(model.trains):
                on = [i for i, (r, _, _) in enumerate(tr.path) if r in cells]
                if on:
                    spans[(k, parent)] = (min(on), max(on))
                    spans_of[k].append((parent, min(on), max(on)))
    inside = defaultdict(list)                               # (k, parent) -> [(column, a, b)]: inside over [a, b)
    cost, starts, idx, val, meta = [], [], [], [], []

    def arc(k, kind, i, t, c, tail, head, holds, into=None, length=0):
        """into: the leg this arc enters at t (running `length`); kind 'run'/'leave'/'dep' enter, others do not."""
        col = len(cost)
        starts.append(len(idx))
        cost.append(float(c))
        meta.append((k, kind, i, t))
        if tail is not None:
            idx.append(tail); val.append(-1.0)
        if head is not None:
            idx.append(head); val.append(1.0)
        for r, a, b in holds:
            for tt in range(max(a, 0), min(b, T)):
                idx.append(cap_row(r, tt)); val.append(1.0)
        for parent, first, last in spans_of[k]:
            if into is not None and first <= into <= last:
                inside[(k, parent)].append((col, t, t + length))
            if kind == "stand" and first <= i <= last:
                inside[(k, parent)].append((col, t, t + 1))
            if kind in ("run", "clear", "arrive") and i == last:    # leaves the last cell at t: H more
                inside[(k, parent)].append((col, t, t + H))

    for k, tr in enumerate(model.trains):
        L, w = len(tr.path), windows[k]
        F = lambda i, t: node_index.get((k, "F", i, t))      # noqa: E731
        G = lambda i, t: node_index.get((k, "G", i, t))      # noqa: E731
        src = node_index[(k, "src")]
        r0, p0, _ = tr.path[0]
        for t in range(tr.release, tr.release + slack + 1):  # depart; the source row counts departures (= 1)
            holds = [(r0, t, t + p0)] + ([] if tr.terminal_origin else [(r0, tr.release, t)])
            # the source row takes +1 (it counts departures), the head node +1 (inflow)
            arc(k, "dep", -1, t, model.alpha * (t - tr.release) + p0, None, F(0, t + p0), holds, into=0, length=p0)
            idx.insert(starts[-1], src); val.insert(starts[-1], 1.0)
        for i in range(L):
            r, p, s = tr.path[i]
            s = int(s)
            stand = s == 1 or model.resources[r].siding and s != 2
            lo, hi = w[i]
            for t in range(lo, hi + 1):
                if stand and t + 1 <= hi:
                    arc(k, "stand", i, t, model.beta, F(i, t), F(i, t + 1), [(r, t, t + 1)])
                if i == L - 1:
                    arc(k, "arrive", i, t, 0, F(i, t), None, [(r, t, t + H)])
                    continue
                r2, p2, _ = tr.path[i + 1]
                if t + p2 <= w[i + 1][1]:
                    arc(k, "run", i, t, p2, F(i, t), F(i + 1, t + p2), [(r, t, t + H), (r2, t, t + p2)],
                        into=i + 1, length=p2)
                if s == 2:
                    arc(k, "clear", i, t, 0, F(i, t), G(i, t), [(r, t, t + H)])
                    if t + 1 <= hi:
                        arc(k, "wait", i, t, model.beta, G(i, t), G(i, t + 1), [])
                    if t + p2 <= w[i + 1][1]:
                        arc(k, "leave", i, t, p2, G(i, t), F(i + 1, t + p2), [(r2, t, t + p2)], into=i + 1, length=p2)
    # objective constant: none (costs are absolute); a train's arrival arc carries no cost
    n_rows = n_node_rows + nres * T
    lo_r = np.zeros(n_rows)
    hi_r = np.zeros(n_rows)
    for k in range(len(model.trains)):
        lo_r[node_index[(k, "src")]] = hi_r[node_index[(k, "src")]] = 1.0
    for r in range(nres):
        lo_r[n_node_rows + r * T: n_node_rows + (r + 1) * T] = -highspy.kHighsInf
        hi_r[n_node_rows + r * T: n_node_rows + (r + 1) * T] = model.resources[r].tracks
    # sink-less flow: the conservation row of F / G nodes is in - out = 0; the arrive arc only leaves its node
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.addRows(n_rows, lo_r, hi_r, 0, np.zeros(0, dtype=np.int32), np.zeros(0, dtype=np.int32), np.zeros(0))
    n = len(cost)
    h.addCols(n, np.array(cost), np.zeros(n), np.full(n, highspy.kHighsInf), len(idx),
              np.array(starts, dtype=np.int32), np.array(idx, dtype=np.int32), np.array(val))
    n_stretch = 0
    if spans:                                                # opposing pairs: inside_a(t) + inside_b(t) <= 1
        by_parent = defaultdict(list)
        for (k, parent) in spans:
            by_parent[parent].append(k)
        r_lo, r_hi, r_st, r_ix, r_v = [], [], [], [], []
        for parent, ks in by_parent.items():
            cover = {}
            for k in ks:
                c = defaultdict(list)
                for col, a, b in inside[(k, parent)]:
                    for tt in range(a, b):
                        c[tt].append(col)
                cover[k] = c
            for x in range(len(ks)):
                for y in range(x + 1, len(ks)):
                    ka, kb = ks[x], ks[y]
                    if model.trains[ka].direction == model.trains[kb].direction:
                        continue
                    for tt in sorted(set(cover[ka]) & set(cover[kb])):
                        r_st.append(len(r_ix))
                        for col in cover[ka][tt] + cover[kb][tt]:
                            r_ix.append(col); r_v.append(1.0)
                        r_lo.append(-highspy.kHighsInf); r_hi.append(1.0)
        if r_lo:
            h.addRows(len(r_lo), np.array(r_lo), np.array(r_hi), len(r_ix), np.array(r_st, dtype=np.int32),
                      np.array(r_ix, dtype=np.int32), np.array(r_v))
            n_stretch = len(r_lo)
    if integer:
        h.changeColsIntegrality(n, np.arange(n, dtype=np.int32), np.array([highspy.HighsVarType.kInteger] * n))
        h.changeColsBounds(n, np.arange(n, dtype=np.int32), np.zeros(n), np.ones(n))

    def decode(x):
        times = defaultdict(dict)                            # k -> {leg i: entry minute, L: arrival}
        for col in np.nonzero(np.asarray(x) > 0.5)[0]:
            k, kind, i, t = meta[col]
            if kind == "dep":
                times[k][0] = t
            elif kind in ("run", "leave"):
                times[k][i + 1] = t
            elif kind == "arrive":
                times[k][i + 1] = t
        sched = {}
        for k, tr in enumerate(model.trains):
            e = [times[k][i] for i in range(len(tr.path) + 1)]
            sched[tr.train_id] = [(i, model.resources[r].name, e[i], e[i + 1]) for i, (r, _, _) in enumerate(tr.path)]
        return sched
    return {"h": h, "const": 0.0, "decode": decode, "vars": n, "rows": n_rows + n_stretch, "nonzeros": len(idx),
            "stretch_rows": n_stretch, "cap_row0": n_node_rows, "T": T}


def solve_lp(model, window_ub: int, seconds: float = 600.0, threads: int = 4, verbose: bool = False) -> dict:
    b = build(model, window_ub)
    if b is None:
        return {"status": "WINDOW_BELOW_TT0"}
    h = b["h"]
    h.setOptionValue("output_flag", verbose)
    h.setOptionValue("time_limit", float(seconds))
    h.setOptionValue("threads", threads)
    t0 = time.time()
    h.run()
    status = h.modelStatusToString(h.getModelStatus())
    out = {"status": status, "seconds": round(time.time() - t0, 2), "vars": b["vars"], "rows": b["rows"],
           "nonzeros": b["nonzeros"], "window_ub": window_ub}
    if status == "Optimal":
        out["bound"] = h.getInfo().objective_function_value
    return out
