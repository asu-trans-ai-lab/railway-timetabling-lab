# Prototype status

## What is implemented

- Fixed-chain train = job, arc occupation = task.
- Unary resource calendar; double-track/siding distinctions intentionally removed in v0.
- 1/2/5-minute-compatible discretization with conservative upward snapping.
- Waiting allowed between every task.
- Forward DP / longest-path node solver.
- Earliest-conflict selection.
- Exact mutually exclusive `A-first / B-first` resource reservation branch.
- Guaranteed finite initial UB using a global train-priority serialization.
- Greedy UB and beam-search UB alternatives.
- Best-first exact B&B with cycle and bound pruning.
- Positive progress trace: safe time frontier and safe task count.
- Total travel, free-run, delay, objective gap, and delay gap reporting.
- Five analytical/small regression cases.
- Bridge from the existing RAS parser and native C++ network DP to frozen fixed chains.

## Verified small cases (`Delta=1`, `H=3`)

| Case | Trains | Tasks | Root LB | Exact UB | Delay | B&B nodes | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| case01 | 1 | 1 | 5 | 5 | 0 | 1 | PROVEN_OPTIMAL |
| case02 | 2 | 2 | 10 | 18 | 8 | 3 | PROVEN_OPTIMAL |
| case03 | 3 | 3 | 15 | 39 | 24 | 11 | PROVEN_OPTIMAL |
| case04 | 2 | 6 | 30 | 38 | 8 | 3 | PROVEN_OPTIMAL |
| case05 | 3 | 9 | 45 | 69 | 24 | 11 | PROVEN_OPTIMAL |

All best schedules have zero lite resource conflicts.

## Fixed-chain RAS bridge smoke results

These runs are **not numerically comparable to the full RAS benchmark** because the bridge freezes independent routes and treats every arc as one unary resource.  Their purpose is to test pipeline survival and finite-UB construction.

A 100-node exact-search budget was used after obtaining the guaranteed initial UB.

| Simplified bridge | Trains | Tasks | Root LB | Guaranteed initial UB | Best-first UB | Beam-4 UB | 100-node global LB | Conflict-free UB? |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| RAS 1 fixed chain | 12 | 600 | 1970 | 4790 | 2064 | 2059 | 2001 | yes |
| RAS 2 fixed chain | 18 | 845 | 2960 | 16967 | 3375 | 3285 | 2988 | yes |
| RAS 3 fixed chain | 20 | 877 | 3054 | 13728 | 3431 | 3466 | 3088 | yes |

The important debugging result is that the idealized resource scheduling pipeline **never lacks a finite UB**.  The initial priority solution is deliberately conservative, and both best-first search and beam search improve it substantially.

## What this does *not* prove

- It does not reproduce the historical full-RAS objective values.
- It does not yet model separate double tracks, sidings/crossovers, MOW, train direction-specific headways, route choice, speed modes, or K-path domains.
- It does not establish full optimality for the 12/18/20-train bridge runs under the small B&B budget.
- It does not modify the production `ConflictBB`; the new branch is isolated for inspection first.

## Recommended coding sequence

1. Review `case02` and `case03` B&B trees line by line in VS Code.
2. Review `case04` to confirm one early reservation propagates all downstream tasks forward.
3. Port `scheduler.py` (task DAG + longest path) and the reservation branch to C++17.
4. Add fixed train types/speed modes while keeping `K=1`.
5. Add station-specific waiting permissions as an explicit rule rather than implicit complexity.
6. Reintroduce the true single-track/double-track resource map from RAS.
7. Only then add `K>1` route/mode choice, where the covering proof for resource branches must be reconsidered.
