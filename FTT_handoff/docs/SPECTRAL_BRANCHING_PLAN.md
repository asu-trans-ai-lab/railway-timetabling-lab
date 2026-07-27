# Plan — Spectral Branching Screening for Time–Space Railway Timetabling

**Status:** planning. This MD is the moving-forward source of truth. Code follows the plan, not
the other way around.

## 0. Guiding principle (decided with Simon)

- The **working base is the real branch-and-bound**, not the draft PDF:
  - **Zhou & Zhong (2007), TR-B** — *single-track timetabling, B&B with enhanced lower bounds*.
    This is the paper **with the actual B&B** (meet-pass precedence branching, longest-path
    schedule generation, crossing-conflict enhanced LB). It is the algorithm behind
    **TrainTimetablingLite (C++)**.
  - **Meng & Zhou (2014), TR-B** — *N-track simultaneous rerouting + rescheduling*, cumulative-flow
    time-space model + Lagrangian relaxation. Supplies the clean **resource-time H-matrix** and the
    per-train time-dependent shortest-path subproblem (route choice).
- **Do NOT treat the spectral-screening PDF as a fixed spec or benchmark.** It must evolve. We
  *derive* matrix **A** (assignment / resource-time occupancy) and matrix **B** (branch-action ×
  trajectory) **from the running B&B's own trajectories and branching decisions**, then layer
  spectral screening on top and let the design change as we learn.
- **Replicate, then iterate intelligently.** Port the C++ / Zhou-Zhong B&B to Python first; validate
  it reproduces the real search; only then attach screening.

### Stage-0 lesson (what NOT to do — already tried, documented)
`spectral_ttbl/prototype.py` (departure-offset-only columns + entry-time-threshold "actions") is a
dead end: it does not replicate the real meet-pass branching, waiting was modeled by shifting
departures only, and the spectral screen failed to contain the Full-SB action (rank ~35–53/80,
Containment@K=0). Root cause: a train's columns were pure time-shifts, so every action of a train had
a near-identical resource footprint → the H-footprint cannot resolve *which* retiming helps. Keep the
file as a reference of the failure mode; the rebuild below supersedes it.

## 1. Two ways to model waiting (Simon's key point)

A train that "waits" (e.g. a later departure / a meet at a siding) must be representable **without an
artificial `Σx=1`-style constraint forcing it into a waiting area**. Two consistent routes:

- **Way 1 — explicit time-space network (waiting arcs).** Per-train time-expanded graph (Meng-Zhou
  TSG): *cell/segment-traveling arcs* `(i,t)→(j,t+run)`, *station/siding waiting arcs* `(u,t)→(u,t+1)`,
  *origin holding arcs*, *dummy sink arcs*. A trajectory = an origin→destination path; waiting is a
  structural arc, not a side constraint. `Σ_{p∈P_i} x_p = 1` is genuine path selection. **H** = path ×
  (resource-time cell) occupancy with headway tails (h on segments, g on station arrivals). This is the
  **column/path view** used to build A and the screening footprint.
- **Way 2 — timetable format.** A solution is the table `g_Table[train][station] = (arrive, depart,
  dwell)` (exactly the C++ structure). **Waiting is implied by departure − arrival − min-dwell**
  ("level-N departure differences ⇒ the train first waits at the station"). Conflicts and meet-pass
  order are read directly off the timetable; branching adds a precedence at the earliest conflict.
  This is the **closest replica of the C++ B&B** and is the natural state representation for search.

**Decision:** Way 2 (timetable + precedence B&B) is the **search/branching base** (it replicates the
C++). Way 1 (time-space network) is the **column/matrix view** used to derive **A** and the **H**
footprint for screening. The two are kept consistent (timetable ⇄ time-space path are the same object).

## 2. The real branching to replicate (Zhou & Zhong 2007 / C++ `define.h`)

Confirmed against `TrainTimetablingLite .../define.h`:
`g_Table[MAX_TRAINSIZE][MAX_STATIONSIZE][arr/dep/dwell]`, `g_FirstConflictTime`,
`g_ConflictTrainSet[2]`, `g_FirstConflictStationNo`, `g_SectionRunTime[station][2 dirs]`,
`g_StartTime[]`, `g_TrainDirection[]`, beam/LB flags (`g_BeamWidth`, `g_FilterWidth`,
`g_LowerBoundFlag`, `g_bBestSearch`), `g_SetupTimeTable(seed)`.

1. **Schedule generation (node subproblem):** given a precedence set `P(v)`, earliest start of every
   task `t(i,j)` (train i on segment j) = **longest path from the source in the precedence graph**
   (re-optimized incrementally when a child adds 1–2 arcs). Precedence arcs: departure `≥ r_i`;
   run+dwell `s_{i,k} ≥ s_{i,k−1}+p+d`; segment headway `s_{i′,j} ≥ s_{i,j}+p_{i,j}+h_j`; station
   arrival headway `≥ … + g_u`.
2. **Earliest conflict detection:** scan the generated timetable for the earliest time a **segment is
   over-occupied** (`Σ_i δ_{i,j,t} > 1`, opposing or overtaking) or a **station arrival-headway** is
   violated. (= C++ `g_FirstConflictTime`, `g_ConflictTrainSet`, `g_FirstConflictStationNo`.)
3. **Branching:** conflict set `Ω(v)` = tasks involved; create **one child per task in Ω(v)** that
   makes it earliest by adding precedences `t(i,j) ≺ t(i′,j′)` to all others. (Meet-pass order; for a
   pair, this is the two orders i≺i′ / i′≺i.)
4. **Lower bounds:** node LB = longest-path completion `Σ_i e_{i,σ(i,m)}`. **Enhanced crossing-conflict
   (CC) LB:** `LB(v) = Σ_i e_i + Σ_{i,i′} Θ(i,i′)`, with `Θ ∈ {h, g+h, 2h}` from the dwell-window /
   meet-at-station classification (Props 1–3). Cheap, valid; used for fathoming `LB ≥ UB·(1−θ)`.
5. **Search:** DFS / best-bound / beam (γ); incumbent UB by beam/priority heuristic; optimality
   certificate `(UB−LB)/UB`.

## 3. Deriving A and B from the running B&B (the actual ask)

At a search node `n` we expose two matrices straight from the B&B state:

- **A = resource-time occupancy (the draft's H; Zhou-Zhong δ,ε; Meng-Zhou y).**
  Rows = resource-time cells `(segment j, t)` and `(station u, t)`; columns = candidate train
  trajectories realizable under `P(v)` (each train's allowed time-space paths / the current timetable
  rows). `A_{r,p} = 1` iff trajectory p occupies cell r (incl. headway tails h, g). Dual prices from
  the LR/longest-path (`ρ_{j,t}, π/g`) are the row weights.
- **B = branch-action × trajectory.** Columns = the candidate **meet-pass actions** generated from
  `Ω(v)` (conflict resource, train pair, order). `B_{p,a}=1` iff trajectory p is affected by action a;
  `B^L_{·a}, B^R_{·a}` = the two meet-pass orders. **Derived from the B&B's own conflict set and
  precedence additions**, not synthetic thresholds.
- **Footprint** `G_{·a} = A·B_{·a}` (= draft `HB_{·a}`) = the resource-time terrain an action touches.
  Node-specific: `A(n), B(n), Ω(n)` all change per node.

## 4. Spectral screening layer (evolving design)

1. Node-weighted `Ã = W_R A W_P` (W_R from resource dual prices / conflict intensity; W_P from
   longest-path slack / LP activity / reduced cost).
2. Rank-k truncated SVD `Ã ≈ U_k Σ_k V_kᵀ`.
3. Scores per meet-pass action: `S1=‖Σ_k V_kᵀ B_{·a}‖` (structural), `S2=qᵀB_{·a}` (conflict/dual
   activity), `S3=‖U_kᵀ Ã (B^L−B^R)‖` (left-right meet-pass separation). Rank-aggregate → top-`K_pool`.
4. **Certify only the top-`K_pool`** by the real child evaluation (longest-path / CC LB / LR child
   bound) — the exact layer is unchanged; screening only chooses which children to evaluate.
5. **Honest metrics vs Full-SB** (evaluate all `Ω(v)` children): Containment@K, RankGap,
   BoundRecovery@K; child-LB-eval reduction, nodes explored, final gap. Ablations S1/S2/S3.
   Carry over the compressed-TA caveat: the branch-improvement signal is small; rank k and W_R/W_P
   must concentrate energy on the contested cells or screening will miss the Full-SB action.

## 5. Implementation stages

- **Stage A — port the B&B (replicate first). [DONE — `spectral_ttbl/ttbl_bnb.py`]** Faithful Way-2
  timetable-format port of Zhou-Zhong / TrainTimetablingLite: MSVC-RNG Erlang instance generator
  (single-track, 2 directions, 8 sections, fast/slow types, headway g_I=5), forward-sweep schedule
  generation (`Table[train][section][arr,dep,floor,fix]`, waiting via raised floor), earliest-conflict
  detection on the completion-sorted active-task list, **meet-pass precedence branching** (one child
  per contending train; losers pushed past winner+g_I and re-swept), **crossing-conflict LB**, DFS.
  Validated: solves to optimality; CC-LB reduces nodes AND preserves the exact optimum on all tested
  seeds at 6/8/10 trains (e.g. 10-train: 16866→11606 nodes, opt unchanged). LB-validity fix: count a
  crossing conflict only on UNFIXED sections (g_I per disjoint conflict); the g_I*allconflicts+overlap
  variant over-prunes. no-LB DFS = exact ground truth (Full-SB) for Stage B/C.
- **Stage B — expose A and B; Full-SB ground truth.** At each node build `A(n)` (occupancy of current
  trajectories) and `B(n)` (meet-pass candidates from `Ω(v)`). Implement Full-SB (exact child LB for
  every candidate) as the screening-metric ground truth.
- **Stage C — spectral screening + measurement.** Add S1/S2/S3 → top-K → certify; report
  Containment/RankGap/BoundRecovery and search-effort reduction vs Full-SB, pseudo-cost, Random-K,
  Rule-K. Ablations + sensitivity (rank k, K_pool, conflict density).
- **Stage D — evolve.** N-track + route choice (Meng-Zhou cumulative-flow, LR by-train SP); the
  **train/track ("channel") assignment** dimension; new/larger timetable instances. Let the
  formulation and the screening scores evolve (PDF is not frozen).

## 6. Datasets (re-scoped to the B&B base)

- **Single-track (primary):** Zhou-Zhong 138-km / 18-station Fujian corridor (params from the paper);
  random Erlang-departure instances (4–30 trains, gap 90–180 min); **TrainTimetablingLite-generated
  instances** (≤40 trains, ≤10 stations per `define.h`) with `Test*.dat` as validation targets.
- **N-track (Stage D):** Meng-Zhou RAS-2012 corridor (76 nodes / 85 cells, 137.6 km) and the larger
  85/97 network, with MOW capacity-0 cells.
- RAS-2023 metro / operational-strategies data: **demoted to optional** (not the branching base).

## 7. Validation target = the real codes, not the PDF

Ground truth = C++ TrainTimetablingLite B&B (node counts, optimal cost, conflict counts) and
Zhou-Zhong / Meng-Zhou published numbers. The spectral-screening PDF is a design we revise as evidence
comes in.

## 8. Open questions ("think about it")

1. Way 1 vs Way 2 as the *primary* representation — current call: Way 2 base + Way 1 matrix view.
2. How `B(n)` is regenerated per node efficiently (incremental, like the longest-path re-opt).
3. Where the **channel/track assignment** decision enters (extra binary; extra branch-action class).
4. Whether spectral screening actually helps **meet-pass** branching (the honest core test) — or only
   helps once route/track choice enlarges the candidate set (Stage D).
5. Exact child certification cost: longest-path LB (cheap) vs LR child bound vs CC LB — which is the
   right "exact" layer for the screening comparison.
