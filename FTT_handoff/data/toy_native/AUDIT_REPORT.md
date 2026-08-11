# Audit report — toy_native

Three-level synthetic-data evidence (auditability memo, 2026-08-11).

| level | check | verdict | detail |
|---|---|---|---|
| network | block chaining / connectivity | **PASS** | 3 blocks form one origin->destination chain |
| network | length conservation vs manifest | **SKIP** | no manifest (literature/toy instance) |
| network | capacities plausible | **PASS** | cap1 x3, cap2 x0, cap3 x0 |
| network | speeds plausible | **PASS** | 60-60 mph |
| network | meet capability (max siding-free stretch) | **PASS** | longest cap-1 block 2.0 mi (block boundaries = passing points; synthetic where L3 lacks sidings) |
| network | overtake capability | **PASS** | 0% of mileage has cap>=2 |
| network | provenance labels | **PASS** | 11 labeled elements; observed x0; externally_supported x2; inferred x4; synthetic x5 |
| service | demand-derived service | **SKIP** | no service_derivation.csv |
| service | worst-pinch utilization (class-weighted) | **SKIP** | entry span too short for a meaningful utilization ratio |
| service | transit-time sanity | **PASS** | manifest free-run 9 min over 6 mi = 40 mph effective |
| operational | free-flow baseline = 0 | **PASS** | intended arrivals reproduce solver run times exactly |
| operational | LR lower bound finite | **PASS** | root LR LB 10.7 |
| operational | all trains routable (B0) | **PASS** | best dispatching objective 12 |
| operational | independent validator | **PASS** | VALID: all checks pass; recomputed objective = 12.0 |
