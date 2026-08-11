# Audit report — prb_joint_native

Three-level synthetic-data evidence (auditability memo, 2026-08-11).

| level | check | verdict | detail |
|---|---|---|---|
| network | block chaining / connectivity | **PASS** | 27 blocks form one origin->destination chain |
| network | length conservation vs manifest | **PASS** | blocks 177.1 mi vs L3 manifest 177.1 mi |
| network | capacities plausible | **PASS** | cap1 x8, cap2 x14, cap3 x5 |
| network | speeds plausible | **PASS** | 15-42 mph |
| network | meet capability (max siding-free stretch) | **PASS** | longest cap-1 block 6.8 mi (block boundaries = passing points; synthetic where L3 lacks sidings) |
| network | overtake capability | **PASS** | 83% of mileage has cap>=2 |
| network | provenance labels | **PASS** | 14 labeled elements; observed x2; externally_supported x2; inferred x5; synthetic x5 |
| service | class mix vs demand-derived mix | **WARN** | assumed Z:8%/M:17%/G:75% vs derived Z:44%/M:47%/G:9% (max share delta 66%) |
| service | directional balance | **PASS** | derived split EB 68% / WB 32%; scenario is symmetric by construction |
| service | absolute frequency vs derived | **WARN** | scenario ~51 trains/day vs derived 1.5/day. KNOWN GAP: the public L3 demand release is volume-sampled (3.33M cars/70d nationally ~ 400 trains/day US-wide), so derived absolute levels are a FLOOR; mix and balance are the usable evidence. Corridor-level totals rely on corridor_kb / published sources (see provenance + INPUT_ASSUMPTIONS). |
| service | worst-pinch utilization (class-weighted) | **PASS** | 368 exclusive-occupancy min over a 677-min entry span = 54% of the 4.8-mi controlling pinch |
| service | transit-time sanity | **PASS** | manifest free-run 351 min over 177 mi = 30 mph effective |
| operational | free-flow baseline = 0 | **PASS** | intended arrivals reproduce solver run times exactly |
| operational | LR lower bound finite | **PASS** | root LR LB 130.3 |
| operational | all trains routable (B0) | **PASS** | best dispatching objective 321 |
| operational | independent validator | **PASS** | VALID: all checks pass; recomputed objective = 321.0 |
