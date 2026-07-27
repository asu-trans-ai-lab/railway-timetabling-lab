#!/usr/bin/env python3
"""Export a Flatland RailEnv into the CSV format used by the C++ benchmark.

Requires ``flatland-rl>=4``.  The adapter treats an oriented configuration
``((row, col), direction)`` as a network node because Flatland transition
availability depends on both cell and orientation.  This avoids allowing turns
that are not supported by the rail transition map.

Usage from a project that already constructs an environment::

    from flatland_adapter import export_rail_env
    env.reset()
    export_rail_env(env, Path("instances/my_flatland_case"))

The adapter is defensive across minor Flatland API changes and has no dependency
on the solver package itself.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

DIRECTION_DELTAS = [(-1, 0), (0, 1), (1, 0), (0, -1)]


def _successors(rail: Any, position: Tuple[int, int], direction: int):
    """Return ((new_position, new_direction), straight) successors."""
    config = (position, direction)
    if hasattr(rail, "get_successor_configurations"):
        for nxt in rail.get_successor_configurations(config):
            yield nxt, (nxt[1] == direction)
        return
    # Older API fallback.
    transitions = rail.get_transitions(*position, direction)
    for ndir, allowed in enumerate(transitions):
        if not allowed:
            continue
        dr, dc = DIRECTION_DELTAS[ndir]
        yield ((position[0] + dr, position[1] + dc), ndir), (ndir == direction)


def export_rail_env(env: Any, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    configs = []
    for r in range(env.height):
        for c in range(env.width):
            for d in range(4):
                try:
                    succ = list(_successors(env.rail, (r, c), d))
                except Exception:
                    succ = []
                if succ:
                    configs.append(((r, c), d))
    node_id = {cfg: i for i, cfg in enumerate(configs)}
    nodes = []
    for cfg, nid in node_id.items():
        (r, c), d = cfg
        nodes.append(dict(node_id=nid, x=c, y=r,
                          resource_id=r * env.width + c,
                          kind=f"flatland_dir_{d}"))
    arcs = []
    for cfg, u in node_id.items():
        for nxt, straight in _successors(env.rail, *cfg):
            if nxt not in node_id:
                continue
            arcs.append(dict(arc_id=len(arcs), from_node=u, to_node=node_id[nxt],
                             base_cost=1.0 if straight else 1.02,
                             resource_id=100000 + min(u, node_id[nxt])))
    agents = []
    for handle, a in enumerate(env.agents):
        start_cfg = (tuple(a.initial_position), int(a.initial_direction))
        target_candidates = [cfg for cfg in configs if cfg[0] == tuple(a.target)]
        if start_cfg not in node_id or not target_candidates:
            continue
        # A private post-arrival node can be created later by the generator if desired.
        target = node_id[target_candidates[0]]
        speed = 1.0
        sc = getattr(a, "speed_counter", None)
        if sc is not None:
            speed = float(getattr(sc, "max_speed", getattr(sc, "speed", 1.0)))
        interval = max(1, round(1.0 / max(speed, 1e-6)))
        agents.append(dict(
            agent_id=len(agents), start_node=node_id[start_cfg], target_node=target,
            earliest_departure=int(getattr(a, "earliest_departure", 0)),
            preferred_arrival=int(getattr(a, "latest_arrival", env._max_episode_steps)) - 5,
            latest_arrival=int(getattr(a, "latest_arrival", env._max_episode_steps)),
            move_interval=interval, train_class=f"flatland_speed_{speed:g}",
            wait_cost=0.2, tardiness_cost=1.0, prox_weight=0.8,
            spacing_weight=0.15, desired_spacing=2.0,
        ))
    for fn, rows in [("nodes.csv", nodes), ("arcs.csv", arcs), ("agents.csv", agents)]:
        with (out_dir / fn).open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader(); writer.writerows(rows)
    meta = dict(family="Flatland RailEnv export", width=env.width, height=env.height,
                n_nodes=len(nodes), n_arcs=len(arcs), n_agents=len(agents),
                note="Oriented Flatland configurations are exported as nodes.")
    (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit("Import export_rail_env(env, out_dir) from a Flatland experiment.")
