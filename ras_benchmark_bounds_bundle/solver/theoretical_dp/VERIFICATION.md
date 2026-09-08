# Canonical verification

Verification date: 2026-09-08. Tests used the preserved v2 files without algorithm edits. Generated artifacts are under `results/theoretical_dp/`.

## Commands

```bash
make test
.venv/bin/python solver/theoretical_dp/run_tests.py
.venv/bin/python solver/theoretical_dp/run_all_idp.py
PYTHONPATH=solver/theoretical_dp/idp \
  .venv/bin/python -m prototype.resource_reservation_lite.run_all_cases \
  --output results/theoretical_dp/idp2
make test
```

The ZIP included a Linux x86-64 `reservation_bb` executable. It was excluded; the C++ regression rebuilt `reservation_bb.cpp` locally with `c++ -std=c++17 -O2 -Wall -Wextra -pedantic` and then passed against Python on Cases 1-5.

## IDP-0 and IDP-1

| Case | IDP-0 first-train travel | IDP-1 exact objective | IDP-1 states | IDP-2 optimum | Exact match |
|---|---:|---:|---:|---:|---|
| case01 | 5 | 5 | 2 | 5 | yes |
| case02 | 5 | 18 | 5 | 18 | yes |
| case03 | 5 | 39 | 16 | 39 | yes |
| case04 | 15 | 38 | 37 | 38 | yes |
| case05 | 15 | 69 | 634 | 69 | yes |

The separate IDP-0 calendar test placed train A on the shared resource during `[0,5)` and correctly pushed train B to start at 8 and finish at 13 under the 3-minute protected headway.

## IDP-2 exact reservation B&B

| Case | Root LB | Initial UB | Final LB | Final UB | Gap | Nodes | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| case01 | 5 | 5 | 5 | 5 | 0 | 1 | PROVEN_OPTIMAL |
| case02 | 10 | 18 | 18 | 18 | 0 | 3 | PROVEN_OPTIMAL |
| case03 | 15 | 39 | 39 | 39 | 0 | 11 | PROVEN_OPTIMAL |
| case04 | 30 | 38 | 38 | 38 | 0 | 3 | PROVEN_OPTIMAL |
| case05 | 45 | 69 | 69 | 69 | 0 | 11 | PROVEN_OPTIMAL |

Every final schedule reports zero conflicts. Python/C++ status, root LB, optimum, delay, and node count matched on all five cases.

## IDP-3 first-feasible search

Case05 results; elapsed times are machine/run dependent and retained in the JSON output.

| Strategy | First feasible UB | Nodes to first UB |
|---|---:|---:|
| DFS | 69 | 7 |
| best-first | 69 | 11 |
| beam width 1 | 69 | 7 |
| beam width 2 | 69 | 11 |
| beam width 4 | 69 | 11 |
| beam width 8 | 69 | 11 |

## IDP-4 LR and pricing DP

| Case | Best dual LB | UB | Absolute gap after 80 iterations |
|---|---:|---:|---:|
| case01 | 5.000000000 | 5 | 0 |
| case02 | 17.999999993 | 18 | 0.000000007 |
| case03 | 38.999999983 | 39 | 0.000000017 |
| case04 | 37.999999984 | 38 | 0.000000016 |
| case05 | 68.999999940 | 69 | 0.000000060 |

The original IDP-4 tolerance test on case02 passed: dual no greater than UB, dual no lower than free-run bound, and dual gap below `1e-4`.

## Regression result

- Production before integration: `PASS: mini + RAS adapter/DP/LR/B&B/validator/visualization pipeline`.
- Zhou Python tests: 11 passed-equivalent checks (5 IDP test functions and 6 reservation unit tests).
- Zhou C++ reference regression: 1 test passed across all five cases.
- Production after integration: `PASS: mini + RAS adapter/DP/LR/B&B/validator/visualization pipeline`.
