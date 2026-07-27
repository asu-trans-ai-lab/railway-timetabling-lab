"""Shared tensor / low-rank helpers for the response-learning study (numpy + sklearn only).
CP-ALS, HOSVD multilinear rank, reduced-rank multi-output regression, and screening metrics.
Loaded by tensor_features.py and the workflow analysis agents."""
from __future__ import annotations
import numpy as np


# ---------------- decompositions ----------------
def hosvd_ranks(R, thresh=0.9):
    """Tucker/HOSVD multilinear rank: per-mode #components capturing `thresh` energy + energy profile."""
    out = []
    for mode in range(R.ndim):
        unf = np.moveaxis(R, mode, 0).reshape(R.shape[mode], -1)
        unf = unf - unf.mean(axis=1, keepdims=True)
        s = np.linalg.svd(unf, compute_uv=False)
        e = np.cumsum(s ** 2) / max(np.sum(s ** 2), 1e-12)
        out.append({"rank": int(np.searchsorted(e, thresh) + 1),
                    "energy": np.round(e[:6] * 100, 1).tolist()})
    return out


def cp_als(R, rank, iters=60, seed=0, ridge=1e-6, center=True):
    """Plain CP-ALS (numpy) on the (optionally grand-mean-centered) tensor, so relerr is
    comparable to the mode-centered HOSVD diagnostic. Returns factors, weights, relerr."""
    if center:
        R = R - R.mean()
    rng = np.random.default_rng(seed)
    facs = [rng.standard_normal((R.shape[d], rank)) for d in range(R.ndim)]

    def unfold(T, m):
        return np.moveaxis(T, m, 0).reshape(T.shape[m], -1)

    def khatri_rao(mats):
        K = mats[0]
        for M in mats[1:]:
            K = (K[:, None, :] * M[None, :, :]).reshape(-1, K.shape[1])
        return K
    for _ in range(iters):
        for m in range(R.ndim):
            others = [facs[d] for d in range(R.ndim) if d != m]
            KR = khatri_rao(others[::-1])                       # match unfold col order
            G = np.ones((rank, rank))
            for d in range(R.ndim):
                if d != m:
                    G *= facs[d].T @ facs[d]
            facs[m] = unfold(R, m) @ KR @ np.linalg.pinv(G + ridge * np.eye(rank))
    # normalize, extract weights
    w = np.ones(rank)
    for m in range(R.ndim):
        nrm = np.linalg.norm(facs[m], axis=0) + 1e-12
        facs[m] = facs[m] / nrm; w *= nrm
    # reconstruction error
    rec = cp_reconstruct(facs, w)
    err = np.linalg.norm(rec - R) / (np.linalg.norm(R) + 1e-12)
    return facs, w, float(err)


def cp_reconstruct(facs, w):
    rank = len(w)
    R = np.zeros([f.shape[0] for f in facs])
    for r in range(rank):
        comp = w[r]
        vecs = [facs[d][:, r] for d in range(len(facs))]
        term = vecs[0]
        for v in vecs[1:]:
            term = np.multiply.outer(term, v)
        R = R + comp * term
    return R


def reduced_rank_regression(X, Y, rank):
    """multi-output RRR: B_ols then project outputs to rank-r subspace. Returns predictor fn."""
    Xc = np.c_[X, np.ones(len(X))]
    B = np.linalg.lstsq(Xc, Y, rcond=None)[0]
    Yhat = Xc @ B
    ym = Yhat.mean(0)
    U, s, Vt = np.linalg.svd(Yhat - ym, full_matrices=False)
    P = Vt[:rank].T @ Vt[:rank]                                 # output-space rank-r projector

    def predict(Xnew):
        Yh = np.c_[Xnew, np.ones(len(Xnew))] @ B
        return (Yh - ym) @ P + ym
    return predict


# ---------------- screening metrics ----------------
def containment_by_group(score, target, group_ids, Ks=(1, 3, 5)):
    """per-group (node) Containment@K + mean RankGap of the true-best target action under `score`."""
    from collections import defaultdict
    g = defaultdict(list)
    for idx, gid in enumerate(group_ids):
        g[gid].append(idx)
    cont = {K: 0 for K in Ks}; rg = []; n = 0
    for gid, idxs in g.items():
        if len(idxs) < 2:
            continue
        n += 1
        idxs = np.array(idxs)
        astar = idxs[int(np.argmax(target[idxs]))]
        order = idxs[np.argsort(-score[idxs])]
        rg.append(int(np.where(order == astar)[0][0]))
        for K in Ks:
            if astar in set(order[:K].tolist()):
                cont[K] += 1
    return {f"Contain@{K}": round(cont[K] / max(n, 1), 3) for K in Ks} | \
           {"meanRankGap": round(float(np.mean(rg)), 2), "n_groups": n}


def load(npz_path):
    d = np.load(npz_path, allow_pickle=True)
    return {k: d[k] for k in d.files}
