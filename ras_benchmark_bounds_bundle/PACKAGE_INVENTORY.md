# Package Inventory

Audit date: 2026-09-07.

This inventory records the active package as inspected from executable source. Generated `.venv/`, `__pycache__/`, `solver/cpp/build/`, and `results/*` run products are not source components.

| Area | Active files | Verified role |
|---|---|---|
| Data | `data/RAS_data-set_1/`, `data/RAS_data-set_2/`, `data/RAS_data-set_3/` | Shared RAS CSV inputs; not duplicated in weekly archives. |
| Mini data | `data/mini_cases/manifest.json` and six case directories | One-train, no-conflict, conflict, bottleneck, siding, and opposing-direction sanity cases. |
| Adapter | `adapters/ras_adapter.py` | Resolves dataset names and delegates RAS CSV parsing to `solver/python/dp_interface.py::load_dataset`; also loads node positions for plotting. |
| C++ DP | `solver/cpp/network_dp.cpp` | Active single-train time-dependent DP executable. Reads normalized TSV files and writes candidate trajectories. |
| Python/C++ bridge | `solver/python/dp_interface.py::NetworkDP` | Compiles the C++ source, writes `network.tsv`, `requests.tsv`, `lambda.tsv`, MOW and branch-restriction TSVs, invokes the executable, and parses results. |
| LR | `solver/python/lagrangian.py` | Resource-time occupancy, capacity, dual evaluation, and LR iteration driver. |
| Physical LR | `solver/python/physical_lr.py`, `solver/python/physical_lagrangian.py`, `solver/python/physical_constraint_model.py` | Validator-aligned coupling and physical LR support. |
| B&B | `solver/python/branch_and_bound.py::ConflictBB` | Conflict-driven branch-and-bound over validator-certified schedules and DP subproblems. |
| Validator | `validator/validate_schedule.py`, `validator/trusted_physics.py` | Independent serialization entry point plus single-train reconstruction and joint physical checks. |
| Visualization | `visualization/plot_space_time.py::plot_space_time` | Reads `schedule.csv`, draws train trajectories, and labels validator status/conflict count. |
| Experiments | `experiments/common.py`, `experiments/run_mini_cases.py`, `experiments/run_ras_datasets.py` | Shared end-to-end pipeline and batch entry points. |
| Main entry | `run_pipeline.py` | Runs one mini or RAS dataset through adapter, C++ DP, LR/B&B, schedule export, validator, and plot. |
| Outputs | `schedule.json`, `schedule.csv`, `validator_report.json`, `bb_trace.json`, `metrics.json`, `space_time_diagram.png` | Written under the selected result directory by `experiments/common.py::run_pipeline`. |
| Tests | `tests/test_pipeline.py`, `run_tests.sh`, `Makefile` | Builds/imports components and exercises mini plus bounded RAS pipelines. |
| Historical reports | `weekly_reports/` | Report milestones only; no fabricated weekly code snapshots. |
| Original reference | `references/fast_train_original/` | Preserved Fast Train release, not imported or executed by the active package. |

## Executable Pipeline

```text
RAS or mini CSV files
  -> adapters/ras_adapter.py
  -> solver/python/dp_interface.py
  -> normalized TSV boundary
  -> solver/cpp/network_dp.cpp
  -> solver/python/lagrangian.py and solver/python/branch_and_bound.py
  -> schedule.json and schedule.csv
  -> validator/validate_schedule.py
  -> visualization/plot_space_time.py
  -> metrics, trace, validator report, and PNG
```

Each arrow was confirmed from imports, calls, subprocess invocation, and output-writing code rather than inferred from directory names.
