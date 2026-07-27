"""
Tensor response-learning on the single-track B&P/B&B (train sizes 8 & 10).
The solver is the data-generating process; we learn the low-rank structure of its response field.

T1  rich logging: per node, per candidate meet-pass action a=(sec,i,j) we record
    - cheap PARENT-side features  phi(a)  (no child solve required), and
    - the multi-metric RESPONSE  Y(a) = [dL, dR, dStrong, imbalance, dPrimal]  where
      dL,dR = (cost+LB) improvement of the two children, dStrong = mu*min+(1-mu)*max,
      imbalance = |dL-dR|, dPrimal = ||child schedule - parent schedule||_1.
T2  per-node response tensor  R[action, section, metric]  -> HOSVD multilinear rank + modes.
T3  TRANSFER screening: fit shared-factor models on TRAIN instances, screen UNSEEN instances.
    Compare: unsupervised spectral norm, pseudo-cost, Ridge, reduced-rank (tensor) regression,
    gradient boosting.  Metric: out-of-instance Containment@K / RankGap / Spearman vs Full-SB.

Run:  python tensor_response.py --size 10
"""
from __future__ import annotations
import os, argparse, numpy as np
from scipy.stats import spearmanr
import ttbl_bnb as T
from ttbl_bnb import G_I, section_no
import learn_branch as L

try:
    from sklearn.linear_model import Ridge
    from sklearn.ensemble import GradientBoostingRegressor
    HAVE_SK = True
except Exception:
    HAVE_SK = False

MU = 0.5
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lowrank_out")
os.makedirs(OUT, exist_ok=True)
METRICS = ["dL", "dR", "dStrong", "imbalance", "dPrimal"]


# ---------- responses ----------
def schedule_vec(inst, table):
    return np.array([table[t][s][1] for t in range(inst.n) for s in range(inst.S)], float)


def section_metrics(inst, table):
    """per-section [occupancy, injected-wait, contention] -> (S,3)."""
    S = inst.S
    M = np.zeros((S, 3))
    for s in range(S):
        occ = 0.0; wait = 0.0; iv = []
        for t in range(inst.n):
            a, d = table[t][s][0], table[t][s][1]
            occ += d - a
            iv.append((a, d))
            # injected wait before entering s = arrive - (prev depart + dwell)
        M[s, 0] = occ
        # contention = # overlapping train pairs on s (headway aware)
        c = 0
        for x in range(inst.n):
            for y in range(x + 1, inst.n):
                ax, dx = iv[x]; ay, dy = iv[y]
                if ax < dy + G_I and ay < dx + G_I:
                    c += 1
        M[s, 2] = c
    return M


def phi(inst, table, sec, i, j, cd, z0, pure, ncand):
    """cheap parent-side action features (no child solve)."""
    runmax = max(RUN for RUN in [inst.run(i), inst.run(j)])
    return np.array([
        cd, sec / inst.S, (z0 - pure), ncand,
        inst.run(i) / runmax, inst.run(j) / runmax,
        abs(table[i][sec][0] - table[j][sec][0]),
        1.0 if inst.direction[i] != inst.direction[j] else 0.0,
        table[i][sec][0], table[j][sec][0],
    ], float)


def node_actions(ev, inst, table, z0, pure):
    """for each candidate action: cheap features X, response vector Y, dStrong, better-child table."""
    cands = L.candidate_pairs(inst, table)
    X, Y, D, child = [], [], [], []
    sv0 = schedule_vec(inst, table)
    for (k, i, j) in cands:
        ta = L.push_after(ev, table, k, i, j)
        tb = L.push_after(ev, table, k, j, i)
        bL = ev.total_cost(ta) + ev.est_delay(ta) - z0
        bR = ev.total_cost(tb) + ev.est_delay(tb) - z0
        dstrong = MU * min(bL, bR) + (1 - MU) * max(bL, bR)
        better = ta if (ev.total_cost(ta) <= ev.total_cost(tb)) else tb
        dprimal = float(np.abs(schedule_vec(inst, better) - sv0).sum())
        cd = max(0, min(table[i][k][1], table[j][k][1]) - max(table[i][k][0], table[j][k][0]))
        X.append(phi(inst, table, k, i, j, cd, z0, pure, len(cands)))
        Y.append([bL, bR, dstrong, abs(bL - bR), dprimal])
        D.append(dstrong); child.append(better)
    return cands, np.array(X), np.array(Y), np.array(D), child


def collect(size, seeds, max_nodes=120):
    """walk Full-SB search; return pooled rows + per-node groups."""
    groups = []                       # (X, Y, D) per node
    for s in seeds:
        inst = T.make_instance(size, s)
        ev = T.BnB(inst, use_lb=True); ev.opt = float("inf")
        tab = [[[0, 0, 0, 0] for _ in range(inst.S)] for _ in range(inst.n)]
        for tr in range(inst.n):
            ev.extend_job(tab, tr, 0)
        pure = ev.total_cost(tab)
        best = [float("inf")]; stack = [tab]; nodes = 0
        while stack and nodes < max_nodes:
            table = stack.pop(); nodes += 1
            z0 = ev.total_cost(table); lb = ev.est_delay(table)
            if z0 + lb >= best[0]:
                continue
            cands, X, Y, D, child = node_actions(ev, inst, table, z0, pure)
            if not cands:
                best[0] = min(best[0], z0); continue
            if len(cands) >= 2:
                groups.append((inst, table, X, Y, D, cands, child))
            pick = int(np.argmax(D)); k, i, j = cands[pick]
            stack.append(L.push_after(ev, table, k, i, j))
            stack.append(L.push_after(ev, table, k, j, i))
    return groups


# ---------- T2: per-node response tensor + HOSVD ----------
def hosvd_ranks(Rt, thresh=0.9):
    """multilinear rank: per-mode #components for `thresh` energy."""
    ranks = []
    for mode in range(Rt.ndim):
        unf = np.moveaxis(Rt, mode, 0).reshape(Rt.shape[mode], -1)
        unf = unf - unf.mean(axis=1, keepdims=True)
        s = np.linalg.svd(unf, compute_uv=False)
        e = np.cumsum(s ** 2) / max(np.sum(s ** 2), 1e-12)
        ranks.append((int(np.searchsorted(e, thresh) + 1), np.round(e[:4] * 100, 1)))
    return ranks


def T2_structure(groups, label):
    # pick the node with the most candidate actions
    g = max(groups, key=lambda x: len(x[5]))
    inst, table, X, Y, D, cands, child = g
    N = len(cands)
    R = np.zeros((N, inst.S, 3))
    base = section_metrics(inst, table)
    for a in range(N):
        R[a] = section_metrics(inst, child[a]) - base          # response = child - parent, per (section,metric)
    print(f"\n  [T2] per-node RESPONSE TENSOR R[action={N}, section={inst.S}, metric=3]  {label}")
    ranks = hosvd_ranks(R)
    for mode, name in zip(ranks, ["action", "section", "metric"]):
        print(f"       {name:>8} mode: rank@90%={mode[0]}  energy%={mode[1].tolist()}")
    # does the dominant ACTION mode carry the screening signal?
    unf = R.reshape(N, -1); unf = unf - unf.mean(0)
    U, s, Vt = np.linalg.svd(unf, full_matrices=False)
    rho = spearmanr(np.abs(U[:, 0]), D).correlation
    print(f"       dominant action-mode vs true dStrong: Spearman={rho:+.2f}  "
          f"(tensor is low multilinear-rank: {ranks[0][0]}x{ranks[1][0]}x{ranks[2][0]})")


# ---------- T3: transfer screening ----------
def reduced_rank_fit(Xtr, Ytr, rank):
    """reduced-rank (tensor) multi-output regression: B = B_ols truncated to `rank`."""
    Xc = np.c_[Xtr, np.ones(len(Xtr))]
    B = np.linalg.lstsq(Xc, Ytr, rcond=None)[0]               # (d+1) x M
    Yhat = Xc @ B
    U, s, Vt = np.linalg.svd(Yhat - Yhat.mean(0), full_matrices=False)
    Vr = Vt[:rank].T @ Vt[:rank]                              # project outputs to rank-r subspace
    return B, Vr, Ytr.mean(0)


def T3_transfer(size, train_seeds, test_seeds, Ks=(1, 3, 5)):
    gtr = collect(size, train_seeds)
    gte = collect(size, test_seeds)
    Xtr = np.vstack([g[2] for g in gtr]); Ytr = np.vstack([g[3] for g in gtr])
    Dtr = np.concatenate([g[4] for g in gtr])
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    Xn = (Xtr - mu) / sd
    di = METRICS.index("dStrong")
    models = {}
    if HAVE_SK:
        models["ridge"] = Ridge(alpha=1.0).fit(Xn, Dtr)
        models["gbr"] = GradientBoostingRegressor(n_estimators=200, max_depth=3,
                                                  random_state=0).fit(Xn, Dtr)
    # reduced-rank multi-output (tensor) models
    rr = {r: reduced_rank_fit(Xn, Ytr, r) for r in (1, 2, 3)}

    def score(name, Xg):
        Xgn = (Xg - mu) / sd
        if name in ("ridge", "gbr"):
            return models[name].predict(Xgn)
        if name == "pseudocost":
            return Xg[:, 0]                                    # cd feature (overlap)
        if name.startswith("rrr"):
            r = int(name[3]); B, Vr, ym = rr[r]
            Yh = np.c_[Xgn, np.ones(len(Xgn))] @ B
            Yh = (Yh - ym) @ Vr + ym                          # rank-r tensor reconstruction
            return Yh[:, di]
        return np.zeros(len(Xg))

    methods = ["random", "pseudocost", "ridge", "gbr", "rrr1", "rrr2", "rrr3"]
    contain = {m: {K: 0 for K in Ks} for m in methods}
    rg = {m: [] for m in methods}; rho_all = {m: [] for m in methods}
    rng = np.random.default_rng(0)
    nn = 0
    for g in gte:
        X, D = g[2], g[4]
        if len(D) < 2:
            continue
        nn += 1
        astar = int(np.argmax(D))
        for m in methods:
            sc = rng.random(len(D)) if m == "random" else score(m, X)
            order = np.argsort(-sc)
            rg[m].append(int(np.where(order == astar)[0][0]))
            r = spearmanr(sc, D).correlation
            if r == r:
                rho_all[m].append(r)
            for K in Ks:
                if astar in set(order[:K].tolist()):
                    contain[m][K] += 1
    print(f"\n  [T3] TRANSFER screening  size={size}  train seeds {list(train_seeds)} -> "
          f"test seeds {list(test_seeds)}  ({nn} unseen nodes, {len(Dtr)} train rows)")
    print(f"  {'method':>11} {'Contain@1':>9} {'Contain@3':>9} {'Contain@5':>9} "
          f"{'RankGap':>8} {'Spearman':>9}")
    for m in methods:
        c = contain[m]
        print(f"  {m:>11} {c[1]/nn:>9.2f} {c[3]/nn:>9.2f} {c[5]/nn:>9.2f} "
              f"{np.mean(rg[m]):>8.2f} {np.mean(rho_all[m]):>9.3f}")
    return gtr, gte


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=10)
    ap.add_argument("--train", type=int, nargs="*", default=list(range(1, 9)))
    ap.add_argument("--test", type=int, nargs="*", default=list(range(9, 14)))
    args = ap.parse_args()
    print(f"\n{'='*82}\n  TENSOR RESPONSE LEARNING (single-track B&B)  size={args.size}\n{'='*82}")
    gtr, gte = T3_transfer(args.size, tuple(args.train), tuple(args.test))
    T2_structure(gtr, f"(size {args.size})")
