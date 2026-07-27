"""
convert_native.py — native FastTrain instance  ->  unified testbed instance (nodes/arcs/agents/metadata).

Mapping (v0, documented in INSTANCE_SCHEMA.md):
  * each physical link with directional travel time tau is expanded into a chain of tau unit CELLS;
    every cell is one exclusive resource shared by both directions of a bidirectional link
    (block occupancy -> meet/pass conflicts appear exactly as unit-cell contention);
  * link capacity 1 -> exclusive resource per cell; capacity >= 2 -> resource_id = -1 (non-exclusive;
    v0 approximation, flagged in metadata);
  * train -> agent: earliest_departure = entry, preferred_arrival = intended, latest_arrival =
    intended + slack_pad, move_interval = 1 (per-train speed multipliers are baked into the chain
    length at CLASS-average speed; per-train deviations are a v0 approximation, see caveats);
  * objective weights mirror the deviation objective: wait_cost = 0 (free slack), tardiness_cost = 1.

Usage:  python convert_native.py <native_dir> <out_dir> [--horizon-pad 240]
"""
from __future__ import annotations
import os, sys, json, csv, math


def travel_time(length, v, mult):
    v = v * max(mult, 1e-6)
    if v <= 0 or length <= 0:
        return 1
    return max(1, int(length * 60.0 / v + 1.0))


def convert(native_dir, out_dir, horizon_pad=240):
    # ---- read native ----
    with open(os.path.join(native_dir, "input_node.csv")) as f:
        rows = [r for r in csv.reader(f)][1:]
        node_nums = [int(r[0]) for r in rows if r and r[0].strip()]
    with open(os.path.join(native_dir, "input_link.csv")) as f:
        rows = [r for r in csv.reader(f)][1:]
        links = [dict(a=int(r[0]), b=int(r[1]), length=float(r[2]), vFT=float(r[3]),
                      vTF=float(r[4]), cap=max(1, int(r[5])), ltype=int(r[6]),
                      bidir=int(r[7]) == 1) for r in rows if len(r) >= 8]
    trains = []
    with open(os.path.join(native_dir, "input_train_info.csv")) as f:
        rows = [r for r in csv.reader(f)][1:]
        for r in rows:
            if len(r) >= 13:
                trains.append(dict(id=r[0].strip(), o=int(r[1]), d=int(r[2]),
                                   smult=float(r[7]), entry=int(float(r[8])),
                                   intended=float(r[12])))
    if not trains:
        raise SystemExit("no trains in native instance")
    avg_mult = sum(t["smult"] for t in trains) / len(trains)

    # ---- build unified graph: station nodes + per-link unit-cell chains ----
    nid = {}
    nodes = []                      # (node_id, x, y, resource_id, kind)
    def add_node(key, res, kind):
        if key in nid:
            return nid[key]
        i = len(nodes); nid[key] = i
        nodes.append((i, i, 0, res, kind))
        return i
    for n in node_nums:
        add_node(("st", n), -1, "station")
    arcs = []                       # (arc_id, from, to, base_cost, resource_id)
    res_ct = 0
    approx_cap = 0
    for li, L in enumerate(links):
        tau = travel_time(L["length"], L["vFT"], avg_mult)   # class-average chain length
        # chain cells: st(a) -> c1 -> ... -> c_{tau-1} -> st(b); each hop = 1 unit cell resource
        cells = []
        for k in range(tau - 1):
            cells.append(add_node(("c", li, k), -1, "track"))
        path = [nid[("st", L["a"])]] + cells + [nid[("st", L["b"])]]
        for k in range(len(path) - 1):
            if L["cap"] == 1:
                res = res_ct; res_ct += 1
            else:
                res = -1; approx_cap += 1
            arcs.append((len(arcs), path[k], path[k + 1], 1.0, res))
            if L["bidir"]:
                arcs.append((len(arcs), path[k + 1], path[k], 1.0, res))  # same resource both ways
    # per-agent PRIVATE staging nodes (Flatland waiting / DONE_REMOVED semantics): shared origins or
    # terminals must not register as infrastructure conflicts (mirrors generate_instances.py).
    agents = []
    for ti, t in enumerate(trains):
        s = add_node(("ps", ti), -1, "private_start")
        g = add_node(("pg", ti), -1, "private_goal")
        arcs.append((len(arcs), s, nid[("st", t["o"])], 0.5, -1))
        arcs.append((len(arcs), nid[("st", t["d"])], g, 0.5, -1))
        agents.append((ti, s, g, t["entry"],
                       int(t["intended"]) + 2, int(t["intended"]) + horizon_pad + 3, 1,
                       "freight", 0.0, 1.0, 1.0, 0.0, 0.0))

    # ---- coordinates: BFS hop distance from node 0 as a 1-D embedding ----
    # The solvers' proximal-corridor and spacing terms use squared (x,y) distance, so coordinates must
    # reflect graph geometry (adjacent cells ~1 apart). Node-id-as-x breaks corridor screening entirely.
    from collections import deque, defaultdict as dd
    und = dd(list)
    for (_, a, b, _, _) in arcs:
        und[a].append(b); und[b].append(a)
    dist = {0: 0}; dq = deque([0])
    while dq:
        u = dq.popleft()
        for v in und[u]:
            if v not in dist:
                dist[v] = dist[u] + 1; dq.append(v)
    nodes = [(i, dist.get(i, 0), 0, res, kind) for (i, _, _, res, kind) in nodes]

    # ---- write unified ----
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "nodes.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["node_id", "x", "y", "resource_id", "kind"]); w.writerows(nodes)
    with open(os.path.join(out_dir, "arcs.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["arc_id", "from_node", "to_node", "base_cost", "resource_id"])
        w.writerows(arcs)
    with open(os.path.join(out_dir, "agents.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["agent_id", "start_node", "target_node", "earliest_departure", "preferred_arrival",
                    "latest_arrival", "move_interval", "train_class", "wait_cost", "tardiness_cost",
                    "prox_weight", "spacing_weight", "desired_spacing"])
        w.writerows(agents)
    meta = dict(name=os.path.basename(out_dir.rstrip("/\\")), family="converted native FastTrain",
                n_nodes=len(nodes), n_arcs=len(arcs), n_agents=len(agents),
                source=os.path.abspath(native_dir),
                caveats=["v0: class-average speed (per-train smult not honored per link)",
                         f"v0: {approx_cap} cap>=2 cells mapped to non-exclusive resource_id=-1",
                         "MOW windows not converted"])
    with open(os.path.join(out_dir, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(f"converted: {len(nodes)} nodes, {len(arcs)} arcs, {len(agents)} agents "
          f"({res_ct} exclusive cell resources, {approx_cap} non-exclusive)")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    pad = 240
    if "--horizon-pad" in sys.argv:
        pad = int(sys.argv[sys.argv.index("--horizon-pad") + 1])
    convert(sys.argv[1], sys.argv[2], pad)
