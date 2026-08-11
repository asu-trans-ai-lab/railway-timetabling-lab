# Audit report — cn_icmain_native

Three-level synthetic-data evidence (auditability memo, 2026-08-11).

| level | check | verdict | detail |
|---|---|---|---|
| network | block chaining / connectivity | **PASS** | 22 blocks form one origin->destination chain |
| network | length conservation vs manifest | **PASS** | blocks 130.3 mi vs L3 manifest 130.3 mi |
| network | capacities plausible | **PASS** | cap1 x12, cap2 x10, cap3 x0 |
| network | speeds plausible | **PASS** | 25-55 mph |
| network | meet capability (max siding-free stretch) | **PASS** | longest cap-1 block 14.6 mi (block boundaries = passing points; synthetic where L3 lacks sidings) |
| network | overtake capability | **PASS** | 16% of mileage has cap>=2 |
| network | provenance labels | **PASS** | 15 labeled elements; observed x2; externally_supported x2; inferred x5; synthetic x6 |
| service | class mix vs demand-derived mix | **PASS** | assumed Z:40%/M:40%/G:20% vs derived Z:62%/M:34%/G:4% (max share delta 22%) |
| service | directional balance | **PASS** | derived split EB 45% / WB 55%; scenario is symmetric by construction |
| service | absolute frequency vs derived | **WARN** | scenario ~45 trains/day vs derived 1.2/day. KNOWN GAP: the public L3 demand release is volume-sampled (3.33M cars/70d nationally ~ 400 trains/day US-wide), so derived absolute levels are a FLOOR; mix and balance are the usable evidence. Corridor-level totals rely on corridor_kb / published sources (see provenance + INPUT_ASSUMPTIONS). |
| service | worst-pinch utilization (class-weighted) | **PASS** | 360 exclusive-occupancy min over a 645-min entry span = 56% of the 14.6-mi controlling pinch |
| service | transit-time sanity | **PASS** | manifest free-run 160 min over 130 mi = 49 mph effective |
| operational | free-flow baseline = 0 | **PASS** | intended arrivals reproduce solver run times exactly |
| operational | LR lower bound finite | **PASS** | root LR LB 135.1 |
| operational | all trains routable (B0) | **PASS** | best dispatching objective 1910 |
| operational | independent validator | **PASS** | VALID: all checks pass; recomputed objective = 1910.0 |
