#!/usr/bin/env python3
from __future__ import annotations
import csv
from pathlib import Path
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
rows=list(csv.DictReader(open(ROOT/'results/benchmark/benchmark_summary.csv')))
for r in rows:
 for k in ['n_agents','reduced_states','full_states_sampled','initial_hard_conflicts','final_hard_conflicts']:
  r[k]=int(r[k])
 for k in ['reduced_wall_s','full_wall_s']:
  r[k]=float(r[k])
figdir=ROOT/'figures';figdir.mkdir(exist_ok=True)

# Scaling plot.
fig,ax=plt.subplots(figsize=(8,5))
for family,marker in [('ras','o'),('flatland','s')]:
 rr=[r for r in rows if r['instance'].startswith(family)]
 ax.plot([r['n_agents'] for r in rr],[r['reduced_states'] for r in rr],marker=marker,label=family)
ax.set_xlabel('Number of trains');ax.set_ylabel('Reduced joint states generated')
ax.set_title('Conflict-localized joint-state scaling');ax.grid(True,alpha=.25);ax.legend();fig.tight_layout()
fig.savefig(figdir/'scaling_states.png',dpi=220);plt.close(fig)

# Full vs reduced on cases with full runs.
rr=[r for r in rows if r['full_states_sampled']>0]
fig,ax=plt.subplots(figsize=(8,5));x=range(len(rr));w=.36
ax.bar([i-w/2 for i in x],[r['full_states_sampled'] for r in rr],w,label='Full joint DP')
ax.bar([i+w/2 for i in x],[r['reduced_states'] for r in rr],w,label='Quadratic corridor')
ax.set_xticks(list(x),[r['instance'].replace('_','\n') for r in rr],fontsize=8)
ax.set_ylabel('Generated states');ax.set_title('Full versus corridor joint-state search')
ax.legend();fig.tight_layout();fig.savefig(figdir/'full_vs_reduced_states.png',dpi=220);plt.close(fig)

# Conflict elimination.
fig,ax=plt.subplots(figsize=(8,5));x=range(len(rows));w=.36
ax.bar([i-w/2 for i in x],[r['initial_hard_conflicts'] for r in rows],w,label='Independent schedules')
ax.bar([i+w/2 for i in x],[r['final_hard_conflicts'] for r in rows],w,label='Joint schedules')
ax.set_xticks(list(x),[r['instance'].replace('_','\n') for r in rows],fontsize=8)
ax.set_ylabel('Hard conflicts');ax.set_title('Hard-conflict elimination')
ax.legend();fig.tight_layout();fig.savefig(figdir/'conflict_elimination.png',dpi=220);plt.close(fig)

# Sample timetable trajectories from the 12-train corridor.
sched=list(csv.DictReader(open(ROOT/'results/benchmark/ras_corridor_12/schedules.csv')))
fig,ax=plt.subplots(figsize=(9,5))
for aid in range(4):
 s=[r for r in sched if int(r['agent_id'])==aid]
 ax.plot([int(r['time']) for r in s],[int(r['x']) for r in s],label=f'Train {aid}')
ax.set_xlabel('Time step');ax.set_ylabel('Network x-position')
ax.set_title('Sample coordinated corridor timetable');ax.grid(True,alpha=.25);ax.legend(ncol=2)
fig.tight_layout();fig.savefig(figdir/'sample_timetable.png',dpi=220);plt.close(fig)
print('wrote figures to',figdir)

# Corridor-budget sensitivity.
import glob, os
for inst in ['ras_corridor_12','flatland_like_20x20_40']:
    sr=[]
    for fn in glob.glob(str(ROOT/f'results/sensitivity/{inst}_B*/summary.csv')):
        r=next(csv.DictReader(open(fn)))
        B=float(r['budget']); red=int(r['reduced_states']); full=int(r['full_states_sampled'])
        sr.append((B,100*(1-red/full) if full else 0,int(r['final_hard_conflicts'])))
    sr.sort()
    if not sr: continue
    fig,ax=plt.subplots(figsize=(7,4.5))
    ax.plot([x[0] for x in sr],[x[1] for x in sr],marker='o')
    ax.set_xlabel('Quadratic corridor budget');ax.set_ylabel('State reduction (%)')
    ax.set_title(inst.replace('_',' ') + ': corridor sensitivity');ax.grid(True,alpha=.25)
    fig.tight_layout();fig.savefig(figdir/f'{inst}_sensitivity.png',dpi=220);plt.close(fig)
