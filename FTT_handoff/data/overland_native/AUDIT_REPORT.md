# Audit report — overland_native

Three-level synthetic-data evidence (auditability memo, 2026-08-11).

| level | check | verdict | detail |
|---|---|---|---|
| network | block chaining / connectivity | **PASS** | 16 blocks form one origin->destination chain |
| network | length conservation vs manifest | **PASS** | blocks 141.8 mi vs L3 manifest 141.8 mi |
| network | capacities plausible | **PASS** | cap1 x0, cap2 x5, cap3 x11 |
| network | speeds plausible | **PASS** | 21-49 mph |
| network | meet capability (max siding-free stretch) | **PASS** | longest cap-1 block 0.0 mi (block boundaries = passing points; synthetic where L3 lacks sidings) |
| network | overtake capability | **PASS** | 100% of mileage has cap>=2 |
| network | provenance labels | **PASS** | 14 labeled elements; observed x2; externally_supported x2; inferred x5; synthetic x5 |
| service | class mix vs demand-derived mix | **WARN** | assumed Z:50%/M:35%/G:15% vs derived Z:33%/M:65%/G:2% (max share delta 30%) |
| service | directional balance | **PASS** | derived split EB 60% / WB 40%; scenario is symmetric by construction |
| service | absolute frequency vs derived | **WARN** | scenario ~116 trains/day vs derived 1.9/day. KNOWN GAP: the public L3 demand release is volume-sampled (3.33M cars/70d nationally ~ 400 trains/day US-wide), so derived absolute levels are a FLOOR; mix and balance are the usable evidence. Corridor-level totals rely on corridor_kb / published sources (see provenance + INPUT_ASSUMPTIONS). |
| service | transit-time sanity | **PASS** | manifest free-run 192 min over 142 mi = 44 mph effective |
| operational | free-flow baseline = 0 | **PASS** | intended arrivals reproduce solver run times exactly |
| operational | LR lower bound finite | **PASS** | root LR LB 62.7 |
| operational | all trains routable (B0) | **PASS** | best dispatching objective 260 |
| operational | independent validator | **PASS** | VALID: all checks pass; recomputed objective = 260.0 |
