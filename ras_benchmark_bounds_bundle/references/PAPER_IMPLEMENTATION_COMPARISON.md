# Paper and Implementation Comparison

## Source Audited

Lingyun Meng and Xuesong Zhou, “Fast Train: A Computationally Efficient Train Routing and Scheduling Engine for General Rail Networks,” IEEE ITSC 2014, preserved as `fast_train_original/Fast train A computationally efficient train routing and scheduling engine for general rail networks.pdf` (6 pages).

The comparison below uses the paper's printed equations/sections and executable code. Comments alone were not treated as implementation evidence.

| Paper section/equation/algorithm | Meaning | Original Fast Train implementation | Current implementation | Match status | Notes |
|---|---|---|---|---|---|
| Sec. III, cumulative arrival/departure flow | Represent train movement and occupancy in time-space | Implemented operationally through node/time labels and reconstructed train timestamps, not explicit MIP flow variables | `network_dp.cpp` labels and trajectory records | SEMANTICALLY EQUIVALENT | Both generate a timed path; neither current DP nor released original DP stores the paper's binary cumulative variables literally. |
| Eq. (3) | Occupancy from cumulative entry minus departure with headway offsets | `Timetable.cpp::g_UpdateResourceUsageStatus` counts from entry through exit plus `g_SafetyHeadway` | `lagrangian.py::resource_time_occupancy`; physical model uses protected intervals | MODIFIED | Current relaxed-cell accounting and validator physical intervals are deliberately separated. |
| Eq. (4), pp. 3-4 | Bidirectional link capacity/headway constraint | `UsageCount`, `m_LinkCapacity`, and grouped links | protected cells plus `trusted_physics.py::_physical_leg_pair_conflict` | MODIFIED | Current physical feasibility is pairwise validator logic, not solely the relaxed capacity inequality. |
| Eq. (5), p. 4 | Destination deviation plus relaxed resource penalty | DP adds local resource price and absolute terminal completion deviation; running/stopped terms are commented out in `ShortestPath.cpp` | physical cost includes origin wait, running, siding wait, and early/late terminal terms; lambda added separately | MODIFIED | This is a critical objective difference. |
| Eq. (6), p. 4 | Lagrangian dual decomposes by train and subtracts multiplier times capacity | `Timetable.cpp`: `TotalTripPrice - TotalResourcePrice` | `lagrangian.py::evaluate_at_lambda` | SEMANTICALLY EQUIVALENT | Algebraic pattern matches; exact coefficient maps/domains differ. |
| Eq. (7), p. 4 | Train-specific generalized least-cost path | `OptimalTDLabelCorrecting_DoubleQueue` + `FindOptimalSolution` | `network_dp.cpp`, called by `NetworkDP.solve` | SEMANTICALLY EQUIVALENT | Independently reimplemented with different time grid, costs, and restrictions. |
| Sec. IV, pp. 4-5 | Label-correcting time-dependent shortest path | `ShortestPath.cpp` double-queue scan eligible list | current C++ label search | MODIFIED | Current implementation is not a source port and carries current branch constraints. |
| Sec. IV, p. 5 | Trains may wait at cells | `time_stopped = 0..MaxAllowedStopTime` on traversed links | origin departure slack and siding-only waiting | MODIFIED | Current non-siding waiting is prohibited by validator. |
| Sec. IV, p. 5 | Resource cost sums multipliers over selected links/time spans | `GetLocalResourceCost`, inclusive time loop and headway tail | lambda/resource cells written by Python and consumed by C++ | MODIFIED | Time-bin and protected-cell conventions differ. |
| Fig. 7 / Sec. IV | Solve independent train DPs, compute bound, rank trains, recover feasible solution | `g_Timetable_Optimization_Lagrangian_Method`, then priority recovery | LR evaluation plus `ConflictBB`; validator gates incumbents | IMPLEMENTATION EXTENSION | Current replaces priority-only recovery with conflict B&B and independent certification. |
| Sec. IV | Rank trains by Lagrangian profit | `g_FindPriorityRanking` and `g_DeduceToProblemFeasibleSolution` | no equivalent ranking as the primary B&B rule | NOT IMPLEMENTED | Current branching/recovery logic is different. |
| Sec. IV | Subgradient update | `g_UpdateResourcePrices`: projected update, step `max(1/(k+1), floor)`, stale-price reset | `run_lr` and physical LR policies | MODIFIED | Current exposes different update policies, including fluid-queue-informed prices. |
| Sec. IV | Terminate on max iterations, small gap, or stalled feasible solution | LR iteration loop and UB tracking | configured LR/B&B node/depth/iteration statuses | MODIFIED | Current reports budget exhaustion explicitly and does not inherit the exact original rule. |
| Sec. V | RAS-derived 78-node/88-link, 25-train experiment | datasets under original LR test bed/results | top-level RAS datasets have 12/18/20 trains | NOT IMPLEMENTED | Current package does not claim to reproduce the paper's experimental instance. |
| Paper scope | LR + priority heuristic; no branch-and-bound described | no B&B found in released source | `solver/python/branch_and_bound.py` | IMPLEMENTATION EXTENSION | B&B is current new work, not a paper reproduction. |
| Paper/GUI scope | Visualization and feasibility are peripheral | `NEXTA.exe`; source absent | independent plotter and validator | IMPLEMENTATION EXTENSION | Current components are auditable and scriptable but not equivalent to the GUI binary. |

## Critical Findings

1. The dual identity is the strongest semantic match: paper Eq. (6), original `TotalTripPrice - TotalResourcePrice`, and current `evaluate_at_lambda` share the decomposed bound structure.
2. Objective equality is **not** established. Original executable DP emphasizes terminal deviation and prices, while current physical objective also charges running, origin delay, siding wait, and early/late terminal deviation.
3. Original per-link integer-minute occupancy extends through a safety-headway tail. Current relaxed cells and final physical conflict intervals use different, explicitly documented semantics.
4. The paper's priority-rule feasible recovery exists in original C++; current validated B&B/repair pipeline is an extension/replacement.
5. No auditable source for the original RAS `input_rail_*` parser or GUI validator was located; claims about exact adaptation internals remain `UNCLEAR`.

## Unresolved Mapping

- Exact source implementation of `NEXTA.exe` RAS parsing, visualization, and feasibility checks.
- Exact historical commit linking the 2014 paper experiments to the preserved source tree.
- Numerical parity between the original Visual C++ program and the current C++ DP; it is neither claimed nor expected under the documented semantic differences.
