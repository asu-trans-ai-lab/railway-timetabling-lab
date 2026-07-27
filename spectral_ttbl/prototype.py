"""
Spectral Screening for (Approximate) Branching in Time-Space Railway Scheduling
STAGE 1 prototype  --  standalone, grounded in:
  * Zhou & Zhong (2007), TR-B "Single-track train timetabling ... B&B with enhanced
    lower bounds"  ->  the SINGLE-TRACK instance + the MEET-PASS branching (branch on
    the order of two trains at the earliest conflict).  This is the TrainTimetablingLite
    (C++) setting: m stations, n trains, two directions, section/station headways.
  * Meng & Zhou (2014), TR-B  ->  the resource-time H-matrix: occupancy y_f(res,t),
    capacity  sum_f y_f(res,t) <= 1  (section both directions share one circuit).

Design (agreed): diagnostic-small + ENUMERATE columns (no pricing) + validate at the
ROOT node whether the top-K_pool spectral screen contains the Full-SB branching action.

Master (Meng-Zhou cumulative-flow / Zhou-Zhong RCPSP, column form):
    min sum_p c_p x_p
    s.t. sum_{p in P_i} x_p = 1                 each train i           (one trajectory)
         sum_p H_{rp} x_p <= 1                  each resource-time cell r (section/station)
         0 <= x_p <= 1
H_{rp}=1 iff trajectory p occupies resource-time cell r (section j during [enter, enter+run+h);
station u during [arrive, depart+g) -- headway tails h (section) and g (station)).

Branching alphabet (Zhou-Zhong): at the LP solution find conflict cells (sum occ > 1).
For each train i involved at a contested SECTION j at conflict time t*, create a
meet/pass action = an entry-time split of train i on section j at threshold theta=t*:
   child L: i enters section j at <= theta   (forbid i-cols with ent>theta)
   child R: i enters section j at  > theta   (forbid i-cols with ent<=theta)
This is an exact 2-partition of train i's columns; branching on i's entry time relative
to the meet resolves the single-track crossing conflict (the two meet orders).  B / B^L /
B^R are the affected-trajectory matrices (draft Def 2 / Eq 10).

Spectral screening (draft Sec 5): H~=W_R H W_P, rank-k SVD, S1 structural,
S2 LP activity, S3 left-right separation; rank-aggregate; top-K_pool; certify exactly.

Run:  python prototype.py --trains 6 --stations 5 --seed 1
"""
from __future__ import annotations
import argparse
import numpy as np
from scipy import sparse
from scipy.optimize import linprog
from scipy.stats import rankdata


# ============================================================= single-track instance
class Instance:
    """Single-track corridor, two directions. Resources: sections (both directions share
    one physical circuit) and stations. Trajectory columns = departure offset x optional
    single meet-wait at an intermediate station."""

    def __init__(self, n_trains=6, n_stations=5, dt=1.0, seed=0,
                 run_lo=6, run_hi=10, dwell_min=1, h_sec=2, g_sta=3,
                 dep_step=1, ideal_gap=3):
        rng = np.random.default_rng(seed)
        self.m = n_stations                       # stations 0..m-1
        self.S = n_stations - 1                   # sections 0..S-1 (section j: u=j <-> u=j+1)
        self.n = n_trains
        self.h = int(h_sec); self.g = int(g_sta)
        self.dwell_min = int(dwell_min)
        # section run times per direction (outbound dir0, inbound dir1)
        self.run = rng.integers(run_lo, run_hi + 1, size=(self.S, 2))
        # trains: alternate directions; random desired departures (bunched -> conflicts)
        self.dir = np.array([t % 2 for t in range(n_trains)], int)     # 0=outbound,1=inbound
        self.dep0 = np.array([(t // 2) * ideal_gap + rng.integers(0, 3)
                              for t in range(n_trains)], int)
        # departure window wide enough to guarantee SEQUENTIAL feasibility (each train can
        # depart after the whole line clears): span = sum of single-train traversal times.
        trav = int(self.run.max() * self.S + (dwell_min + self.g) * self.m + self.h * self.S)
        self.seq_span = n_trains * trav
        self.cols = []                            # dict(train, dep, cost, sec_ent{j:t}, cells[])
        self.train_cols = [[] for _ in range(n_trains)]
        for i in range(n_trains):
            self._enum_train(i, self.seq_span, dep_step)
        self.nP = len(self.cols)
        self._build_matrices()

    def _route(self, i):
        """ordered list of (section_j, direction) for train i."""
        if self.dir[i] == 0:                       # outbound: station 0->m-1, sections 0..S-1
            return [(j, 0) for j in range(self.S)]
        return [(j, 1) for j in range(self.S - 1, -1, -1)]  # inbound

    def _enum_train(self, i, dep_window, dep_step):
        """Departure-offset columns (wait at origin). Each column = a full space-time
        trajectory at fixed min dwell; the only freedom is departure time. Single-track
        meets are resolved by departure timing (and by the branch's entry-time split)."""
        route = self._route(i)
        for dep in range(self.dep0[i], self.dep0[i] + dep_window + 1, dep_step):
            cells, sec_ent, t = [], {}, dep
            for kpos, (j, d) in enumerate(route):
                start_u = j if d == 0 else j + 1
                t_enter = t + self.dwell_min
                for tt in range(t, t_enter + self.g):            # station occ + g tail
                    cells.append((("U", start_u), tt))
                rt = int(self.run[j, d])
                for tt in range(t_enter, t_enter + rt + self.h):  # section occ + h tail
                    cells.append((("S", j), tt))
                t = t_enter + rt
                sec_ent[j] = t_enter
            end_u = self.m - 1 if self.dir[i] == 0 else 0
            for tt in range(t, t + self.g):
                cells.append((("U", end_u), tt))
            cost = float(dep - self.dep0[i])                      # additional delay
            pidx = len(self.cols)
            self.cols.append(dict(train=i, dep=dep, cost=cost, sec_ent=sec_ent, cells=cells))
            self.train_cols[i].append(pidx)

    def _build_matrices(self):
        cid = {}
        rows, cb = [], []
        for p, c in enumerate(self.cols):
            for rc in c["cells"]:
                r = cid.setdefault(rc, len(cid))
                rows.append(r); cb.append(p)
        self.nR = len(cid)
        self.res_of = {v: k for k, v in cid.items()}
        self.H = sparse.csr_matrix((np.ones(len(rows)), (rows, cb)),
                                   shape=(self.nR, self.nP)).tocsr()
        self.cost = np.array([c["cost"] for c in self.cols], float)
        ar, ac = [], []
        for i, ps in enumerate(self.train_cols):
            for p in ps:
                ar.append(i); ac.append(p)
        self.A = sparse.csr_matrix((np.ones(len(ar)), (ar, ac)),
                                   shape=(self.n, self.nP))
        # section-cell rows only (sections are the single-track scarce resource, cap=1;
        # stations are sidings -> uncapacitated). map row->(j,t)
        self.sec_rows = {r: rc[0][1] for r, rc in self.res_of.items() if rc[0][0] == "S"}
        self.row_time = {r: rc[1] for r, rc in self.res_of.items()}


# ============================================================= master LP + duals
def solve_master(inst, forbid=None, active_cap=None):
    forbid = forbid or set()
    ub = np.ones(inst.nP)
    for p in forbid:
        ub[p] = 0.0
    if active_cap is None:
        deg = np.asarray((inst.H > 0).sum(axis=1)).ravel()
        sec = np.array(sorted(inst.sec_rows.keys()))            # only SECTION rows are cap=1
        active_cap = sec[deg[sec] >= 2] if sec.size else np.array([], int)
    Hc = inst.H[active_cap]
    res = linprog(inst.cost, A_ub=Hc, b_ub=np.ones(len(active_cap)),
                  A_eq=inst.A, b_eq=np.ones(inst.n),
                  bounds=list(zip(np.zeros(inst.nP), ub)), method="highs")
    if not res.success:
        return None
    lam = np.zeros(inst.nR)
    lam[active_cap] = np.maximum(-np.asarray(res.ineqlin.marginals), 0.0)
    pi = -np.asarray(res.eqlin.marginals)
    rc = inst.cost - (inst.A.T @ pi) - (inst.H.T @ lam)
    return dict(x=res.x, z=float(res.fun), lam=lam, pi=pi, rc=rc, active_cap=active_cap)


# ============================================================= meet-pass candidates
def conflict_actions(inst, sol, tol=1e-6, max_actions=80):
    """Branch candidates from FRACTIONAL train assignments (capacity is enforced in the LP,
    so conflicts surface as fractionality). For each train whose fractional columns disagree
    on the entry time into a section it uses, create an entry-time split (meet-pass order)
    action at each threshold between those entry times. Exact 2-partition of the train's cols."""
    x = sol["x"]; acts, seen = [], set()
    for i, ps in enumerate(inst.train_cols):
        fr = [p for p in ps if tol < x[p] < 1 - tol]
        if len(fr) < 2:
            continue
        secs = {}
        for p in fr:
            for j, e in inst.cols[p]["sec_ent"].items():
                secs.setdefault(j, set()).add(e)
        for j, es in secs.items():
            es = sorted(es)
            if len(es) < 2:
                continue
            for a in range(len(es) - 1):
                theta = es[a]
                left = {p for p in ps if j in inst.cols[p]["sec_ent"]
                        and inst.cols[p]["sec_ent"][j] <= theta}
                right = {p for p in ps if j in inst.cols[p]["sec_ent"]
                         and inst.cols[p]["sec_ent"][j] > theta}
                key = (i, j, theta)
                if left and right and key not in seen:
                    seen.add(key)
                    acts.append(dict(train=i, sec=j, theta=theta, left=left, right=right))
                    if len(acts) >= max_actions:
                        return acts
    return acts


def build_B(inst, acts):
    nA = len(acts)
    B = np.zeros((inst.nP, nA)); BL = np.zeros((inst.nP, nA)); BR = np.zeros((inst.nP, nA))
    for a, act in enumerate(acts):
        for p in act["left"]:
            BL[p, a] = 1.0; B[p, a] = 1.0
        for p in act["right"]:
            BR[p, a] = 1.0; B[p, a] = 1.0
    return B, BL, BR


# ============================================================= spectral screening
def screening_scores(inst, sol, acts, B, BL, BR, k=10):
    lam, x, rc = sol["lam"], sol["x"], sol["rc"]
    deg = np.asarray((inst.H > 0).sum(axis=1)).ravel()
    wR = 1.0 + 1.0 * lam + 0.5 * (deg >= 2)
    wP = 1.0 + 1.0 * np.abs(x) + 0.5 * np.maximum(0.0, -rc)
    Ht = (sparse.diags(wR) @ inst.H @ sparse.diags(wP))
    U, S, Vt = np.linalg.svd(Ht.toarray(), full_matrices=False)
    k = min(k, len(S)); Uk, Sk, Vtk = U[:, :k], S[:k], Vt[:k, :]
    S1 = np.linalg.norm((Sk[:, None] * Vtk) @ B, axis=0)                 # Eq 24
    q = np.abs(x) + np.maximum(0.0, -rc) + 0.5 * (inst.H.T @ (lam > 0))  # Eq 25-26
    S2 = q @ B
    UtH = Uk.T @ Ht                                                       # Eq 27
    S3 = np.linalg.norm(UtH @ (BL - BR), axis=0)
    agg = rankdata(S1) + rankdata(S2) + rankdata(S3)
    return dict(S1=S1, S2=S2, S3=S3, agg=agg)


# ============================================================= exact certification
def exact_delta(inst, sol, act, mu=0.5):
    z0, ac = sol["z"], sol["active_cap"]
    sl = solve_master(inst, forbid=act["right"], active_cap=ac)     # child L: keep left
    sr = solve_master(inst, forbid=act["left"], active_cap=ac)      # child R: keep right
    if sl is None or sr is None:
        return -1e9
    dL, dR = sl["z"] - z0, sr["z"] - z0
    return mu * min(dL, dR) + (1.0 - mu) * max(dL, dR)


# ============================================================= root experiment
def run_root(n_trains=6, n_stations=5, seed=0, k=10, Kpools=(1, 2, 3, 5), verbose=True):
    inst = Instance(n_trains=n_trains, n_stations=n_stations, seed=seed)
    sol = solve_master(inst)
    if sol is None:
        print("  [master LP infeasible]"); return None
    frac = int(np.sum((sol["x"] > 1e-6) & (sol["x"] < 1 - 1e-6)))
    acts = conflict_actions(inst, sol)
    if verbose:
        print(f"\n{'='*80}\n  STAGE-1 ROOT  |  single-track (Zhou-Zhong) | "
              f"{n_trains} trains, {n_stations} stations, seed={seed}\n{'='*80}")
        print(f"  columns nP={inst.nP}  resource-time cells nR={inst.nR}  "
              f"active-cap rows={len(sol['active_cap'])}")
        print(f"  LP z0={sol['z']:.3f}  fractional x={frac}  meet-pass actions |A|={len(acts)}")
    if len(acts) < 2:
        print("  [root LP integral / <2 candidate actions -- raise trains or bunch departures]")
        return dict(inst=inst, sol=sol, acts=acts)
    B, BL, BR = build_B(inst, acts)
    sc = screening_scores(inst, sol, acts, B, BL, BR, k=k)
    dex = np.array([exact_delta(inst, sol, a) for a in acts], float)
    a_sb = int(np.argmax(dex))
    order = np.argsort(-sc["agg"])
    rank_sb = int(np.where(order == a_sb)[0][0])
    if verbose:
        asb = acts[a_sb]
        print(f"\n  Full-SB best action a*={a_sb}: train {asb['train']}, section {asb['sec']}, "
              f"theta={asb['theta']}, Delta_exact={dex[a_sb]:.4f}")
        print(f"  spectral rank of Full-SB action = {rank_sb+1}/{len(acts)}  (RankGap={rank_sb})")
        print(f"\n  {'Kpool':>6} {'Contain@K':>10} {'BoundRec@K':>11} {'ChildLPsaved':>13}")
        for K in Kpools:
            pool = set(order[:K].tolist())
            contain = int(a_sb in pool)
            rec = max(dex[p] for p in pool) / (dex[a_sb] + 1e-9)
            saved = 1.0 - K / len(acts)
            print(f"  {K:>6} {contain:>10} {rec:>11.3f} {saved:>13.3f}")
        rng = np.random.default_rng(seed)
        K = Kpools[len(Kpools) // 2]
        rnd = np.mean([a_sb in set(rng.choice(len(acts), min(K, len(acts)), replace=False))
                       for _ in range(300)])
        rule = int(a_sb in set(np.argsort(-sc["S2"])[:K].tolist()))
        print(f"\n  Contain@{K}:  Spectral={int(a_sb in set(order[:K]))}  "
              f"Rule(S2)={rule}  Random(avg)={rnd:.2f}")
    return dict(inst=inst, sol=sol, acts=acts, scores=sc, dex=dex, a_sb=a_sb, order=order)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trains", type=int, default=6)
    ap.add_argument("--stations", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--k", type=int, default=10)
    args = ap.parse_args()
    run_root(n_trains=args.trains, n_stations=args.stations, seed=args.seed, k=args.k)
