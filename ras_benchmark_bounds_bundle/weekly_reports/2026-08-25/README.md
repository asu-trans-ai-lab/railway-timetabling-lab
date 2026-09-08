# RAS Weekly Report — 2026-08-25

## 1. Report

- Original filename: `weekly_Aug_25_2 (1).pptx`
- Archived filename: `report/RAS_weekly_report_2026-08-25.pptx`
- Date shown on cover: August 25, 2026.
- SHA-256: `27ec37266b705319c645462126369dcd2bc166f295c9d5d22652c9d1948d04c9`

## 2. Weekly Objective

Clarify the fluid-queue price interpretation, report a Dataset 3 LR lower bound and independently validated feasible upper bound, and describe candidate-selection and sequential recovery approaches.

## 3. What Changed Since Previous Report

Relative to the 08/18 architecture deck, this report adds concrete Dataset 3 LB/UB/gap values, a space-time LB/UB comparison, explicit independent validation of the selected 20-train trajectory combination, and multi-order sequential recovery. It describes pruning as the next step and “not fully implemented” (slide 12).

The implementation evolved continuously after this report.
Current source paths listed below are descendants of the components
discussed in this weekly report and should not automatically be interpreted
as exact historical source snapshots.

## 4. Dataset(s)

- RAS Dataset 3: `../../data/RAS_data-set_3/`.

## 5. Reported Methods

- Fluid queue interpretation and dimensional scaling (slides 5-6).
- Generalized DP cost (slide 7).
- LR lower bound and candidate-combination feasible UB (slides 8-10).
- Independent pairwise schedule validation (slide 10).
- Sequential DP recovery and multiple ordering heuristics (slides 11, 13).
- Proposed g+h/B&B-style pruning, explicitly not fully implemented (slide 12).

## 6. Reported Results

- Dataset 3 LB = **3361.2895** (slide 8).
- Dataset 3 validated UB = **5869.9012** (slides 8, 10).
- Absolute gap = **2508.6117**; relative gap = **42.7369%** normalized by UB (slide 8).
- A 20-train trajectory selection passed the report's continuous-headway validation (slide 10).

## 7. Mapping to Current Implementation

| Reported component | Current path | Function/class | Relationship |
|---|---|---|---|
| LR/DP LB | `../../solver/python/lagrangian.py`, `../../solver/cpp/network_dp.cpp` | `evaluate_at_lambda`, C++ DP | descendant with modified semantics |
| Candidate combination/UB | `../../solver/python/branch_and_bound.py` | `ConflictBB` | replaced/extended |
| Independent validation | `../../validator/validate_schedule.py` | `certified_upper_bound` | current descendant |
| Sequential recovery idea | `../../solver/python/branch_and_bound.py` | incumbent construction through DP/validation | conceptual descendant |
| Pruning | `../../solver/python/branch_and_bound.py` | bound/node pruning | later implemented; not historical snapshot |
| Space-time plot | `../../visualization/plot_space_time.py` | `plot_space_time` | current replacement |

## 8. Historical Git Provenance

`6ecf7ac3740e649723c7bc5382da5fea87821b67` dated 2026-08-24 (“Merge solver fixes 1-3 into asu”) is a **LIKELY HISTORICAL STATE** for slide 2's merge statement, but no evidence ties it to the Dataset 3 values. No **VERIFIED RESULT-PRODUCING COMMIT** was located.

## 9. Reproduction Status

- Reported LB/UB/gap: **HISTORICAL REPORTED RESULT**.
- Exact original run files/configuration: **ORIGINAL ARTIFACT NOT LOCATED**.
- Same workflow with current code: **REPRODUCIBLE WITH CURRENT IMPLEMENTATION**, but current semantics/results must not be presented as reproducing the exact historical numbers.
