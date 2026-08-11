# Audit report — moffat_native

Three-level synthetic-data evidence (auditability memo, 2026-08-11).

| level | check | verdict | detail |
|---|---|---|---|
| network | block chaining / connectivity | **PASS** | 25 blocks form one origin->destination chain |
| network | length conservation vs manifest | **PASS** | blocks 185.8 mi vs L3 manifest 185.8 mi |
| network | capacities plausible | **PASS** | cap1 x21, cap2 x4, cap3 x0 |
| network | speeds plausible | **PASS** | 14-49 mph |
| network | meet capability (max siding-free stretch) | **PASS** | longest cap-1 block 14.2 mi (block boundaries = passing points; synthetic where L3 lacks sidings) |
| network | overtake capability | **PASS** | 4% of mileage has cap>=2 |
| network | provenance labels | **PASS** | 15 labeled elements; observed x2; externally_supported x2; inferred x5; synthetic x6 |
| service | class mix vs demand-derived mix | **WARN** | assumed Z:20%/M:40%/G:40% vs derived Z:35%/M:63%/G:1% (max share delta 39%) |
| service | directional balance | **PASS** | derived split EB 32% / WB 68%; scenario is symmetric by construction |
| service | absolute frequency vs derived | **WARN** | scenario ~14 trains/day vs derived 4.7/day. KNOWN GAP: the public L3 demand release is volume-sampled (3.33M cars/70d nationally ~ 400 trains/day US-wide), so derived absolute levels are a FLOOR; mix and balance are the usable evidence. Corridor-level totals rely on corridor_kb / published sources (see provenance + INPUT_ASSUMPTIONS). |
| service | worst-pinch utilization (class-weighted) | **PASS** | 400 exclusive-occupancy min over a 1017-min entry span = 39% of the 13.9-mi controlling pinch |
| service | transit-time sanity | **PASS** | manifest free-run 422 min over 186 mi = 26 mph effective |
| operational | free-flow baseline = 0 | **PASS** | intended arrivals reproduce solver run times exactly |
| operational | LR lower bound finite | **PASS** | root LR LB 145.8 |
| operational | all trains routable (B0) | **WARN** | OPEN instance: sequential dispatching cannot route all trains; documented benchmark frontier (B&B LB exists; complete schedule is the Week-4 target) |
