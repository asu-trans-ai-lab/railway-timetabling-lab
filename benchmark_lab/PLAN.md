# Benchmark Lab — from Stage-0 mechanism verification to a classical railway scheduling laboratory

Authoritative plan (Simon, 2026-06). The existing packages (jtv, ras_flatland, control_trajectory,
fasttrain, nt_spacetime) are **Stage 0: mechanism verification** — they demonstrate LR oscillation,
joint-transition visibility, and group-size growth, but provide no serious classical-solver comparison
(the integer examples report zero MIP nodes; branching effectiveness is untested). The paper cannot rely
on joint-DP state reduction alone: it needs a **classical railway scheduling laboratory** around it.

**Stage-0 closing evidence (RAS Set 3 unified, 2026-06):** pair joint DPs are individually feasible
(full DP 15/15) and the corridor works after the coordinate fix (reduced 28/28), but iterative
fixed-neighbor repair made the global timetable WORSE (conflicts 81 → 113). The repair engine alone
cannot close a real network — the master/CG layer and classical benchmarks are not optional.

## 1. The benchmark ladder (replaces M0–M4)

| ID | Model | Purpose | Existing asset | Status |
|---|---|---|---|---|
| B0 | FCFS / priority dispatching (7 rules + serial resource schedule, best-of-N) | feasible UB, warm starts | `fasttrain` priority_ub (1 rule) | partial |
| B1 | Arc-time Lagrangian relaxation | dual LB, TV(λ) oscillation | `fasttrain.cpp` (reproduces Meng–Zhou); `jtv` LR | **done** (add TV reporting) |
| B2 | Time-indexed MILP on (k,i,j,t,t′) arcs | exact reference, small cases | — (scipy.milp/HiGHS ready) | to build |
| B3 | Disjunctive / precedence branch-and-bound | classical branching benchmark | `ttbl_bnb.py` (single-track, exact); `fasttrain --bnb` (LR-based, segment enum) | **partial** (unify + full node statistics) |
| B4 | CP-SAT job-shop | strong independent exact baseline | — (`pip install ortools` required) | to build |
| B5 | Individual train-path column generation | LP LB, per-train pricing | `nt_spacetime.py` (enumerated columns, **no real pricing loop**); `jtv` M1 | partial → add pricing DP |
| B6 | Branch-and-price (pricing at every node, branching rules: precedence / resource-time / route / column-pair; pricing must respect branch decisions) | exact path-based integer benchmark | — | to build (the big one) |
| B7 | Full joint DP | exact benchmark, very small groups | `ras_flatland` full DP; `jtv` pair DP | **done** |
| P1 | Hard-feasible group supercolumn CG | interaction visibility | `jtv` M2 (E3: gap 0.5 → 0.0) | prototype |
| P2 | P1 + quadratic companion pricing | finite-step column valuation | `jtv` M3 | prototype |
| P3 | Adaptive grouping + exact fallback | full proposed method | `DESIGN_FREEZE.md` Algorithm A | designed |

Paper questions this separates: LR bound usefulness · CG vs arc formulation strength · branching
efficiency to executable timetables · supercolumn relaxation strengthening · quadratic-valuation savings.

## 2. Bounds are first-class outputs
UB = min{FCFS, priority, repair, joint, CP, B&B}; LB = max{independent, LR, CG, B&B-node, LP-MILP}.
**Common reporting record** (every solver, every run — `record_schema.py`):
```
instance_id, method, objective, best_lower_bound, best_upper_bound, relative_gap,
feasible, optimality_proven, time_to_first_feasible, total_runtime,
nodes, columns, pricing_calls, dp_labels, hard_conflicts
```
B&B additionally: root LB, initial UB, best-bound + incumbent trajectories, nodes generated/processed/
pruned-by-bound/pruned-by-infeasibility, max depth, time-to-first-feasible, time-to-optimum, final gap.

## 3. Standardized topology families (paper names, not "chains")
- **C1 single-track corridor** — ordered stations, one shared track/segment, sidings, bidirectional,
  meet/pass only at designated locations.
- **C2 double-track corridor** — two directional tracks, crossovers, same-direction headway, overtaking
  stations, station-throat conflicts.
- **CM m-track network** — m parallel tracks, switch arcs, junctions, route choice, platform assignment,
  controlled interaction density.
- **G grid railway** — physical (i,j); space-time-state (i,j,t,s); canonical transition record
  **(k, i, j, i′, j′, t, t′, s, s′)** = the generator's standard schema for all families.

## 4. Generator: feasible answer FIRST (`generators/`)
1. infrastructure (topology, resources, directions, sidings, switches, platforms, throats);
2. **conflict-free base timetable** by sequential assignment → known feasible z_base, UB_base;
3. planning windows around the base (release, preferred, earliest/latest, dwell, deviation weights);
4. controlled congestion injection (δ_release, ρ_opposing, ρ_shared, ρ_crossovers) — base stays feasible;
5. ground-truth manifest: seed, base timetable + objective, conflict graph, expected component sizes,
   exact/LB when computed.
Config dimensions per the YAML spec in the directive (topology / agents / service / interaction /
benchmark blocks; service uses the frozen progress+frontier completion encoding).

## 5. External benchmark families
- **RAS / FastTrain instances — MANDATORY** legacy regression: confirm old LR numbers, then individual
  CG, then B&B, then group supercolumns on the same data (`FTT_handoff/data/RAS_set3_native` +
  `unified_testbed/instances/ras_set3_unified`).
- **TTPLib** (ttplib.zib.de): external validity, best-known bounds, TraVis comparison.
- **Flatland**: an *adapter*, not the only generator (both directions: canonical grid ↔ RailEnv);
  deterministic first, malfunctions later as rolling-horizon stress.

## 5b. Session-2 additions (2026-07)
- **C2 double-track family** in `chain_generator.py` (`--family c2`: two directional tracks/segment +
  sidings; C1 output verified byte-identical after refactor). First instance `C2_L6_n8_seed21`:
  CP-SAT [39, 43] in 120 s — topology effect visible (8 trains: 138 single-track vs 43 double-track).
- **Visualization Views A+B built** (`visualization/timetable.py`): time-space timetable +
  resource-time occupancy from the B0 dispatcher schedule (seed1 figure = the PROVEN optimum, dev 29).
- **P1 joint-DP pricing: designed, deliberately deferred.** A rushed build risks an invalid "certified"
  claim. Design for next session: pair labels (t, loc_i, rem_i, loc_j, rem_j) with rem = remaining
  in-link time (dwell chosen at entry; occupancy [enter, arrive+HW) per link); feasibility = interval
  disjointness per cap-1 link (each train uses each link once on corridors); cost dominance on
  (locs, rems); validate on a 2-train C1 instance vs the CP-SAT optimum before ANY bound claim.
  Until then P1 stays honestly labeled "restricted group LP (order-enum pricing)".

## 5c. External evaluation (2026-07) — accepted findings + gate plan
An independent review of the packaged lab confirmed the architecture and the classical ladder, and
found real defects (all verified in our code and fixed): **(a)** B6 could declare LB=UB "proven" on an
empty heap while stalled/unconverged subtrees had been discarded — seed1's B&P "proof" carried 12
stalled nodes and is RETRACTED as a standalone proof (29 remains proven by B2/B3/B4); fixed: stalled
subtrees keep their parent bounds, block certification, and cap the global LB. **(b)** P1 could record
an ACTIVE artificial column (objective ~1e5) as a feasible UB; fixed: dummy-active ⇒ infeasible, no UB.
**(c)** B4 grouped parallel links by unordered node pair and could traverse one-way C2 tracks backward;
fixed with a direction filter (C2 bounds re-run). Also accepted: B6's departure-window branching is
finite and pricing-compatible but NOT complete for same-departure route fractionality; P1's order
enumeration is not a joint Bellman recursion and does not yet test the central contribution.
**Verdict adopted:** classical benchmark section is emerging; proposed-method results are NOT ready.

**Gate plan (next sessions, in order):**
1. **CORE DONE - Common schedule contract + independent validator** (`validate_schedule.py`): checks 1-8 (continuity, travel times, no-node-wait, departure window, DIRECTION, capacity/headway/MOW, horizon, objective recompute); B4 exports `results/schedules/<inst>_B4.csv`; all four proven optima independently VALIDATED (toy 12, seed1 29, seed3 57, C2 49 - the C2 check confirms the direction fix end-to-end); tamper test rejected (exit 1). REMAINING: B0/B5/B6/P1 exports, pipeline wiring, visualizer reading the contract. Original scope: — every solver exports `schedule.csv` /
   `resource_occupation.csv` / `solver_iterations.csv`; a separate validator checks continuity, travel
   times, windows, conflicts, headways, MOW, direction, completion. No `hard_conflicts=0` claim without
   it. Visualizer reads the same contract (today it regenerates a B0 schedule internally).
2. **Exact-equivalence gate re-run under the validator** (2–4 trains): objective equality AND
   independently validated feasibility for B2/B3/B4/B6/B7.
3. **True pair joint DP** for P1 (state (x_i, ell_i, q_i, x_j, ell_j, q_j, sigma_ij); validate vs
   B2/B4 before any bound claim), then corridor reduction and quadratic valuation on top.
4. **Precedence-complete B&P**: add i≺_r j vs j≺_r i branching (pricing must enforce it); departure
   windows remain the first-level rule.
5. **Generator expansion**: C3/C4/grid with full ground-truth manifests.
Paper interpretation until gates pass: individual CG exposes measurable gaps, branching closes them on
small cases, and the restricted group pricer does not yet demonstrate an advantage — motivating the
joint-state oracle and the controlled quadratic ablation.

## 6. Visualization (`visualization/`, every solver exports the same schedule format)
A time–space timetable (paths, stations, sidings, waits, overtakes, conflicts, incumbent vs relaxation,
branch decisions) · B resource–time occupancy (use, capacity, violations, LR prices, branch constraints)
· C B&B tree (per-node conflict, LB, UB, gap, status) · D CG dashboard (RMP obj, min reduced cost,
columns, fractionality, pricing/master runtimes) · E joint-state view ((x_i(t), x_j(t)) with forbidden
regions and accepted transitions).

## 7. Experiment program
A exact equivalence (2–4 trains: MILP = CP-SAT = B&B = B&P = full joint DP) · B single-track precedence
branching (trains/sidings/opposing/slack; heuristic vs LR vs B&B vs B&P vs group) · C double-track
overtaking · D m-track & grid scaling (m∈{1..4}, K∈{10,20,40,80}, component size independent of K) ·
E relaxation strength (root LP gap, integer gap, nodes, time-to-first-feasible; individual vs group
columns) · F quadratic pricing ablation (same group DP; FO vs proximal vs diagonal-Q vs full cross-agent
Q; accepted columns, gain/column, CG iterations, master reopts, pricing time).

## 8. Module layout (this folder)
```
benchmark_lab/
  PLAN.md, record_schema.py
  solvers/        priority_heuristics.py lr_timetabling.py time_indexed_milp.py
                  precedence_branch_bound.py cp_sat_timetabling.py individual_column_generation.py
                  branch_and_price.py full_joint_dp.py group_supercolumn_cg.py adaptive_exact_solver.py
  generators/     chain_generator.py multitrack_generator.py grid_generator.py
                  feasible_schedule_builder.py conflict_injector.py flatland_adapter.py
  visualization/  timetable.py resource_time.py branch_tree.py cg_dashboard.py joint_state.py
```
Reuse policy: wrap existing engines (fasttrain, jtv, ras_flatland DP) behind the common record rather
than reimplementing; new code only where the ladder has gaps (B2, B4, B6, B0-suite, generators, viz).

## 9. Build order (each step gated on the previous producing common records)
1. ✅ **done** — `record_schema.py` + `wrap_existing.py`: 10 common records across B1/B3/B7/B5/P1/P2 on
   toy + RAS + jtv instances; regression asserts pass (RAS B1 LB 2237.2/UB 3580 dag; toy B3 proven 12;
   toy B7 0 conflicts; jtv rerun fresh on this machine reproduces E3 group gap 0.0). RAS B3 budgeted run
   improved the incumbent to **UB 3374** (gap 33.0%).
2. ✅ **done (C1)** — `generators/chain_generator.py`: feasible-first C1 corridors in NATIVE format
   (+ unified dialect). Serial-resource base schedule = guaranteed UB (directive B0's fallback);
   intended = free-flow arrival; congestion via release compression. First pool
   (`results/instances/`): C1_L4_n4_seed1 — **exact optimum 29 PROVEN, 1385 B&B nodes** (UB_serial 75);
   C1_L4_n4_seed2 — **optimum 20 PROVEN, 609 nodes** (UB_serial 71); C1_L6_n6_seed3 — 6 trains,
   UB_serial 276, exact pending. Real branching effort at last (vs the Stage-0 "0 MIP nodes" complaint).
   *Honest note:* the ras_flatland corridor-DP on C1_seed1 unified makes conflicts WORSE (4→10) even
   wide — one dense 4-train corridor component, the known fixed-neighbor-repair boundary case (and its
   MAXG=3 compile cap silently skips size-4 groups). These instances are exactly the discriminating
   testbed: classical B-solvers handle them; P-methods must too.
3. ✅ **done (B0 + B2)** —
   - `solvers/priority_heuristics.py` (**B0**): 6 rules (fcfs/edd/minslack/class/conflict/rand-best-of-N)
     over a faithful residual-capacity sequential-insertion dispatcher (fasttrain semantics; O(1)
     blocked-prefix feasibility). C1 pool: seed1 B0_best **29 = optimum** (class/conflict rules), seed2
     **20 = optimum** (randomized), seed3 69 (vs serial 276), toy 12 = optimum on every rule.
   - `solvers/time_indexed_milp.py` (**B2**): time-indexed MILP on (k,i,j,t,t′) via scipy/HiGHS, faithful
     occupancy semantics; documented restrictions (toward-destination arcs, pad, smax) make it an UB —
     equality with the B&B-proven optimum certifies both.
4. ✅ **EXPERIMENT A GATE (B2 ≡ B3) PASSES**: seed1 **29.0 = 29.0**, seed2 **20.0 = 20.0**, toy
   **12.0 = 12.0**, all proven, by two independent exact formulations (HiGHS MILP vs LR-based segment
   B&B). B4 CP-SAT joins the gate once ortools is approved; B6 B&P joins when built.
   **RAS Set 3 B0**: fcfs 3730 · edd 3992 · minslack 4049 · **class 3526** · conflict 4283 · rand5 3693 —
   the class rule beats the LR's own priority UB (3580) and sits 4.5% above the B&B incumbent (3374).
   Current RAS bounds: **LB 2261.8 · UB 3374 · gap 33.0%** (UB=min over B0/B1/B3).
5. ✅ **B4 CP-SAT** (`solvers/cp_sat_timetabling.py`, ortools installed): job-shop mapping (train=job,
   link=machine, optional intervals for mainline-vs-siding, NoOverlap; faithful occupancy incl. headway
   and in-link dwell; corridor-chain routing v0). **Gate: OPTIMAL 29/20/12 in milliseconds** — the gate
   now has THREE independent exact solvers (HiGHS MILP = LR-B&B = CP-SAT). CP-SAT is the fastest exact
   labeler by far (seed1 0.0s vs MILP 8.9s vs B&B 56s); it proved **seed3 optimum = 57** (0.8s), recorded
   in the manifest (fasttrain B&B cross-check running).
6. ✅ **B5 individual-path CG with a REAL pricing loop** (`solvers/individual_column_generation.py`):
   restricted LP master (convexity + touched-cell capacity) + exact per-train priced tdsp; converged
   pricing ⇒ true path-LP lower bound; integer RMP (no branching) = valid UB. Two bugs found and fixed
   en route, both worth remembering: (a) a truncated column-dedup key silently rejected distinct columns
   → fake convergence with an INVALID bound (LP "LB" 51 > optimum 29 was the tell); (b) scipy/HiGHS
   `eqlin.marginals` ARE the textbook convexity duals π — negating them (as an older prototype did for
   the unused π) kills pricing; verified by strong duality (Σπ − λᵀcap = primal).
   **Results (LP LB ≤ opt ≤ integer RMP everywhere):** toy 12=12=12 · seed1 **23.75 ≤ 29 ≤ 42** ·
   seed2 20=20=20 · seed3 **51 ≤ 57 ≤ 69**. The individual-path LP shows REAL integrality gaps on C1
   (18.1% / 10.5%) — the measurable target for P1 group supercolumns and for B6 branch-and-price.
3. B0 heuristic suite; B2 time-indexed MILP (scipy/HiGHS); B4 CP-SAT (after `pip install ortools`).
4. Experiment A: exact equivalence gate — nothing proceeds until all exact methods agree.
5. B5 pricing loop, then B6 branch-and-price with the four branching rules.
6. P1–P3 on the same instances; Experiments B–F; visualization filled in alongside (A/B views first).
Only after these benchmarks function do we judge whether joint-transition visibility and quadratic
companion pricing beat classical LR, CG, B&B, and B&P.
