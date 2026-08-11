# Audit report — panhandle_native

Three-level synthetic-data evidence (auditability memo, 2026-08-11).

| level | check | verdict | detail |
|---|---|---|---|
| network | block chaining / connectivity | **PASS** | 24 blocks form one origin->destination chain |
| network | length conservation vs manifest | **PASS** | blocks 207.0 mi vs L3 manifest 207.0 mi |
| network | capacities plausible | **PASS** | cap1 x2, cap2 x22, cap3 x0 |
| network | speeds plausible | **PASS** | 14-49 mph |
| network | meet capability (max siding-free stretch) | **PASS** | longest cap-1 block 1.2 mi (block boundaries = passing points; synthetic where L3 lacks sidings) |
| network | overtake capability | **PASS** | 99% of mileage has cap>=2 |
| network | provenance labels | **PASS** | 14 labeled elements; observed x2; externally_supported x2; inferred x5; synthetic x5 |
| service | class mix vs demand-derived mix | **PASS** | assumed Z:44%/M:44%/G:12% vs derived Z:50%/M:41%/G:9% (max share delta 6%) |
| service | directional balance | **PASS** | derived split EB 54% / WB 46%; scenario is symmetric by construction |
| service | absolute frequency vs derived | **WARN** | scenario ~80 trains/day vs derived 8.3/day. KNOWN GAP: the public L3 demand release is volume-sampled (3.33M cars/70d nationally ~ 400 trains/day US-wide), so derived absolute levels are a FLOOR; mix and balance are the usable evidence. Corridor-level totals rely on corridor_kb / published sources (see provenance + INPUT_ASSUMPTIONS). |
| service | worst-pinch utilization (class-weighted) | **PASS** | 128 exclusive-occupancy min over a 577-min entry span = 22% of the 1.2-mi controlling pinch |
| service | transit-time sanity | **PASS** | manifest free-run 276 min over 207 mi = 45 mph effective |
| operational | free-flow baseline = 0 | **PASS** | intended arrivals reproduce solver run times exactly |
| operational | LR lower bound finite | **PASS** | root LR LB 149.5 |
| operational | all trains routable (B0) | **PASS** | best dispatching objective 436 |
| operational | independent validator | **PASS** | VALID: all checks pass; recomputed objective = 436.0 |
