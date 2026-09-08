# Result Manifest — 2026-09-01

| Result ID | Slide | Dataset | Experiment | Metric | Reported value | Current artifact | Status |
|---|---:|---|---|---|---:|---|---|
| 0901-R1 | 3 | RAS Dataset 1 | validated scorecard | LB / UB / gap / runtime / conflicts | 1817.10 / 2134.21 / 14.86% / 68.3 s / 0 | no exact archived run located | HISTORICAL REPORTED RESULT |
| 0901-R2 | 3 | RAS Dataset 2 | validated scorecard | LB / UB / gap / runtime / conflicts | 2653.81 / 6281.93 / 57.75% / 456.0 s / 0 | no exact archived run located | HISTORICAL REPORTED RESULT |
| 0901-R3 | 3-4 | RAS Dataset 3 | strengthened bound + recovery | LB / UB / gap / runtime / conflicts | 3366.818 / 4797.846 / 29.83% / 1456.56 s / 0 | later source constants corroborate LB/UB only | PARTIALLY REPRODUCIBLE; exact historical run unknown |
| 0901-R4 | 5-6 | RAS Dataset 3 | OFQ+LR+DP | baseline LB | 3345.109 | later source constant corroborates value | HISTORICAL REPORTED RESULT |
| 0901-R5 | 5-6 | RAS Dataset 3 | joint-order MIP | LB uplift | 3345.109 -> 3366.818 (+0.649%) | later source constants/tests | PARTIALLY REPRODUCIBLE; producing artifact not identified |
| 0901-R6 | 5-6 | RAS Dataset 3 | B&B/CBS branching | minimum-child uplift | 0 | no exact archived run matched | Historical reported result; original result artifact not yet located. |
| 0901-R7 | 6 | RAS Dataset 3 | degeneracy audit | records with multiple exact minimizers | 88% | no exact archived run matched | Historical reported result; original result artifact not yet located. |
| 0901-R8 | 5 | RAS Dataset 3 | pair-deep recovery | UB improvement | 4830.411 -> 4797.846 | later final UB constant only | PARTIALLY REPRODUCIBLE; exact accepted-move artifact not identified |

Current bundle smoke summaries under `../../results/` use bounded tests and must not be treated as reproductions of these full historical runs.
