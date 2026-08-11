# Audit report — transcon_clovis_native

Three-level synthetic-data evidence (auditability memo, 2026-08-11).

| level | check | verdict | detail |
|---|---|---|---|
| network | block chaining / connectivity | **PASS** | 25 blocks form one origin->destination chain |
| network | length conservation vs manifest | **PASS** | blocks 236.6 mi vs L3 manifest 236.6 mi |
| network | capacities plausible | **PASS** | cap1 x4, cap2 x21, cap3 x0 |
| network | speeds plausible | **PASS** | 28-49 mph |
| network | meet capability (max siding-free stretch) | **PASS** | longest cap-1 block 8.1 mi (block boundaries = passing points; synthetic where L3 lacks sidings) |
| network | overtake capability | **PASS** | 94% of mileage has cap>=2 |
| network | provenance labels | **PASS** | 14 labeled elements; observed x2; externally_supported x3; inferred x5; synthetic x4 |
| service | class mix vs demand-derived mix | **PASS** | assumed Z:44%/M:38%/G:19% vs derived Z:53%/M:44%/G:3% (max share delta 15%) |
| service | directional balance | **PASS** | derived split EB 47% / WB 53%; scenario is symmetric by construction |
| service | absolute frequency vs derived | **WARN** | scenario ~96 trains/day vs derived 4.9/day. KNOWN GAP: the public L3 demand release is volume-sampled (3.33M cars/70d nationally ~ 400 trains/day US-wide), so derived absolute levels are a FLOOR; mix and balance are the usable evidence. Corridor-level totals rely on corridor_kb / published sources (see provenance + INPUT_ASSUMPTIONS). |
| service | worst-pinch utilization (class-weighted) | **PASS** | 418 exclusive-occupancy min over a 481-min entry span = 87% of the 8.1-mi controlling pinch |
| service | transit-time sanity | **PASS** | manifest free-run 318 min over 237 mi = 45 mph effective |
| operational | free-flow baseline = 0 | **PASS** | intended arrivals reproduce solver run times exactly |
| operational | LR lower bound finite | **PASS** | root LR LB 204.4 |
| operational | all trains routable (B0) | **PASS** | best dispatching objective 1379 |
| operational | independent validator | **PASS** | VALID: all checks pass; recomputed objective = 1379.0 |
