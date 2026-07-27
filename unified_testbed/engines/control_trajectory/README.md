# Coordination-Agent + Trajectory-Agent Joint Timetabling Prototype

This package implements the combined architecture:

```text
Coordination agent
  conflict graph + resource prices + precedence + proximal corridor
        |
        v
Trajectory agents / temporary hyper-trajectory agents
  physically feasible individual or joint time-space-state DP
        |
        v
Accept/reject update + group splitting + iteration diagnostics
```

## Main files

- `src/coordination_trajectory_solver.cpp`: C++17 solver.
- `python/generate_control_demo.py`: 8/16/32/64-agent railway cases.
- `python/collect_results.py`: benchmark aggregation.
- `python/verify_results.py`: automated correctness checks.
- `python/plot_control_results.py`: paper-facing figures.
- `docs/CONTROL_TRAJECTORY_ARCHITECTURE.md`: mathematical and software architecture.
- `python/flatland_adapter.py`: adapter for exporting a Flatland `RailEnv`.

## Run

```bash
bash run_all.sh
```

Or manually:

```bash
make
python python/generate_control_demo.py
./coordination_trajectory_solver \
  instances/control_corridor_16 \
  results/control_corridor_16 \
  --budget=6 --rho=0.18 --max-group=3 --full-limit=3 \
  --price-step=0.25 --precedence=0.75
```

## Implemented control actions

- dynamic interaction groups;
- resource-time price updates;
- local precedence bias;
- group-specific corridor budget;
- reduced/full joint-DP comparison;
- accept/reject with rollback;
- group dissolution after conflict resolution.

Hard block/resource exclusivity and edge-swap safety remain inside the trajectory
transition generator and cannot be relaxed by the control agent.

## Results

See `results/CONTROL_TRAJECTORY_REPORT.md` and
`results/control_trajectory_benchmark_summary.csv`.

The current benchmark is deliberately sparse: independent meet/pass communities
show that population size can grow while the largest hyper-agent group stays
small. Dense conflict components remain a boundary case.
