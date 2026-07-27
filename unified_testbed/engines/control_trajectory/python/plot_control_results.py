#!/usr/bin/env python3
from __future__ import annotations
import csv
from pathlib import Path
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
with (ROOT/'results'/'control_trajectory_benchmark_summary.csv').open() as f:
    rows=list(csv.DictReader(f))
for r in rows:
    for k in ['n_agents','initial_conflicts','final_conflicts','reduced_states','full_states_sampled','accepted_actions','control_actions']:
        r[k]=int(float(r[k]))

agents=[r['n_agents'] for r in rows]
full=[r['full_states_sampled'] for r in rows]
red=[r['reduced_states'] for r in rows]
fig,ax=plt.subplots(figsize=(7.5,4.5))
ax.plot(agents,full,marker='o',label='Full joint DP')
ax.plot(agents,red,marker='o',label='Quadratic corridor DP')
ax.set_xlabel('Number of trajectory agents')
ax.set_ylabel('Explored joint states')
ax.set_title('Control-localized joint-state scaling')
ax.legend();fig.tight_layout();fig.savefig(ROOT/'figures'/'control_joint_state_scaling.png',dpi=180);plt.close(fig)

before=[r['initial_conflicts'] for r in rows];after=[r['final_conflicts'] for r in rows]
fig,ax=plt.subplots(figsize=(7.5,4.5))
x=range(len(rows));w=.38
ax.bar([i-w/2 for i in x],before,width=w,label='Before coordination')
ax.bar([i+w/2 for i in x],after,width=w,label='After coordination')
ax.set_xticks(list(x),[str(a) for a in agents]);ax.set_xlabel('Number of trajectory agents');ax.set_ylabel('Hard conflicts')
ax.set_title('Coordination-agent conflict elimination');ax.legend();fig.tight_layout();fig.savefig(ROOT/'figures'/'control_conflict_elimination.png',dpi=180);plt.close(fig)

# Time-space diagram for the 8-agent case.
case=ROOT/'results'/'control_corridor_8'/'schedules.csv'
with case.open() as f: sched=list(csv.DictReader(f))
fig,ax=plt.subplots(figsize=(8,5))
for aid in sorted({int(r['agent_id']) for r in sched}):
    q=[r for r in sched if int(r['agent_id'])==aid]
    ax.plot([int(r['time']) for r in q],[int(r['x']) for r in q],marker='.',label=f'Agent {aid}')
ax.set_xlabel('Time');ax.set_ylabel('Corridor position x');ax.set_title('Accepted trajectory-agent schedules after coordination')
ax.legend(ncol=2,fontsize=8);fig.tight_layout();fig.savefig(ROOT/'figures'/'control_timetable_8_agents.png',dpi=180);plt.close(fig)
print('Figures written to',ROOT/'figures')
