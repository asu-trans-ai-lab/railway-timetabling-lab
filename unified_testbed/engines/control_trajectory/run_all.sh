#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
make
python python/generate_control_demo.py
for n in 8 16 32 64; do
  ./coordination_trajectory_solver \
    "instances/control_corridor_${n}" \
    "results/control_corridor_${n}" \
    --budget=6 --rho=0.18 --max-group=3 --full-limit=3 --max-rounds=8 \
    --price-step=0.25 --precedence=0.75
done
python python/collect_results.py
python python/verify_results.py
python python/plot_control_results.py
