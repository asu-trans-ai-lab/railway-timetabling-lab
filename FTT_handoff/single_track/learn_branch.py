"""
Stage C -- learned latent branching score vs Full-SB / pseudo-cost.

Uses a strong-branching CONFLICT-RESOLUTION search (De Reyck-Herroelen style) on the
single-track timetable (Way 2), where the branch CHOICE is scoreable:
  * node state = timetable `table`; a candidate branching ACTION = a meet-pass conflict
    (section s, up-train i, down-train j) whose occupancies overlap within headway.
  * branching on (s,i,j) = two children: i-before-j (push j past i+g_I) and j-before-i.
  * Full-SB score of an action = strong-branching delta  mu*min(dL,dR)+(1-mu)*max(dL,dR)
    with dL,dR = child (total_cost + CC-LB) improvements over the node.
  * feasible leaf = no remaining conflicts -> incumbent.

We (1) collect Full-SB (features, delta) over TRAIN instances, (2) fit a model on
[low-rank footprint coords z_a = U_k^T g_a  ++  structural features], (3) evaluate on TEST
instances: node-level Containment@K / RankGap / BoundRecovery@K for learned vs unsupervised
spectral vs pseudo-cost vs random; and (4) full-search NODE COUNT to optimality when the
branch is selected by {Full-SB, learned, pseudo-cost, earliest}.

Run:  python learn_branch.py
"""
from __future__ import annotations
import os, numpy as np
from scipy.stats import spearmanr
import ttbl_bnb as T
from ttbl_bnb import G_I, section_no

try:
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.linear_model import Ridge
    HAVE_SK = True
except Exception:
    HAVE_SK = False

MU = 0.5
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lowrank_out")
os.makedirs(OUT, exist_ok=True)


# ============================================================= core operations
def clone(table):
    return [[c[:] for c in row] for row in table]


def candidate_pairs(inst, table):
    """ALL single-track conflicts on a section within headway g_I: meet (opposing) AND
    overtake (same direction). (sec, a, b) for every train pair whose occupancies overlap."""
    S = inst.S
    out = []
    for k in range(S):
        for a in range(inst.n):
            aa, da = table[a][k][0], table[a][k][1]
            for b in range(a + 1, inst.n):
                ab, db = table[b][k][0], table[b][k][1]
                if aa < db + G_I and ab < da + G_I:           # headway-aware overlap
                    out.append((k, a, b))
    return out


def push_after(ev, table, sec, first, second):
    """child: `first` before `second` on `sec` -> push `second` past first+g_I, re-sweep."""
    t = clone(table)
    comp = t[first][sec][1]
    t[second][sec][0] = comp + G_I
    t[second][sec][2] = comp + G_I
    d2 = ev.inst.direction[second]
    ev.extend_job(t, second, sec if d2 == 0 else ev.inst.S - 1 - sec)
    return t


def sb_delta(ev, table, sec, i, j):
    """exact strong-branching score of resolving conflict (sec,i,j) + footprint cells."""
    z0 = ev.total_cost(table)
    tA = push_after(ev, table, sec, i, j)        # i first
    tB = push_after(ev, table, sec, j, i)        # j first
    dL = ev.total_cost(tA) + ev.est_delay(tA) - z0
    dR = ev.total_cost(tB) + ev.est_delay(tB) - z0
    delta = MU * min(dL, dR) + (1 - MU) * max(dL, dR)
    ai, di = table[i][sec][0], table[i][sec][1]
    aj, dj = table[j][sec][0], table[j][sec][1]
    lo, hi = min(ai, aj), max(di, dj) + G_I
    foot = frozenset(("S", sec, t) for t in range(lo, hi))
    cd = max(0, min(di, dj) - max(ai, aj))       # overlap extent (pseudo-cost proxy)
    return delta, foot, cd, (z0,)


def struct_feats(inst, table, sec, i, j, cd, z0, pure, ncand):
    return np.array([cd, sec / inst.S, (z0 - pure), ncand,
                     inst.run(i), inst.run(j),
                     abs(table[i][sec][0] - table[j][sec][0])], float)


# ============================================================= SB search (data + node count)
def sb_search(inst, select="fullsb", model=None, Uk=None, cellidx=None, pure=None,
              node_limit=200_000, collect=None):
    """DFS conflict-resolution B&B. `select` decides which conflict to branch on.
    Returns (opt, nodes). If collect is a list, append (features..., true_delta) per node."""
    ev = T.BnB(inst, use_lb=True); ev.opt = float("inf")
    root = clone([[0, 0, 0, 0] for _ in range(inst.S)] for _ in range(inst.n)) \
        if False else None
    tab = [[[0, 0, 0, 0] for _ in range(inst.S)] for _ in range(inst.n)]
    for tr in range(inst.n):
        ev.extend_job(tab, tr, 0)
    if pure is None:
        pure = ev.total_cost(tab)
    best = [float("inf")]
    nodes = [0]
    stack = [tab]
    while stack:
        if nodes[0] >= node_limit:
            break
        table = stack.pop()
        nodes[0] += 1
        z0 = ev.total_cost(table)
        lb = ev.est_delay(table)
        if z0 + lb >= best[0]:
            continue
        cands = candidate_pairs(inst, table)
        if not cands:
            if z0 < best[0]:
                best[0] = z0
            continue
        # score candidates
        deltas, foots, cds = [], [], []
        need_sb = (select == "fullsb") or (collect is not None)
        for (k, i, j) in cands:
            if need_sb:
                d, foot, cd, _ = sb_delta(ev, table, k, i, j)
            else:
                # cheap proxy footprint/cd without solving children
                ai, di = table[i][k][0], table[i][k][1]
                aj, dj = table[j][k][0], table[j][k][1]
                lo, hi = min(ai, aj), max(di, dj) + G_I
                foot = frozenset(("S", k, t) for t in range(lo, hi))
                cd = max(0, min(di, dj) - max(ai, aj)); d = None
            deltas.append(d); foots.append(foot); cds.append(cd)
        if collect is not None:
            for idx, (k, i, j) in enumerate(cands):
                collect.append((foots[idx], struct_feats(inst, table, k, i, j, cds[idx],
                                z0, pure, len(cands)), deltas[idx]))
        # pick the conflict to branch on
        if select == "fullsb":
            pick = int(np.argmax(deltas))
        elif select == "earliest":
            pick = int(np.argmin([min(table[i][k][1], table[j][k][1])
                                  for (k, i, j) in cands]))
        elif select == "pseudocost":
            pick = int(np.argmax(cds))
        elif select == "learned":
            X = np.array([_feat_vec(foots[t], struct_feats(inst, table, *cands[t], cds[t],
                          z0, pure, len(cands)), Uk, cellidx) for t in range(len(cands))])
            pred = model.predict(X)
            pick = int(np.argmax(pred))
        else:
            pick = 0
        k, i, j = cands[pick]
        # push both children
        stack.append(push_after(ev, table, k, i, j))   # i first
        stack.append(push_after(ev, table, k, j, i))   # j first
    return best[0], nodes[0]


def _feat_vec(foot, sfeat, Uk, cellidx):
    z = np.zeros(Uk.shape[1]) if Uk is not None else np.zeros(0)
    if Uk is not None:
        g = np.zeros(Uk.shape[0])
        for cell in foot:
            r = cellidx.get(cell)
            if r is not None:
                g[r] = 1.0
        z = Uk.T @ g
    return np.concatenate([z, sfeat])


# ============================================================= experiment
def run(n_trains=6, train_seeds=range(1, 9), test_seeds=range(9, 14), k=20):
    print(f"\n{'='*80}\n  STAGE C -- learned latent branching score  |  {n_trains} trains\n{'='*80}")
    # ---- collect Full-SB training data ----
    train = []
    for s in train_seeds:
        sb_search(T.make_instance(n_trains, s), select="fullsb", collect=train)
    foots = [r[0] for r in train if r[2] is not None]
    sfeats = [r[1] for r in train if r[2] is not None]
    y = np.array([r[2] for r in train if r[2] is not None], float)
    print(f"  training rows (node x candidate): {len(y)}")
    # ---- global footprint basis U_k ----
    cellidx = {}
    for f in foots:
        for c in f:
            cellidx.setdefault(c, len(cellidx))
    A = np.zeros((len(cellidx), len(foots)))
    for jx, f in enumerate(foots):
        for c in f:
            A[cellidx[c], jx] = 1.0
    U, S, Vt = np.linalg.svd(A, full_matrices=False)
    Uk = U[:, :min(k, U.shape[1])]
    X = np.array([_feat_vec(foots[t], sfeats[t], Uk, cellidx) for t in range(len(foots))])
    # ---- fit model ----
    if HAVE_SK:
        model = GradientBoostingRegressor(n_estimators=200, max_depth=3, random_state=0)
    else:
        model = None
    if model is not None:
        model.fit(X, y)

    # ---- node-level screening metrics on TEST nodes ----
    print(f"\n  NODE-LEVEL SCREENING (test seeds {list(test_seeds)}), vs Full-SB best action:")
    methods = {"learned": [], "spectral": [], "pseudocost": [], "random": [], "footsize": []}
    rng = np.random.default_rng(0)
    test = []
    for s in test_seeds:
        sb_search(T.make_instance(n_trains, s), select="fullsb", collect=test)
    # group test rows by node (consecutive rows from same node share the same z0 & ncand);
    # simpler: re-collect per node. Re-run, grouping:
    node_groups = _collect_grouped(n_trains, test_seeds)
    contain = {m: {K: 0 for K in (1, 3, 5)} for m in methods}
    rankgap = {m: [] for m in methods}
    n_nodes_eval = 0
    for grp in node_groups:
        cands, fts, sfs, dl, cds = grp
        if len(cands) < 2:
            continue
        n_nodes_eval += 1
        astar = int(np.argmax(dl))
        Xg = np.array([_feat_vec(fts[t], sfs[t], Uk, cellidx) for t in range(len(cands))])
        scores = {
            "learned": model.predict(Xg) if model is not None else np.zeros(len(cands)),
            "spectral": np.linalg.norm(Xg[:, :Uk.shape[1]], axis=1),     # unsupervised latent norm
            "pseudocost": np.array(cds, float),
            "random": rng.random(len(cands)),
            "footsize": np.array([len(f) for f in fts], float),
        }
        for m, sc in scores.items():
            order = np.argsort(-sc)
            rg = int(np.where(order == astar)[0][0])
            rankgap[m].append(rg)
            for K in (1, 3, 5):
                if astar in set(order[:K].tolist()):
                    contain[m][K] += 1
    print(f"  evaluated {n_nodes_eval} test nodes with >=2 candidates")
    print(f"  {'method':>12} {'Contain@1':>9} {'Contain@3':>9} {'Contain@5':>9} {'meanRankGap':>12}")
    for m in methods:
        c = contain[m]
        print(f"  {m:>12} {c[1]/n_nodes_eval:>9.2f} {c[3]/n_nodes_eval:>9.2f} "
              f"{c[5]/n_nodes_eval:>9.2f} {np.mean(rankgap[m]):>12.2f}")

    # ---- full-search node count to optimality ----
    print(f"\n  NODE COUNT TO OPTIMALITY (test seeds):")
    print(f"  {'seed':>4} {'opt':>6} {'earliest':>9} {'pseudocost':>10} {'learned':>8} {'fullSB':>7}")
    for s in test_seeds:
        inst = T.make_instance(n_trains, s)
        o_e, n_e = sb_search(inst, select="earliest")
        o_p, n_p = sb_search(inst, select="pseudocost")
        o_l, n_l = sb_search(inst, select="learned", model=model, Uk=Uk, cellidx=cellidx)
        o_f, n_f = sb_search(inst, select="fullsb")
        ok = "OK" if o_e == o_p == o_l == o_f else "MISMATCH"
        print(f"  {s:>4} {o_f:>6} {n_e:>9} {n_p:>10} {n_l:>8} {n_f:>7}  {ok}")


def _collect_grouped(n_trains, seeds):
    """re-run Full-SB, returning per-node grouped (cands, foots, sfeats, deltas, cds)."""
    groups = []
    for s in seeds:
        inst = T.make_instance(n_trains, s)
        ev = T.BnB(inst, use_lb=True); ev.opt = float("inf")
        tab = [[[0, 0, 0, 0] for _ in range(inst.S)] for _ in range(inst.n)]
        for tr in range(inst.n):
            ev.extend_job(tab, tr, 0)
        pure = ev.total_cost(tab)
        best = [float("inf")]; stack = [tab]; nodes = 0
        while stack and nodes < 200_000:
            table = stack.pop(); nodes += 1
            z0 = ev.total_cost(table); lb = ev.est_delay(table)
            if z0 + lb >= best[0]:
                continue
            cands = candidate_pairs(inst, table)
            if not cands:
                best[0] = min(best[0], z0); continue
            fts, sfs, dl, cds = [], [], [], []
            for (k, i, j) in cands:
                d, foot, cd, _ = sb_delta(ev, table, k, i, j)
                fts.append(foot); cds.append(cd); dl.append(d)
                sfs.append(struct_feats(inst, table, k, i, j, cd, z0, pure, len(cands)))
            groups.append((cands, fts, sfs, np.array(dl, float), cds))
            pick = int(np.argmax(dl)); k, i, j = cands[pick]
            stack.append(push_after(ev, table, k, i, j))
            stack.append(push_after(ev, table, k, j, i))
    return groups


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--trains", type=int, default=6)
    ap.add_argument("--k", type=int, default=20)
    args = ap.parse_args()
    if not HAVE_SK:
        print("WARNING: scikit-learn not available; learned model disabled.")
    run(n_trains=args.trains, k=args.k)
