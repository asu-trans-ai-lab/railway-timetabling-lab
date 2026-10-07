"""LP lower bound from the space-time arc formulation (Prof. Zhou's IJTT' network), on the resource-chain Model the
engines read.

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
sum of the arcs holding r at minute t <= tracks_r. With x binary this is the timetabling model without the single-track
stretch rows of cell models; with x >= 0 its optimum is a valid lower bound. Each train's network is a shortest-path
polytope, so this LP bound equals the best Lagrangian bound max_lambda L(lambda) of the capacity rows (Geoffrion 1974);
the subgradient in the kernel approaches it from below.

Window: with a known timetable value U, every train's delay in a timetable of value <= U is at most U - TT0, so every
event lies in [earliest, earliest + U - TT0]; this keeps every timetable of value <= U.

    solve_lp(model, window_ub, seconds) -> {"bound", "status", "seconds", "vars", "rows", "nonzeros"}
"""
from __future__ import annotations

import time

import numpy as np


def solve_lp(model, window_ub: int, seconds: float = 600.0, threads: int = 4, verbose: bool = False) -> dict:
    import highspy
    H = model.headway
    free = [sum(p for _, p, _ in t.path) for t in model.trains]
    slack = window_ub - sum(free)
    if slack < 0:
        return {"status": "WINDOW_BELOW_TT0"}
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
    cost, starts, idx, val = [], [], [], []

    def arc(c, tail, head, holds):
        starts.append(len(idx))
        cost.append(float(c))
        if tail is not None:
            idx.append(tail); val.append(-1.0)
        if head is not None:
            idx.append(head); val.append(1.0)
        for r, a, b in holds:
            for t in range(max(a, 0), min(b, T)):
                idx.append(cap_row(r, t)); val.append(1.0)

    for k, tr in enumerate(model.trains):
        L, w = len(tr.path), windows[k]
        F = lambda i, t: node_index.get((k, "F", i, t))      # noqa: E731
        G = lambda i, t: node_index.get((k, "G", i, t))      # noqa: E731
        src = node_index[(k, "src")]
        r0, p0, _ = tr.path[0]
        for t in range(tr.release, tr.release + slack + 1):  # depart; the source row counts departures (= 1)
            holds = [(r0, t, t + p0)] + ([] if tr.terminal_origin else [(r0, tr.release, t)])
            starts.append(len(idx)); cost.append(float(model.alpha * (t - tr.release) + p0))
            idx.append(src); val.append(1.0)
            head = F(0, t + p0)
            idx.append(head); val.append(1.0)
            for r, a, b in holds:
                for tt in range(max(a, 0), min(b, T)):
                    idx.append(cap_row(r, tt)); val.append(1.0)
        for i in range(L):
            r, p, s = tr.path[i]
            s = int(s)
            stand = s == 1 or model.resources[r].siding and s != 2
            lo, hi = w[i]
            for t in range(lo, hi + 1):
                if stand and t + 1 <= hi:
                    arc(model.beta, F(i, t), F(i, t + 1), [(r, t, t + 1)])
                if i == L - 1:
                    arc(0, F(i, t), None, [(r, t, t + H)])
                    continue
                r2, p2, _ = tr.path[i + 1]
                if t + p2 <= w[i + 1][1]:
                    arc(p2, F(i, t), F(i + 1, t + p2), [(r, t, t + H), (r2, t, t + p2)])
                if s == 2:
                    arc(0, F(i, t), G(i, t), [(r, t, t + H)])
                    if t + 1 <= hi:
                        arc(model.beta, G(i, t), G(i, t + 1), [])
                    if t + p2 <= w[i + 1][1]:
                        arc(p2, G(i, t), F(i + 1, t + p2), [(r2, t, t + p2)])
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
    h.setOptionValue("output_flag", verbose)
    h.setOptionValue("time_limit", float(seconds))
    h.setOptionValue("threads", threads)
    h.addRows(n_rows, lo_r, hi_r, 0, np.zeros(0, dtype=np.int32), np.zeros(0, dtype=np.int32), np.zeros(0))
    n = len(cost)
    h.addCols(n, np.array(cost), np.zeros(n), np.full(n, highspy.kHighsInf), len(idx),
              np.array(starts, dtype=np.int32), np.array(idx, dtype=np.int32), np.array(val))
    t0 = time.time()
    h.run()
    status = h.modelStatusToString(h.getModelStatus())
    out = {"status": status, "seconds": round(time.time() - t0, 2), "vars": n, "rows": n_rows, "nonzeros": len(idx),
           "window_ub": window_ub}
    if status == "Optimal":
        out["bound"] = h.getInfo().objective_function_value
    return out
