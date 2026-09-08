# Historical RAS bounds retained as a reference, not as lite-model targets

The attached benchmark bundle already contains a September 1, 2026 historical scorecard under `weekly_reports/2026-09-01/`:

| Full historical benchmark | Trains | Reported LB | Validated UB | Reported gap | Conflicts |
|---|---:|---:|---:|---:|---:|
| RAS 1 | 12 | 1817.10 | 2134.21 | 14.86% | 0 |
| RAS 2 | 18 | 2653.81 | 6281.93 | 57.75% | 0 |
| RAS 3 | 20 | 3366.82 | 4797.85 | 29.83% | 0 |

Those rows are explicitly marked in the original bundle as **historical reported results** whose exact producing snapshots were not fully recovered.  They are still useful as an important sanity fact: all three full benchmark datasets have previously had finite, validator-clean feasible schedules reported.

The new `resource_reservation_lite` fixed-chain bridge has a different feasible set and objective semantics, so its numerical LB/UB values **must not be compared directly** with the historical scorecard.  Its purpose is narrower: prove that the resource-reservation scheduling kernel can always obtain a finite feasible incumbent, expose chronological progress, and close the small canonical cases exactly before full RAS physics is reintroduced.
