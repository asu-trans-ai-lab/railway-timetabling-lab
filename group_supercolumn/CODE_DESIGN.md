# Group-Supercolumn CG — Integrated Source-Code Design + Dataset Generator

Companion to `DESIGN_FREEZE.md` (identity and decisions frozen there; nothing here reopens them).
Grounded in existing, validated assets — reuse, don't rewrite.

## 1. M0–M4 mapped onto existing code

| Model | Function | Existing asset (validated) | Work needed |
|---|---|---|---|
| **M0** | Arc-time LR baseline | `FTT_handoff/n_track/fasttrain.cpp` — reproduces Meng–Zhou (LB 2279 / UB 3803 / 40.1% on RAS Set 3); DAG SP (~2.9×); LR-B&B as the exact-fallback skeleton | none (baseline as-is) |
| **M1** | Individual-trajectory CG | `n_track/nt_spacetime.py` — column LP master (177 cols toy / 7140 ds3), HiGHS duals λ (=rho), π (=pi per train) | port master to C++ *or* keep Python master + C++ pricer via CSV bridge (start: Python master) |
| **M2** | Hard-feasible group-supercolumn CG | — (new) | `gsc_pricing.cpp`: pair/small-group joint DP over `JointState`; group pools; disjoint-partition master rows Σy_Gp=1 |
| **M3** | M2 + quadratic companion valuation | — (new, small) | `gsc_quadratic.cpp`: FiniteMove valuation ΔF̂ around donor p⁰; selects candidates, never certifies |
| **M4** | Integer group master + exact fallback | LR-B&B in `fasttrain.cpp` (proven-optimal on toy) | integer master over supercolumns; fallback = existing B&B on the unresolved component |

Certification: L1/L2 emitted per pricing round (`PricingCertificate`); L3 only via M4's complete search.

## 2. Source tree (new work lives here; handoff stays a frozen snapshot)
```
group_supercolumn/
  DESIGN_FREEZE.md          the frozen identity + 10 decisions
  CODE_DESIGN.md            this file
  include/gsc_types.h       FROZEN structs
  src/
    gsc_state.cpp           CompletionState machine + state-sufficiency merge (freeze #4)
    gsc_single_dp.cpp       single-agent DP (reuse fasttrain tdsp_dag transition rules verbatim:
                            travel(), siding dwell, headway occupancy, forbidden windows)
    gsc_joint_dp.cpp        pair/small-group joint DP: reachable JointState generation,
                            hard-transition feasibility F_ij(S,A,S'), safety pruning, dominance,
                            FO-priced transition costs  -> exact group pricing (L2)
    gsc_quadratic.cpp       donor-aware finite-move valuation (freeze #6); corridor from D(p,p0)
    gsc_master.py           restricted master (extends nt_spacetime.py): + group convexity rows,
                            + incompatibility cuts, + partition versioning; emits lambda, pi_G
    gsc_grouping.cpp        hard-coupling rule (logical) + Gamma_soft scoring + merge/split
                            (freeze #1-#3); partition version bump; pool archival
    gsc_driver.py           Algorithm A loop (master <-> pricers via csv/json bridge);
                            escalation ladder (freeze #9)
  gen/
    make_instance.py        dataset generator (below)
  data/                     generated instances (git-ignored except manifests)
```
Bridge (first implementation): Python master ⇄ C++ pricers over flat files
(`duals.csv`: cell,λ + group,π · `columns.csv`: serialized supercolumns). Same pattern the
LR/LP cross-checks already use; replace with pybind later only if profiling demands.

## 3. Dataset generator — `gen/make_instance.py`
Existing data lacks service-task structure (freeze #4 needs it). The generator SYNTHESIZES
task lists on top of the two proven physical formats, so every instance runs on M0 unchanged.

**Inputs (CLI):** `--base {toy_native, RAS_set3_native, synthetic}` `--trains N` `--tasks-per-train k`
`--hard-pairs h` `--seed s` `--horizon T`

**Outputs (one directory per instance):**
- `input_node.csv, input_link.csv, input_train_info.csv, input_MOW.csv, FTSettings.ini`
  — native FastTrain format (M0/M4 run unchanged).
- `input_service_tasks.csv` — NEW: `train_id, task_seq, node_id, min_dwell, due_time, weight`
  (ordered tasks → κ prefix; bounded frontier → f mask; dwell duration → τ^remain).
- `input_hard_coupling.csv` — NEW: `train_i, train_j, type{crossing, shared_siding, transfer}, cell_hint`
  (H^hard = 1 pairs; generator guarantees each is REAL: it plants overlapping free-flow
  occupancy windows so ignoring the pair is infeasible or costly).
- `manifest.json` — seed, parameters, planted-conflict ledger, free-flow bounds.

**Generation ladder (matches first-implementation scope):**
1. `G1-pair`: 2 trains, 1 planted crossing on a cap-1 link — the minimal joint-DP test
   (joint optimum computable by hand; FO certificate must reach L2).
2. `G2-chain`: 4–6 trains on the toy corridor, 2–3 hard pairs forming a chain — merge logic
   {i,j}+{k}→{i,j,k}; split after resolution.
3. `G3-ras`: RAS Set 3 physical network + synthesized tasks, 8–12 hard pairs — the M2/M3
   comparison instance (dual oscillation & fractional-relaxation experiments).

**Planted-truth requirement:** every instance's manifest records the planted conflicts and, for
G1/G2, the enumerated optimum — experiments report against ground truth, not judgment.

## 4. The three controlled experiments (frozen scope)
| exp | question | measured on | metric |
|---|---|---|---|
| E1 dual oscillation | does joint visibility damp λ oscillation vs M0/M1? | G2, G3 | per-cell λ variance across iterations; D_oscillation |
| E2 fractional relaxation | does the group master tighten the LP vs individual columns? | G2, G3 | LP obj gap to integer optimum; #fractional |
| E3 group size | value vs cost of larger K_max | G3 | bound improvement & wall-clock vs K_max ∈ {1,2,3,4} |

## 5. Build & conventions
- C++17, `g++ -O2 -std=c++17 -static` (same as fasttrain; AV note: build/run verification
  copies under `spectral_ttbl/` if train_scheduling exec-lock recurs).
- Python 3.11, numpy/scipy/pandas (same env as nt_spacetime).
- Every pricer emits a `PricingCertificate`; the driver logs the level per round — the paper's
  three-level reporting (freeze #7) falls straight out of the logs.
- No SVD/learned grouping anywhere in this tree (freeze #10). The screening research stays in
  `FTT_handoff/` as the (honest) motivating evidence.
