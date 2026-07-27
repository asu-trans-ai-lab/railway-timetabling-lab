# Benchmark Run Summary

The packaged run uses single-process, single-thread C++17. Full and reduced DP use the same transition generator and hard constraints; only the quadratic corridor filter differs.

| Case | Agents | Initial conflicts | Final conflicts | Full states | Reduced states | Reduction | Full time (s) | Reduced time (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ras_corridor_12 | 12 | 6 | 0 | 72,530 | 12,066 | 83.36% | 0.0142 | 0.0027 |
| ras_corridor_24 | 24 | 16 | 0 | 1,908,083 | 117,915 | 93.82% | 0.5969 | 0.0428 |
| ras_corridor_48 | 48 | 32 | 0 | 5,323,508 | 341,498 | 93.59% | 1.5785 | 0.1194 |
| flatland_like_20x20_40 | 40 | 20 | 0 | 233,407 | 86,145 | 63.09% | 0.0457 | 0.0194 |
| flatland_like_30x30_80 | 80 | 40 | 0 | 461,532 | 169,620 | 63.25% | 0.0933 | 0.0428 |

## Interpretation

- Every packaged case eliminates the hard conflicts in the independent schedules.
- The RAS-style cases retain 6.2%–16.6% of full joint states.
- The Flatland-like cases retain about 36.8%–36.9% of full joint states.
- These are synthetic, conflict-localized cases. The results establish a scalable prototype mechanism, not production railway performance.
- Native Flatland replay and malfunction experiments remain the next integration step.