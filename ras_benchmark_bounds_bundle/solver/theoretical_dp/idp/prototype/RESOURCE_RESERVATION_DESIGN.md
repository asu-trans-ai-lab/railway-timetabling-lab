# Resource reservation B&B: mathematical design

## 1. Fixed-chain model

Train/job `i` is a chain of tasks `k=1,...,m_i`.  Task `(i,k)` occupies unary resource `r(i,k)` for snapped duration `p(i,k)`.

Let `s(i,k)` be its start time and `rho_i` its release time.

Train-chain constraints:

```
s(i,1) >= rho_i
s(i,k+1) >= s(i,k) + p(i,k)
```

Waiting is therefore implicit and can occur before every task.

## 2. Resource conflict

For two tasks of different trains using the same resource, protected intervals are

```
[s_a, s_a + p_a + H)
[s_b, s_b + p_b + H)
```

A conflict exists when the protected intervals overlap.

## 3. Mutually exclusive resource branch

For selected conflict `(a,b,r)`:

```
child A: reserve r for a before b
         s_b >= s_a + p_a + H

child B: reserve r for b before a
         s_a >= s_b + p_b + H
```

With fixed chains, both tasks must use resource `r`, so every feasible schedule is in at least one child.  The two children are mutually exclusive except at impossible simultaneous strict order.

## 4. Node DP / longest path

All train-chain and resource-reservation constraints are inequalities

```
s_v >= s_u + lag(u,v)
```

If their directed graph is acyclic, the componentwise earliest schedule is obtained by a topological longest-path DP.  If the graph is cyclic, the node is infeasible.

Because unresolved resource conflicts are relaxed, the total travel time of the earliest schedule is a lower bound on every complete timetable in that B&B node.

## 5. Objective

For final task completion `C_i`:

```
travel_i = C_i - rho_i
Z = sum_i travel_i
Z0 = sum_(i,k) p(i,k)
D = Z - Z0
```

The exact solver reports both objective gap and delay gap.

## 6. Search policy

- Node selection: best lower bound; ties favor more safe tasks and later safe frontier.
- Conflict selection: earliest unresolved protected resource overlap.
- UB: guaranteed train-priority serialization, optionally improved by greedy/beam search.
- Prune by cycle or `LB >= UB`.

## 7. Chronological progress metric

Let `tau_safe` be the earliest unresolved conflict time.  A task is counted safe if its protected interval is complete by `tau_safe`.  A feasible timetable has all tasks safe.

This is a diagnostic, not a proof device.  It makes forward progress visible during debugging.

## 8. Scope boundary

The lite proof assumes fixed task chains and unary resources.  When `K>1` route/mode choices are introduced, a branch must remain covering even if one train can avoid the contested resource.  That extension should be designed separately; do not reuse the fixed-chain covering proof silently.
