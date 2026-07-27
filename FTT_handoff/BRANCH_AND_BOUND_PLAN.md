# Plan — Branch-and-Bound on the N-track LR to Close the Optimality Gap

## 0. Goal & current state
The N-track Lagrangian relaxation (`n_track/fasttrain.cpp`) reproduces Meng & Zhou: on RAS Data Set 3 it
gives **LB ≈ 2279, UB ≈ 3803, gap ≈ 40%** after 10 subgradient iterations. The LR alone *cannot* close
that gap — its dual bound is fixed by the relaxation, and the priority-rule UB is heuristic. To obtain a
**provably optimal (or small-gap) timetable** we wrap the LR in a **branch-and-bound (LR-based B&B)**, the
same recipe Zhou & Zhong (2007) used to get guaranteed optimality on single-track (already reproduced in
`single_track/ttbl_bnb.py`). This is also the regime where our spectral/tensor branching-screening is
meant to pay off (large candidate sets, true duals).

## 1. Why LR-based B&B closes the gap
- The LR **dualizes the link-capacity constraints** `Σ_f y_f(l,t) ≤ Cap(l,t)`. At the root the relaxed
  optimum is capacity-*infeasible* (two trains share a single-track cell) → that infeasibility is the gap.
- **Branching adds constraints to the per-train subproblems** that remove a specific capacity conflict,
  tightening the relaxation. Each branch raises the node LB; the tree's leaves are capacity-feasible
  (LB = UB). Global LB = min over open nodes; global UB = best incumbent; **gap = (UB − LB)/UB → 0**.
- The LR gives a **strong, cheap bound at every node** (warm-started from the parent) — that strength is
  exactly why LR-based B&B converges in practice for railway scheduling.

## 2. The B&B framework (to build on `fasttrain.cpp`)
**Node** `v` = the LR problem + a set of **branching restrictions** `R(v)` (per-train forbidden cells /
precedences). Root: `R = ∅`.

**Bounding at a node** (reuse the existing LR loop):
1. Run the subgradient LR for a few iterations *with the node's restrictions applied to each train's
   time-dependent shortest path* (warm-start multipliers `ρ` from the parent). → **node LB** `= ΣtripPrice
   − Σprice` (the existing formula), and per-train solutions.
2. Run the priority-rule heuristic on the relaxed solution → a feasible schedule → candidate **incumbent
   UB**. Update global UB if better.
3. **Fathom** `v` if `nodeLB ≥ globalUB − ε` (no improving solution below it) or if the relaxed solution is
   already capacity-feasible (a feasible leaf → update incumbent, fathom).

**Branching** (resolve one capacity conflict — meet/pass order):
1. From the node's relaxed solution, find a **violated cell** `(l, t*)` with `Σ_f y_f(l,t*) > Cap(l,t*)`
   (the existing `usage[l][t] − cap[l][t] > 0`). Pick the **most violated / earliest** one (and, later,
   the screening-selected one — §4).
2. Identify the two trains `i, j` occupying `l` around `t*` (a meet on a bidirectional single-track link,
   or an overtake on a same-direction link). Their disjunction is **"i traverses l before j"** OR **"j
   before i"** — a complete, exclusive 2-way split.
3. Create two children by **time-windowed forbidding** in the per-train SP:
   - Child A (`i ≺ j` on `l`): forbid `j` from occupying `l` during `[enter_i, exit_i + headway)` (push j
     after i clears). Add `(l, [enter_i, exit_i+h))` to `R_j(vA)`.
   - Child B (`j ≺ i` on `l`): symmetric, forbid `i` during `j`'s window.
   Both children are valid (their union = parent); each removes the conflict at `(l,t*)`.
4. The forbidding is enforced in `tdsp()` exactly like the existing siding/hazmat `ForbiddenLinkIDs`
   mechanism, but **time-windowed per train**: when expanding train `f`, skip any traversal of link `l`
   whose occupancy window intersects a forbidden interval in `R_f(v)`.

**Search**: best-first by node LB (closes the global gap fastest) with a stack fallback for memory; DFS to
find a good incumbent early, then best-bound. Standard fathom/incumbent bookkeeping; stop at gap ≤ ε or
node/time budget.

## 3. Implementation in the clean C++ (`fasttrain.cpp`)
The clean rewrite already exposes everything B&B needs as plain arrays/functions:
- `tdsp()` — add a `const Restrictions* R` arg; in the arc loop, `continue` if the traversal of link `l`
  over `[t, arrive+headway)` hits any forbidden interval in `R[train][l]`. (Reuse the existing per-arc
  window loop.)
- Wrap the current LR loop into `double bound_node(const Node& v, Solution& sol, double& ub)` returning
  the node LB and a feasible UB, warm-starting `price` from `v.parentPrice`.
- `Node { Restrictions R; vector<vector<double>> price0; double lb; }`. A `std::priority_queue` of open
  nodes by LB (best-first).
- `find_conflict(usage, cap, sol) -> (l, t*, i, j)`; `make_children(v, l, t*, i, j) -> {vA, vB}` adding the
  two forbidden windows.
- Driver: push root; loop pop-lowest-LB → bound → fathom/branch → update global LB/UB/gap; print the gap
  trajectory; export the optimal timetable XML (the export hook already exists).

Memory/perf: warm-start `ρ` per node (huge speedup, like Zhou-Zhong); cap LR iterations per node (e.g.
3-5) since bounds need only be *good*, not converged; incremental SP re-solve only for the two
restricted trains where possible.

## 4. The research hook — spectral screening selects the branch (the payoff)
A node often has **many** violated cells / candidate conflicts. Full strong branching would bound both
children of every candidate (expensive). Instead, plug in the validated screening:
- Score each candidate conflict by the **true-dual gradient-weighted** signal (it's already in
  `nt_screen.py`: separate the conflicting trains' high-`ρ` vs low-`ρ` exposure using the node's real
  multipliers `ρ_{l,t}`), plus the rich features / response-tensor coordinates (`single_track/` research).
- **Screen to the top-K conflicts, bound those children exactly (cheap LR), branch on the best.** This is
  *screening-before-certification* applied to B&B branch selection — the contribution. The N-track regime
  (large candidate sets + true LR duals) is exactly where single-track found the dual layer underused.
- Metrics to report: Containment@K (does the screen pick the conflict full strong branching would?),
  node-count / LR-solve reduction vs full strong branching and vs the most-violated rule, and the
  **gap-closing curve** (global gap vs nodes/time).

## 5. Milestones
- **B1 — Restrictions in `tdsp()`** ✅ **done**: `using Restrictions = vector<vector<pair<int,int>>>`
  ([nL] → forbidden `[t0,t1)` windows); `tdsp()` takes an optional `const Restrictions*` and rejects any
  traversal whose occupancy window `[enter, exit+headway)` intersects a forbidden window. Self-test
  (`./fasttrain.exe <dir> --selftest`) forbids train 0's earliest-occupied link and confirms the re-solved
  path uses **no** forbidden cell → **PASS** on RAS Set 3.
- **B2 — Node bounding** ✅ **done**: `bound_node(R, iters, freeTravel, &ub, verbose)` wraps the subgradient
  LR with restrictions applied to every train's `tdsp()` and the priority-rule UB, warm-starting from the
  global `price` (the parent's multipliers) and resetting relax-and-cut memory per node. Returns node LB +
  feasible UB. Root call `bound_node(emptyR, …)` **reproduces the pre-refactor solver exactly**
  (LB 1785→2278.9, UB 4787→3803, gap 40.1%).
- **B3 — Conflict detection + 2-way branching** ✅ **done**. Two cleanly separated pieces:
  - **The mechanism / split (sound, invariant)** — "fix one train's segment, block the resource, offset the
    others" (the **Zhou & Zhong 2005 EJOR** enumeration rule, Alg.3 Step 6: tree levels are *trains*; fix a
    train's schedule, the rest yield to the space-time resource constraints). Implemented as `--split`:
    - **`segment`** *(default, the enumeration rule)*: at conflict `(l,t*)` between i,j, child A commits i's
      occupancy window on `l` and forbids j from it (j offsets *past* i's segment); child B commits j
      symmetrically. The two children are the meet/pass **orders** — exhaustive on block-occupancy
      single-track (two trains on a cap-1 link can't overlap, so one whole window precedes the other), and
      each branch advances a *whole segment*.
    - **`cell`**: child A forbids j from just `(l,t*)`, child B forbids i — a minimal always-valid fallback.
    On the toy both prove the same optimum (LB=UB=12) but **segment = 19 nodes vs cell = 51** — committing a
    segment and letting LR offset the rest is ~2.7× tighter, as expected from the 2005 enumeration.
  - **The selection (heuristic, swappable)** — `find_conflict(sol, crit, …)` picks *which* violated cell to
    fix. **There is no single "exact" branching rule**; the choice is a criterion, and it only changes the
    tree's shape/size, never correctness. Implemented criteria (`--branch`): `most-violated` (default),
    `earliest` ("first-thing-first"), `latest` (a completion-time flavour). On the toy all three prove the
    same optimum but the tree differs — most-violated/earliest = **51 nodes**, latest = **19 nodes** —
    confirming that "first-thing-first" is often *not* best and a smarter ("second-best") criterion pays
    off. This selection slot is exactly where **B6's screening / learned score** plugs in.
  **Restrictions are now per-train**: `Restrictions = vector<TrainRestr>` (`[nTrain][nL]→windows`).
  *(Framing note — Simon: this mirrors classic machine/chain-scheduling B&B, where the **bound criteria**
  and the **branch selection** are design choices per objective, while fixing one event + blocking the
  resource is the invariant step. The goal on real instances is not a proof of canonical branching but a
  good criterion + a kept incumbent + a shrinking gap, revisable later.)*
- **B4 — best-first B&B driver** ✅ **done**: `run_bnb()` — `std::priority_queue` of nodes keyed by a valid
  subtree LB (parent's bound), each carrying its per-train restrictions + a `shared_ptr` warm-start `price`
  snapshot (siblings share). Per node: `price = warm`, `bound_node()` → LB + feasible UB + primal; fathom
  by **infeasible route**, **bound** (`LB ≥ globalUB`), or **feasible leaf** (no conflict → record exact
  incumbent); else branch. Tracks global LB (monotone via best-first pop) / UB / gap; stops at `gapTol`,
  node budget, or **tree exhaustion → PROVEN OPTIMAL**. Flags: `--bnb [--nodes N] [--gap g]`.
  **Validated** on a hand-checkable native toy (3 trains, single corridor, headway 1): root LR gap 11.2%,
  B&B reaches **gap 0.00%, LB=UB=12, incumbent arrivals 9 13 17** (= the hand-computed optimum); segment
  enumeration exhausts the tree in **19 nodes** (cell split: 51). Regression: root LR + B1 self-test unchanged.
  Flags: `--bnb [--nodes N] [--gap g] [--branch most-violated|earliest|latest] [--split segment|cell]`.
- **B5 — Scale to RAS Set 3 / cut per-node cost** ✅ **done**. The bottleneck was per-node cost (each node ≈
  a full 10-iter LR ≈ 27 s). Three levers, all implemented:
  - **Asymmetric LR depth**: `--root-iters` (default = ini MaxIter, converges the root bound) vs
    `--child-iters` (default 3 — children warm-start from the parent's `ρ`, so a few iterations re-tighten).
  - **UB once per node**: the priority-rule UB (a full train-pass) now runs only on a node's last LR
    iteration instead of every iteration (~halves the per-node passes).
  - **Wall-clock budget**: `--time S` (chrono) stops cleanly with a final gap report (no more `timeout`
    kills); node log now prints elapsed seconds. Net: per-node cost down several-fold → deeper tree in
    fixed time. **RAS Set 3**: ~**8× more nodes in fixed wall-clock** — 66 nodes / 220 s (≈3.3 s/node) vs
    the pre-B5 20 nodes / 540 s (≈27 s/node).
- **B6 — Screening-guided branch selection** ✅ **done**. Implemented in the `find_conflict` selection slot:
  - **`--branch dual`** — the gradient-weighted screening score `ρ_{l,t}·(usage−cap)`: the LR dual price is
    the LB's sensitivity to that resource's capacity, so branching where the bound is most price-sensitive
    should tighten it fastest. The cheap proxy (nt_screen.py thesis, here with TRUE LR duals).
  - **`--branch strong`** (`--strong-k K`) — the oracle: screen the K highest-dual conflicts, bound both
    children of each with a 1-iter warm LR, branch on the one maximising the worse child LB. `dual` is
    judged against this (does `dual` pick what `strong` would?).
  On the *symmetric* toy all of {most-violated, dual, strong} prove the same optimum in the same 31 nodes
  (no dual heterogeneity to exploit — honest: the payoff regime is the heterogeneous real instance, not the
  toy). **RAS Set 3 (equal 220 s wall-clock)**: `dual` reached **gap 36.0% (UB 3583)** in **45 nodes** vs
  `most-violated` **gap 38.9% (UB 3751)** in 66 nodes — the dual-weighted screen found a better incumbent in
  fewer nodes, i.e. the screening pays off exactly where predicted (heterogeneous real duals), and not on
  the symmetric toy.

**Honest open issue (motivates the next step).** In both RAS runs the *global LB* barely rose above the root
(~2292) — with `--child-iters 3` the per-node bounds are too weak for best-first to lift the global LB, so
the gap (~36%) is being closed mostly from the *UB* side. Substantially closing it needs stronger per-node
bounds, which we can only afford if the per-train shortest path is much cheaper. So the **highest-value next
work is speeding up the exact SP** (the LR LB requires each subproblem solved exactly, so we keep it exact):
- rewrite `tdsp` as a **time-layered DAG DP** (the time-expanded graph is acyclic — drop the priority queue;
  O(states+arcs), several× faster, identical result);
- **collapse the ~1200-state departure-slack seeding** into one origin state + a zero-cost hold arc;
- **band the time horizon per train** to `[entry, entry+freeFlow+usefulSlack]` (and tighten the top with the
  incumbent UB) instead of scanning all 1440 minutes;
- optional valid *relaxations* (coarse time grid rounded down; drop dwell/headway) for cheap **lower-bound**
  SPs on most iterations — never a greedy/heuristic SP (that would invalidate the LB).
With a faster exact SP we can raise `--child-iters` (or do incremental re-solve of only the offset trains)
and actually drive the global LB up.

- **SP1 — Faster exact shortest path** ✅ **done** (option A, exactness-preserving). The time-expanded graph
  is a **DAG** (every arc strictly increases time, travel ≥ 1), so a single sweep in increasing time order
  is exact and **heap-free** — `tdsp_dag` (O(states+arcs)), selected by `--sp dag` *(default)*. The
  classical Dijkstra is kept as `tdsp_dijkstra` (`--sp dijkstra`) for verification and for any future model
  change that breaks the DAG property ("keep the classical implementation if we need to search"). Both share
  identical transition rules.
  - **Speedup: RAS Set 3 root LR 7.31 s → 2.48 s (~2.9×)**, same number of iterations.
  - **Exactness**: per shortest-path *cost* the two are identical (free-flow baseline and LR iters 1–2 match
    Dijkstra to the decimal across all 40 trains); DAG still drives the toy to **proven-optimal gap 0, dev 12,
    arrivals 9 13 17**. They can pick *different equal-cost paths* on ties, so the LR subgradient trajectory
    and final root LB/UB differ slightly (e.g. root LB≈2237 / UB≈3580 under DAG vs 2279 / 3803 under
    Dijkstra) — standard LR degeneracy, both valid lower/upper bounds. Documented numbers elsewhere were
    produced under Dijkstra; DAG is now the fast default.
  - **Caveat retained**: an *approximate/heuristic* SP would return a too-expensive path → LB over-estimate →
    invalid bound. So only exact (DAG/Dijkstra) or valid *relaxation* SPs are admissible — never a greedy SP.

## 7. Key finding: the bottleneck is **bound tightness, not compute**
With the 3× faster SP we re-ran RAS Set 3 (dual selection, DAG, `--child-iters 6`, 200 s) → 60 nodes, but the
result is essentially unchanged: **global LB ≈ 2262, UB ≈ 3561, gap ≈ 36%** (cf. the slower run: 45 nodes,
LB 2292, UB 3583, gap 36%). **The global LB does not lift above the root LR bound (~2300–2360).**

Why: the LR dual bound here is inherently loose (Meng & Zhou's own LB plateaus ≈ 2359 vs best UB ≈ 3471 — a
large Lagrangian duality gap). Forbidding one train's segment per node barely raises that node's LR bound,
because the bound is dominated by the *global* per-train shortest-paths + dual penalty; you must fix a great
many conflicts before the bound moves. So best-first keeps a frontier of open nodes pinned near the root
bound, the global LB stalls, and the gap closes **only from the UB side** (incumbent 3803 → 3561). The SP
speedup buys *more nodes*, not a *tighter bound*.

**Implication for next work** (this is the real lever, not more compute):
- **Tighter per-node bound**: drive the LR closer to convergence, and/or add **cuts** — e.g. a crossing-
  conflict / enhanced LB in the spirit of `single_track/ttbl_bnb.py` (which cut nodes 7.5→35.6% there).
- **LB-raising branching**: resolve a *bottleneck resource's full train ordering* at once (not one conflict),
  so a node's bound jumps materially.
- **Tighter relaxation entirely**: the cumulative-flow **LP** master (`n_track/nt_spacetime.py`) or an
  **ADMM** consensus bound (Simon's idea) instead of subgradient duals — these can give a stronger root and
  per-node bound, which is what actually closes the gap. The screening (B6) already helps the **UB**/branch
  side; the **LB** side needs a stronger relaxation.
- **Future (Simon) — ADMM variable-fixing**: beyond subgradient LR, the per-node bound can be an **ADMM**
  scheme that iteratively fixes train variables (consensus on the shared space-time resource) and offsets
  the rest — a tighter/penalty-stabilised alternative to the dual prices for committing a train's segment.
  Slots in as an alternate `bound_node` without changing the branch/search structure.

## 6. Risks & mitigations
- **Bound strength per node**: too few LR iterations → weak LB → big tree. Mitigate with warm-started `ρ`
  and a small adaptive iteration cap.
- **Branching completeness**: time-windowed forbidding must keep the two children's union = parent
  (it does: "j after i's window" ∪ "i after j's window" covers all orderings of the pair on `l`); confirm
  no feasible schedule is excluded by both children.
- **Tree size**: many conflicts per node → rely on the screening (§4) + best-first + good incumbent (warm
  priority-rule) to keep the tree small; report honestly if the gap only partially closes within budget.
- **Validation**: always cross-check B&B optimum on the toy against exhaustive/independent solve before
  trusting RAS-scale gaps (same discipline that caught the single-track LB-validity bug).
```
Engine to extend: n_track/fasttrain.cpp   |   branching reference: single_track/ttbl_bnb.py
screening to plug in: n_track/nt_screen.py + single_track/{learn_branch,tensor_*}.py
```
