# RAS Weekly Report Archive

These folders preserve **report milestones**, not four artificial code snapshots. The implementation evolved continuously and current source is maintained once at the bundle root.

```text
2026-08-11: RAS problem/data semantics and initial feasibility framing
      |
      v
2026-08-17 archive (deck cover: 2026-08-18): Python/C++ DP and LR architecture
      |
      v
2026-08-25: Dataset 3 LR lower bound and validated feasible upper bound
      |
      v
2026-09-01: P1-P3 scorecard, stronger bound, recovery, and B&B diagnostics
      |
      v
current ras_benchmark_bounds_bundle
```

| Archive folder | Date shown in report | RAS milestone | Reproduction classification |
|---|---|---|---|
| `2026-08-11/` | 2026-08-11 | Problem definition, hard/soft distinction, RAS schema comparison, solver concepts | PARTIALLY REPRODUCIBLE; no historical result-producing commit identified |
| `2026-08-17/` | 2026-08-18 | Python/C++ single-train DP, LR decomposition, fluid queue price loop, LB/UB framing | REPRODUCIBLE WITH CURRENT IMPLEMENTATION at architecture level; exact historical implementation unknown |
| `2026-08-25/` | 2026-08-25 | Dataset 3 LB 3361.2895, validated UB 5869.9012, sequential recovery and proposed pruning | HISTORICAL REPORTED RESULT; original run artifact not located |
| `2026-09-01/` | 2026-09-01 | P1-P3 validated scorecard; D3 LB strengthened to 3366.818 and UB to 4797.846; B&B diagnostics | HISTORICAL REPORTED RESULT; values are corroborated in later source constants but exact producing artifacts/commit are not established |

The archived presentations are unmodified originals. Some reports contain broader corridor material; those slides remain embedded only to preserve report integrity. No corresponding corridor or Version B code/data was added to this ANL/RAS package.
