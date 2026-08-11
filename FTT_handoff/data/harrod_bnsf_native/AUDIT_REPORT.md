# Audit report — harrod_bnsf_native

Three-level synthetic-data evidence (auditability memo, 2026-08-11).

| level | check | verdict | detail |
|---|---|---|---|
| network | block chaining / connectivity | **PASS** | 6 blocks form one origin->destination chain |
| network | length conservation vs manifest | **SKIP** | no manifest (literature/toy instance) |
| network | capacities plausible | **PASS** | cap1 x0, cap2 x6, cap3 x0 |
| network | speeds plausible | **PASS** | 24-49 mph |
| network | meet capability (max siding-free stretch) | **PASS** | longest cap-1 block 0.0 mi (block boundaries = passing points; synthetic where L3 lacks sidings) |
| network | overtake capability | **PASS** | 100% of mileage has cap>=2 |
| network | provenance labels | **PASS** | 11 labeled elements; observed x0; externally_supported x2; inferred x4; synthetic x5 |
| service | demand-derived service | **SKIP** | no service_derivation.csv |
| service | transit-time sanity | **PASS** | manifest free-run 108 min over 56 mi = 31 mph effective |
| operational | free-flow baseline = 0 | **PASS** | intended arrivals reproduce solver run times exactly |
| operational | LR lower bound finite | **PASS** | root LR LB 84.0 |
| operational | all trains routable (B0) | **PASS** | best dispatching objective 421 |
| operational | independent validator | **PASS** | VALID: all checks pass; recomputed objective = 421.0 |
