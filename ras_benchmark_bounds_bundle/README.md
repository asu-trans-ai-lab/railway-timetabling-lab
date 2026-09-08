# ANL/RAS Train Scheduling Pipeline

A clean, reproducible package for the ANL/RAS railway timetabling workflow.
It contains the input data, RAS adapter, native C++ train DP, Python
Lagrangian/B&B solver, independent validator, visualization, experiments, and
tests. Its scope is limited to ANL/RAS train scheduling.

## Repository Purpose

This is the clean ANL/RAS scheduling package. It intentionally excludes Version B, nationwide freight-network preprocessing, Southern Transcon extraction code/data, and other planning-layer material. The only broader-corridor content is embedded inside unmodified historical PowerPoint reports where removing slides would destroy report integrity.

`PACKAGE_INVENTORY.md` records the source-level audit of every active pipeline component.

## Pipeline Architecture

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

The actual call chain is `run_pipeline.py` -> `experiments/common.py::run_pipeline` -> `adapters/ras_adapter.py` -> `solver/python/dp_interface.py::NetworkDP` -> `solver/cpp/network_dp.cpp`, followed by schedule serialization, independent validation, and plotting.

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
- `weekly_reports/`: four report milestones with result manifests and provenance labels.
- `references/`: preserved original Fast Train release and paper/code crosswalks.

## Where Are the Major Components?

| Question | Location |
|---|---|
| RAS datasets | `data/RAS_data-set_1/`, `data/RAS_data-set_2/`, `data/RAS_data-set_3/` |
| Mini sanity datasets | `data/mini_cases/` |
| RAS adapter | `adapters/ras_adapter.py` and `solver/python/dp_interface.py::load_dataset` |
| C++ engine | `solver/cpp/network_dp.cpp` |
| Python/C++ interface | `solver/python/dp_interface.py::NetworkDP` |
| LR | `solver/python/lagrangian.py` plus `physical_lr.py`/`physical_lagrangian.py` |
| B&B | `solver/python/branch_and_bound.py` |
| Validator | `validator/validate_schedule.py`, `validator/trusted_physics.py` |
| Visualization | `visualization/plot_space_time.py` |
| Weekly RAS reports | `weekly_reports/` |
| Original Fast Train | `references/fast_train_original/` |
| Paper-vs-code audit | `references/PAPER_IMPLEMENTATION_COMPARISON.md` |

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

## RAS Weekly Report Archive

These are report milestones, not code snapshots. See `weekly_reports/README.md` and each dated `RESULT_MANIFEST.md`.

| Date/archive | Main milestone | Dataset(s) | Report | Results | Reproduction status |
|---|---|---|---|---|---|
| 2026-08-11 | RAS problem/data semantics, feasibility framing, initial 12-train example | RAS 1 schema; separate corridor small case | `weekly_reports/2026-08-11/report/` | No RAS LB/UB; 300 s rule and 12-train input summary | PARTIALLY REPRODUCIBLE; exact historical source unknown |
| 2026-08-17 (cover says 08/18) | Python/C++ DP, LR/fluid queue architecture, LB/UB framework | RAS 3 | `weekly_reports/2026-08-17/report/` | Methodological; no D3 numerical bound | Architecture reproducible; exact historical source unknown |
| 2026-08-25 | D3 LR lower bound, validated feasible UB, sequential recovery | RAS 3 | `weekly_reports/2026-08-25/report/` | LB 3361.2895, UB 5869.9012, gap 42.7369% | HISTORICAL REPORTED RESULT; original run artifact not located |
| 2026-09-01 | P1-P3 scorecard, joint-order strengthening, recovery and B&B diagnostics | RAS 1/2/3 | `weekly_reports/2026-09-01/report/` | D3 LB 3366.818, UB 4797.846, gap 29.83%; all UBs zero conflicts | PARTIALLY REPRODUCIBLE; exact producing artifacts/commit unknown |

## Fast Train Reference Implementation

`references/fast_train_original/` preserves the complete selected local Fast Train release (excluding only Finder `.DS_Store` cache files). It is reference-only: no active package module imports or compiles it. Source selection and public-tree comparison are recorded in `references/fast_train_original/README_ORIGINAL_SOURCE.md`.

The original C++ DP/LR code is under `references/fast_train_original/LR-C++ solution framework/Fast train-V1.0-Min Total Delay Time/FastTrain/`. The original RAS GUI release is under `references/fast_train_original/GUI Release for RAS Problem Solving Competition/`; its parser/validator source was not included in the public release.

## Paper and Implementation Comparison

- `references/FAST_TRAIN_LINEAGE.md`: evidence-labelled Fast Train paper -> original engine -> RAS release -> current package lineage.
- `references/IMPLEMENTATION_CROSSWALK.md`: 30-component executable semantics comparison.
- `references/PAPER_IMPLEMENTATION_COMPARISON.md`: paper equations/algorithms mapped to original and current source.

The central conclusion is architectural/semantic lineage, not source identity: the current DP/LR stack is an independent reimplementation with material objective, discretization, capacity, validation, and B&B extensions.
