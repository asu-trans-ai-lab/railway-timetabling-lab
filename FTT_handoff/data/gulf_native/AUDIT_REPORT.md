# Audit report — gulf_native

Three-level synthetic-data evidence (auditability memo, 2026-08-11).

| level | check | verdict | detail |
|---|---|---|---|
| network | block chaining / connectivity | **PASS** | 25 blocks form one origin->destination chain |
| network | length conservation vs manifest | **PASS** | blocks 216.9 mi vs L3 manifest 216.9 mi |
| network | capacities plausible | **PASS** | cap1 x19, cap2 x6, cap3 x0 |
| network | speeds plausible | **PASS** | 16-39 mph |
| network | meet capability (max siding-free stretch) | **PASS** | longest cap-1 block 17.2 mi (block boundaries = passing points; synthetic where L3 lacks sidings) |
| network | overtake capability | **PASS** | 7% of mileage has cap>=2 |
| network | provenance labels | **PASS** | 15 labeled elements; observed x2; externally_supported x2; inferred x5; synthetic x6 |
| service | class mix vs demand-derived mix | **WARN** | assumed Z:33%/M:50%/G:17% vs derived Z:98%/M:0%/G:2% (max share delta 65%) |
| service | directional balance | **PASS** | derived split EB 57% / WB 43%; scenario is symmetric by construction |
| service | absolute frequency vs derived | **WARN** | scenario ~20 trains/day vs derived 0.5/day. KNOWN GAP: the public L3 demand release is volume-sampled (3.33M cars/70d nationally ~ 400 trains/day US-wide), so derived absolute levels are a FLOOR; mix and balance are the usable evidence. Corridor-level totals rely on corridor_kb / published sources (see provenance + INPUT_ASSUMPTIONS). |
| service | worst-pinch utilization (class-weighted) | **PASS** | 338 exclusive-occupancy min over a 867-min entry span = 39% of the 17.2-mi controlling pinch |
| service | transit-time sanity | **PASS** | manifest free-run 377 min over 217 mi = 35 mph effective |
| operational | free-flow baseline = 0 | **PASS** | intended arrivals reproduce solver run times exactly |
| operational | LR lower bound finite | **PASS** | root LR LB 150.5 |
| operational | all trains routable (B0) | **WARN** | OPEN instance: sequential dispatching cannot route all trains; documented benchmark frontier (B&B LB exists; complete schedule is the Week-4 target) |
