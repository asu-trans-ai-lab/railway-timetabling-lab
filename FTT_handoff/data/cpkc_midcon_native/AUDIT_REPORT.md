# Audit report — cpkc_midcon_native

Three-level synthetic-data evidence (auditability memo, 2026-08-11).

| level | check | verdict | detail |
|---|---|---|---|
| network | block chaining / connectivity | **PASS** | 20 blocks form one origin->destination chain |
| network | length conservation vs manifest | **PASS** | blocks 209.8 mi vs L3 manifest 209.8 mi |
| network | capacities plausible | **PASS** | cap1 x18, cap2 x2, cap3 x0 |
| network | speeds plausible | **PASS** | 41-41 mph |
| network | meet capability (max siding-free stretch) | **PASS** | longest cap-1 block 15.9 mi (block boundaries = passing points; synthetic where L3 lacks sidings) |
| network | overtake capability | **PASS** | 1% of mileage has cap>=2 |
| network | provenance labels | **PASS** | 15 labeled elements; observed x2; externally_supported x2; inferred x5; synthetic x6 |
| service | class mix vs demand-derived mix | **PASS** | assumed Z:33%/M:50%/G:17% vs derived Z:47%/M:38%/G:15% (max share delta 14%) |
| service | directional balance | **PASS** | derived split EB 50% / WB 50%; scenario is symmetric by construction |
| service | absolute frequency vs derived | **WARN** | scenario ~21 trains/day vs derived 0.1/day. KNOWN GAP: the public L3 demand release is volume-sampled (3.33M cars/70d nationally ~ 400 trains/day US-wide), so derived absolute levels are a FLOOR; mix and balance are the usable evidence. Corridor-level totals rely on corridor_kb / published sources (see provenance + INPUT_ASSUMPTIONS). |
| service | worst-pinch utilization (class-weighted) | **PASS** | 300 exclusive-occupancy min over a 817-min entry span = 37% of the 15.9-mi controlling pinch |
| service | transit-time sanity | **PASS** | manifest free-run 319 min over 210 mi = 39 mph effective |
| operational | free-flow baseline = 0 | **PASS** | intended arrivals reproduce solver run times exactly |
| operational | LR lower bound finite | **PASS** | root LR LB 107.4 |
| operational | all trains routable (B0) | **WARN** | OPEN instance: sequential dispatching cannot route all trains; documented benchmark frontier (B&B LB exists; complete schedule is the Week-4 target) |
