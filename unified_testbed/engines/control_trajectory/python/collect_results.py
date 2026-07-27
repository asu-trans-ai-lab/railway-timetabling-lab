#!/usr/bin/env python3
from __future__ import annotations
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rows=[]
for p in sorted((ROOT/'results').glob('control_corridor_*/summary.csv')):
    with p.open() as f:
        rows.append(next(csv.DictReader(f)))

num_fields = {
    'n_nodes':int,'n_arcs':int,'n_agents':int,'horizon':int,'initial_conflicts':int,'final_conflicts':int,
    'control_iterations':int,'control_actions':int,'accepted_actions':int,'rejected_actions':int,
    'reduced_states':int,'reduced_transitions':int,'screened_candidates':int,
    'full_states_sampled':int,'full_transitions_sampled':int,'full_comparisons':int,
    'exact_objective_matches':int
}
float_fields={'initial_objective','final_objective','resource_price_l1','reduced_wall_s','full_wall_s','max_objective_gap','budget','rho','price_step','precedence_weight'}
for r in rows:
    for k,typ in num_fields.items():r[k]=typ(r[k])
    for k in float_fields:r[k]=float(r[k])
    r['state_reduction_pct']=100*(1-r['reduced_states']/r['full_states_sampled']) if r['full_states_sampled'] else 0
    r['transition_reduction_pct']=100*(1-r['reduced_transitions']/r['full_transitions_sampled']) if r['full_transitions_sampled'] else 0
    r['speedup_vs_full']=r['full_wall_s']/r['reduced_wall_s'] if r['reduced_wall_s'] else 0

fields=list(rows[0].keys())
out=ROOT/'results'/'control_trajectory_benchmark_summary.csv'
with out.open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

agg={k:sum(r[k] for r in rows) for k in ['n_agents','initial_conflicts','final_conflicts','control_actions','accepted_actions','rejected_actions','reduced_states','reduced_transitions','full_states_sampled','full_transitions_sampled','full_comparisons','exact_objective_matches']}
agg_state=100*(1-agg['reduced_states']/agg['full_states_sampled'])
agg_trans=100*(1-agg['reduced_transitions']/agg['full_transitions_sampled'])
agg_rw=sum(r['reduced_wall_s'] for r in rows);agg_fw=sum(r['full_wall_s'] for r in rows)
report=ROOT/'results'/'CONTROL_TRAJECTORY_REPORT.md'
report.write_text(f'''# Coordination-Agent + Trajectory-Agent Benchmark Report

## Headline results

- Four cases with **{agg['n_agents']} agent-instance observations**.
- **{agg['initial_conflicts']} initial hard conflicts reduced to {agg['final_conflicts']}**.
- **{agg['accepted_actions']}/{agg['control_actions']} coordination actions accepted**; {agg['rejected_actions']} rejected.
- **{agg['exact_objective_matches']}/{agg['full_comparisons']}** corridor/full joint-DP objective matches.
- Joint states: **{agg['full_states_sampled']:,} -> {agg['reduced_states']:,}**, a **{agg_state:.2f}% reduction**.
- Joint transitions: **{agg['full_transitions_sampled']:,} -> {agg['reduced_transitions']:,}**, a **{agg_trans:.2f}% reduction**.
- Aggregate sampled joint-DP time: **{agg_fw:.6f}s -> {agg_rw:.6f}s**, a **{agg_fw/agg_rw:.2f}x speedup**.

## Case table

| Case | Agents | Conflicts before/after | Actions accepted | Exact matches | State reduction | Transition reduction | Speedup |
|---|---:|---:|---:|---:|---:|---:|---:|
'''+''.join(
    f"| {r['instance']} | {r['n_agents']} | {r['initial_conflicts']} / {r['final_conflicts']} | {r['accepted_actions']} / {r['control_actions']} | {r['exact_objective_matches']} / {r['full_comparisons']} | {r['state_reduction_pct']:.2f}% | {r['transition_reduction_pct']:.2f}% | {r['speedup_vs_full']:.2f}x |\n" for r in rows
)+'''\n## Interpretation\n\nThe coordination agent does not replace physical trajectory optimization. It identifies conflict-connected groups, raises resource-time prices, chooses a local precedence bias, defines the proximal corridor, and accepts or rejects the hyper-agent response. The trajectory agents retain responsibility for physically feasible individual or joint movement under immutable hard safety constraints.\n\nThe current cases consist of independent meet/pass communities, so total work grows approximately linearly with the number of communities. Dense conflict components remain the intended boundary case.\n''')
print(out)
print(report)
