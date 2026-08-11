# Audit report — transcon_clovis_native_fullday

Three-level synthetic-data evidence (auditability memo, 2026-08-11).

| level | check | verdict | detail |
|---|---|---|---|
| network | block chaining / connectivity | **PASS** | 25 blocks form one origin->destination chain |
| network | length conservation vs manifest | **PASS** | blocks 236.6 mi vs L3 manifest 236.6 mi |
| network | capacities plausible | **PASS** | cap1 x4, cap2 x21, cap3 x0 |
| network | speeds plausible | **PASS** | 28-49 mph |
| network | meet capability (max siding-free stretch) | **PASS** | longest cap-1 block 8.1 mi (block boundaries = passing points; synthetic where L3 lacks sidings) |
| network | overtake capability | **PASS** | 94% of mileage has cap>=2 |
| network | provenance labels | **PASS** | 14 labeled elements; observed x2; externally_supported x2; inferred x5; synthetic x5 |
| service | class mix vs demand-derived mix | **PASS** | assumed Z:45%/M:39%/G:16% vs derived Z:53%/M:44%/G:3% (max share delta 13%) |
| service | directional balance | **PASS** | derived split EB 47% / WB 53%; scenario is symmetric by construction |
| service | absolute frequency vs derived | **WARN** | scenario ~122 trains/day vs derived 4.9/day. KNOWN GAP: the public L3 demand release is volume-sampled (3.33M cars/70d nationally ~ 400 trains/day US-wide), so derived absolute levels are a FLOOR; mix and balance are the usable evidence. Corridor-level totals rely on corridor_kb / published sources (see provenance + INPUT_ASSUMPTIONS). |
| service | worst-pinch utilization (class-weighted) | **WARN** | 1140 exclusive-occupancy min over a 1040-min entry span = 110% of the 8.1-mi controlling pinch (documented over-saturated stress scenario) |
| service | transit-time sanity | **PASS** | manifest free-run 318 min over 237 mi = 45 mph effective |
| operational | free-flow baseline = 0 | **PASS** | intended arrivals reproduce solver run times exactly |
| operational | LR lower bound finite | **PASS** | root LR LB 1087.1 |
| operational | all trains routable (B0) | **WARN** | OPEN instance: sequential dispatching cannot route all trains; documented benchmark frontier (B&B LB exists; complete schedule is the Week-4 target) |
