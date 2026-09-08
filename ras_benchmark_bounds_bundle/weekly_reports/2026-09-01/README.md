# RAS Weekly Report — 2026-09-01

## 1. Report

- Original filename: `Weekly_Update_Sep_1 (1).pptx`
- Archived filename: `report/RAS_weekly_report_2026-09-01.pptx`
- Date shown on cover: September 1, 2026.
- SHA-256: `365be7215fa7d4e121fe04e6691988aa093d14e7ad02a5b1d39ffb85c9110dca`

## 2. Weekly Objective

Summarize validated P1-P3 bounds/gaps, distinguish what is complete for Dataset 3, assess LB-strengthening and B&B experiments, and state the remaining OFQ ablation question.

## 3. What Changed Since Previous Report

Relative to 08/25, the deck reports validated UBs for all three RAS datasets, improves Dataset 3 LB from the OFQ/LR/DP baseline 3345.109 to 3366.818 through joint-order strengthening, improves the Dataset 3 UB from 4830.411 to 4797.846, reports an overtaking correction, and records that B&B/CBS branching gave zero minimum-child uplift for the tested P3 configuration.

The implementation evolved continuously after this report.
Current source paths listed below are descendants of the components
discussed in this weekly report and should not automatically be interpreted
as exact historical source snapshots.

## 4. Dataset(s)

- `../../data/RAS_data-set_1/`
- `../../data/RAS_data-set_2/`
- `../../data/RAS_data-set_3/`

## 5. Reported Methods

- OFQ + LR + train-level DP baseline.
- Joint-order MIP LB strengthening.
- B&B/CBS-style conflict branching and cross-resource coupling pilots.
- Pair-deep feasible recovery and full physical validation.
- Same-direction main-track overtaking correction.

## 6. Reported Results

| Problem | Trains | LB | Validated UB | Gap | Runtime | Conflicts |
|---|---:|---:|---:|---:|---:|---:|
| RAS 1 | 12 | 1817.10 | 2134.21 | 14.86% | 68.3 s | 0 |
| RAS 2 | 18 | 2653.81 | 6281.93 | 57.75% | 456.0 s | 0 |
| RAS 3 | 20 | 3366.82 | 4797.85 | 29.83% | 1456.56 s | 0 |

Source: slide 3. Slide 4 gives Dataset 3 as LB 3366.818, UB 4797.846, 20/20 trains, validator PASS, zero conflicts. Slide 5 reports baseline LB 3345.109, joint-order gain to 3366.818 (+0.649%), zero useful B&B uplift, and pair-deep UB improvement 4830.411 -> 4797.846. Slide 6 reports 88% of node-train records retained multiple exact minimizers.

## 7. Mapping to Current Implementation

| Reported component | Current path | Function/class | Relationship |
|---|---|---|---|
| OFQ/LR/DP | `../../solver/python/fluid_queue.py`, `../../solver/python/lagrangian.py`, `../../solver/cpp/network_dp.cpp` | queue, LR, DP | descendant with documented modifications |
| Joint physical couplings | `../../solver/python/physical_constraint_model.py` | coupling families | adapted/current descendant |
| B&B/CBS branching | `../../solver/python/branch_and_bound.py`, `conflict_branch.py` | `ConflictBB` | current descendant/reimplementation |
| Overtaking rule | `../../validator/trusted_physics.py` | `_forbidden_main_track_overtake` | current descendant |
| Pair-deep recovery/UB | `../../solver/python/branch_and_bound.py` | validated incumbent handling | replaced/extended |
| Full validator | `../../validator/validate_schedule.py` | `validate_upper_bound_schedule` | current descendant |

## 8. Historical Git Provenance

`1870b34f926a5302e292402090ad16477d50f230` dated 2026-09-01 (“Add corridor benchmark source and fixtures”) is the **NEAREST KNOWN COMMIT** introducing a large body of relevant RAS/DP/LR/validation material. The commit also contains unrelated corridor code and is not proven to be the exact result-producing state. No verified result-producing commit was located.

## 9. Reproduction Status

- Slide values: **HISTORICAL REPORTED RESULT**.
- Later source files contain matching constants for Dataset 3 (`3345.109160482375`, `3366.818246`, `4797.846048621338`), which corroborates continued use of those values but does not establish original run provenance.
- Exact historical run/configuration for all P1-P3 rows: **EXACT HISTORICAL IMPLEMENTATION UNKNOWN / ORIGINAL ARTIFACT NOT LOCATED**.
- Current bundle can run the same data flow, but its bounded smoke outputs are not replacements for these historical full-run results.
