# TrainTimetablingLite resource-reservation prototype

This directory adds a deliberately idealized scheduling kernel **next to** the existing RAS DP/LR/B&B code.  The original solver files are not modified.

The prototype implements the meeting principle directly:

> train = job; segment occupation = task; single-track segment = unary resource; a B&B branch reserves a contested resource for one task before the other; waiting is allowed between tasks; the node subproblem propagates every affected task forward by dynamic programming / longest path.

## Why this is separate from the current `ConflictBB`

The packaged exact B&B currently branches by excluding one exact movement event from one train or the other.  That is a valid negative/negative disjunction, but it is not the intentionally simple **resource reservation** semantics wanted for the debugging benchmark.

`prototype/resource_reservation_lite/` therefore gives us a small transparent kernel before we change the production B&B.

## v0 assumptions

- Single-capacity resource only.  No double-track interpretation.
- Fixed task chain for each train/job (`K=1`).
- Waiting is allowed between every consecutive task.
- Discrete time grid, default `Delta = 1 min`.
- Safety/headway default `H = 3 min`; values not aligned with the grid are snapped upward conservatively.
- Objective: total train travel/flow time from release to completion.
- Reported decomposition: `total travel = free-run total + total delay`.
- No MOW, siding, crossover, speed-mode, hazmat, overtaking, or route-choice logic inside the lite kernel.

These are intentional restrictions for correctness and debugging.  The RAS bridge freezes a path chosen by the existing C++ DP and then applies the idealized resource model to that fixed chain; it is **not** a replacement for the full RAS model.

## Core recursion

For train `i`, task `k` has processing duration `p[i,k]` and uses resource `r[i,k]`.
The train-chain constraint is

```
start(i,k+1) >= start(i,k) + p(i,k)
```

If tasks `A` and `B` conflict on resource `r`, the B&B creates the mutually exclusive children

```
A reserves r first: start(B) >= start(A) + p(A) + H
B reserves r first: start(A) >= start(B) + p(B) + H
```

At a node, all inherited resource reservations plus the train-chain constraints form a directed task graph.  A topological longest-path pass computes all earliest task start times.  A cycle is an infeasible node.

Unresolved resource overlaps are ignored in that pass, so the earliest schedule is a valid node lower bound.  The **earliest unresolved conflict** is selected for branching.

## Guaranteed initial UB

The prototype always constructs a finite incumbent under the v0 assumptions.  It orders trains by `(release time, train id)` and consistently gives the lower-ranked train first use of every shared resource.  All cross-train reservation edges point in the same train-priority direction, so the task graph is acyclic.  The schedule can be conservative but is feasible.

A greedy earliest-conflict repair and a beam search are also included for tighter feasible solutions.

## Positive progress / survival diagnostics

Every B&B trace row reports

- selected earliest conflict,
- resource reservation decision,
- node LB and incumbent UB,
- unresolved conflict count,
- `safe_frontier_min`,
- `safe_tasks / total_tasks`,
- node and gap status.

`safe_frontier_min` is the earliest unresolved conflict time; `safe_tasks` counts protected task occupations fully completed before that frontier.  A fully feasible schedule has all tasks safe.

## Run the five idealized cases

From the bundle root:

```bash
PYTHONPATH=. python -m prototype.resource_reservation_lite.run_all_cases \
  --output prototype/results/idealized
```

Expected exact results at `Delta=1`, `H=3`:

| Case | Trains | Tasks | Root LB | Optimal UB | Delay |
|---|---:|---:|---:|---:|---:|
| 01 one train / one resource | 1 | 1 | 5 | 5 | 0 |
| 02 two trains / one resource | 2 | 2 | 10 | 18 | 8 |
| 03 three trains / one resource | 3 | 3 | 15 | 39 | 24 |
| 04 two trains / three resources | 2 | 6 | 30 | 38 | 8 |
| 05 three trains / three resources | 3 | 9 | 45 | 69 | 24 |

## Compare UB methods and exact best-first B&B

```bash
PYTHONPATH=. python -m prototype.resource_reservation_lite.compare_strategies \
  --instance prototype/resource_reservation_lite/cases/case05_three_trains_three_resources.json \
  --output prototype/results/idealized/case05_strategy_compare.json
```

This compares

- guaranteed global train-priority UB,
- greedy earliest-conflict UB,
- beam widths 1/2/4/8,
- exact best-first resource B&B.

## Run against an RAS fixed-chain bridge

The existing RAS parser and native C++ network DP are reused to obtain each train's independent route.  The route is frozen and converted to a task chain.

```bash
PYTHONPATH=. python -m prototype.resource_reservation_lite.run_ras_fixed_chain \
  --dataset RAS_data-set_1 \
  --output prototype/results/ras1_full \
  --max-nodes 300 \
  --beam-width 8
```

For a faster debugging run:

```bash
PYTHONPATH=. python -m prototype.resource_reservation_lite.run_ras_fixed_chain \
  --dataset RAS_data-set_1 \
  --limit-trains 3 \
  --output prototype/results/ras1_3trains
```

The bridge writes `fixed_chain_instance.json` so the exact same simplified instance can be rerun without calling the native route DP again.

## Tests

```bash
PYTHONPATH=. python -m unittest prototype.resource_reservation_lite.tests.test_lite -v
```

The tests cover all five idealized cases and the 5-minute-grid conservative headway snap.

## Recommended next step

Do **not** immediately merge this branch rule into the current production B&B.  First inspect the five B&B traces in VS Code and verify the intended invariants:

1. every branch is a complete `A-first / B-first` resource disjunction;
2. inherited reservations never disappear;
3. adding a reservation never lowers the node LB;
4. the earliest unresolved conflict and safe-time frontier move forward;
5. a finite UB exists immediately;
6. Cases 1-5 close at gap zero.

After that, port the forward task-graph DP and reservation branch to C++, then reintroduce route/mode choice (`K>1`) and the full RAS resource physics one layer at a time.

## C++17 reference port

A compact C++17 version of the same fixed-chain reservation B&B is included under:

```text
prototype/resource_reservation_lite/cpp/reservation_bb.cpp
```

It is intentionally separate from the existing full network DP so the scheduling logic is easy to inspect in VS Code.  The Python and C++ implementations are regression-tested to produce identical status, root LB, optimal UB, delay, and B&B node counts on Cases 1-5.

```bash
make -C prototype/resource_reservation_lite/cpp
PYTHONPATH=. python -m unittest prototype.resource_reservation_lite.tests.test_cpp_reference -v
```
