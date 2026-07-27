#!/usr/bin/env python3
from __future__ import annotations
import csv, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
fail=[]
for p in sorted((ROOT/'results').glob('control_corridor_*/summary.csv')):
    r=next(csv.DictReader(p.open()))
    checks={
        'zero final conflicts': int(r['final_conflicts'])==0,
        'all actions accepted': int(r['accepted_actions'])==int(r['control_actions']),
        'no rejected actions': int(r['rejected_actions'])==0,
        'all full comparisons exact': int(r['exact_objective_matches'])==int(r['full_comparisons']),
        'state reduction': int(r['reduced_states'])<int(r['full_states_sampled']),
        'transition reduction': int(r['reduced_transitions'])<int(r['full_transitions_sampled']),
        'zero objective gap': float(r['max_objective_gap'])<=1e-8,
    }
    for name,ok in checks.items():
        if not ok:fail.append(f'{p.parent.name}: {name}')
if fail:
    print('\n'.join(fail));sys.exit(1)
print('All coordination/trajectory checks passed.')
