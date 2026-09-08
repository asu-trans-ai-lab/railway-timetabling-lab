# Implementation Crosswalk

`FastTrain/` below means `fast_train_original/LR-C++ solution framework/Fast train-V1.0-Min Total Delay Time/FastTrain/`.

| Component | Fast Train path/function | RAS Fast Train adaptation | Current path/function | Relationship | Important difference |
|---|---|---|---|---|---|
| 1. Data format | `FastTrain.cpp::g_ReadInputFiles` | `GUI Release.../RAS_data-set_*`; binary parser | `adapters/ras_adapter.py`, `dp_interface.py::load_dataset` | REIMPLEMENTED | Current Python reads RAS CSV and emits TSV; original general source reads `input_node/link`. |
| 2. RAS parser | Not present in auditable source | `NEXTA.exe` (source absent) | `load_ras_dataset` | UNKNOWN | Functional old adapter exists only as binary; no direct code comparison possible. |
| 3. Network representation | `Network.h`, `BuildSpaceTimeNetworkForTimetabling` | Unknown internals | `Arc`; C++ `Arc`/adjacency | REIMPLEMENTED | Current uses explicit normalized records and no original global arrays. |
| 4. Train representation | `Network.h::CTrain` | Unknown internals | `dp_interface.py::Train`; C++ request | REIMPLEMENTED | Current active fields are a narrower auditable subset. |
| 5. C++ engine | `FastTrain.cpp`, `ShortestPath.cpp`, `Timetable.cpp` | `NEXTA.exe` | `solver/cpp/network_dp.cpp` | REPLACED | Monolithic Visual C++ program replaced by portable DP subprocess. |
| 6. DP state | node and integer time label arrays | Unknown | node/time labels plus restrictions/history state | MODIFIED | Current adds explicit branch/history constraints. |
| 7. DP transition | `OptimalTDLabelCorrecting_DoubleQueue` | Unknown | transitions in `network_dp.cpp` | REIMPLEMENTED | Same train-specific time-space idea, different code and state details. |
| 8. Time discretization | integer minute divisions | Unknown | configurable floating grid snapped to ticks | MODIFIED | Numerical trajectories need not match. |
| 9. Running time | `GetTrainRunningTimeInMinuteDivision` | RAS speed fields | raw `60*length/speed/smult`, then grid snap | MODIFIED | Original rounds into minute divisions. |
| 10. Waiting | `time_stopped` up to link max | GUI shows sidings | origin delay and siding waits | MODIFIED | Current validator rejects en-route waits on non-sidings. |
| 11. Objective | destination deviation; running/stopped terms commented in DP | RAS scoring not auditable in binary | origin wait + running + siding wait + early/late arrival | MODIFIED | Current active primal semantics are not identical to original code or paper Eq. (5). |
| 12. Schedule adherence/delay | `abs(realized-planned completion)` | Scheduled-arrival fields | terminal want early/late plus entry delay | MODIFIED | Different field mapping and cost decomposition. |
| 13. LR formulation | `g_Timetable_Optimization_Lagrangian_Method` | Unknown | `lagrangian.py::evaluate_at_lambda`, `run_lr` | REIMPLEMENTED | Same decomposable dual pattern; capacity coefficients differ. |
| 14. Multiplier meaning | per-link, per-minute resource price | Unknown | resource-time and optional physical event prices | MODIFIED | Current supports validator-aligned event couplings. |
| 15. Multiplier update | `g_UpdateResourcePrices` | Unknown | `run_lr`; physical LR modules | MODIFIED | Original uses projected `1/(k+1)` floor and stale reset; current policies are explicit/configurable. |
| 16. Capacity/resource | `UsageCount`, `m_LinkCapacity`, link groups | GUI track conflicts | protected cells and validator couplings | MODIFIED | Current separates relaxed cells from physical feasibility. |
| 17. Same-direction headway | occupancy through exit + `g_SafetyHeadway` | guide says checked | `headway_model.py`, trusted validator | MODIFIED | Current segment-clearance semantics and overtaking check are explicit. |
| 18. Opposing conflict | Eq. (4), grouped directional links | guide nonconcurrency | `trusted_physics.py::_physical_leg_pair_conflict` | REIMPLEMENTED | Current pairwise certificate records are inspectable. |
| 19. Siding | max waiting/track links | GUI dotted trajectories | track type `S`, dwell/pass logic | MODIFIED | Current siding dwell is validated explicitly. |
| 20. Overtaking | not separately explicit in released DP | not documented beyond conflicts | `_forbidden_main_track_overtake` | NEW | Current has an explicit same-direction main-track rule. |
| 21. MOW | `g_ReadMOWCSVFile`; zero capacity | guide checks MOW | MOW TSV + validator checks | REIMPLEMENTED | Current checks at both DP input and independent validation. |
| 22. Route alternatives | full network or path-based mode | GUI route display | full graph DP with candidate enumeration | MODIFIED | No original `TrainPath` object is reused. |
| 23. Lower bound | `TotalTripPrice-TotalResourcePrice` | Unknown | LR evaluation and lower-bound validator | REIMPLEMENTED | Formula pattern matches; domain/capacity scope is current-specific. |
| 24. Upper bound | priority-rule sequential recovery | GUI feasibility check | validator-certified incumbent from B&B | REPLACED | Current solver cannot self-certify; independent validator gates UB. |
| 25. B&B | Not found | Not found | `branch_and_bound.py::ConflictBB` | NEW | Conflict branching is a current extension. |
| 26. Feasibility validation | capacity assertions inside solver | `NEXTA.exe`, only headway/nonconcurrency/MOW documented | `validator/validate_schedule.py`, `trusted_physics.py` | NEW | Current validates topology, metadata, timing, objective, restrictions, MOW, conflicts, and overtaking. |
| 27. Schedule output | CSV/XML exporters | `output_schedule.xml` | `schedule.json`, `schedule.csv` | REPLACED | Current JSON retains solver/config metadata and CSV feeds plotting. |
| 28. Visualization | no source plotter | `NEXTA.exe` network/string diagram | `plot_space_time.py` | NEW | Current plot is scriptable and includes validator status. |
| 29. Termination | max iterations, gap, no UB improvement | Unknown | LR and B&B budgets/statuses | MODIFIED | Current explicitly reports node/depth budget exhaustion. |
| 30. Language/interface | Visual C++ 2008 monolith | Windows GUI executable | Python 3 + C++17 subprocess/TSV | REPLACED | Portable split architecture; no binary API compatibility. |

## Adapter Boundary

```text
RAS CSV -> Python Arc/Train/MOW -> normalized TSV -> current C++ DP -> TSV result -> Python DPResult
```

This is not the same internal representation as original Fast Train. No evidence supports labelling the current adapter `SAME` or a direct translation of the unavailable `NEXTA.exe` parser.
