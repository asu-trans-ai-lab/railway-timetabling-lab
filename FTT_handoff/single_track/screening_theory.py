"""
(b) "Compressed Response Screening" (screening_illustrated.pdf) run on TRAIN SCHEDULING
(single-track timetabling B&B), NOT UE-on-Sioux-Falls.

Mapping (one B&B node = one decision point, like the PDF's N moves on L coords):
  * design move z_a   = candidate meet-pass branching action (sec, i, j) at the node
  * response x*(z_a)  = the resulting CHILD schedule (per-train, per-section departure times),
                        a vector in R^L with L = n_trains * n_sections
  * score S(x)        = total completion time = sum over trains of last-section departure
                        => S is LINEAR: S = c^T x, with c the selector of last-section cells.
                        (The PDF's "linear score => exact" case: S(x)-S(xhat) = c^T delta exactly.)
  * nabla S = c       (the cost gradient; here a sparse 0/1 selector, NOT uniform).

We then reproduce the PDF quantities on this real instance:
  V = [x_a] centered, SVD -> sigma_i^2 (response variances);
  tail(r)=sum_{i>r}sigma_i^2 ; gradient-weighted tail(r)=sum_{i>r}sigma_i^2 (u_i^T c)^2 ;
  sigma_score(r)=sqrt(GWtail) ; loose=||c|| sqrt(tail) ; trueRMSE(r)=rms_a(S_a - c^T xhat_a(r)) ;
  K'(r)=#{moves within 2 sigma_score(r) of best score} ; inverse rule r*=min r with sigma_score<=tau.

Run:  python screening_theory.py --trains 6 --seed 3
"""
from __future__ import annotations
import os, argparse, numpy as np
import ttbl_bnb as T
from ttbl_bnb import G_I, section_no
import learn_branch as L

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lowrank_out")
os.makedirs(OUT, exist_ok=True)


def root_table(inst):
    ev = T.BnB(inst, use_lb=True); ev.opt = float("inf")
    tab = [[[0, 0, 0, 0] for _ in range(inst.S)] for _ in range(inst.n)]
    for tr in range(inst.n):
        ev.extend_job(tab, tr, 0)
    return ev, tab


def schedule_vec(inst, table):
    """flatten per-train per-section DEPARTURE times -> response vector in R^(n*S)."""
    return np.array([table[t][s][1] for t in range(inst.n) for s in range(inst.S)], float)


def cost_selector(inst):
    """c such that S = c^T x = sum_t depart[t, last_section(t)] (linear score, nabla S = c)."""
    c = np.zeros(inst.n * inst.S)
    for t in range(inst.n):
        last = section_no(inst.direction[t], inst.S - 1, inst.S)
        c[t * inst.S + last] = 1.0
    return c


def response_field(inst, table, child="ifirst"):
    """response field at a node: columns = child schedules over candidate meet-pass actions."""
    ev = T.BnB(inst, use_lb=True); ev.opt = float("inf")
    cands = L.candidate_pairs(inst, table)
    X, S = [], []
    c = cost_selector(inst)
    for (k, i, j) in cands:
        ta = L.push_after(ev, table, k, i, j)        # i-first child
        tb = L.push_after(ev, table, k, j, i)        # j-first child
        # response = the BETTER (min-cost) child schedule (the move's realized outcome)
        sa = ev.total_cost(ta); sb = ev.total_cost(tb)
        t_resp = ta if sa <= sb else tb
        X.append(schedule_vec(inst, t_resp))
        S.append(min(sa, sb))
    return np.array(X).T, np.array(S), c, cands


def analyze(inst, table, taus=None, label=""):
    X, S, c, cands = response_field(inst, table)
    L_, N = X.shape
    if N < 3:
        print(f"  [node has only {N} candidate moves -- skip]"); return None
    xbar = X.mean(axis=1)
    Xc = X - xbar[:, None]
    U, sig, Wt = np.linalg.svd(Xc, full_matrices=False)
    var = sig ** 2
    trace = var.sum()
    energy = np.cumsum(var) / max(trace, 1e-12)
    uC = U.T @ c                                       # (u_i^T nabla S)
    cn = np.linalg.norm(c)
    r95 = int(np.searchsorted(energy, 0.95) + 1)
    print(f"\n{'='*88}")
    print(f"  (b) COMPRESSED RESPONSE SCREENING -- TRAIN SCHEDULING {label}")
    print(f"      L={L_} (={inst.n} trains x {inst.S} sections),  N={N} meet-pass moves")
    print(f"{'='*88}")
    print(f"  response trace sum sigma_i^2 = {trace:,.1f}   ||nabla S||={cn:.2f}   "
          f"rank@95% energy = {r95}")
    print(f"  top variances sigma_i^2: {np.round(var[:6],1).tolist()}")
    print(f"  cumulative energy %:    {np.round(energy[:6]*100,1).tolist()}")
    print(f"\n  {'r':>2} {'energy%':>7} {'tail':>9} {'GW-tail':>9} {'sig_score':>9} "
          f"{'loose':>8} {'trueRMSE':>8} {'K(2sig)':>7}")
    Smin = S.min(); rows = []
    for r in range(1, min(L_, N)):
        tail = var[r:].sum()
        gw = float((var[r:] * uC[r:] ** 2).sum())
        ss = np.sqrt(gw)
        loose = cn * np.sqrt(tail)
        Ur = U[:, :r]
        Xhat = xbar[:, None] + Ur @ (Ur.T @ Xc)
        Shat = c @ Xhat
        rmse = float(np.sqrt(np.mean((Shat - S) ** 2)))
        Kp = int(np.sum(S - Smin <= 2 * ss))
        rows.append((r, tail, gw, ss, loose, rmse, Kp))
        print(f"  {r:>2} {energy[r-1]*100:>6.1f} {tail:>9.1f} {gw:>9.1f} {ss:>9.2f} "
              f"{loose:>8.1f} {rmse:>8.2f} {Kp:>7}")
    if taus is None:
        smax = rows[0][3]
        taus = [round(smax * f, 1) for f in (0.5, 0.25, 0.1)]
    print(f"\n  inverse rank rule r* = min r with sigma_score(r) <= tau:")
    for tau in taus:
        rstar = next((r for (r, _, _, ss, *_) in rows if ss <= tau), None)
        print(f"     tau={tau:>7}  ->  r* = {rstar}")
    ss95 = next(ss for (r, _, _, ss, *_) in rows if r == r95)
    gaps = np.sort(S - Smin)
    print(f"\n  TIE CLUSTER at r={r95}: sigma_score={ss95:.2f}, 2-sigma band={2*ss95:.1f}")
    print(f"     score gaps above best (sorted): {np.round(gaps,1).tolist()}")
    print(f"     => K' = {int(np.sum(S-Smin <= 2*ss95))} moves inside band (rest safely dropped)")
    np.save(os.path.join(OUT, "screen_train_var.npy"), var)
    np.save(os.path.join(OUT, "screen_train_uC.npy"), uC)
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trains", type=int, default=6)
    ap.add_argument("--seed", type=int, default=3)
    args = ap.parse_args()
    inst = T.make_instance(args.trains, args.seed)
    ev, tab = root_table(inst)
    analyze(inst, tab, label=f"(root node, {args.trains} trains, seed {args.seed})")
