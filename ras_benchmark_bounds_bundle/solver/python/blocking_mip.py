"""RAS 2026 blocking-plan MIP (Prof. Zhou, Oct 8): block design, blocking sequences and block routes decided together,
with every constraint of the problem statement and the official validator v2.0 (fast_validator_v2_0.py).

Sets: yards Y; commodities k (origin o_k, destination d_k, volume v_k, type tau_k); physical links l (length, capacity).
Candidate blocks b = (i, j, tau): yard i to yard j carrying one commodity type (C8); intermodal and automobile only
direct o_k -> d_k (rule 9). Candidate routes p of block b: every simple physical path from i to j no longer than
1.3 x the shortest one (C6), enumerated in full, so no route is left out.

Variables (common part)
    y_b      1 if block b is built
    z_bp     1 if block b runs on route p              sum_p z_bp = y_b
    w_bp     cars of block b on route p (>= 0)        w_bp <= V_tau z_bp,  sum_p w_bp = q_b (block volume)
Constraints
    C2  sum of classification blocks (manifest, coal, grain) built at yard i <= tracks_i
    C3  cars classified at yard i (inbound to i, i not the origin of the car) <= handling capacity_i
    C4  q_b >= minvol(shortest distance i -> j) y_b
    C5  sum over blocks and routes through link l of w_bp <= capacity_l
    C6  by the route enumeration;  C7 one sequence per commodity (binary);  C1b no yard visited twice
Cost  fixed * sum y_b  +  sum_bp length_p w_bp  (car-miles)  +  sum_i handling cost_i * cars classified at i
      (+ interchange cost * q_b for a block between two Class-I railroads; zero in single-railroad instances)

Two formulations of the commodity part:
  arc     x_kb = 1 if commodity k uses block b: flow conservation from o_k to d_k on the block graph, at most one
          block into each yard, MTZ order u_ki to exclude subtours;  q_b = sum_k v_k x_kb,  x_kb <= y_b
  chain   lambda_kc = 1 if commodity k uses blocking chain c (a simple yard sequence o_k -> ... -> d_k, all
          enumerated):  sum_c lambda_kc = 1;  q_b = sum_k v_k sum_{c contains b} lambda_kc;  sum_{c contains b}
          lambda_kc <= y_b
Both describe the same set of blocking plans, so they have the same optimum; the chain LP excludes the fractional
cycles the arc LP allows, so its bound can only be higher.

    inst = load(path)                        # a solution_result-style JSON (inputs) or a folder of CSV files
    solve(inst, form="arc"|"chain", solver="highs"|"cplex", seconds) -> dict with "plan" (validator JSON outputs)
"""
from __future__ import annotations

import heapq
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

CLASSIFICATION = {"manifest", "coal", "grain"}
BLOCK_TYPE = {"merchandise": "manifest"}           # block label of a commodity type (C2 counts manifest/coal/grain)


def direct_only(ctype: str) -> bool:
    s = ctype.lower()
    return "intermodal" in s or "auto" in s


def load(path) -> dict:
    p = Path(path)
    if p.is_dir():
        import csv
        rd = lambda n: list(csv.DictReader(open(p / n)))                     # noqa: E731
        settings = {r["parameter"]: float(r["value"]) for r in rd("setting.csv")}
        nodes, links, demands = rd("node.csv"), rd("link.csv"), rd("demand.csv")
    else:
        data = json.load(open(p))["inputs"]
        settings, nodes, links, demands = data["settings"], data["nodes"], data["links"], data["demands"]
    return {"settings": settings, "nodes": nodes, "links": links, "demands": demands}


def minvol(dist: float, s: dict) -> float:
    if dist < 100:
        return float(s["min_block_vol_short(<100mi)"])
    if dist <= 500:
        return float(s["min_block_vol_med(100-500mi)"])
    return float(s["min_block_vol_long(>500mi)"])


def prepare(inst: dict) -> dict:
    s = inst["settings"]
    yards = {int(n["node_id"]): n for n in inst["nodes"] if n["node_type"] == "yard"}
    adj = defaultdict(list)                                                  # undirected, as the validator
    for l in inst["links"]:
        u, v, d = int(l["from_node_id"]), int(l["to_node_id"]), float(l["length"])
        adj[u].append((v, d, int(l["link_id"])))
        adj[v].append((u, d, int(l["link_id"])))

    def dijkstra(src):
        dist, pq = {src: 0.0}, [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            for v, w, _ in adj[u]:
                if d + w < dist.get(v, float("inf")):
                    dist[v] = d + w
                    heapq.heappush(pq, (d + w, v))
        return dist
    sp = {i: dijkstra(i) for i in yards}
    ratio = float(s["max_circuitous_ratio"])

    def routes(i, j):
        """every simple path i -> j with length <= ratio * sp(i, j) (+1e-4 as the validator)."""
        bound = ratio * sp[i][j] + 1e-4
        to_j = sp[j]                                                         # remaining distance lower bound
        out, stack = [], [(i, [i], [], 0.0)]
        while stack:
            u, nodes, links, d = stack.pop()
            if u == j:
                out.append((nodes, links, d))
                continue
            for v, w, lid in adj[u]:
                if v not in nodes and d + w + to_j.get(v, float("inf")) <= bound:
                    stack.append((v, nodes + [v], links + [lid], d + w))
        return out

    comms = []
    for d in inst["demands"]:
        comms.append({"id": int(d["commodity_id"]), "o": int(d["origin_yard_id"]), "d": int(d["dest_yard_id"]),
                      "v": float(d["volume"]) * float(s.get("demand_multiplier", 1.0)),
                      "type": str(d["commodity_type"])})
    types = sorted({c["type"] for c in comms})
    blocks = []                                                              # (i, j, type)
    for t in types:
        if direct_only(t):
            pairs = sorted({(c["o"], c["d"]) for c in comms if c["type"] == t})
        else:
            pairs = [(i, j) for i in yards for j in yards if i != j]
        for i, j in pairs:
            if j in sp[i]:
                blocks.append((i, j, t))
    R = {}
    for (i, j) in {(b[0], b[1]) for b in blocks}:
        R[i, j] = routes(i, j)
    rr = lambda n: str(n.get("railroad_id") or n.get("carrier_id") or "").upper().replace("CSXT", "CSX")  # noqa
    return {"settings": s, "yards": yards, "sp": sp, "routes": R, "comms": comms, "types": types, "blocks": blocks,
            "links": {int(l["link_id"]): l for l in inst["links"]}, "rr": {y: rr(n) for y, n in yards.items()}}


def chains(P: dict, c: dict, max_blocks: int | None = None):
    """every simple yard sequence o -> ... -> d over candidate blocks of the commodity's type."""
    if direct_only(c["type"]):
        return [[c["o"], c["d"]]]
    ys = list(P["yards"])
    out, stack = [], [[c["o"]]]
    while stack:
        seq = stack.pop()
        u = seq[-1]
        if u == c["d"]:
            out.append(seq)
            continue
        if max_blocks is not None and len(seq) - 1 >= max_blocks:
            continue
        for v in ys:
            if v not in seq and v != u and (u, v) in P["routes"] and P["routes"][u, v]:
                stack.append(seq + [v])
    return out


class Builder:
    def __init__(self):
        self.lo, self.hi, self.cost, self.integer = [], [], [], []
        self.r_lo, self.r_hi, self.r_st, self.r_ix, self.r_v = [], [], [], [], []

    def var(self, lo, hi, cost=0.0, integer=True):
        self.lo.append(float(lo)); self.hi.append(float(hi)); self.cost.append(float(cost))
        self.integer.append(integer)
        return len(self.lo) - 1

    def row(self, terms, lo, hi):
        self.r_st.append(len(self.r_ix))
        for c, v in terms:
            self.r_ix.append(c); self.r_v.append(float(v))
        self.r_lo.append(float(lo)); self.r_hi.append(float(hi))

    def highs(self):
        import highspy
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        n = len(self.lo)
        h.addVars(n, np.array(self.lo), np.array(self.hi))
        kinds = [highspy.HighsVarType.kInteger if f else highspy.HighsVarType.kContinuous for f in self.integer]
        h.changeColsIntegrality(n, np.arange(n, dtype=np.int32), np.array(kinds))
        h.changeColsCost(n, np.arange(n, dtype=np.int32), np.array(self.cost))
        h.addRows(len(self.r_lo), np.array(self.r_lo), np.array(self.r_hi), len(self.r_ix),
                  np.array(self.r_st, dtype=np.int32), np.array(self.r_ix, dtype=np.int32), np.array(self.r_v))
        return h


def build(P: dict, form: str, max_blocks: int | None = None, unserved: bool = False) -> dict:
    """unserved: the competition's stress score (validator v2.0): a commodity may get fewer cars than its demand
    (or none) on its one sequence, at stress_penalty_M per unserved car-mile (shortest distance o -> d)."""
    s = P["settings"]
    inf = float("inf")
    B = Builder()
    fixed, ic = float(s["block_fixed_cost"]), float(s.get("interchange_cost", 0.0))
    coef = float(s["transport_cost_coefficient"])
    vol_of_type = defaultdict(float)
    for c in P["comms"]:
        vol_of_type[c["type"]] += c["v"]
    Y, Z, W, Q = {}, {}, {}, {}
    for b in P["blocks"]:
        i, j, t = b
        cap = vol_of_type[t]
        inter = ic if P["rr"][i] and P["rr"][j] and P["rr"][i] != P["rr"][j] else 0.0
        Y[b] = B.var(0, 1, fixed)
        Q[b] = B.var(0, cap, inter, integer=False)
        zs = []
        for p, (_, _, dist) in enumerate(P["routes"][i, j]):
            Z[b, p] = B.var(0, 1)
            W[b, p] = B.var(0, cap, coef * dist, integer=False)
            B.row([(W[b, p], 1), (Z[b, p], -cap)], -inf, 0)                 # w <= V z
            zs.append(Z[b, p])
        B.row([(z, 1) for z in zs] + [(Y[b], -1)], 0, 0)                    # one route iff built
        B.row([(W[b, p], 1) for p in range(len(zs))] + [(Q[b], -1)], 0, 0)  # route flows = block volume
        B.row([(Q[b], 1), (Y[b], -minvol(P["sp"][i][j], s))], 0, inf)        # C4
        B.row([(Q[b], 1), (Y[b], -cap)], -inf, 0)
    # C2 classification tracks
    for y, n in P["yards"].items():
        cls = [Y[b] for b in P["blocks"] if b[0] == y and BLOCK_TYPE.get(b[2], b[2]) in CLASSIFICATION]
        if cls:
            B.row([(c, 1) for c in cls], -inf, float(n["num_tracks"]))
    # C5 link capacity
    on_link = defaultdict(list)
    for (b, p), w in W.items():
        for lid in P["routes"][b[0], b[1]][p][1]:
            on_link[lid].append(w)
    for lid, ws in on_link.items():
        B.row([(w, 1) for w in ws], -inf, float(P["links"][lid]["capacity"]))
    # handling: H_i = cars classified at yard i
    Hv = {y: B.var(0, sum(c["v"] for c in P["comms"]), float(n["handling_cost"]), integer=False)
          for y, n in P["yards"].items()}
    vol_terms = defaultdict(list)                                           # b -> [(col, v)]
    hand_terms = defaultdict(list)                                          # yard -> [(col, v)]
    X, S, F, G = {}, {}, {}, {}
    const = 0.0
    pen = float(s.get("stress_penalty_M", 5.0))
    for k, c in enumerate(P["comms"]):
        # served: S_k = 1 if the commodity gets a blocking sequence, F_k cars on it; without `unserved` both fixed
        if unserved:
            S[k] = B.var(0, 1)
            sp_od = P["sp"][c["o"]][c["d"]]
            F[k] = B.var(0, c["v"], -pen * sp_od, integer=False)              # stress: M * unserved car-miles
            const += pen * sp_od * c["v"]
            B.row([(F[k], 1), (S[k], -c["v"])], -inf, 0)
    if form == "arc":
        for k, c in enumerate(P["comms"]):
            bs = [b for b in P["blocks"] if b[2] == c["type"]]
            if direct_only(c["type"]):
                bs = [b for b in bs if (b[0], b[1]) == (c["o"], c["d"])]
            out_, in_, gout, gin = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
            for b in bs:
                if b[1] == c["o"] or b[0] == c["d"]:
                    continue                                                # never back into o or out of d
                X[k, b] = x = B.var(0, 1)
                out_[b[0]].append(x); in_[b[1]].append(x)
                B.row([(x, 1), (Y[b], -1)], -inf, 0)
                if unserved:                                                # cars of k on b: g <= v x
                    G[k, b] = g = B.var(0, c["v"], integer=False)
                    B.row([(g, 1), (x, -c["v"])], -inf, 0)
                    gout[b[0]].append(g); gin[b[1]].append(g)
                    vol_terms[b].append((g, 1.0))
                    if b[0] != c["o"]:
                        hand_terms[b[0]].append((g, 1.0))
                else:
                    vol_terms[b].append((x, c["v"]))
                    if b[0] != c["o"]:
                        hand_terms[b[0]].append((x, c["v"]))
            for y in P["yards"]:
                rhs = 1 if y == c["o"] else (-1 if y == c["d"] else 0)
                terms = [(x, 1) for x in out_[y]] + [(x, -1) for x in in_[y]]
                if unserved:
                    B.row(terms + [(S[k], -rhs)], 0, 0)
                    B.row([(g, 1) for g in gout[y]] + [(g, -1) for g in gin[y]] + [(F[k], -rhs)], 0, 0)
                else:
                    B.row(terms, rhs, rhs)
                if in_[y]:
                    B.row([(x, 1) for x in in_[y]], -inf, 1)
            if not direct_only(c["type"]):                                  # MTZ: no subtour
                n = len(P["yards"])
                U = {y: B.var(0, n - 1, integer=False) for y in P["yards"]}
                B.row([(U[c["o"]], 1)], 0, 0)
                for b in bs:
                    if (k, b) in X:
                        # u_j >= u_i + 1 - n (1 - x)
                        B.row([(U[b[1]], 1), (U[b[0]], -1), (X[k, b], -n)], 1 - n, inf)
    elif form == "chain":
        for k, c in enumerate(P["comms"]):
            cs = chains(P, c, max_blocks)
            lams, gs = [], []
            uses = defaultdict(list)
            for seq in cs:
                lam = B.var(0, 1)
                lams.append(lam)
                X[k, tuple(seq)] = lam
                if unserved:
                    G[k, tuple(seq)] = g = B.var(0, c["v"], integer=False)
                    B.row([(g, 1), (lam, -c["v"])], -inf, 0)
                    gs.append(g)
                    term, coef_ = g, 1.0
                else:
                    term, coef_ = lam, c["v"]
                for a, b_ in zip(seq[:-1], seq[1:]):
                    blk = (a, b_, c["type"])
                    uses[blk].append(lam)
                    vol_terms[blk].append((term, coef_))
                for y in seq[1:-1]:
                    hand_terms[y].append((term, coef_))
            if unserved:
                B.row([(lam, 1) for lam in lams] + [(S[k], -1)], 0, 0)
                B.row([(g, 1) for g in gs] + [(F[k], -1)], 0, 0)
            else:
                B.row([(lam, 1) for lam in lams], 1, 1)
            for blk, ls in uses.items():
                B.row([(lam, 1) for lam in ls] + [(Y[blk], -1)], -inf, 0)
    else:
        raise ValueError(form)
    for b in P["blocks"]:
        B.row(vol_terms[b] + [(Q[b], -1)], 0, 0)
    for y, n in P["yards"].items():
        B.row(hand_terms[y] + [(Hv[y], -1)], 0, 0)
        B.row([(Hv[y], 1)], -inf, float(n["handling_capacity"]))           # C3
    return {"B": B, "Y": Y, "Z": Z, "W": W, "Q": Q, "X": X, "H": Hv, "S": S, "F": F, "form": form, "const": const}


def decode(P: dict, M: dict, x) -> dict:
    """The plan in the validator's output format."""
    built = [b for b in P["blocks"] if x[M["Y"][b]] > 0.5 and x[M["Q"][b]] > 1e-6]
    bid = {b: n + 1 for n, b in enumerate(built)}
    design, routes, seqs = [], [], []
    for b in built:
        p = next(p for p in range(len(P["routes"][b[0], b[1]])) if x[M["Z"][b, p]] > 0.5)
        nodes, links, _ = P["routes"][b[0], b[1]][p]
        design.append({"block_id": bid[b], "from_yard_id": b[0], "to_yard_id": b[1],
                       "block_type": BLOCK_TYPE.get(b[2], b[2]), "block_volume": round(x[M["Q"][b]])})
        routes.append({"block_id": bid[b], "from_yard_id": b[0], "to_yard_id": b[1],
                       "physical_path_nodes": " -> ".join(map(str, nodes)),
                       "physical_path_links": " -> ".join(map(str, links))})
    for k, c in enumerate(P["comms"]):
        vol = c["v"]
        if M["S"]:
            vol = float(x[M["F"][k]])
            if x[M["S"][k]] < 0.5 or vol <= 1e-6:
                continue                                                    # unserved: no row (validator 9b)
        if M["form"] == "chain":
            seq = next(list(key[1]) for key, col in M["X"].items() if key[0] == k and x[col] > 0.5)
        else:
            nxt = {b[0]: b[1] for (kk, b), col in M["X"].items() if kk == k and x[col] > 0.5}
            seq = [c["o"]]
            while seq[-1] != c["d"]:
                seq.append(nxt[seq[-1]])
        chain = [bid[(a, b_, c["type"])] for a, b_ in zip(seq[:-1], seq[1:])]
        seqs.append({"commodity_id": c["id"], "commodity_type": c["type"], "origin_yard_id": c["o"],
                     "dest_yard_id": c["d"], "volume": round(vol, 6), "blocking_sequence": " -> ".join(map(str, chain))})
    return {"1 Block Design": design, "2 Blocking Sequence": seqs, "3 Block Route": routes}


def lp_value(h, threads: int) -> float | None:
    import highspy
    lp = h.getLp()
    lp.integrality_ = []
    g = highspy.Highs()
    g.setOptionValue("output_flag", False)
    g.setOptionValue("threads", threads)                     # HiGHS fixes its thread pool at the first run
    g.passModel(lp)
    g.run()
    return g.getInfo().objective_function_value if g.modelStatusToString(g.getModelStatus()) == "Optimal" else None


def solve(inst: dict, form: str = "arc", solver: str = "highs", seconds: float = 600.0, threads: int = 2,
          max_blocks: int | None = None, unserved: bool = False) -> dict:
    import re
    from solver.python.mip_cumflow import run_cplex
    t0 = time.time()
    P = prepare(inst)
    M = build(P, form, max_blocks, unserved)
    h = M["B"].highs()
    out = {"form": form, "solver": solver, "vars": h.getNumCol(), "rows": h.getNumRow(),
           "binaries": int(sum(M["B"].integer)), "blocks": len(P["blocks"]),
           "routes": sum(len(r) for r in P["routes"].values()), "build_seconds": round(time.time() - t0, 2)}
    lpv = lp_value(h, threads)
    out["lp"] = None if lpv is None else lpv + M["const"]
    t0 = time.time()
    if solver == "cplex":
        status, objv, bnd, x = run_cplex(h, seconds, threads)
        m = re.findall(r"Nodes = (\d+)", getattr(run_cplex, "last_log", ""))
        out.update(status=status, seconds=round(time.time() - t0, 2), nodes=int(m[-1]) if m else None)
        if x is None:
            return out
        proven = "optimal" in status and "tolerance" not in status
    else:
        h.setOptionValue("time_limit", float(seconds))
        h.setOptionValue("threads", threads)
        h.setOptionValue("mip_rel_gap", 0.0)
        h.run()
        info = h.getInfo()
        status = h.modelStatusToString(h.getModelStatus())
        out.update(status=status, seconds=round(time.time() - t0, 2), nodes=int(info.mip_node_count))
        if info.primal_solution_status != 2:
            return out
        x = np.array(h.getSolution().col_value)
        objv, bnd, proven = info.objective_function_value, info.mip_dual_bound, status == "Optimal"
    out.update(value=float(objv) + M["const"], bound=float(bnd) + M["const"], proven=proven, plan=decode(P, M, x),
               served=sum(1 for k in M["S"] if x[M["S"][k]] > 0.5) if M["S"] else len(P["comms"]))
    return out
