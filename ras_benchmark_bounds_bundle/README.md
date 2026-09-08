# ANL/RAS Train Scheduling Pipeline

A clean, reproducible package for the ANL/RAS railway timetabling workflow.
It contains the input data, RAS adapter, native C++ train DP, Python
Lagrangian/B&B solver, independent validator, visualization, experiments, and
tests. Its scope is limited to ANL/RAS train scheduling.

## Pipeline

```text
RAS or mini CSV dataset
        |
        v
adapters/ras_adapter.py
        |
        v
solver/cpp/network_dp.cpp  <->  solver/python/dp_interface.py
        |
        +--> solver/python/lagrangian.py
        |
        +--> solver/python/branch_and_bound.py
        |
        v
schedule.json + schedule.csv
        |
        +--> validator/validate_schedule.py --> PASS / FAIL
        |
        +--> visualization/plot_space_time.py --> space_time_diagram.png
        |
        v
metrics.json + bb_trace.json
```

## Directory Map

- `data/mini_cases/`: six 1–3 train sanity-check datasets.
- `data/RAS_data-set_1/`, `2/`, `3/`: complete RAS input directories.
- `adapters/ras_adapter.py`: RAS CSV to common `Arc`, `Train`, and MOW objects.
- `solver/cpp/network_dp.cpp`: native per-train time-dependent DP engine.
- `solver/python/dp_interface.py`: compiles/calls the C++ engine and decodes paths.
- `solver/python/lagrangian.py`: Lagrangian relaxation and zero-multiplier bound.
- `solver/python/branch_and_bound.py`: exact conflict-based B&B.
- `validator/validate_schedule.py`: independent physical feasibility gate.
- `visualization/plot_space_time.py`: space-time and trajectory plotting.
- `experiments/`: common mini/RAS pipeline runners.
- `results/`: generated schedules, validation, figures, metrics, and traces.
- `tests/test_pipeline.py`: end-to-end regression covering mini and RAS datasets.

## Install

Requirements: Python 3.10+, `g++` with C++17 support, and internet access for
the one Python plotting dependency.

```bash
./setup.sh
```

This creates an isolated `.venv`, installs Matplotlib, and compiles
`solver/cpp/network_dp.cpp` to `solver/cpp/build/network_dp`.

## Run One Dataset

```bash
.venv/bin/python run_pipeline.py --dataset case03_two_trains_conflict
.venv/bin/python run_pipeline.py --dataset RAS_data-set_1
```

Mini cases default to a 500-node/30-depth exact budget. RAS datasets default to
a safe three-node/one-depth compatibility run. Increase budgets explicitly:

```bash
.venv/bin/python run_pipeline.py --dataset RAS_data-set_2 \
  --max-nodes 1000 --max-depth 50 --output results/ras2_larger_budget
```

## Run Standard Suites

```bash
make mini   # all six mini datasets
make ras    # bounded D1/D2/D3 pipeline runs
make test   # full end-to-end regression
```

## Independent Validation

The validator reads the serialized solver output rather than trusting a solver
status flag:

```bash
.venv/bin/python -m validator.validate_schedule \
  --dataset case03_two_trains_conflict \
  --schedule results/case03_two_trains_conflict/schedule.json
```

Only validator `PASS` schedules produce a certified finite UB. A RAS root
relaxation with conflicts is saved for diagnosis but remains validator `FAIL`.

## Visualization

Every normal pipeline run automatically produces `space_time_diagram.png`.
The plotting tool can also be called independently:

```bash
.venv/bin/python -m visualization.plot_space_time \
  --dataset case03_two_trains_conflict \
  --schedule results/case03_two_trains_conflict/schedule.csv \
  --validator-report results/case03_two_trains_conflict/validator_report.json \
  --output results/case03_two_trains_conflict/space_time_diagram.png
```

Solid trajectory segments are main track; dashed segments are siding use. The
title reports validator status and conflict count.

## Scope of Evidence

- All six mini cases are expected to reach `PROVEN_OPTIMAL` and validator `PASS`.
- D1/D2/D3 default runs verify complete data loading, native DP execution,
  Lagrangian evaluation, B&B branching, serialization, validation, plotting,
  and metrics.
- The default bounded RAS runs do not claim full-instance convergence. They may
  report no finite UB and `DEPTH_BUDGET_EXHAUSTED` by design.
- Current production headway is `SEGMENT_CLEARANCE_V1`, `h = 3 minutes`.
