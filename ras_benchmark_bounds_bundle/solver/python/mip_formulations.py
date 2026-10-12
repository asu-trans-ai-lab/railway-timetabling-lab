"""Three integer formulations of one timetabling problem (the resource-chain Model of solver/python/siding_model.py),
for Prof. Zhou's Oct 8 request: code every formulation, show they are equivalent, then compare them.

  arc         time-indexed space-time arc flow (solver/python/lp_spacetime.py, binary arcs)
              x_a = 1  <=>  the train uses arc a of its own time-expanded network (depart at t, run on at t, stand ...)
  cumulative  cumulative flow, Meng & Zhou (2014) model P3; the "by time t" variables of Bertsimas & Stock Patterson
              (1998) (solver/python/mip_cumflow.py)
              E[k,i,t] = 1  <=>  train k has entered leg i by minute t;  occupancy = E[k,i,t] - E[k,i+1,t-H]
  bigm        event times + precedence binaries (the big-M disjunctive formulation; Zhou & Zhong 2007 branch on the
              same precedence decisions)
              s[k,i] = minute train k enters leg i (s[k,L] = arrival);
              z = 1  <=>  hold a ends (+H) before hold b starts, on a resource both use

Every formulation reads the same Model, uses the same window (event of leg i within [earliest, earliest + U - TT0],
which keeps every timetable of value <= U; see the proof in lp_spacetime.py) and the same objective (mip_cumflow.py, cost section)
    min sum_k [ alpha sum_{t >= R_k} (1 - E[k,0,t]) + sum_i p_ki + beta (sum_t (E[k,0,t] - E[k,L_k,t]) - sum_i p_ki) ]
    (big-M: alpha (s[k,0] - R_k) + sum_i p_ki + beta (s[k,L_k] - s[k,0] - sum_i p_ki)),
and its timetable goes through the same validator (solver/python/siding_validate.py).

The big-M model, written out (hold of (k, i): [start, end + H), start = s[k,i] (the release on the first leg of a train
whose origin is inside), end = s[k,i+1] (s[k,i] + p for a pocket leg)):
    s[k,0] >= R_k,   s[k,i+1] - s[k,i] >= p_ki   (= where the train may not stand)
    single-track resource, holds a, b of different trains:
        start_b >= end_a + H - M_ab (1 - z_ab)      start_a >= end_b + H - M_ba z_ab
        with M_ab = max end_a + H - min start_b from the window (the smallest valid value)
    resource with c >= 2 tracks: track assignment y[a, j] (sum_j y = 1), and the same pair rows only when both use
        track j:  - M (2 - y[a,j] - y[b,j]) on the right; holds on one track form an interval graph, so a c-colouring
        exists iff no minute has more than c holds (the capacity row of the other two formulations)
    stretch (single track cut into cells): the same disjunction for each opposing pair over
        [entry of the first cell, exit of the last cell + H)

    solve(model, form, U, solver="highs"|"cplex", seconds, threads) -> dict
    unit_model() -> the hand-solvable 2-train case (optimum 8)
"""
from __future__ import annotations

import re
import time
from collections import defaultdict

import numpy as np

from solver.python.siding_model import Model, Resource, Train

FORMS = ("arc", "cumulative", "bigm")


def unit_model() -> Model:
    """Two trains released at 0 share one resource: A runs 3, B runs 2, H = 1, minimise the sum of completion times.
    A first: 3 + (3 + 1 + 2) = 9; B first: 2 + (2 + 1 + 3) = 8. The optimum is 8."""
    res = [Resource("R", "w", "e", 1, False, frozenset())]
    trains = [Train("A", 0, "w", "e", 1, True, [(0, 3, 0)]), Train("B", 0, "w", "e", 1, True, [(0, 2, 0)])]
    return Model("unit2", 1, 1, 1, res, trains, [])


def window(model: Model, U: int):
    """earliest[k][i] (entry of leg i, i = L: arrival) and the slack U - TT0."""
    free = [sum(p for _, p, _ in t.path) for t in model.trains]
    earliest = []
    for t in model.trains:
        acc, e = t.release, []
        for _, p, _ in t.path:
            e.append(acc)
            acc += p
        e.append(acc)
        earliest.append(e)
    return earliest, U - sum(free)


def horizon(model: Model, U: int) -> int:
    earliest, slack = window(model, U)
    return max(e[-1] for e in earliest) + slack + model.headway + 1


def build_bigm(model: Model, U: int, share: bool = False, cycles: bool = False) -> dict | None:
    """share: one precedence variable per pair of trains and run of shared single-track legs (consecutive in both
    paths, no multi-track leg or siding in between, inside one block of each, a block = the legs a train runs without
    standing) instead of one per resource -- on single
    track the order of two trains cannot change inside such a run (H >= 1); between runs they may use parallel
    resources and pass. The choice of integer variables Liebchen studies for PESP. cycles: the 3-cycle (transitivity)
    inequalities z_ab + z_bc - z_ac in [0, 1] for three trains on one single-track resource, PESP's cycle
    inequalities for triangles."""
    import highspy
    H = model.headway
    earliest, slack = window(model, U)
    if slack < 0:
        return None
    lo, hi, integ, cost = [], [], [], []

    def var(l, u, c=0.0, integer=True):
        lo.append(float(l)); hi.append(float(u)); cost.append(float(c)); integ.append(integer)
        return len(lo) - 1

    S = {}
    const = 0.0
    for k, tr in enumerate(model.trains):
        for i in range(len(tr.path) + 1):
            S[k, i] = var(earliest[k][i], earliest[k][i] + slack)
        run = sum(p for _, p, _ in tr.path)
        L = len(tr.path)
        # run (1 - beta) + alpha (s0 - R) + beta (sL - s0)
        const += run * (1 - model.beta) - model.alpha * tr.release
        cost[S[k, 0]] += model.alpha - model.beta
        cost[S[k, L]] += model.beta
    r_lo, r_hi, r_st, r_ix, r_v = [], [], [], [], []
    inf = highspy.kHighsInf

    def row(terms, l, u):
        r_st.append(len(r_ix))
        for c, v in terms:
            r_ix.append(c); r_v.append(float(v))
        r_lo.append(float(l)); r_hi.append(float(u))

    for k, tr in enumerate(model.trains):
        for i, (r, p, stand) in enumerate(tr.path):
            may_stand = int(stand) in (1, 2) or model.resources[r].siding
            row([(S[k, i + 1], 1), (S[k, i], -1)], p, inf if may_stand else p)

    # a hold: (train, start = (column | None, constant), end = (column, constant)) -> [start, end + H)
    def hold(k, i):
        tr = model.trains[k]
        r, p, stand = tr.path[i]
        start = (None, tr.release) if (i == 0 and not tr.terminal_origin) else (S[k, i], 0)
        end = (S[k, i], p) if int(stand) == 2 else (S[k, i + 1], 0)
        return start, end

    def rng(term):                       # (min, max) of a hold endpoint over the window
        col, c = term
        return (c, c) if col is None else (lo[col] + c, hi[col] + c)

    def lin(term, sign):                 # linear terms and constant of sign * term
        col, c = term
        return ([] if col is None else [(col, sign)]), sign * c

    n_pairs = n_fixed = 0
    blk = {}                                             # (k, i) -> block of leg i of train k
    for k, tr in enumerate(model.trains):
        b_ = 0
        for i, (r, p, stand) in enumerate(tr.path):
            blk[k, i] = b_
            if int(stand) in (1, 2) or model.resources[r].siding:
                b_ += 1
    pos = [{r: i for i, (r, _, _) in enumerate(tr.path)} for tr in model.trains]
    run_of = {}                                          # (k, m) -> {leg i of k: run id} for k < m

    def run_id(k, i, m, j):
        """Shared legs of trains k < m form runs: consecutive in both paths and inside one block of each. The order
        of k and m cannot change inside a run on single track (H >= 1); between runs they may use different
        (parallel) resources and pass, so each run gets its own variable."""
        if (k, m) not in run_of:
            # only single-track legs: on a resource with 2+ tracks (or a siding) the two trains may meet or overtake,
            # so a multi-track leg between two shared single-track legs breaks the run
            common = sorted((ii, pos[m][r]) for r, ii in pos[k].items()
                            if r in pos[m] and model.resources[r].tracks == 1 and not model.resources[r].siding)
            ids, cur, prev = {}, 0, None
            for ii, jj in common:
                if prev is not None and not (ii == prev[0] + 1 and abs(jj - prev[1]) == 1
                                             and blk[k, ii] == blk[k, prev[0]] and blk[m, jj] == blk[m, prev[1]]):
                    cur += 1
                ids[ii] = cur
                prev = (ii, jj)
            run_of[(k, m)] = ids
        return run_of[(k, m)][i]
    Zs = {}                                              # shared: (k, m, run), k < m -> z (1: k first)
    zmap = {}                                            # (resource key, k, m), k < m -> z column (1: k first)

    def disjunction(a, b, tracks=None, trains=None, key=None, where=None):
        """a, b: (start, end) of two holds: b after a (start_b - end_a >= H) or a after b. tracks: [(y_a, y_b)] per
        track on a multi-track resource (the rows then apply only when both use that track), None on a single track."""
        nonlocal n_pairs, n_fixed
        (sa, ea), (sb, eb) = a, b
        if rng(sb)[0] >= rng(ea)[1] + H or rng(sa)[0] >= rng(eb)[1] + H:
            return                                        # apart at every point of the window
        orders = []                                       # (later start, earlier end, M) for each possible order
        if rng(sb)[1] >= rng(ea)[0] + H:
            orders.append((sb, ea, rng(ea)[1] + H - rng(sb)[0]))
        if rng(sa)[1] >= rng(eb)[0] + H:
            orders.append((sa, eb, rng(eb)[1] + H - rng(sa)[0]))
        if not orders:                                    # they overlap whatever the order
            if tracks is None:
                row([], 1, 0)                             # infeasible within the window
            else:
                for ya, yb in tracks:
                    row([(ya, 1), (yb, 1)], -inf, 1)      # different tracks
            return
        if key is not None:                               # shared variable: 1 = the lower-index train first
            ab_first = trains[0] < trains[1]              # does "a before b" mean "lower index first"?
            if key not in Zs:
                Zs[key] = var(0, 1)
                n_pairs += 1
            z = Zs[key]
            if where is not None:
                zmap[(where,) + tuple(sorted(trains))] = z
            labels = []
            if rng(sb)[1] >= rng(ea)[0] + H:
                labels.append(1 if ab_first else 0)
            if rng(sa)[1] >= rng(eb)[0] + H:
                labels.append(0 if ab_first else 1)
            if len(orders) == 1:
                row([(z, 1)], labels[0], labels[0])       # only one order fits the window
            for (s2, e1, M), val in zip(orders, labels):
                t1, c1 = lin(s2, 1)
                t2, c2 = lin(e1, -1)
                terms, rhs = t1 + t2, H - c1 - c2
                if len(orders) == 2:                      # active when z == val
                    if val == 1:
                        terms.append((z, -M)); rhs -= M
                    else:
                        terms.append((z, M))
                row(terms, rhs, inf)
            return
        z = None
        if len(orders) == 2:
            z = var(0, 1)
            n_pairs += 1
            if where is not None and trains is not None:
                zmap[(where,) + tuple(sorted(trains))] = z    # holds come in train order: 1 = lower index first
        else:
            n_fixed += 1
        for o, (s2, e1, M) in enumerate(orders):
            # s2 - e1 >= H - M * [z off] - M * (2 - y_a - y_b);  z off: 1 - z for the first order, z for the second
            t1, c1 = lin(s2, 1)
            t2, c2 = lin(e1, -1)
            terms, rhs = t1 + t2, H - c1 - c2
            if z is not None:
                if o == 0:
                    terms.append((z, -M)); rhs -= M
                else:
                    terms.append((z, M))
            for track in (tracks or [None]):
                if track is None:
                    row(terms, rhs, inf)
                else:
                    ya, yb = track
                    row(terms + [(ya, -M), (yb, -M)], rhs - 2 * M, inf)

    holds = defaultdict(list)
    for k, tr in enumerate(model.trains):
        for i, (r, _, _) in enumerate(tr.path):
            holds[r].append((k, i))
    for r, hs in holds.items():
        cap = model.resources[r].tracks
        if len(hs) <= cap:
            continue
        if cap == 1:
            for x in range(len(hs)):
                for y in range(x + 1, len(hs)):
                    (k1, i1), (k2, i2) = hs[x], hs[y]
                    if k1 != k2:
                        key = None
                        if share:
                            (ka, ia), (kb, ib) = ((k1, i1), (k2, i2)) if k1 < k2 else ((k2, i2), (k1, i1))
                            key = (ka, kb, run_id(ka, ia, kb, ib))
                        disjunction(hold(k1, i1), hold(k2, i2), trains=(k1, k2), key=key, where=("r", r))
        else:
            Y = {h_: [var(0, 1) for _ in range(cap)] for h_ in hs}
            for h_ in hs:
                row([(c, 1) for c in Y[h_]], 1, 1)
            for x in range(len(hs)):
                for y in range(x + 1, len(hs)):
                    if hs[x][0] != hs[y][0]:
                        disjunction(hold(*hs[x]), hold(*hs[y]), list(zip(Y[hs[x]], Y[hs[y]])))
    # stretches cut into cells: opposing trains never overlap from the first cell entry to the last cell exit + H
    from solver.python.lp_spacetime import stretches
    for parent, cells in stretches(model).items():
        cells = set(cells)
        span = {}
        for k, tr in enumerate(model.trains):
            on = [i for i, (r, _, _) in enumerate(tr.path) if r in cells]
            if on:
                first, last = min(on), max(on)
                _, p_last, s_last = tr.path[last]
                end = (S[k, last], p_last) if int(s_last) == 2 else (S[k, last + 1], 0)
                span[k] = ((S[k, first], 0), end, first)
        ks = list(span)
        for x in range(len(ks)):
            for y in range(x + 1, len(ks)):
                k1, k2 = ks[x], ks[y]
                if model.trains[k1].direction != model.trains[k2].direction:
                    key = None
                    if share:                            # the stretch's cells are one run of both trains
                        ka, kb = min(k1, k2), max(k1, k2)
                        key = (ka, kb, run_id(ka, span[ka][2], kb, pos[kb][model.trains[ka].path[span[ka][2]][0]]))
                    disjunction(span[k1][:2], span[k2][:2], trains=(k1, k2), key=key, where=("s", parent))
    n_cycle = 0
    if cycles:                                           # transitivity on every resource / stretch with 3 trains
        by_where = defaultdict(dict)
        for (where, k1, k2), z in zmap.items():
            by_where[where][(k1, k2)] = z
        seen = set()
        for where, zs in by_where.items():
            ts = sorted({k for pair in zs for k in pair})
            for a_ in range(len(ts)):
                for b_ in range(a_ + 1, len(ts)):
                    if (ts[a_], ts[b_]) not in zs:
                        continue
                    for c_ in range(b_ + 1, len(ts)):
                        za, zb, zc = zs.get((ts[a_], ts[b_])), zs.get((ts[b_], ts[c_])), zs.get((ts[a_], ts[c_]))
                        if za is None or zb is None or zc is None or (za, zb, zc) in seen:
                            continue
                        seen.add((za, zb, zc))
                        row([(za, 1), (zb, 1), (zc, -1)], 0, 1)    # a<b and b<c => a<c; and the reverse
                        n_cycle += 1
    # objective cut: value <= U
    obj = [(c, v) for c, v in enumerate(cost) if v]
    row(obj, -inf, U - const)

    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    n = len(lo)
    h.addVars(n, np.array(lo), np.array(hi))
    h.changeColsIntegrality(n, np.arange(n, dtype=np.int32),
                            np.array([highspy.HighsVarType.kInteger] * n))
    h.changeColsCost(n, np.arange(n, dtype=np.int32), np.array(cost))
    h.addRows(len(r_lo), np.array(r_lo), np.array(r_hi), len(r_ix), np.array(r_st, dtype=np.int32),
              np.array(r_ix, dtype=np.int32), np.array(r_v))

    def decode(x):
        sched = {}
        for k, tr in enumerate(model.trains):
            e = [int(round(x[S[k, i]])) for i in range(len(tr.path) + 1)]
            sched[tr.train_id] = [(i, model.resources[r].name, e[i], e[i + 1]) for i, (r, _, _) in enumerate(tr.path)]
        return sched
    return {"h": h, "const": const, "decode": decode, "vars": n, "rows": len(r_lo), "nonzeros": len(r_ix),
            "pairs": n_pairs, "fixed_pairs": n_fixed, "cycle_rows": n_cycle}


def build(model: Model, form: str, U: int) -> dict | None:
    if form == "arc":
        from solver.python.lp_spacetime import build as arc
        return arc(model, U, integer=True, stretch=True)
    if form == "cumulative":
        from solver.python.mip_cumflow import build as cum
        b = cum(model, horizon(model, U), window_ub=U)
        if b is not None:
            h = b["h"]
            b.update(vars=h.getNumCol(), nonzeros=int(h.getLp().a_matrix_.value_.__len__()))
        return b
    if form == "bigm":
        return build_bigm(model, U)
    if form == "bigm_shared":
        return build_bigm(model, U, share=True)
    if form == "bigm_cycles":
        return build_bigm(model, U, cycles=True)
    if form == "bigm_shared_cycles":
        return build_bigm(model, U, share=True, cycles=True)
    raise ValueError(form)


def lp_relaxation(b: dict, seconds: float, threads: int) -> dict:
    """The LP relaxation of a built model (the same rows, integrality dropped), by HiGHS."""
    import highspy
    lp = b["h"].getLp()
    lp.integrality_ = []
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(seconds))
    h.setOptionValue("threads", threads)
    h.passModel(lp)
    t0 = time.time()
    h.run()
    st = h.modelStatusToString(h.getModelStatus())
    out = {"lp_status": st, "lp_seconds": round(time.time() - t0, 2)}
    if st == "Optimal":
        out["lp"] = h.getInfo().objective_function_value + b["const"]
    return out


def solve(model: Model, form: str, U: int, solver: str = "highs", seconds: float = 600.0, threads: int = 2,
          lp: bool = True) -> dict:
    from solver.python.mip_cumflow import run_cplex
    t0 = time.time()
    b = build(model, form, U)
    if b is None:
        return {"status": "WINDOW_BELOW_TT0"}
    out = {"form": form, "solver": solver, "U": U, "build_seconds": round(time.time() - t0, 2),
           **{k: b[k] for k in ("vars", "rows", "nonzeros", "pairs", "fixed_pairs", "stretch_rows", "cycle_rows")
              if k in b}}
    if lp:
        out.update(lp_relaxation(b, seconds, threads))
    h = b["h"]
    t0 = time.time()
    if solver == "cplex":
        status, objv, bnd, x = run_cplex(h, seconds, threads)
        log = getattr(run_cplex, "last_log", "")
        m = re.findall(r"Nodes = (\d+)", log)
        out["nodes"] = int(m[-1]) if m else None
        out.update(status=status, seconds=round(time.time() - t0, 2))
        if x is None:
            return out
        value, bound = objv + b["const"], bnd + b["const"]
        proven = "optimal" in status and "tolerance" not in status
    else:
        h.setOptionValue("time_limit", float(seconds))
        h.setOptionValue("threads", threads)
        h.setOptionValue("mip_rel_gap", 0.0)
        h.run()
        info = h.getInfo()
        status = h.modelStatusToString(h.getModelStatus())
        out.update(status=status, seconds=round(time.time() - t0, 2), nodes=int(info.mip_node_count))
        if info.primal_solution_status != 2:
            return out
        x = np.array(h.getSolution().col_value)
        value, bound = info.objective_function_value + b["const"], info.mip_dual_bound + b["const"]
        proven = status == "Optimal"
    out.update(value=int(round(value)), bound=float(bound), proven=proven, schedule=b["decode"](x))
    return out
