"""
Stage B probe -- test the B&B more + GENERATE the action / solution-trajectory data and
analyze its LOW-RANK structure (the empirical bridge to spectral branch screening).

Produces, from the faithful single-track B&B (ttbl_bnb.py):
  (1) Algorithm test sweep: no-LB vs CC-LB node counts, optimum agreement, across seeds/sizes.
  (2) COLUMN POOL  H  (resource-time cells x train trajectories collected over the search):
      SVD -> singular spectrum, rank capturing 95%/99% variance, effective (participation) rank.
      Evidence that the resource-time incidence is low-rank (draft Sec 2.4 / 5.2).
  (3) ACTION FOOTPRINT matrix  G  (resource-time cells x meet-pass branch actions, = H B.a):
      SVD -> low-rank of the branch-action space.
  (4) SCREENING PREVIEW: per-action spectral score ||Sigma_k V_k^T b_a|| vs the exact branch
      RESPONSE (child cost delta): Spearman correlation -> does the low-rank footprint predict
      which meet-pass branch helps? (lightweight Stage-C look on REAL B&B data.)

Saves arrays to  spectral_ttbl/lowrank_out/.
Run:  python lowrank_probe.py
"""
from __future__ import annotations
import os
import numpy as np
from scipy.stats import spearmanr
import ttbl_bnb as T

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lowrank_out")
os.makedirs(OUT, exist_ok=True)


# ---------- helpers ----------
def cells_to_matrix(cells_list, dedup=True, max_cols=4000):
    """list[frozenset[cell]] -> 0/1 dense matrix (n_cells x n_cols) + stats."""
    if dedup:
        seen, uniq = set(), []
        for c in cells_list:
            if c and c not in seen:
                seen.add(c); uniq.append(c)
        cells_list = uniq
    cells_list = [c for c in cells_list if c][:max_cols]
    idx = {}
    for c in cells_list:
        for cell in c:
            if cell not in idx:
                idx[cell] = len(idx)
    M = np.zeros((len(idx), len(cells_list)), dtype=float)
    for j, c in enumerate(cells_list):
        for cell in c:
            M[idx[cell], j] = 1.0
    return M, len(cells_list)


def svd_report(name, M, n_raw):
    if M.size == 0 or min(M.shape) < 2:
        print(f"  [{name}] too small ({M.shape})"); return None
    s = np.linalg.svd(M, compute_uv=False)
    energy = np.cumsum(s ** 2) / np.sum(s ** 2)
    r95 = int(np.searchsorted(energy, 0.95) + 1)
    r99 = int(np.searchsorted(energy, 0.99) + 1)
    eff = float(np.sum(s) ** 2 / np.sum(s ** 2))            # participation ratio
    full = min(M.shape)
    print(f"  [{name}]  shape={M.shape} (raw cols={n_raw}, unique used={M.shape[1]})")
    print(f"     full rank<= {full} | rank@95%={r95}  rank@99%={r99}  eff(participation)={eff:.1f}"
          f"  -> compression {M.shape[1]}/{r95} = {M.shape[1]/max(1,r95):.1f}x at 95%")
    print(f"     top-8 singular values: {np.round(s[:8],2).tolist()}")
    return s


# ---------- (1) algorithm test sweep ----------
def algo_sweep(sizes=(6, 8, 10), seeds=(1, 2, 3, 4, 5)):
    print(f"\n{'='*78}\n  (1) ALGORITHM TEST  -- no-LB vs CC-LB nodes, optimum agreement\n{'='*78}")
    print(f"  {'trains':>6} {'seed':>4} {'opt':>6} {'delay':>6} {'nodes_noLB':>11} "
          f"{'nodes_LB':>9} {'reduction':>9} {'opt==':>6}")
    agg = {}
    for n in sizes:
        red = []
        for s in seeds:
            inst = T.make_instance(n, s)
            r0 = T.BnB(inst, use_lb=False).solve()
            r1 = T.BnB(inst, use_lb=True).solve()
            ok = "OK" if r0["opt"] == r1["opt"] else "MISMATCH"
            rr = r1["nodes"] / max(1, r0["nodes"])
            red.append(rr)
            print(f"  {n:>6} {s:>4} {r1['opt']:>6} {r1['delay']:>6} {r0['nodes']:>11} "
                  f"{r1['nodes']:>9} {1-rr:>8.0%} {ok:>6}")
        agg[n] = float(np.mean(red))
    print("  mean node-reduction by size:",
          {k: f"{1-v:.0%}" for k, v in agg.items()})


# ---------- (2-4) low-rank generation + analysis ----------
def lowrank(n_trains=8, seeds=(1, 2, 3, 4, 5), svd_rank=10):
    print(f"\n{'='*78}\n  (2-4) LOW-RANK ANALYSIS  -- {n_trains} trains, seeds {list(seeds)}\n{'='*78}")
    cols, foot, resp, meta = [], [], [], []
    for s in seeds:
        inst = T.make_instance(n_trains, s)
        bnb = T.BnB(inst, use_lb=True, collect=True)
        bnb.solve()
        cols += bnb.col_cells
        foot += bnb.act_foot
        resp += bnb.act_resp
        meta += bnb.act_meta
    print(f"  collected: {len(cols)} trajectory columns, {len(foot)} branch actions")

    # (2) column pool low-rank
    Hm, nraw = cells_to_matrix(cols, dedup=True)
    sH = svd_report("column pool H (resource-time x trajectory)", Hm, len(cols))

    # (3) action footprint low-rank
    Gm, graw = cells_to_matrix(foot, dedup=True)
    sG = svd_report("action footprint G (resource-time x meet-pass action)", Gm, len(foot))

    # (4) screening preview: does low-rank footprint predict branch response?
    print("\n  (4) SCREENING PREVIEW (real B&B data):")
    # build action matrix WITHOUT dedup (keep response alignment), shared cell index with foot
    idx = {}
    for c in foot:
        for cell in c:
            idx.setdefault(cell, len(idx))
    if len(idx) >= 2 and len(foot) >= 5:
        A = np.zeros((len(idx), len(foot)))
        for j, c in enumerate(foot):
            for cell in c:
                A[idx[cell], j] = 1.0
        # weight rows by how contested they are (resource pressure) = row frequency
        w = A.sum(axis=1); w = 1.0 + w / max(1.0, w.max())
        Aw = A * w[:, None]
        U, S, Vt = np.linalg.svd(Aw, full_matrices=False)
        k = min(svd_rank, len(S))
        score = np.linalg.norm((S[:k, None] * Vt[:k, :]), axis=0)       # ||Sigma_k V_k^T e_a|| per action
        r = np.array(resp, float)
        rho = spearmanr(score, r).correlation
        # top-K containment of the max-response action
        a_star = int(np.argmax(r))
        order = np.argsort(-score)
        for K in (1, 3, 5, 10):
            contain = int(a_star in set(order[:K].tolist()))
            print(f"     K={K:>2}: best-response action in top-K spectral screen? {bool(contain)}")
        print(f"     Spearman(spectral score, exact branch response) = {rho:+.3f}  "
              f"(rank of best action = {int(np.where(order==a_star)[0][0])+1}/{len(foot)})")
    else:
        print("     [not enough actions/cells for screening preview]")

    # save arrays
    if sH is not None:
        np.save(os.path.join(OUT, f"sv_columnpool_n{n_trains}.npy"), sH)
    if sG is not None:
        np.save(os.path.join(OUT, f"sv_actionfootprint_n{n_trains}.npy"), sG)
    np.save(os.path.join(OUT, f"action_response_n{n_trains}.npy"), np.array(resp, float))
    print(f"\n  -> saved singular spectra + responses to {OUT}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--trains", type=int, default=8)
    ap.add_argument("--seeds", type=int, nargs="*", default=[1, 2, 3, 4, 5])
    ap.add_argument("--no-sweep", action="store_true")
    args = ap.parse_args()
    if not args.no_sweep:
        algo_sweep()
    lowrank(n_trains=args.trains, seeds=tuple(args.seeds))
