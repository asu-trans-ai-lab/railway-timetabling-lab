# RAS Weekly Report — 2026-08-17 Archive

## 1. Report

- Original filename: `weekly_Aug_17_ (1).pptx`
- Archived filename: `report/RAS_weekly_report_2026-08-17.pptx`
- Archive label requested: 2026-08-17.
- Date shown on cover: **August 18, 2026**. The archive folder retains the requested 08/17 label, but the report itself is an 08/18 deck.
- SHA-256: `af016310b6b3ae9e2f62fb8b9d1f0da29d15c25c19a18316335b8716facb0609`

## 2. Weekly Objective

Explain the Dataset 3 solver architecture: a Python/C++ boundary, train-specific time-dependent DP, resource-time LR decomposition, fluid-queue price feedback, and separate LB and feasible-UB certificates.

## 3. What Changed Since Previous Report

Relative to 08/11's problem/formulation overview, the deck gives an algorithmic implementation architecture and detailed DP/LR equations. It separates the C++ train best-response kernel from the Python congestion-price loop and explains why a relaxed LB needs a distinct feasible recovery for an UB.

The implementation evolved continuously after this report.
Current source paths listed below are descendants of the components
discussed in this weekly report and should not automatically be interpreted
as exact historical source snapshots.

## 4. Dataset(s)

- RAS Dataset 3: `../../data/RAS_data-set_3/` (slide 2).

## 5. Reported Methods

- Python ↔ C++ solver architecture (slide 2).
- Train-level time-dependent DP with state `(node,time)`, running and waiting transitions (slides 6-8).
- Soft LR/fluid queue loop with `A`, `Q`, capacity `mu`, `rho`, nonlinear target price, and smoothing (slides 9-13).
- Dual LB `sum_i SP_i(lambda) - lambda^T C`, best over iterations (slide 14).
- Feasible timetable recovery and validated UB (slides 15-16).

## 6. Reported Results

This deck is primarily methodological. It reports complexity `O(I*N*K*S)` (slide 4) and illustrative numerical examples, but no Dataset 3 LB, UB, gap, runtime, or validator status.

## 7. Mapping to Current Implementation

| Reported component | Current path | Function/class | Relationship |
|---|---|---|---|
| Python/C++ boundary | `../../solver/python/dp_interface.py` | `NetworkDP` | current descendant; boundary is TSV/subprocess |
| C++ time-space DP | `../../solver/cpp/network_dp.cpp` | DP executable | reimplemented/current descendant |
| LR evaluation/update | `../../solver/python/lagrangian.py` | `evaluate_at_lambda`, `run_lr` | adapted/current descendant |
| Fluid queue | `../../solver/python/fluid_queue.py` | queue helpers | current descendant |
| Feasible recovery/UB | `../../solver/python/branch_and_bound.py` | `ConflictBB` | later replacement/extension |
| Independent validation | `../../validator/validate_schedule.py` | `certified_upper_bound` | independent new component |

## 8. Historical Git Provenance

No commit dated 08/17-08/18 was found for the current corridor benchmark path. `db6a8ea58c1579920947a1648531cb35342bd382` (2026-08-11) is the preceding known state and `6ecf7ac3740e649723c7bc5382da5fea87821b67` (2026-08-24, “Merge solver fixes 1-3 into asu”) is the next nearby solver commit. Both are **NEAREST KNOWN COMMIT**, not verified result-producing commits.

## 9. Reproduction Status

- Architecture and equations: **REPRODUCIBLE WITH CURRENT IMPLEMENTATION**, subject to the differences in `references/IMPLEMENTATION_CROSSWALK.md`.
- Exact historical source state: **EXACT HISTORICAL IMPLEMENTATION UNKNOWN**.
- Numerical Dataset 3 result: not present in this report.
