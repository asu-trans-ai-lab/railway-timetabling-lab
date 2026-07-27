#!/usr/bin/env python3
"""Generate disconnected railway meet/pass communities for the control/trajectory prototype."""
from __future__ import annotations
import csv, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def add_node(nodes, x, y, resource, kind):
    nid = len(nodes)
    nodes.append((nid, x, y, resource, kind))
    return nid


def add_arc(arcs, u, v, cost=1.0, resource=-1):
    arcs.append((len(arcs), u, v, cost, resource))


def build_case(name: str, pairs: int, release_stride: int = 2):
    out = ROOT / "instances" / name
    out.mkdir(parents=True, exist_ok=True)
    nodes, arcs, agents = [], [], []

    for g in range(pairs):
        y0 = g * 4
        # Private staging/removal nodes, never shared.
        sa = add_node(nodes, -2, y0, -1, "private_start")
        ga = add_node(nodes, 8, y0, -1, "private_goal")
        sb = add_node(nodes, 8, y0 + 1, -1, "private_start")
        gb = add_node(nodes, -2, y0 + 1, -1, "private_goal")

        # Physical single-track corridor with a two-track meet/pass pocket.
        L = add_node(nodes, 0, y0, 1000 + g * 20 + 0, "terminal")
        P1 = add_node(nodes, 1, y0, 1000 + g * 20 + 1, "track")
        JL = add_node(nodes, 2, y0, 1000 + g * 20 + 2, "junction")
        M = add_node(nodes, 3, y0, 1000 + g * 20 + 3, "main")
        S = add_node(nodes, 3, y0 + 1, 1000 + g * 20 + 4, "siding")
        JR = add_node(nodes, 4, y0, 1000 + g * 20 + 5, "junction")
        P5 = add_node(nodes, 5, y0, 1000 + g * 20 + 6, "track")
        R = add_node(nodes, 6, y0, 1000 + g * 20 + 7, "terminal")

        # Staging arcs.
        add_arc(arcs, sa, L, 0.4)
        add_arc(arcs, R, ga, 0.4)
        add_arc(arcs, sb, R, 0.4)
        add_arc(arcs, L, gb, 0.4)

        # Main route is inserted before siding route so independent plans conflict.
        undirected = [(L, P1), (P1, JL), (JL, M), (M, JR), (JR, P5), (P5, R),
                      (JL, S), (S, JR)]
        for u, v in undirected:
            add_arc(arcs, u, v, 1.0)
            add_arc(arcs, v, u, 1.0)

        release = g % release_stride
        horizon = 18 + release
        a = len(agents)
        agents.append((a, sa, ga, release, 10 + release, horizon, 1,
                       "express", 0.20, 1.0, 1.0, 0.20, 2.0))
        b = len(agents)
        agents.append((b, sb, gb, release, 10 + release, horizon, 1,
                       "regional", 0.25, 1.2, 1.0, 0.20, 2.0))

    with (out / "nodes.csv").open("w", newline="") as f:
        w = csv.writer(f); w.writerow(["node_id","x","y","resource_id","kind"]); w.writerows(nodes)
    with (out / "arcs.csv").open("w", newline="") as f:
        w = csv.writer(f); w.writerow(["arc_id","from_node","to_node","cost","resource_id"]); w.writerows(arcs)
    with (out / "agents.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["agent_id","start_node","goal_node","release","preferred_arrival","deadline",
                    "movement_interval","train_class","wait_cost","tardy_cost","prox_weight",
                    "spacing_weight","desired_spacing"])
        w.writerows(agents)
    (out / "metadata.json").write_text(json.dumps({
        "name": name,
        "description": "Disconnected meet/pass communities for coordination-agent and trajectory-agent testing",
        "agents": len(agents),
        "expected_components": pairs,
        "off_network_staging": True,
    }, indent=2))


if __name__ == "__main__":
    build_case("control_corridor_8", pairs=4)
    build_case("control_corridor_16", pairs=8)
    build_case("control_corridor_32", pairs=16)
    build_case("control_corridor_64", pairs=32)
    print("Generated control_corridor_8, 16, 32, and 64")
