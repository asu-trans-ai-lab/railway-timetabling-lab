# IDP Lab v2 — Verified Status

## Scope

This version intentionally remains at the idealized single/unary-resource, fixed-chain, waiting-allowed level. It is a reference/meta-ground, not yet a full RAS replacement.

## Verified canonical results

| Case | Trains | Tasks | IDP-1 states | Exact objective (IDP-1 = IDP-2) | IDP-2 B&B nodes | IDP-4 best dual (80 iters) |
|---|---:|---:|---:|---:|---:|---:|
| case01 | 1 | 1 | 2 | 5 | 1 | 5.000000 |
| case02 | 2 | 2 | 5 | 18 | 3 | 18.000000 (numerical tolerance) |
| case03 | 3 | 3 | 16 | 39 | 11 | 39.000000 (numerical tolerance) |
| case04 | 2 | 6 | 37 | 38 | 3 | 38.000000 (numerical tolerance) |
| case05 | 3 | 9 | 634 | 69 | 11 | 69.000000 (numerical tolerance) |

The IDP-1 state count already illustrates why the full global Bellman state is pedagogically useful but not intended as the medium/large-scale solver: the 3-train/3-resource chain requires 634 memoized states even in this tiny case.

## First-UB behavior

For `case05_three_trains_three_resources`:

- DFS reaches the first feasible UB = 69 after 7 evaluated nodes.
- Best-first reaches UB = 69 after 11 evaluated nodes.
- Beam width 1 reaches UB = 69 after 7 evaluated nodes.
- Beam widths 2/4/8 reach UB = 69 after 11 evaluated nodes.

This deliberately separates **incumbent construction behavior** from the exact B&B proof, whose baseline already installs a guaranteed finite constructive UB.

## Regression status

- `python -m pytest -q prototype/idp_lab/tests prototype/resource_reservation_lite/tests tests`: **12 passed**.
- original package `bash run_tests.sh`: **PASS — datasets, native DP, Lagrangian relaxation, and conflict B&B**.

## Deliberately deferred

The following are not yet mixed into the IDP baseline:

1. no-wait / restricted-wait stations;
2. heterogeneous train processing times;
3. multiple operating modes;
4. K-path route choice;
5. siding and double-track rules;
6. full RAS physical constraints;
7. branch-and-price master/pricing integration;
8. learned conflict ordering.

They should be added one at a time after the preceding level passes the canonical regression suite.
