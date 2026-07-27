"""
N-track TENSOR screening across MULTIPLE B&P nodes (extends nt_screen.py from one root node to a walk).

At each node of a depth-first walk (descending by the Full-SB best action) we record, per candidate
departure-threshold action a:
  * cheap PARENT-side scores (no child solve): primalgw, dualgw, primalSVD, dualSVD  (from nt_screen),
  * the per-action response signature over the node's involved cells, in TWO channels --
        channel 0 = b_a[c] * load[c]   (PRIMAL congestion-weighted, load = H@x),
        channel 1 = b_a[c] * rho[c]    (DUAL price-weighted),
    stacked into a per-node response TENSOR  R[action, cell, channel],
  * the Full-SB ground-truth dStrong (two child LP re-solves, node-aware).

TENSOR screening fuses the primal+dual channels through a shared low-rank action/cell subspace:
  * tensorHOSVD : truncate the action & cell modes to 95% energy (Tucker projection), score_a =
                  |sum over (cell,channel) of the low-rank reconstruction R_hat[a]|  -- a denoised,
                  primal+dual-fused generalisation of svd_cr (which compresses one channel at a time).
  * tensorCP    : same score from a CP-ALS rank-r reconstruction.

We then report, ACROSS ALL NODES, Containment@K / mean RankGap / mean per-node Spearman vs Full-SB for
every method -- the multi-node primal/dual/SVD/tensor comparison.

Run:  python nt_tensor.py --inst ds3 --max_cand 8 --max_nodes 6 --time 240
      python nt_tensor.py --inst toy --max_cand 40 --max_nodes 20
"""
from __future__ import annotations
import argparse, time, numpy as np
from collections import defaultdict
from scipy.stats import spearmanr
import nt_spacetime as NT
import nt_screen as NS
import tensor_lib as TL

BIG = 1e7


def node_full_sb(ctx, node_forbid, acts, z0):
    """Full strong branching at a node: child solves combine the node's forbidden columns with the
    action's (left/right) split. Returns dStrong per action + #solves."""
    nf = set(node_forbid); dS = []; nsolve = 0
    for a in acts:
        rl = NT.solve_ctx(ctx, forbid=nf | set(a["right"])); nsolve += 1   # left child
        rr = NT.solve_ctx(ctx, forbid=nf | set(a["left"]));  nsolve += 1   # right child
        dL = (rl["obj"] - z0) if rl["success"] else BIG
        dR = (rr["obj"] - z0) if rr["success"] else BIG
        dS.append(NS.MU * min(dL, dR) + (1 - NS.MU) * max(dL, dR))
    return np.array(dS), nsolve


# ----------------------------------------------------------------- per-node response tensor
def build_node_tensor(ctx, acts, x, rho, load, cid):
    """R[action, cell, channel] over the node's involved cells; channel 0=primal(load), 1=dual(rho)."""
    cells = sorted({c for a in acts for p in a["fr"] for c in ctx["cols"][p]["cells"] if c in cid})
    loc = {c: i for i, c in enumerate(cells)}
    R = np.zeros((len(acts), len(cells), 2))
    for ai, a in enumerate(acts):
        for p in a["fr"]:
            sgn = 1.0 if p in a["left"] else -1.0
            for c in ctx["cols"][p]["cells"]:
                if c in cid:
                    j = loc[c]; b = sgn * x[p]; r = cid[c]
                    R[ai, j, 0] += b * load[r]      # primal congestion-weighted response
                    R[ai, j, 1] += b * rho[r]       # dual price-weighted response
    return R


def _mode_proj(T, U, mode):
    """project tensor T onto subspace U (cols) along `mode`: T x_mode (U U^T)."""
    Tm = np.moveaxis(T, mode, 0); sh = Tm.shape
    P = U @ (U.T @ Tm.reshape(sh[0], -1))
    return np.moveaxis(P.reshape(sh), 0, mode)


def _leading(T, mode, thresh):
    unf = np.moveaxis(T, mode, 0).reshape(T.shape[mode], -1)
    U, s, _ = np.linalg.svd(unf, full_matrices=False)
    e = np.cumsum(s ** 2) / max(np.sum(s ** 2), 1e-12)
    r = max(1, int(np.searchsorted(e, thresh) + 1))
    return U[:, :r]


def tensor_scores(R, thresh=0.95, cp_rank=2):
    """tensorHOSVD + tensorCP screening scores (fused primal+dual, low-rank-denoised)."""
    Ua = _leading(R, 0, thresh); Uc = _leading(R, 1, thresh)
    Rhat = _mode_proj(_mode_proj(R, Ua, 0), Uc, 1)          # Tucker projection (channels kept full)
    sHO = np.abs(Rhat.sum(axis=(1, 2)))
    try:
        facs, w, _ = TL.cp_als(R, rank=min(cp_rank, R.shape[0], R.shape[1]), iters=40, center=False)
        Rcp = TL.cp_reconstruct(facs, w)
        sCP = np.abs(Rcp.sum(axis=(1, 2)))
    except Exception:
        sCP = sHO.copy()
    return sHO, sCP


# ----------------------------------------------------------------- multi-node walk
def walk(inst, dt, headway, dep_step, max_cand, max_nodes, time_budget):
    ctx = NT.build_ctx(inst, dt=dt, headway_min=headway, dep_step=dep_step)
    cid = NS.cell_to_row(ctx)
    methods = ["primalgw", "dualgw", "primalSVD", "dualSVD", "tensorHOSVD", "tensorCP", "pseudocost", "random"]
    pooled = {m: [] for m in methods}; target = []; gids = []
    rng = np.random.default_rng(0)
    stack = [frozenset()]; nodes = 0; t0 = time.time(); total_solve = 0; ndepth = {}
    print(f"\n{'='*88}\n  N-TRACK MULTI-NODE TENSOR SCREENING  |  RAS {inst}  "
          f"(max_cand={max_cand}, max_nodes={max_nodes})\n{'='*88}")
    while stack and nodes < max_nodes and (time.time() - t0) < time_budget:
        nf = stack.pop()
        r = NT.solve_ctx(ctx, forbid=nf)
        if not r["success"]:
            continue
        x, rho, z0 = r["x"], r["rho"], r["obj"]
        load = np.asarray(ctx["H"] @ x).ravel()
        acts = NS.candidates(ctx, x, max_cand=max_cand)
        if len(acts) < 2:
            continue                                        # near-integral leaf
        gid = nodes; nodes += 1
        dS, nsolve = node_full_sb(ctx, nf, acts, z0); total_solve += nsolve
        # cheap scores
        sc = NS.scores(ctx, acts, x, rho, load, cid)
        B = NS.action_response(ctx, acts, x, cid)
        Vc = B.T - B.T.mean(axis=1, keepdims=True)
        sv = np.linalg.svd(Vc, compute_uv=False)
        energy = np.cumsum(sv ** 2) / max(np.sum(sv ** 2), 1e-12)
        r95 = int(np.searchsorted(energy, 0.95) + 1)
        sc["primalSVD"] = NS.svd_cr(B, load, r95)
        sc["dualSVD"] = NS.svd_cr(B, rho, r95)
        # tensor scores
        R = build_node_tensor(ctx, acts, x, rho, load, cid)
        sc["tensorHOSVD"], sc["tensorCP"] = tensor_scores(R)
        sc["pseudocost"] = np.array([a["frac"] for a in acts])
        sc["random"] = rng.random(len(acts))
        for m in methods:
            pooled[m].append(np.asarray(sc[m], float))
        target.append(dS); gids.append(np.full(len(acts), gid))
        astar = int(np.argmax(dS))
        print(f"   node {gid:>2} (depth {len(nf):>3} forbid)  |A|={len(acts):>2}  LP={z0:>8.1f}  "
              f"Full-SB best dStrong={dS[astar]:>8.2f}  ({nsolve} solves, {time.time()-t0:>5.1f}s)")
        # descend by Full-SB best (follow the real tree)
        a = acts[astar]
        stack.append(nf | frozenset(a["right"]))            # left child (keep left)
        stack.append(nf | frozenset(a["left"]))             # right child
    if not target:
        print("  [no multi-candidate nodes collected]"); return
    # aggregate
    score = {m: np.concatenate(pooled[m]) for m in methods}
    tgt = np.concatenate(target); gid_all = np.concatenate(gids)
    # per-node Spearman (mean over nodes)
    def mean_spear(s):
        out = []
        for g in np.unique(gid_all):
            mask = gid_all == g
            if mask.sum() >= 2 and np.std(s[mask]) > 0 and np.std(tgt[mask]) > 0:
                rr = spearmanr(s[mask], tgt[mask]).correlation
                if rr == rr:
                    out.append(rr)
        return float(np.mean(out)) if out else float("nan")
    n_nodes = len(np.unique(gid_all))
    print(f"\n  collected {n_nodes} multi-candidate nodes, {len(tgt)} candidate rows, "
          f"{total_solve} Full-SB child solves ({time.time()-t0:.1f}s)")
    print(f"\n  {'method':>12} {'Contain@1':>9} {'Contain@3':>9} {'Contain@5':>9} "
          f"{'RankGap':>8} {'Spearman':>9}")
    for m in methods:
        c = TL.containment_by_group(score[m], tgt, gid_all, Ks=(1, 3, 5))
        print(f"  {m:>12} {c['Contain@1']:>9.2f} {c['Contain@3']:>9.2f} {c['Contain@5']:>9.2f} "
              f"{c['meanRankGap']:>8.2f} {mean_spear(score[m]):>9.3f}")
    K = 5
    print(f"\n  SCREENING SPEEDUP @K={K}: Full-SB does |A|x2 child solves/node; a screen evaluates 2K "
          f"-> up to {max_cand}/{K:.0f}x fewer per node when |A|={max_cand}.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--inst", default="ds3", choices=list(NT.INSTS))
    ap.add_argument("--dt", type=float, default=1.0)
    ap.add_argument("--headway", type=float, default=3.0)
    ap.add_argument("--dep_step", type=int, default=8)
    ap.add_argument("--max_cand", type=int, default=8)
    ap.add_argument("--max_nodes", type=int, default=6)
    ap.add_argument("--time", type=float, default=240.0)
    args = ap.parse_args()
    walk(args.inst, args.dt, args.headway, args.dep_step, args.max_cand, args.max_nodes, args.time)
