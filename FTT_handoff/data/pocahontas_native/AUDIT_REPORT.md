# Audit report — pocahontas_native

Three-level synthetic-data evidence (auditability memo, 2026-08-11).

| level | check | verdict | detail |
|---|---|---|---|
| network | block chaining / connectivity | **PASS** | 40 blocks form one origin->destination chain |
| network | length conservation vs manifest | **PASS** | blocks 308.7 mi vs L3 manifest 308.7 mi |
| network | capacities plausible | **PASS** | cap1 x9, cap2 x31, cap3 x0 |
| network | speeds plausible | **PASS** | 19-30 mph |
| network | meet capability (max siding-free stretch) | **PASS** | longest cap-1 block 11.5 mi (block boundaries = passing points; synthetic where L3 lacks sidings) |
| network | overtake capability | **PASS** | 88% of mileage has cap>=2 |
| network | provenance labels | **PASS** | 15 labeled elements; observed x2; externally_supported x2; inferred x5; synthetic x6 |
| service | class mix vs demand-derived mix | **WARN** | assumed Z:12%/M:25%/G:62% vs derived Z:52%/M:46%/G:1% (max share delta 61%) |
| service | directional balance | **WARN** | derived split EB 24% / WB 76%; scenario is symmetric by construction |
| service | absolute frequency vs derived | **WARN** | scenario ~27 trains/day vs derived 2.8/day. KNOWN GAP: the public L3 demand release is volume-sampled (3.33M cars/70d nationally ~ 400 trains/day US-wide), so derived absolute levels are a FLOOR; mix and balance are the usable evidence. Corridor-level totals rely on corridor_kb / published sources (see provenance + INPUT_ASSUMPTIONS). |
| service | worst-pinch utilization (class-weighted) | **PASS** | 550 exclusive-occupancy min over a 862-min entry span = 64% of the 11.5-mi controlling pinch |
| service | transit-time sanity | **PASS** | manifest free-run 737 min over 309 mi = 25 mph effective |
| operational | free-flow baseline = 0 | **PASS** | intended arrivals reproduce solver run times exactly |
| operational | LR lower bound finite | **PASS** | root LR LB 140.6 |
| operational | all trains routable (B0) | **PASS** | best dispatching objective 525 |
| operational | independent validator | **PASS** | VALID: all checks pass; recomputed objective = 525.0 |
