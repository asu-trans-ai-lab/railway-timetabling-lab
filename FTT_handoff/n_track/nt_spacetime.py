"""
N-track space-time network + path-based LP master for the INFORMS RAS data
(Meng & Zhou 2014 REF-SRR / cumulative-flow formulation), built on the real RAS CSVs.

This is the missing piece: ras_solver.py only had a greedy dispatcher + synthetic-SVD.
Here we build the genuine time-expanded network and an LP relaxation that yields REAL
DUALS (cell-time resource prices rho_{a,t} and train-assignment prices pi_f) and REAL
route/track choice — the N-track regime our screening/tensor research needs.

Model (column form):
    min  sum_p c_p x_p
    s.t. sum_{p in P_f} x_p = 1                  one trajectory per train f      (dual pi_f)
         sum_p H[(a,t),p] x_p <= Cap(a,t)        cell-time capacity              (dual rho_{a,t})
         0 <= x_p <= 1
  cell (a,t) = physical track-arc a occupied during 1-min bin t (incl. headway tail).
  Parallel tracks between a node pair are SEPARATE arcs -> multi-track capacity emerges.
  Bidirectional single-track arc -> one shared cell -> opposing trains cannot overlap (meet-pass).
  MOW window -> Cap(a,t)=0 (arc blocked).
  c_p = schedule deviation: |arrival - terminal_want_time| + sum|node arrival - scheduled| + delay.

Columns are ENUMERATED (departure offset x track-assignment variants) with a wide departure
window guaranteeing sequential feasibility (enumerate-first; column generation is the next step).

Run:  python nt_spacetime.py --inst toy
      python nt_spacetime.py --inst ds3
"""
from __future__ import annotations
import os, argparse, math
from collections import defaultdict, deque
import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import linprog

INSTS = {"toy": "RAS_Toy_problem", "ds3": "RAS_data-set_3"}
_HERE = os.path.dirname(os.path.abspath(__file__))
# Resolve each instance's data dir from the first existing candidate, so the script runs both inside the
# self-contained handoff (../data/arc_format_*) and from spectral_ttbl (../Meng_Zhou_RAS_network_timetabling).
_CANDS = {
    "toy": ["../data/arc_format_RAS_Toy_problem",
            "../Meng_Zhou_RAS_network_timetabling/RAS_Toy_problem",
            "../../Meng_Zhou_RAS_network_timetabling/RAS_Toy_problem",
            "../../../Meng_Zhou_RAS_network_timetabling/RAS_Toy_problem"],
    "ds3": ["../data/arc_format_RAS_data-set_3",
            "../Meng_Zhou_RAS_network_timetabling/RAS_data-set_3",
            "../../Meng_Zhou_RAS_network_timetabling/RAS_data-set_3",
            "../../../Meng_Zhou_RAS_network_timetabling/RAS_data-set_3"],
}


def inst_dir(inst):
    for rel in _CANDS[inst]:
        p = os.path.normpath(os.path.join(_HERE, rel))
        if os.path.isdir(p):
            return p
    raise FileNotFoundError(f"no data dir for instance '{inst}'; tried {_CANDS[inst]}")


# ============================================================= parse RAS CSVs
def parse(data_dir):
    A = pd.read_csv(os.path.join(data_dir, "input_rail_arc.csv"))
    arcs = []
    for _, r in A.iterrows():
        arcs.append(dict(arc_id=int(r["arc_id"]), a=int(r["A_node_id"]), b=int(r["B_node_id"]),
                         length=float(r["length"]), tt=str(r["track_type"]),
                         vAB=float(r["default_AB_speed_per_hour"]),
                         vBA=float(r["default_BA_speed_per_hour"]),
                         bidir=int(r["bidirectional_flag"]) == 1))
    T = pd.read_csv(os.path.join(data_dir, "input_train_info.csv"))
    trains = []
    for _, r in T.iterrows():
        trains.append(dict(id=str(r["train_header"]), entry=float(r["entry_time"]),
                           o=int(r["origin_node_id"]), d=int(r["destination_node_id"]),
                           dir="E" if r["direction"] == "EASTBOUND" else "W",
                           smult=float(r["speed_multiplier"]),
                           want=float(r["terminal_want_time"])))
    sched = defaultdict(dict)
    sp = os.path.join(data_dir, "input_train_schedule_arrival.csv")
    if os.path.exists(sp):
        for _, r in pd.read_csv(sp).iterrows():
            sched[str(r["train_header"])][int(r["node_id"])] = float(r["schedule_arrival_time"])
    mow = []
    mp = os.path.join(data_dir, "input_MOW.csv")
    if os.path.exists(mp):
        for _, r in pd.read_csv(mp).iterrows():
            mow.append((int(r["A_node_id"]), int(r["B_node_id"]),
                        float(r["start_time_in_min"]), float(r["end_time_in_min"])))
    return arcs, trains, dict(sched), mow


def arc_time(arc, direction_AtoB, smult):
    """travel time (min) along arc in the given physical direction."""
    v = (arc["vAB"] if direction_AtoB else arc["vBA"]) * max(smult, 1e-6)
    if v <= 0 or arc["length"] <= 0:
        return 1
    return max(1, (arc["length"] / v) * 60.0)


# ============================================================= routing (node path + parallel tracks)
def build_graph(arcs):
    nbr = defaultdict(list)              # node -> list of (other_node, arc, AtoB)
    par = defaultdict(list)              # (min,max) node pair -> [arc_ids] parallel tracks
    by_id = {a["arc_id"]: a for a in arcs}
    for a in arcs:
        nbr[a["a"]].append((a["b"], a, True))
        if a["bidir"]:
            nbr[a["b"]].append((a["a"], a, False))
        par[(min(a["a"], a["b"]), max(a["a"], a["b"]))].append(a["arc_id"])
    return nbr, par, by_id


def node_path(nbr, o, d, prefer_main=True):
    """BFS fewest-hops o->d, preferring non-switch/siding arcs."""
    best = None
    q = deque([(o, [], 0)]); seen = {}
    while q:
        n, path, sw = q.popleft()
        if n == d:
            if best is None or sw < best[1]:
                best = (path, sw)
            continue
        key = (n, sw)
        if key in seen and seen[key] <= len(path):
            continue
        seen[key] = len(path)
        cand = sorted(nbr[n], key=lambda x: (x[1]["tt"] in ("SW", "S", "C"), x[1]["length"]))
        for nn, arc, ab in cand:
            if len(path) > 250:
                continue
            sw2 = sw + (1 if arc["tt"] in ("SW", "S", "C") else 0)
            q.append((nn, path + [(arc, ab, n, nn)], sw2))
    return best[0] if best else []


# ============================================================= space-time columns
def enum_columns(train, npath, dep_window, dep_step, par, by_id, extra_deps=()):
    """columns = (departure offset) x (track-assignment variant per multi-track segment).
    extra_deps: additional base-choice departure offsets (e.g. sequential fallback)."""
    cols = []
    # candidate track sets per traversed segment (parallel arcs in needed direction)
    seg_tracks = []
    for (arc, ab, n_from, n_to) in npath:
        pair = (min(n_from, n_to), max(n_from, n_to))
        tracks = [by_id[aid] for aid in par[pair]]
        # only tracks usable in this physical direction
        usable = [t for t in tracks if (t["a"] == n_from or t["bidir"])]
        if not usable:
            usable = [arc]
        seg_tracks.append((usable, n_from, n_to))
    # base track choice = first usable; plus 1 alternate variant per multi-track segment
    base_choice = [0] * len(seg_tracks)
    variants = [base_choice]
    for si, (usable, _, _) in enumerate(seg_tracks):
        if len(usable) > 1:
            v = base_choice.copy(); v[si] = 1; variants.append(v)
    window_deps = list(range(0, dep_window + 1, dep_step))
    dep_choice = [(d, variants) for d in window_deps]
    for ed in extra_deps:                                  # fallback deps: base choice only
        dep_choice.append((int(ed), [base_choice]))
    for dep, choices in dep_choice:
        for choice in choices:
            t = train["entry"] + dep
            segs = []; ok = True
            for si, (usable, n_from, n_to) in enumerate(seg_tracks):
                arc = usable[min(choice[si], len(usable) - 1)]
                ab = (arc["a"] == n_from)
                tt = arc_time(arc, ab, train["smult"])
                enter, exitt = t, t + tt
                segs.append((arc["arc_id"], n_from, n_to, enter, exitt))
                t = exitt
            if ok:
                cols.append(dict(dep=dep, segs=segs, arrival=t,
                                 choice=tuple(choice)))
    return cols


def col_cells(segs, headway_bins, dt):
    """resource-time cells (arc_id, bin) occupied incl. headway tail."""
    cells = []
    for (aid, nf, nt, enter, exitt) in segs:
        b0 = int(enter / dt); b1 = int(math.ceil(exitt / dt)) + headway_bins
        for b in range(b0, b1):
            cells.append((aid, b))
    return cells


def col_cost(train, segs, sched, dt):
    arr = segs[-1][4]
    dev = abs(arr - train["want"])                       # terminal deviation
    # intermediate scheduled-arrival deviations (node arrival ~ exit of seg ending at node)
    node_arr = {}
    for (aid, nf, nt, enter, exitt) in segs:
        node_arr[nt] = exitt
    for nid, sa in sched.get(train["id"], {}).items():
        if nid in node_arr:
            dev += abs(node_arr[nid] - sa)
    return dev


# ============================================================= build + solve LP
def build_ctx(inst="toy", dt=1.0, headway_min=3.0, dep_step=2):
    """Build the N-track space-time instance; return a context for (re-)solving."""
    data_dir = inst_dir(inst)
    arcs, trains, sched, mow = parse(data_dir)
    nbr, par, by_id = build_graph(arcs)
    headway_bins = max(1, int(round(headway_min / dt)))

    # per-train node path + free-flow arrival (for departure window sizing)
    paths = {}
    ff = {}
    for tr in trains:
        np_ = node_path(nbr, tr["o"], tr["d"])
        paths[tr["id"]] = np_
        t = tr["entry"]
        for (arc, ab, nf, nt) in np_:
            t += arc_time(arc, ab, tr["smult"])
        ff[tr["id"]] = t - tr["entry"]
    # bounded "interesting" departure window + per-train STAGGERED sequential fallback
    # (guarantees a feasible all-fallback assignment without an enormous window)
    max_ff = max(ff.values()) if ff else 10.0
    window = int(min(sum(ff.values()) + headway_min * len(trains), 8 * max_ff + 60))
    stagger = max_ff + 2 * headway_min
    seq = window

    # enumerate columns
    cols = []                  # global list of dict + train index
    train_cols = defaultdict(list)
    col_train = []
    for ti, tr in enumerate(trains):
        fallback = ti * stagger                          # staggered -> mutually conflict-free
        ccs = enum_columns(tr, paths[tr["id"]], window, dep_step, par, by_id,
                           extra_deps=[fallback])
        for c in ccs:
            c["cost"] = col_cost(tr, c["segs"], sched, dt)
            c["cells"] = col_cells(c["segs"], headway_bins, dt)
            pid = len(cols); cols.append(c)
            train_cols[ti].append(pid); col_train.append(ti)
    nP = len(cols)

    # MOW-blocked cells -> drop columns using a blocked arc-bin
    mow_arc_bins = set()
    for (a, b, st, en) in mow:
        for arc in arcs:
            if {arc["a"], arc["b"]} == {a, b}:
                for bb in range(int(st / dt), int(math.ceil(en / dt))):
                    mow_arc_bins.add((arc["arc_id"], bb))
    if mow_arc_bins:
        for c in cols:
            if any(cell in mow_arc_bins for cell in c["cells"]):
                c["cost"] += 1e6                     # soft-forbid (keep feasibility)

    # H (cells x cols), Cap (=1 per arc-bin), A (trains x cols)
    cid = {}
    rows, cc = [], []
    for p, c in enumerate(cols):
        for cell in c["cells"]:
            r = cid.setdefault(cell, len(cid))
            rows.append(r); cc.append(p)
    nR = len(cid)
    H = sparse.csr_matrix((np.ones(len(rows)), (rows, cc)), shape=(nR, nP)).tocsr()
    cap = np.ones(nR)
    cost = np.array([c["cost"] for c in cols])
    ar, acc = [], []
    for ti in range(len(trains)):
        for p in train_cols[ti]:
            ar.append(ti); acc.append(p)
    Aeq = sparse.csr_matrix((np.ones(len(ar)), (ar, acc)), shape=(len(trains), nP)).tocsr()

    # only capacity rows with >=2 columns can bind
    deg = np.asarray((H > 0).sum(axis=1)).ravel()
    act = np.where(deg >= 2)[0]
    cell_of = {v: k for k, v in cid.items()}
    return dict(inst=inst, dt=dt, trains=trains, n_arcs=len(arcs), n_trains=len(trains),
                cols=cols, train_cols=dict(train_cols), nP=nP, nR=nR,
                H=H, cap=cap, Aeq=Aeq, cost=cost, act=act, cell_of=cell_of,
                seq=seq, headway_bins=headway_bins)


def solve_ctx(ctx, forbid=()):
    """Solve the LP relaxation with `forbid` columns fixed to 0 (for SB child re-solves).
    Returns dict(success, obj, x, rho (per cell row), pi (per train))."""
    nP = ctx["nP"]
    ub = np.ones(nP)
    for p in forbid:
        ub[p] = 0.0
    act = ctx["act"]
    res = linprog(ctx["cost"], A_ub=ctx["H"][act], b_ub=ctx["cap"][act],
                  A_eq=ctx["Aeq"], b_eq=np.ones(ctx["n_trains"]),
                  bounds=list(zip(np.zeros(nP), ub)), method="highs")
    if not res.success:
        return dict(success=False, message=res.message)
    rho = np.zeros(ctx["nR"]); rho[act] = np.maximum(-np.asarray(res.ineqlin.marginals), 0.0)
    pi = -np.asarray(res.eqlin.marginals)
    return dict(success=True, obj=float(res.fun), x=res.x, rho=rho, pi=pi)


def build_and_solve(inst="toy", dt=1.0, headway_min=3.0, dep_step=2, verbose=True):
    ctx = build_ctx(inst, dt, headway_min, dep_step)
    r = solve_ctx(ctx)
    out = dict(inst=inst, n_arcs=ctx["n_arcs"], n_trains=ctx["n_trains"], n_cols=ctx["nP"],
               n_cells=ctx["nR"], n_cap_rows=len(ctx["act"]), seq_window=ctx["seq"],
               headway_bins=ctx["headway_bins"])
    if not r["success"]:
        out["status"] = r["message"]; print(out); return out
    x, rho, pi = r["x"], r["rho"], r["pi"]
    out.update(status="ok", obj=r["obj"], rho=rho, pi=pi, x=x, ctx=ctx,
               frac=int(np.sum((x > 1e-6) & (x < 1 - 1e-6))), n_binding=int(np.sum(rho > 1e-6)))
    if verbose:
        cell_of, train_cols, cols, trains = ctx["cell_of"], ctx["train_cols"], ctx["cols"], ctx["trains"]
        print(f"\n{'='*78}\n  N-TRACK SPACE-TIME LP  |  RAS {inst}\n{'='*78}")
        print(f"  arcs={ctx['n_arcs']} trains={ctx['n_trains']} columns={ctx['nP']} "
              f"cell-time rows={ctx['nR']} (cap-active {len(ctx['act'])})")
        print(f"  dep window={ctx['seq']} bins  headway={ctx['headway_bins']} bins")
        print(f"  LP objective (total schedule deviation) = {r['obj']:.1f}")
        print(f"  fractional x = {out['frac']}   binding cell-time duals rho>0 = {out['n_binding']}")
        top = np.argsort(-rho)[:8]
        print(f"\n  top cell-time resource prices (arc, time-bin): rho")
        for rr in top:
            if rho[rr] <= 0:
                break
            aid, b = cell_of[rr]
            print(f"    arc {aid:>3}  t~{b*dt:>5.0f} min   rho = {rho[rr]:.2f}")
        print(f"\n  {'train':>6} {'dir':>4} {'entry':>6} {'arrival':>8} {'want':>6} {'dev':>7} {'cols':>5}")
        for ti, tr in enumerate(trains):
            ps = train_cols[ti]; pbest = max(ps, key=lambda p: x[p]); c = cols[pbest]
            print(f"  {tr['id']:>6} {tr['dir']:>4} {tr['entry']:>6.0f} {c['arrival']:>8.1f} "
                  f"{tr['want']:>6.0f} {abs(c['arrival']-tr['want']):>7.1f} {len(ps):>5}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--inst", default="toy", choices=list(INSTS))
    ap.add_argument("--dt", type=float, default=1.0)
    ap.add_argument("--headway", type=float, default=3.0)
    ap.add_argument("--dep_step", type=int, default=2)
    args = ap.parse_args()
    build_and_solve(args.inst, dt=args.dt, headway_min=args.headway, dep_step=args.dep_step)
