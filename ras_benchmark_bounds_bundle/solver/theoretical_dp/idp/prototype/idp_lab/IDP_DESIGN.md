# IDP Design Contract

## 1. Physical representation

Train `i` is job `J_i`. Its physical route is a chain of tasks

`a_i0 -> a_i1 -> ... -> a_iK`.

Each task occupies exactly one unary resource (single-track block / machine) for a snapped processing time.

## 2. Resource-reservation branch

For a conflict between tasks A and B on resource r, the exact branch is

- child 1: A reserves r before B;
- child 2: B reserves r before A.

Computationally this is stored as a difference constraint, but semantically it is a **resource reservation**, not a global train precedence.

## 3. Forward-progress invariant

At each B&B node, detect the earliest unresolved conflict. Track:

- safe time frontier;
- safe task count / total task count;
- number of inherited reservations;
- current LB and incumbent UB.

The branch should make chronological progress. If an inherited fixed reservation is violated or an earlier already-safe conflict reappears, treat that as a debugging alarm.

## 4. Metrics

Do not report one ambiguous “gap.” Report:

- objective absolute gap = UB - LB;
- objective relative gap = (UB-LB)/UB;
- free-run total;
- total delay;
- average train delay;
- max train delay;
- p95 train delay;
- max/average delay ratio;
- nodes generated / expanded / pruned;
- nodes and time to first UB;
- conflict-history features.

## 5. Progressive complexity

Do not add the next dimension until the current one passes all canonical cases:

1. fixed chain;
2. waiting-buffer restrictions;
3. train heterogeneity;
4. operating modes;
5. K paths;
6. siding/double-track rules;
7. full RAS details.

LR, branch-and-price and conflict learning must reuse the same physical chain and resource semantics so results remain comparable.
