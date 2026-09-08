# Result Manifest — 2026-08-17 Archive

| Result ID | Slide | Dataset | Experiment | Metric | Reported value | Current artifact | Status |
|---|---:|---|---|---|---|---|---|
| 0817-R1 | 2 | RAS Dataset 3 | Solver architecture | boundary | Python ↔ C++ | `../../solver/python/dp_interface.py`, `../../solver/cpp/network_dp.cpp` | REPRODUCIBLE WITH CURRENT IMPLEMENTATION |
| 0817-R2 | 4 | RAS Dataset 3 | Complexity discussion | rough effort | `O(I*N*K*S)` | none; explanatory expression | HISTORICAL REPORTED METHOD |
| 0817-R3 | 14 | RAS Dataset 3 | LR dual | LB identity | `sum SP_i(lambda)-lambda^T C` | `../../solver/python/lagrangian.py` | SEMANTICALLY REPRODUCIBLE |
| 0817-R4 | 15-16 | RAS Dataset 3 | Feasible recovery | certificate relation | `LB <= OPT <= UB` | `../../validator/validate_schedule.py` | SEMANTICALLY REPRODUCIBLE |

No Dataset 3 numerical bound/result artifact is claimed by this deck.
