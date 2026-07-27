"""
Foundation for tensor response-learning: the RICH multi-layer feature + multi-output response
collector (the 'mapping is the research' layer, not H/B->SVD->label).

For every B&B node and candidate meet-pass action a=(sec,i,j) we log:
  feature layers (cheap, PARENT-side):
    node : depth-proxy (delay-so-far), n_candidates, n_conflicts, mean_contention, gap-proxy
    action: meet_flag, overlap cd, sec_rel, terminal_flag, arr_gap, run_i, run_j
    dual  : contention(sec), occupancy(sec), slack(sec), contention(sec-1), contention(sec+1)  [LP-dual proxies]
    traj  : completion_i, completion_j, delay_i, delay_j, conflict_count_i, conflict_count_j
  multi-output RESPONSE Y (the targets, from child probing):
    dL, dR, dStrong, dUB, imbalance, dPrimal, dViolation(feasibility recovery)
  per-section response Rsec (S x 3) for tensor decomposition.

Saves  lowrank_out/tensor_dataset_n{size}.npz  with X, Y, Rsec, groups (inst,node), dStrong,
feature_names, layer_index, metric_names.

Run:  python tensor_features.py --size 10 --seeds 1..20
"""
from __future__ import annotations
import os, argparse, numpy as np
import ttbl_bnb as T
from ttbl_bnb import G_I
import learn_branch as L

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lowrank_out")
os.makedirs(OUT, exist_ok=True)
MU = 0.5
METRICS = ["dL", "dR", "dStrong", "dUB", "imbalance", "dPrimal", "dViolation"]
SECMETRICS = ["occupancy", "contention", "wait"]

FEATS = (["node_delay", "n_cand", "n_conf", "mean_contention", "gap_proxy"]            # node (5)
         + ["meet", "cd", "sec_rel", "terminal", "arr_gap", "run_i", "run_j"]          # action (7)
         + ["cont_sec", "occ_sec", "slack_sec", "cont_prev", "cont_next"]              # dual proxy (5)
         + ["compl_i", "compl_j", "delay_i", "delay_j", "nconf_i", "nconf_j"])         # traj (6)
LAYER_INDEX = {"node": (0, 5), "action": (5, 12), "dual": (12, 17), "traj": (17, 23)}


def sched(inst, table):
    return np.array([table[t][s][1] for t in range(inst.n) for s in range(inst.S)], float)


def sec_metrics(inst, table):
    S = inst.S; M = np.zeros((S, 3))
    for s in range(S):
        occ = 0.0; iv = []
        for t in range(inst.n):
            a, d = table[t][s][0], table[t][s][1]
            occ += d - a; iv.append((a, d))
        c = sum(1 for x in range(inst.n) for y in range(x + 1, inst.n)
                if iv[x][0] < iv[y][1] + G_I and iv[y][0] < iv[x][1] + G_I)
        M[s, 0] = occ; M[s, 1] = c
        M[s, 2] = max(0.0, (max(d for _, d in iv) - min(a for a, _ in iv)) - occ)   # idle/wait proxy
    return M


def contention(inst, table, s):
    iv = [(table[t][s][0], table[t][s][1]) for t in range(inst.n)]
    return sum(1 for x in range(inst.n) for y in range(x + 1, inst.n)
               if iv[x][0] < iv[y][1] + G_I and iv[y][0] < iv[x][1] + G_I)


def train_conflicts(inst, table, t):
    """# sections where train t overlaps another within headway."""
    c = 0
    for s in range(inst.S):
        a, d = table[t][s][0], table[t][s][1]
        for o in range(inst.n):
            if o == t:
                continue
            ao, do = table[o][s][0], table[o][s][1]
            if a < do + G_I and ao < d + G_I:
                c += 1; break
    return c


def collect(size, seeds, max_nodes=100):
    rows_X, rows_Y, rows_R, inst_ids, node_ids, dstrong = [], [], [], [], [], []
    for s in seeds:
        inst = T.make_instance(size, s)
        ev = T.BnB(inst, use_lb=True); ev.opt = float("inf")
        tab = [[[0, 0, 0, 0] for _ in range(inst.S)] for _ in range(inst.n)]
        for tr in range(inst.n):
            ev.extend_job(tab, tr, 0)
        pure = ev.total_cost(tab)
        best = [float("inf")]; stack = [tab]; nodes = 0; nid = 0
        while stack and nodes < max_nodes:
            table = stack.pop(); nodes += 1
            z0 = ev.total_cost(table); lb = ev.est_delay(table)
            if z0 + lb >= best[0]:
                continue
            cands = L.candidate_pairs(inst, table)
            if not cands:
                best[0] = min(best[0], z0); continue
            nid += 1
            base_sec = sec_metrics(inst, table)
            mean_cont = float(np.mean([contention(inst, table, s) for s in range(inst.S)]))
            sv0 = sched(inst, table)
            tconf = {t: train_conflicts(inst, table, t) for t in set(
                [i for _, i, _ in cands] + [j for _, _, j in cands])}
            best_pick = None; best_d = -1e18
            cache = []
            for (k, i, j) in cands:
                ta = L.push_after(ev, table, k, i, j)
                tb = L.push_after(ev, table, k, j, i)
                ca, cb = ev.total_cost(ta), ev.total_cost(tb)
                bL = ca + ev.est_delay(ta) - z0
                bR = cb + ev.est_delay(tb) - z0
                dS = MU * min(bL, bR) + (1 - MU) * max(bL, bR)
                better = ta if ca <= cb else tb
                dprimal = float(np.abs(sched(inst, better) - sv0).sum())
                cont_after = sum(contention(inst, better, s) for s in range(inst.S))
                cont_before = sum(contention(inst, table, s) for s in range(inst.S))
                dviol = float(cont_before - cont_after)
                cd = max(0, min(table[i][k][1], table[j][k][1]) - max(table[i][k][0], table[j][k][0]))
                Y = [bL, bR, dS, min(ca, cb) - z0, abs(bL - bR), dprimal, dviol]
                Rsec = sec_metrics(inst, better) - base_sec
                X = [
                    (z0 - pure), len(cands), len(cands), mean_cont, (z0 - pure) / max(pure, 1),   # node
                    1.0 if inst.direction[i] != inst.direction[j] else 0.0, cd, k / inst.S,
                    1.0 if k in (0, inst.S - 1) else 0.0, abs(table[i][k][0] - table[j][k][0]),
                    inst.run(i), inst.run(j),                                                     # action
                    contention(inst, table, k), base_sec[k, 0], base_sec[k, 2],
                    contention(inst, table, max(0, k - 1)), contention(inst, table, min(inst.S - 1, k + 1)),  # dual
                    table[i][T.section_no(inst.direction[i], inst.S - 1, inst.S)][1] - pure / inst.n,
                    table[j][T.section_no(inst.direction[j], inst.S - 1, inst.S)][1] - pure / inst.n,
                    sum(table[i][s][2] for s in range(inst.S)), sum(table[j][s][2] for s in range(inst.S)),
                    tconf[i], tconf[j],                                                           # traj
                ]
                cache.append((X, Y, Rsec, dS))
                if dS > best_d:
                    best_d = dS; best_pick = (k, i, j)
            for (X, Y, Rsec, dS) in cache:
                rows_X.append(X); rows_Y.append(Y); rows_R.append(Rsec)
                inst_ids.append(s); node_ids.append(nid); dstrong.append(dS)
            k, i, j = best_pick
            stack.append(L.push_after(ev, table, k, i, j))
            stack.append(L.push_after(ev, table, k, j, i))
    return (np.array(rows_X, float), np.array(rows_Y, float), np.array(rows_R, float),
            np.array(inst_ids), np.array(node_ids), np.array(dstrong, float))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=10)
    ap.add_argument("--seeds", type=int, nargs="*", default=list(range(1, 21)))
    args = ap.parse_args()
    X, Y, R, inst_ids, node_ids, dS = collect(args.size, args.seeds)
    path = os.path.join(OUT, f"tensor_dataset_n{args.size}.npz")
    np.savez(path, X=X, Y=Y, Rsec=R, inst_id=inst_ids, node_id=node_ids, dStrong=dS,
             feature_names=np.array(FEATS), metric_names=np.array(METRICS),
             secmetric_names=np.array(SECMETRICS),
             layer_index=np.array(list(LAYER_INDEX.items()), dtype=object))
    print(f"saved {path}")
    print(f"  samples={len(X)}  features={X.shape[1]}  metrics={Y.shape[1]}  "
          f"Rsec={R.shape}  instances={len(set(inst_ids.tolist()))}  nodes={len(set(node_ids.tolist()))}")
