#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
make
python python/generate_instances.py
rm -rf results/final
mkdir -p results/final
run_case(){
  local name="$1" budget="$2"
  mkdir -p "results/final/$name"
  ./joint_timetable_solver "instances/$name" "results/final/$name" \
    --budget="$budget" --rho=0.18 --radius=0 --max-group=3 \
    --full-limit=3 --max-rounds=10
}
run_case ras_corridor_12 4
run_case ras_corridor_24 6
run_case ras_corridor_48 8
run_case flatland_like_20x20_40 4
run_case flatland_like_30x30_80 4
rm -rf results/fullcheck_ras24 results/fullcheck_ras48
cp -r results/final/ras_corridor_24 results/fullcheck_ras24
cp -r results/final/ras_corridor_48 results/fullcheck_ras48
python python/collect_results.py
python python/plot_results.py
