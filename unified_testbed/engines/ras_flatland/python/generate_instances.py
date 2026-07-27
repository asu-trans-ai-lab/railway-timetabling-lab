#!/usr/bin/env python3
"""Generate RAS-style network timetabling and Flatland-like benchmark instances.

The output is intentionally simple CSV so the C++ solver and Python tools share
bit-identical data.  Each physical rail cell is a node; moves consume one base
simulation tick, while train-specific ``move_interval`` models slower trains.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


@dataclass
class Builder:
    name: str
    nodes: List[dict] = field(default_factory=list)
    arcs: List[dict] = field(default_factory=list)
    agents: List[dict] = field(default_factory=list)
    _coord_to_id: Dict[Tuple[int, int], int] = field(default_factory=dict)

    def node(self, x: int, y: int, resource_id: int = -1, kind: str = "track") -> int:
        key = (x, y)
        if key in self._coord_to_id:
            nid = self._coord_to_id[key]
            if resource_id >= 0 and self.nodes[nid]["resource_id"] < 0:
                self.nodes[nid]["resource_id"] = resource_id
            return nid
        nid = len(self.nodes)
        self._coord_to_id[key] = nid
        self.nodes.append(
            dict(node_id=nid, x=x, y=y, resource_id=resource_id, kind=kind)
        )
        return nid

    def private_node(self, x: int, y: int, kind: str) -> int:
        """Create a node even when another node has the same display coordinates."""
        nid = len(self.nodes)
        self.nodes.append(dict(node_id=nid, x=x, y=y, resource_id=-1, kind=kind))
        return nid

    def edge(self, u: int, v: int, cost: float = 1.0, resource_id: int = -1,
             bidirectional: bool = True) -> None:
        aid = len(self.arcs)
        self.arcs.append(dict(arc_id=aid, from_node=u, to_node=v,
                              base_cost=cost, resource_id=resource_id))
        if bidirectional:
            aid = len(self.arcs)
            self.arcs.append(dict(arc_id=aid, from_node=v, to_node=u,
                                  base_cost=cost, resource_id=resource_id))

    def agent(self, start: int, goal: int, release: int, preferred: int, deadline: int,
              move_interval: int, train_class: str, wait_cost: float = 0.25,
              tardiness_cost: float = 1.0, prox_weight: float = 1.0,
              spacing_weight: float = 0.15, desired_spacing: float = 2.0) -> None:
        k = len(self.agents)
        self.agents.append(dict(
            agent_id=k, start_node=start, target_node=goal,
            earliest_departure=release, preferred_arrival=preferred,
            latest_arrival=deadline, move_interval=move_interval,
            train_class=train_class, wait_cost=wait_cost,
            tardiness_cost=tardiness_cost, prox_weight=prox_weight,
            spacing_weight=spacing_weight, desired_spacing=desired_spacing,
        ))

    def write(self, root: Path, family: str, notes: str) -> Path:
        out = root / self.name
        out.mkdir(parents=True, exist_ok=True)
        for fn, rows in [("nodes.csv", self.nodes), ("arcs.csv", self.arcs),
                         ("agents.csv", self.agents)]:
            with (out / fn).open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader(); w.writerows(rows)
        meta = dict(name=self.name, family=family, n_nodes=len(self.nodes),
                    n_arcs=len(self.arcs), n_agents=len(self.agents), notes=notes)
        (out / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return out


def add_corridor_module(b: Builder, x0: int, y0: int, length: int, module_id: int,
                        with_siding: bool = True) -> Tuple[int, int]:
    """Single-track line with one bypass siding around the midpoint."""
    main = []
    for i in range(length + 1):
        # Group cells into coarse block resources, but leave station/siding endpoints free.
        main.append(b.node(x0 + i, y0, -1, "station" if i in (0, length) else "track"))
    for i in range(length):
        b.edge(main[i], main[i + 1], 1.0, module_id * 1000 + i)
    if with_siding:
        m = length // 2
        s1 = b.node(x0 + m - 1, y0 + 1, -1, "siding")
        s2 = b.node(x0 + m, y0 + 1, -1, "siding")
        s3 = b.node(x0 + m + 1, y0 + 1, -1, "siding")
        b.edge(main[m - 2], s1, 1.05, module_id * 1000 + 100)
        b.edge(s1, s2, 1.0, module_id * 1000 + 101)
        b.edge(s2, s3, 1.0, module_id * 1000 + 102)
        b.edge(s3, main[m + 2], 1.05, module_id * 1000 + 103)
    return main[0], main[-1]


def add_staged_agent(b: Builder, entry: int, exit_node: int, release: int, preferred: int,
                     deadline: int, move_interval: int, train_class: str,
                     wait_cost: float, tardiness_cost: float, prox_weight: float,
                     spacing_weight: float, desired_spacing: float = 2.0) -> None:
    """Give each train private pre-departure and post-arrival staging nodes.

    This mirrors Flatland's waiting/off-grid and DONE_REMOVED semantics and avoids
    counting shared origins or terminals as infrastructure conflicts.
    """
    k = len(b.agents)
    en = b.nodes[entry]; ex = b.nodes[exit_node]
    # Private nodes may share display coordinates with the entry/exit but retain
    # distinct node identities, mirroring off-grid waiting and DONE_REMOVED states.
    start = b.private_node(en["x"], en["y"], "private_start")
    goal = b.private_node(ex["x"], ex["y"], "private_goal")
    b.edge(start, entry, 0.5, -1, bidirectional=False)
    b.edge(exit_node, goal, 0.5, -1, bidirectional=False)
    b.agent(start, goal, release, preferred + 2, deadline + 3, move_interval,
            train_class, wait_cost, tardiness_cost, prox_weight, spacing_weight,
            desired_spacing)


def make_ras_corridors(name: str, modules: int, trains_per_module: int,
                       length: int, seed: int) -> Builder:
    rng = random.Random(seed)
    b = Builder(name)
    for g in range(modules):
        left, right = add_corridor_module(b, 0, g * 4, length, g, with_siding=True)
        base = (g % 4) * 2
        # Opposing trains are deliberately released into the same corridor window.
        add_staged_agent(b, left, right, base, base + length + 2, base + length + 12,
                         1, "passenger", 0.22, 1.2, 1.0, 0.25)
        add_staged_agent(b, right, left, base + 1, base + 2 * length + 4, base + 2 * length + 16,
                         1 if g % 3 else 2, "freight" if g % 3 == 0 else "passenger",
                         0.18, 0.9, 1.0, 0.25)
        if trains_per_module >= 3:
            # A later same-direction train creates a local three-train component.
            add_staged_agent(b, left, right, base + 3, base + length + 7, base + length + 18,
                             1, "regional", 0.24, 1.0, 0.9, 0.20)
        if trains_per_module >= 4:
            add_staged_agent(b, right, left, base + 5, base + length + 9, base + length + 22,
                             2, "freight", 0.16, 0.8, 0.8, 0.20)
    return b


def make_flatland_like(name: str, width: int, height: int, agents: int, seed: int) -> Builder:
    """Flatland-like grid benchmark made of localized switch communities.

    Each module is a plus-shaped rail junction with horizontal and vertical bypass
    sidings. Four trains use the module in two separated time windows, producing
    local pair conflicts rather than one artificial network-wide clique. This is
    the intended test of conflict-localized hyper-agent lifting.
    """
    b = Builder(name)
    n_modules = max(1, agents // 4)
    cols_mod = max(1, int(math.ceil(math.sqrt(n_modules))))
    for g in range(n_modules):
        ox = (g % cols_mod) * 12
        oy = (g // cols_mod) * 12
        # Horizontal and vertical main tracks share the center switch.
        horiz = [b.node(ox + i, oy + 4, -1, "switch" if i == 4 else "track") for i in range(9)]
        vert = [b.node(ox + 4, oy + i, -1, "switch" if i == 4 else "track") for i in range(9)]
        for i in range(8):
            b.edge(horiz[i], horiz[i + 1], 1.0, 20_000 + g * 100 + i)
            b.edge(vert[i], vert[i + 1], 1.0, 30_000 + g * 100 + i)
        # Horizontal bypass siding around the center.
        hs = [b.node(ox + i, oy + 5, -1, "siding") for i in range(2, 7)]
        b.edge(horiz[1], hs[0], 1.0, 40_000 + g * 100)
        for i in range(4): b.edge(hs[i], hs[i + 1], 1.0, 40_001 + g * 100 + i)
        b.edge(hs[-1], horiz[7], 1.0, 40_010 + g * 100)
        # Vertical bypass siding.
        vs = [b.node(ox + 5, oy + i, -1, "siding") for i in range(2, 7)]
        b.edge(vert[1], vs[0], 1.0, 50_000 + g * 100)
        for i in range(4): b.edge(vs[i], vs[i + 1], 1.0, 50_001 + g * 100 + i)
        b.edge(vs[-1], vert[7], 1.0, 50_010 + g * 100)

        base = (g % 5) * 2
        add_staged_agent(b, horiz[0], horiz[-1], base, base + 12, base + 28,
                         1, "express", 0.20, 1.2, 0.8, 0.18, 2.0)
        add_staged_agent(b, horiz[-1], horiz[0], base + 1, base + 15, base + 34,
                         2 if g % 3 == 0 else 1, "freight" if g % 3 == 0 else "regional",
                         0.18, 1.0, 0.8, 0.18, 2.0)
        base2 = 24 + (g % 5) * 2
        add_staged_agent(b, vert[0], vert[-1], base2, base2 + 12, base2 + 28,
                         1, "regional", 0.20, 1.0, 0.8, 0.18, 2.0)
        add_staged_agent(b, vert[-1], vert[0], base2 + 1, base2 + 15, base2 + 34,
                         2 if g % 4 == 0 else 1, "slow_freight" if g % 4 == 0 else "regional",
                         0.18, 0.9, 0.8, 0.18, 2.0)
    return b

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "instances")
    args = ap.parse_args(); args.out.mkdir(parents=True, exist_ok=True)
    specs = [
        (make_ras_corridors("ras_corridor_12", 6, 2, 12, 11), "RAS-style corridor",
         "Six independent single-track meet/pass modules; 12 trains."),
        (make_ras_corridors("ras_corridor_24", 8, 3, 14, 17), "RAS-style corridor",
         "Eight local three-train timetable components; 24 trains."),
        (make_ras_corridors("ras_corridor_48", 16, 3, 16, 23), "RAS-style corridor",
         "Sixteen local three-train timetable components; 48 trains."),
        (make_flatland_like("flatland_like_20x20_40", 20, 20, 40, 31), "Flatland-like grid",
         "Sparse 20x20 rail grid with switches, exclusive cells, and mixed speeds."),
        (make_flatland_like("flatland_like_30x30_80", 30, 30, 80, 47), "Flatland-like grid",
         "Sparse 30x30 rail grid with 80 mixed-speed trains."),
    ]
    manifest = []
    for b, family, notes in specs:
        path = b.write(args.out, family, notes)
        manifest.append(dict(name=b.name, family=family, n_nodes=len(b.nodes),
                             n_arcs=len(b.arcs), n_agents=len(b.agents), path=str(path)))
        print(f"wrote {b.name}: {len(b.nodes)} nodes, {len(b.arcs)} arcs, {len(b.agents)} agents")
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
