from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Sequence, Tuple
import json
import math
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

from .model import (
    Agent, ServiceTask, Trajectory, build_line_network, build_cross_network,
    find_node_by_coord,
)
from .dp import single_agent_dp, joint_group_dp
from .algorithms import (
    lagrangian_relaxation, conflict_keys, individual_column_generation,
    group_column_generation,
)
from .master import solve_master


def _plot_timetable(paths: Sequence[Trajectory], network, path: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for traj in paths:
        for aid, states in traj.states_by_agent.items():
            xs, ys = [], []
            for st in states:
                if st.node >= 0:
                    xs.append(st.time)
                    ys.append(network.nodes[st.node].x + 0.08 * network.nodes[st.node].y)
            ax.plot(xs, ys, marker="o", label=f"agent {aid}")
    ax.set_xlabel("time")
    ax.set_ylabel("projected position")
    ax.set_title(title)
    ax.legend()
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def experiment_dual_oscillation(out: Path) -> dict:
    net = build_line_network(5)
    agents = [
        Agent(0, 0, 4, 0, 16, (ServiceTask(0, 1, 1),)),
        Agent(1, 4, 0, 0, 16, (ServiceTask(0, 3, 1),)),
    ]
    lr_paths, prices, records = lagrangian_relaxation(net, agents, iterations=18, base_step=2.8)
    ref_paths = []
    for a in agents:
        p = single_agent_dp(net, a)
        assert p is not None
        ref_paths.append(p)
    reference = Trajectory(
        (0, 1), {0: ref_paths[0].states_by_agent[0], 1: ref_paths[1].states_by_agent[1]},
        frozenset(ref_paths[0].footprint | ref_paths[1].footprint),
        sum(p.physical_cost for p in ref_paths), sum(p.priced_cost for p in ref_paths),
        (ref_paths[0].signature, ref_paths[1].signature),
    )
    joint = joint_group_dp(net, agents, reference=reference, rho=0.12)
    assert joint is not None
    df = pd.DataFrame([r.__dict__ for r in records])
    df.to_csv(out / "e2_lr_history.csv", index=False)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(df["iteration"], df["conflicts"], marker="o", label="hard conflicts")
    ax.plot(df["iteration"], df["total_price"], marker="s", label="total LR price")
    ax.set_xlabel("LR iteration")
    ax.set_title("E2: price adjustment and residual conflict")
    ax.legend(); ax.grid(True, alpha=0.25); fig.tight_layout()
    fig.savefig(out / "e2_lr_oscillation.png", dpi=180); plt.close(fig)
    _plot_timetable(ref_paths, net, out / "e2_independent_timetable.png", "Independent trajectories")
    _plot_timetable([joint], net, out / "e2_joint_timetable.png", "Joint-transition-visible timetable")
    return {
        "lr_final_conflicts": int(records[-1].conflicts),
        "lr_total_variation": float(df["price_tv_step"].sum()),
        "lr_priority_flips": int(sum(records[k].entry_order != records[k-1].entry_order for k in range(1, len(records)))),
        "joint_conflicts": len(conflict_keys([joint])),
        "joint_generated_states": joint.generated_states,
        "joint_scanned_transitions": joint.scanned_transitions,
        "joint_objective": joint.physical_cost,
    }


def _manual_fractional_columns() -> Tuple[Dict[Tuple[int, ...], List[Trajectory]], Dict[Tuple[int, ...], List[Trajectory]], dict]:
    # Three-agent odd-cycle incompatibility. Each agent selects early (cost 0) or late (cost 1).
    # Early pairs are pairwise incompatible. The individual-column LP may set every early variable to 0.5.
    resources = {
        ("pair", 1, 0): 1.0,  # conflicts agents 0 and 1
        ("pair", 2, 0): 1.0,  # conflicts agents 1 and 2
        ("pair", 3, 0): 1.0,  # conflicts agents 0 and 2
    }
    early_fp = {
        0: frozenset({("pair", 1, 0), ("pair", 3, 0)}),
        1: frozenset({("pair", 1, 0), ("pair", 2, 0)}),
        2: frozenset({("pair", 2, 0), ("pair", 3, 0)}),
    }
    individual: Dict[Tuple[int, ...], List[Trajectory]] = {}
    for i in range(3):
        early = Trajectory((i,), {i: []}, early_fp[i], 0.0, 0.0, (i, "early"))
        late = Trajectory((i,), {i: []}, frozenset(), 1.0, 1.0, (i, "late"))
        individual[(i,)] = [early, late]
    group_cols: List[Trajectory] = []
    # Feasible integer patterns have at most one early agent.
    for early_agent in [-1, 0, 1, 2]:
        cost = 3.0 if early_agent < 0 else 2.0
        sig = tuple("early" if i == early_agent else "late" for i in range(3))
        fp = early_fp[early_agent] if early_agent >= 0 else frozenset()
        group_cols.append(Trajectory((0, 1, 2), {0: [], 1: [], 2: []}, fp, cost, cost, sig))
    grouped = {(0, 1, 2): group_cols}
    return individual, grouped, resources


def experiment_fractional_master(out: Path) -> dict:
    individual, grouped, capacities = _manual_fractional_columns()
    lp_ind = solve_master(individual, capacities, integer=False)
    ip_ind = solve_master(individual, capacities, integer=True)
    lp_group = solve_master(grouped, capacities, integer=False)
    ip_group = solve_master(grouped, capacities, integer=True)
    rows = [
        {"model": "individual_LP", "objective": lp_ind.objective, "nodes": 0},
        {"model": "individual_integer", "objective": ip_ind.objective, "nodes": ip_ind.mip_node_count},
        {"model": "group_LP", "objective": lp_group.objective, "nodes": 0},
        {"model": "group_integer", "objective": ip_group.objective, "nodes": ip_group.mip_node_count},
    ]
    pd.DataFrame(rows).to_csv(out / "e3_fractional_master.csv", index=False)
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.bar([r["model"] for r in rows], [r["objective"] for r in rows])
    ax.set_ylabel("objective")
    ax.set_title("E3: individual-column convexification versus group supercolumns")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout(); fig.savefig(out / "e3_fractional_master.png", dpi=180); plt.close(fig)
    fractional_ratio = float(np.mean((lp_ind.weights > 1e-7) & (lp_ind.weights < 1 - 1e-7)))
    return {
        "individual_lp": lp_ind.objective,
        "individual_integer": ip_ind.objective,
        "individual_integrality_gap": ip_ind.objective - lp_ind.objective,
        "individual_fractional_variable_ratio": fractional_ratio,
        "group_lp": lp_group.objective,
        "group_integer": ip_group.objective,
        "group_integrality_gap": ip_group.objective - lp_group.objective,
        "individual_mip_nodes": ip_ind.mip_node_count,
        "group_mip_nodes": ip_group.mip_node_count,
    }


def _cross_agents(net) -> List[Agent]:
    left = find_node_by_coord(net, -2, 0); right = find_node_by_coord(net, 2, 0)
    bottom = find_node_by_coord(net, 0, -2); top = find_node_by_coord(net, 0, 2)
    near_left = find_node_by_coord(net, -1, 0); near_right = find_node_by_coord(net, 1, 0)
    near_bottom = find_node_by_coord(net, 0, -1); near_top = find_node_by_coord(net, 0, 1)
    return [
        Agent(0, left, right, 0, 22, (ServiceTask(0, near_left, 1),)),
        Agent(1, right, left, 0, 22, (ServiceTask(0, near_right, 1),)),
        Agent(2, bottom, top, 0, 22, (ServiceTask(0, near_bottom, 1),)),
        Agent(3, top, bottom, 0, 22, (ServiceTask(0, near_top, 1),)),
    ]


def experiment_group_size(out: Path) -> dict:
    net = build_cross_network(2)
    agents = _cross_agents(net)
    partitions = {
        1: [[agents[0]], [agents[1]], [agents[2]], [agents[3]]],
        2: [[agents[0], agents[1]], [agents[2], agents[3]]],
        3: [[agents[0], agents[1], agents[2]], [agents[3]]],
        4: [agents],
    }
    rows = []
    selected_for_plot = None
    for kmax, groups in partitions.items():
        cols = []
        feasible = True
        for group in groups:
            if len(group) == 1:
                p = single_agent_dp(net, group[0])
            else:
                p = joint_group_dp(net, group)
            if p is None:
                feasible = False; break
            cols.append(p)
        conflicts = len(conflict_keys(cols)) if feasible else -1
        rows.append({
            "Kmax": kmax,
            "feasible": feasible,
            "hard_conflicts": conflicts,
            "generated_states": sum(p.generated_states for p in cols) if feasible else math.nan,
            "scanned_transitions": sum(p.scanned_transitions for p in cols) if feasible else math.nan,
            "objective": sum(p.physical_cost for p in cols) if feasible else math.inf,
        })
        if kmax == 4 and feasible:
            selected_for_plot = cols
    df = pd.DataFrame(rows)
    df.to_csv(out / "e4_group_size.csv", index=False)
    fig, ax1 = plt.subplots(figsize=(7.5, 4.5))
    ax1.plot(df["Kmax"], df["hard_conflicts"], marker="o", label="hard conflicts")
    ax1.set_xlabel("maximum active group size")
    ax1.set_ylabel("hard conflicts")
    ax2 = ax1.twinx()
    ax2.plot(df["Kmax"], df["generated_states"], marker="s", label="generated states")
    ax2.set_ylabel("generated states")
    ax1.grid(True, alpha=0.25)
    lines = ax1.lines + ax2.lines
    ax1.legend(lines, [l.get_label() for l in lines], loc="upper left")
    fig.tight_layout(); fig.savefig(out / "e4_group_size.png", dpi=180); plt.close(fig)
    if selected_for_plot:
        _plot_timetable(selected_for_plot, net, out / "e4_full_joint_timetable.png", "Four-agent joint timetable")
    return {str(int(r.Kmax)): {k: (bool(v) if k == "feasible" else (float(v) if isinstance(v, (np.floating, float)) else int(v))) for k, v in r._asdict().items() if k != "Kmax"} for r in df.itertuples(index=False)}


def experiment_column_generation(out: Path) -> dict:
    net = build_line_network(5)
    agents = [
        Agent(0, 0, 4, 0, 17, (ServiceTask(0, 1, 1),)),
        Agent(1, 4, 0, 0, 17, (ServiceTask(0, 3, 1),)),
    ]
    pools1, master1, hist1 = individual_column_generation(net, agents)
    pools2, master2, hist2 = group_column_generation(net, [agents], use_quadratic=False)
    pools3, master3, hist3 = group_column_generation(net, [agents], use_quadratic=True)
    pd.DataFrame(hist1).to_csv(out / "m1_individual_cg_history.csv", index=False)
    pd.DataFrame(hist2).to_csv(out / "m2_group_cg_history.csv", index=False)
    pd.DataFrame(hist3).to_csv(out / "m3_quadratic_group_cg_history.csv", index=False)
    return {
        "M1": {"objective": master1.objective, "iterations": len(hist1), "columns": sum(len(v) for v in pools1.values())},
        "M2": {"objective": master2.objective, "iterations": len(hist2), "columns": sum(len(v) for v in pools2.values())},
        "M3": {"objective": master3.objective, "iterations": len(hist3), "columns": sum(len(v) for v in pools3.values()), "quadratic_candidates": sum(h.get("qp_distinct", 0) for h in hist3)},
    }


def run_all(output_dir: str | Path) -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "E2_dual_oscillation": experiment_dual_oscillation(out),
        "E3_fractional_master": experiment_fractional_master(out),
        "E4_group_size": experiment_group_size(out),
        "M1_M3_column_generation": experiment_column_generation(out),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary
