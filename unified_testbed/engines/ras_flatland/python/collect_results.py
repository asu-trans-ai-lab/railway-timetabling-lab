#!/usr/bin/env python3
from __future__ import annotations
import csv, json, shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
selections={
 'ras_corridor_12': ROOT/'results'/'final'/'ras_corridor_12',
 'ras_corridor_24': ROOT/'results'/'fullcheck_ras24',
 'ras_corridor_48': ROOT/'results'/'fullcheck_ras48',
 'flatland_like_20x20_40': ROOT/'results'/'final'/'flatland_like_20x20_40',
 'flatland_like_30x30_80': ROOT/'results'/'final'/'flatland_like_30x30_80',
}
outdir=ROOT/'results'/'benchmark';outdir.mkdir(parents=True,exist_ok=True)
rows=[]
for name,src in selections.items():
 dst=outdir/name
 if dst.exists(): shutil.rmtree(dst)
 shutil.copytree(src,dst)
 r=next(csv.DictReader(open(src/'summary.csv')))
 for key in ['n_nodes','n_arcs','n_agents','horizon','initial_hard_conflicts','final_hard_conflicts',
             'interaction_components','solved_joint_groups','failed_joint_groups','reduced_states',
             'reduced_transitions','screened_candidates','full_states_sampled','full_transitions_sampled']:
  r[key]=int(r[key])
 for key in ['reduced_wall_s','full_wall_s','budget','rho','coupling_radius']:
  r[key]=float(r[key])
 r['state_reduction_pct']=(100*(1-r['reduced_states']/r['full_states_sampled'])
                           if r['full_states_sampled'] else None)
 r['transition_reduction_pct']=(100*(1-r['reduced_transitions']/r['full_transitions_sampled'])
                                if r['full_transitions_sampled'] else None)
 r['speedup_vs_full']=(r['full_wall_s']/r['reduced_wall_s']
                       if r['full_wall_s'] and r['reduced_wall_s'] else None)
 rows.append(r)
fields=[]
for r in rows:
 for k in r:
  if k not in fields:fields.append(k)
with open(outdir/'benchmark_summary.csv','w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
(outdir/'benchmark_summary.json').write_text(json.dumps(rows,indent=2))
print(outdir/'benchmark_summary.csv')
