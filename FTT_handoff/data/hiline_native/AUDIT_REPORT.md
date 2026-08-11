# Audit report — hiline_native

Three-level synthetic-data evidence (auditability memo, 2026-08-11).

| level | check | verdict | detail |
|---|---|---|---|
| network | block chaining / connectivity | **PASS** | 21 blocks form one origin->destination chain |
| network | length conservation vs manifest | **PASS** | blocks 203.8 mi vs L3 manifest 203.8 mi |
| network | capacities plausible | **PASS** | cap1 x13, cap2 x8, cap3 x0 |
| network | speeds plausible | **PASS** | 14-55 mph |
| network | meet capability (max siding-free stretch) | **PASS** | longest cap-1 block 17.6 mi (block boundaries = passing points; synthetic where L3 lacks sidings) |
| network | overtake capability | **PASS** | 27% of mileage has cap>=2 |
| network | provenance labels | **PASS** | 15 labeled elements; observed x2; externally_supported x2; inferred x5; synthetic x6 |
| service | class mix vs demand-derived mix | **PASS** | assumed Z:38%/M:38%/G:25% vs derived Z:36%/M:56%/G:8% (max share delta 19%) |
| service | directional balance | **PASS** | derived split EB 51% / WB 49%; scenario is symmetric by construction |
| service | absolute frequency vs derived | **WARN** | scenario ~26 trains/day vs derived 3.6/day. KNOWN GAP: the public L3 demand release is volume-sampled (3.33M cars/70d nationally ~ 400 trains/day US-wide), so derived absolute levels are a FLOOR; mix and balance are the usable evidence. Corridor-level totals rely on corridor_kb / published sources (see provenance + INPUT_ASSUMPTIONS). |
| service | worst-pinch utilization (class-weighted) | **PASS** | 340 exclusive-occupancy min over a 897-min entry span = 38% of the 17.6-mi controlling pinch |
| service | transit-time sanity | **PASS** | manifest free-run 250 min over 204 mi = 49 mph effective |
| operational | free-flow baseline = 0 | **PASS** | intended arrivals reproduce solver run times exactly |
| operational | LR lower bound finite | **PASS** | root LR LB 120.4 |
| operational | all trains routable (B0) | **WARN** | sequential dispatching cannot route all trains, but a complete feasible schedule exists via branch-and-bound (see CORRIDOR_COMPARISON.md) — documented |
