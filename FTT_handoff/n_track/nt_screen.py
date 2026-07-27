"""
Branch-and-price screening on the N-track space-time instance (nt_spacetime.py),
with the TRUE LP duals as the gradient-weight (the regime single-track lacked).

At a node (LP relaxation with fractional trajectory assignment), candidate branching actions
are departure-time threshold splits per fractional train (exact 2-partition of its columns).
Full strong branching re-solves both child LPs for every candidate; we screen cheaply and ask:
does the screen contain the Full-SB action in its top-K, and how many child LP solves are saved?

Screening scores compared:
  * dual-gw   : TRUE-dual gradient-weighted -- separates a train's high-rho vs low-rho columns
                (uses only the parent LP duals rho_{a,t}; NO child solve).  This is the
                Eq. (5) score realized with real duals.
  * pseudocost: fractional mass of the train (cheap, dual-free).
  * random.
Metric: Containment@K / RankGap / BoundRecovery@K vs Full-SB; LP-solve speedup = |A|/K.

Run:  python nt_screen.py --inst toy
      python nt_screen.py --inst ds3 --max_cand 30 --dep_step 8
"""
from __future__ import annotations
import argparse, time, numpy as np
from scipy.stats import spearmanr
import nt_spacetime as NT

MU = 0.5
BIG = 1e7


def cell_to_row(ctx):
    return {cell: row for row, cell in ctx["cell_of"].items()}


def rho_load(ctx, cid, rho, p):
    """sum of dual prices over the cells a column occupies (its DUAL resource-price exposure)."""
    return float(sum(rho[cid[c]] for c in ctx["cols"][p]["cells"] if c in cid))


def prim_load(ctx, cid, load, p):
    """sum of PRIMAL cell flow (H@x) over the cells a column occupies (its primal congestion exposure).
    Primal analog of rho_load: how contended are the resources this column uses, by LP flow not price."""
    return float(sum(load[cid[c]] for c in ctx["cols"][p]["cells"] if c in cid))


def action_response(ctx, acts, x, cid):
    """Per-action primal RESPONSE vector b_a over cells: frac-weighted net occupancy shift of the split
    (left columns +x, right columns -x). Columns of B^T are the 'response field' for SVD screening."""
    nR = ctx["nR"]
    B = np.zeros((len(acts), nR))
    for ai, a in enumerate(acts):
        for p in a["fr"]:
            sgn = 1.0 if p in a["left"] else -1.0
            for c in ctx["cols"][p]["cells"]:
                if c in cid:
                    B[ai, cid[c]] += sgn * x[p]
    return B                                              # (nActions x nCells)


def svd_cr(B, g, rank):
    """Compressed-response screening score (screening_illustrated.pdf):
       score_a = sum_{i<r} sigma_i (u_i^T g)(v_i^T b_a) = b_a . (U_r U_r^T g),  with V = B^T centered.
    g = gradient in cell-space (rho for DUAL-SVD, primal load for PRIMAL-SVD)."""
    V = B.T                                               # (nCells x nActions)
    Vc = V - V.mean(axis=1, keepdims=True)                # center over actions
    U, s, Wt = np.linalg.svd(Vc, full_matrices=False)
    r = max(1, min(rank, U.shape[1]))
    gproj = U[:, :r] @ (U[:, :r].T @ g)                   # gradient projected to rank-r response subspace
    return np.abs(B @ gproj)                              # |gradient-weighted low-rank response| per action


def candidates(ctx, x, tol=1e-6, max_cand=40):
    """departure-threshold split actions per fractional train (exact 2-partition)."""
    cols, tcs = ctx["cols"], ctx["train_cols"]
    acts = []
    for ti, ps in tcs.items():
        fr = [p for p in ps if tol < x[p] < 1 - tol]
        if len(fr) < 2:
            continue
        deps = sorted({cols[p]["dep"] for p in fr})
        for k in range(len(deps) - 1):
            th = deps[k]
            L = {p for p in ps if cols[p]["dep"] <= th}
            R = {p for p in ps if cols[p]["dep"] > th}
            if L and R:
                acts.append(dict(train=ti, theta=th, left=L, right=R,
                                 frac=float(sum(x[p] for p in fr)), fr=fr))
    acts.sort(key=lambda a: -a["frac"])
    return acts[:max_cand]


def full_sb(ctx, acts, z0):
    """exact strong-branching delta per action via child LP re-solves."""
    dstrong = []; t0 = time.time(); nsolve = 0
    for a in acts:
        rl = NT.solve_ctx(ctx, forbid=a["right"]); nsolve += 1   # left child: keep left
        rr = NT.solve_ctx(ctx, forbid=a["left"]); nsolve += 1    # right child: keep right
        dL = (rl["obj"] - z0) if rl["success"] else BIG
        dR = (rr["obj"] - z0) if rr["success"] else BIG
        dstrong.append(MU * min(dL, dR) + (1 - MU) * max(dL, dR))
    return np.array(dstrong), nsolve, time.time() - t0


def scores(ctx, acts, x, rho, load, cid):
    """cheap screening scores (no child solve): dual-gw, primal-gw, pseudocost."""
    dualgw, primalgw, pc = [], [], []
    dcache, pcache = {}, {}
    def dl(p):
        if p not in dcache: dcache[p] = rho_load(ctx, cid, rho, p)
        return dcache[p]
    def pl(p):
        if p not in pcache: pcache[p] = prim_load(ctx, cid, load, p)
        return pcache[p]
    for a in acts:
        Lf = [p for p in a["fr"] if p in a["left"]]
        Rf = [p for p in a["fr"] if p in a["right"]]
        dL = np.mean([dl(p) for p in Lf]) if Lf else 0.0
        dR = np.mean([dl(p) for p in Rf]) if Rf else 0.0
        pL = np.mean([pl(p) for p in Lf]) if Lf else 0.0
        pR = np.mean([pl(p) for p in Rf]) if Rf else 0.0
        dualgw.append(a["frac"] * abs(dL - dR))          # DUAL gradient-weighted score
        primalgw.append(a["frac"] * abs(pL - pR))        # PRIMAL gradient-weighted score (flow, no duals)
        pc.append(a["frac"])                              # pseudocost (frac mass, dual-free)
    return dict(dualgw=np.array(dualgw), primalgw=np.array(primalgw), pseudocost=np.array(pc))


def run(inst="toy", dt=1.0, headway=3.0, dep_step=4, max_cand=40, Ks=(1, 3, 5), seed=0):
    print(f"\n{'='*82}\n  N-TRACK B&P SCREENING  |  RAS {inst}\n{'='*82}")
    ctx = NT.build_ctx(inst, dt=dt, headway_min=headway, dep_step=dep_step)
    r0 = NT.solve_ctx(ctx)
    z0 = r0["obj"]; x = r0["x"]; rho = r0["rho"]
    load = np.asarray(ctx["H"] @ x).ravel()              # primal cell flow (congestion), no duals
    cid = cell_to_row(ctx)
    nfrac = int(np.sum((x > 1e-6) & (x < 1 - 1e-6)))
    nbind = int(np.sum(rho > 1e-6))
    acts = candidates(ctx, x, max_cand=max_cand)
    print(f"  columns={ctx['nP']} cell-rows={ctx['nR']} (binding duals {nbind})  LP obj={z0:.1f}")
    print(f"  fractional x={nfrac}  candidate branch actions |A|={len(acts)} (capped {max_cand})")
    if len(acts) < 2:
        print("  [<2 candidates -- LP near-integral; raise trains/instance]"); return
    dS, nsolve_full, t_full = full_sb(ctx, acts, z0)
    sc = scores(ctx, acts, x, rho, load, cid)
    # SVD compressed-response screening (primal gradient = flow; dual gradient = rho)
    B = action_response(ctx, acts, x, cid)
    Vc = (B.T - B.T.mean(axis=1, keepdims=True))
    sv = np.linalg.svd(Vc, compute_uv=False)
    energy = np.cumsum(sv ** 2) / max(np.sum(sv ** 2), 1e-12)
    r95 = int(np.searchsorted(energy, 0.95) + 1)
    sc["primalSVD"] = svd_cr(B, load, r95)
    sc["dualSVD"] = svd_cr(B, rho, r95)
    rng = np.random.default_rng(seed)
    sc["random"] = rng.random(len(acts))
    a_star = int(np.argmax(dS))
    print(f"\n  Full-SB: {len(acts)} actions x2 child LP solves = {nsolve_full} solves ({t_full:.1f}s)")
    print(f"  Full-SB best action: train {acts[a_star]['train']} theta={acts[a_star]['theta']} "
          f"dStrong={dS[a_star]:.2f}")
    print(f"  response field SVD: rank@95% energy = {r95}/{len(acts)}  "
          f"(top energy% {np.round(energy[:4]*100,1).tolist()})")
    print(f"\n  {'method':>11} {'Contain@1':>9} {'Contain@3':>9} {'Contain@5':>9} "
          f"{'RankGap':>8} {'Spearman':>9} {'BoundRec@5':>10}")
    for m in ("primalgw", "dualgw", "primalSVD", "dualSVD", "pseudocost", "random"):
        s = sc[m]; order = np.argsort(-s)
        row = {}
        for K in Ks:
            row[K] = int(a_star in set(order[:K].tolist()))
        rg = int(np.where(order == a_star)[0][0])
        rho_s = spearmanr(s, dS).correlation
        brec = max(dS[p] for p in order[:5]) / (dS[a_star] + 1e-9)
        print(f"  {m:>11} {row[1]:>9} {row[3]:>9} {row[5]:>9} {rg:>8} "
              f"{(rho_s if rho_s==rho_s else 0):>9.3f} {brec:>10.3f}")
    K = Ks[-1]
    print(f"\n  SCREENING SPEEDUP @K={K}: Full-SB {nsolve_full} child solves -> screen {2*K} "
          f"= {nsolve_full/(2*K):.1f}x fewer LP re-solves (dual-gw contains best@{K}? "
          f"{bool(a_star in set(np.argsort(-sc['dualgw'])[:K].tolist()))})")
    return dict(inst=inst, nA=len(acts), nfrac=nfrac, nbind=nbind, dS=dS, sc=sc, a_star=a_star)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--inst", default="toy", choices=list(NT.INSTS))
    ap.add_argument("--dt", type=float, default=1.0)
    ap.add_argument("--headway", type=float, default=3.0)
    ap.add_argument("--dep_step", type=int, default=4)
    ap.add_argument("--max_cand", type=int, default=40)
    args = ap.parse_args()
    run(args.inst, dt=args.dt, headway=args.headway, dep_step=args.dep_step, max_cand=args.max_cand)
