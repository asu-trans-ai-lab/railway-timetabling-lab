# RAS Weekly Report — 2026-08-11

## 1. Report

- Original filename: `Aug_11_v1 (1).pptx`
- Archived filename: `report/RAS_weekly_report_2026-08-11.pptx`
- Date shown on cover: August 11, 2026.
- SHA-256: `24951321062a8cc739f40e23fc443053797706baa422d774704a6825354c7e75`

## 2. Weekly Objective

Establish the RAS movement-planning problem, distinguish hard physical feasibility from soft performance terms, describe the RAS data schema, and show candidate solver formulations and an initial 12-train meet/pass example.

## 3. Baseline / Starting Point

The deck presents the problem inputs/outputs and 300-second safety separation (slides 2-3), preprocessing and RAS schema concepts (slides 14-18), MILP variables/constraints (slides 15, 17-20), and a 12-train siding meet/pass schedule (slide 22). It does not report a verified LR/B&B bound for RAS Dataset 1-3.

The implementation evolved continuously after this report.
Current source paths listed below are descendants of the components
discussed in this weekly report and should not automatically be interpreted
as exact historical source snapshots.

## 4. Dataset(s)

- RAS Dataset 1 schema/example: `../../data/RAS_data-set_1/` (slide 16 reports 12 trains).
- The 12-train result on slide 22 is a Southern Transcon small case, not proven to be one of the current `data/mini_cases/`; its code/data is intentionally not packaged here.

## 5. Reported Methods

- Feasibility-first evaluation and independent-validator concept.
- MILP/CPLEX formulation with entry/exit times, track choice, sequencing, rolling horizon, and heuristic fixing.
- Main/siding/crossover route decisions, MOW, siding length/hazmat restrictions, overtaking and 300-second separation.

## 6. Reported Results

- RAS Dataset 1 example size: 12 trains (slide 16).
- Small corridor case: one train waits 79 minutes while three trains pass; two other waits are 12 and 26 minutes (slide 22). This is retained as historical report content, not claimed as a current RAS result.

## 7. Mapping to Current Implementation

| Reported component | Current path | Function/class | Relationship |
|---|---|---|---|
| RAS CSV schema | `../../adapters/ras_adapter.py` | `load_ras_dataset` | adapted/current descendant |
| Route/timing engine | `../../solver/cpp/network_dp.cpp` | C++ main DP | replaced by DP implementation |
| Physical feasibility | `../../validator/validate_schedule.py` | `validate_upper_bound_schedule` | independent new validator |
| Siding/opposing sanity cases | `../../data/mini_cases/` | manifest cases | conceptual descendant, not historical snapshot |

## 8. Historical Git Provenance

| Label | Commit | Date | Message | Assessment |
|---|---|---|---|---|
| NEAREST KNOWN COMMIT | `ce68650036ae744ae857bc3c8fa7ba86e49c73fb` | 2026-08-11 | Turnkey corridor timetabling package: 11 instances, solvers, validator, work plan, kickoff deck | Same date and related corridor material, but no evidence that it generated this report or current RAS components. |
| NEAREST KNOWN COMMIT | `db6a8ea58c1579920947a1648531cb35342bd382` | 2026-08-11 | Auditability layer: provenance labels, demand-derived service (blocking chain), three-level audits | Same date; mostly corridor audit work, not a verified RAS result-producing state. |

## 9. Reproduction Status

- RAS schema loading and feasibility concepts: **REPRODUCIBLE WITH CURRENT IMPLEMENTATION**.
- Slide 22 schedule: **HISTORICAL REPORTED RESULT; EXACT HISTORICAL IMPLEMENTATION UNKNOWN**.
- Original numerical artifact for slide 22: **ORIGINAL ARTIFACT NOT LOCATED**.
