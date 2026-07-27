# Compressed Response Screening for Railway Branching — Theory ↔ Evidence, and Redrafted Key Section

Storyline: **train scheduling** (single-track timetabling B&B), not UE-on-Sioux-Falls. All numbers
come from the faithful B&B port (`spectral_ttbl/ttbl_bnb.py`) and the probes
(`lowrank_probe.py`, `learn_branch.py`, `screening_theory.py`).

The decision point is a **B&B node**. The candidate "moves" are the **meet-pass branching actions**
`a=(section, train i, train j)`. The response to a move is the **child schedule** `x*(a)` (per-train,
per-section departure times); the score is the **total completion time** `S = c^T x` — a *linear*
functional whose gradient `∇S = c` selects the last-section-departure cells.

---

## (a) Theory ↔ evidence: the screening derivation on real train-scheduling data

| screening_illustrated.pdf | concept | train-scheduling evidence (real numbers) |
|---|---|---|
| §2 / Fig 2 | response field is low-rank; σ_i² = response variance | root node, 6 trains: variances `[2265,1083,264,86,40,0]`, **95% energy in 3 of 5 dirs**. 10 trains: 95% in ~10–13 of 22–30 dirs. Moderately low-rank, fast-ish decay. |
| §3 / Fig 3 | truncation discards spectral tail `Σ_{i>r}σ_i²` | tail falls monotonically with r (e.g. 6-train: 1473→389→126→40→0 for r=1..5). |
| §4 / Fig 4 | **score error is gradient-weighted**: `Var(S-err)=Σ_{i>r}σ_i²(uᵢᵀ∇S)² ≤ ‖∇S‖²·tail` | **the decisive finding.** 6-train: at r=3 (95% energy) σ_score=9.37, but the gradient-weighted tail **collapses at r=4** (σ_score 9.37→0.62, trueRMSE 3.82→0.25). A *low-energy* direction (#4, only 2.3% energy) carries almost all the **score-relevant** variance. Ordering holds: `trueRMSE(3.82) ≤ GW σ_score(9.37) ≤ loose(27.4)` — the scalar bound overstates ~7×. Same cliff on 10-train nodes (GW drops at dirs ~13–23). |
| §5 | ranking = signal vs noise, `σ_score ≈ ‖∇S‖√tail` | confirmed; the loose bound `‖∇S‖√tail` is 5–20× the realized score error across all nodes. |
| §6 / Fig 5 | **tie cluster** `K' = #{j: S_j−S_best ≤ 2σ_score}` | 6-train r=3: 2σ band=18.7, score gaps `[0,0,8,8,8,16]` → **K'=6 (all moves tied)**; raise to r=4 → K'=2. 10-train seed2 r=10: band 22.5, gaps 0–20 → **K'=23 (all)**; r=14→K'=5; r=21→K'=2. |
| §7 / Fig 6 | **inverse rank rule** `r* = min{r: σ_score(r) ≤ τ}` | 6-train: r*=4 across τ (cliff). 10-train seed2: τ=12.1→r*=9, τ=6.0→r*=12, τ=2.4→r*=14 (rank grows as tolerance tightens). |
| §8 | rank acts like sample size; decision stays exact | K' shrinks monotonically with r (6→2, 31→2); only the kept K' are re-solved exactly. |

### The two-line summary the data forces
1. **Low-rank structure is real but not the whole story.** The response field is low-rank, but the
   variance that matters for *ranking* lives in a few **low-energy, high-gradient-alignment** directions.
   Energy-based truncation (95% → r=3 on the 6-train node) is the *wrong* rank: it leaves σ_score≈9.4,
   larger than the score gaps (8–16), so every move stays in the tie cluster. The **gradient-weighted**
   rank (r=4) is the right one.
2. **This is why unsupervised spectral scores fail and learned/gradient-aware scores win** (independent
   confirmation, `learn_branch.py`, 1279 test nodes): node-level Containment@K vs Full-SB —
   learned **0.37 / 0.77 / 0.92** (K=1/3/5) beats unsupervised spectral norm **0.17 / 0.56 / 0.77**,
   pseudo-cost 0.27/0.63/0.80, random 0.27/0.67/0.85. The learned score is implicitly estimating the
   `(uᵢᵀ∇S)` weighting that the unsupervised norm discards. **Containment@K is the empirical tie
   cluster `K'`:** the best branch is rarely top-1 (large K') but almost always within the top ~5.

### Honest boundary (what the data does NOT support)
- **No node-count reduction on single-track.** The cheap earliest-conflict rule already minimizes the
  tree (e.g. 9297 learned vs 1535 earliest nodes); strong-branching-style selection (and its learned
  proxy) does not shrink it. The screening payoff is *ranking/validation effort*, not tree size —
  consistent with the PDF's "cheap to rank, exact to decide", and with the compressed-TA finding.
- **The branch score gaps are small** (0–18 time units) relative to the energy-truncation noise, so the
  tie cluster is large at moderate rank — meet-pass moves are often genuinely near-tied. This *limits*
  how much any screen can do at top-1 and *argues for* validating the whole tie cluster K' exactly.

---

## (c) Redrafted key section (for the railway paper)

> Replaces the unsupervised S₁/S₂/S₃ "Spectral Screening Method". Repositions per the agreed framing:
> *approximate, gradient-weighted screening before exact certification of a tie cluster.*

### 5. Compressed response screening for branching candidates

At a branch-and-price node `n`, let `A` be the candidate meet-pass actions (Section 4). Each action
`a` yields a child whose re-optimized schedule is the **response** `x(a)∈ℝ^L` (resource-time / schedule
coordinates) and whose bound is the **score** `S(x(a))`. Full strong branching evaluates `S` for every
`a`; we instead screen cheaply and certify only a small set exactly.

**Response field and its low-rank structure.** Solve a small subset of actions and stack their
responses `V=[x(a₁),…,x(a_m)]`; center and take the SVD `V_c=UΣW^⊤`. The squared singular values are
the response variances `σ_i²`. A rank-`r` proxy reconstructs any response in `r` latent coordinates,
`x̂(a)=x̄+U_rα(a)`, `α(a)=U_r^⊤(x(a)−x̄)`, with reconstruction error confined to the discarded tail
`Σ_{i>r}σ_i²`.

**From flow error to score error (the screening object).** We rank by `S`, not by `x`, so the relevant
error is in the score. A first-order expansion gives `S(x(a))−S(x̂(a)) ≈ ∇S^⊤δ(a)`, and for the
single-track objective `S=c^⊤x` (total completion time) this is **exact**, `=c^⊤δ(a)`. Hence the
score-noise variance is the **gradient-weighted tail**
```
        Var(S-err) ≈ Σ_{i>r} σ_i² (u_i^⊤ ∇S)²   ≤   ‖∇S‖² Σ_{i>r} σ_i² .
```
*A discarded direction hurts the ranking only if the objective gradient points along it.* This is the
key correction to magnitude-only spectral scores: the screening score must be **gradient-weighted**,
i.e. it must approximate `S(x(a))` itself, not the footprint norm `‖Σ_kV_k^⊤ b_a‖`. In practice we
realize this with a learned score `Ŝ(a)=f_θ(α(a), φ(n,a))` regressed on exact responses, where
`φ` adds node features (dual prices, conflict overlap, depth). On single-track instances this learned,
gradient-aware score dominates the unsupervised spectral norm and pseudo-cost at every K (Containment@5
0.92 vs 0.77 / 0.80; Section 7).

**Screening to the statistical tie cluster.** Model proxy scores as `Ŝ_a=S_a+ε_a`,
`ε_a∼𝒩(0,σ_score²(r))` with `σ_score(r)=√(Σ_{i>r}σ_i²(u_i^⊤∇S)²)`. A worse action can outrank the best
only if it lies within the proxy noise, so the **kept set is the tie cluster**
```
        A_K = { a : Ŝ_a − Ŝ_best ≤ 2 σ_score(r) },     K' = |A_K|.
```
Only `A_K` is evaluated by exact child LP/CG/strong-branching certification; the decision is exact
provided the true best lies in `A_K`. The rank is chosen by the inverse rule
`r* = min{ r : σ_score(r) ≤ τ }`, the analogue of a sample-size calculation: higher rank shrinks the
score noise — but governed by the **spectral decay rate**, not `1/√n`.

**Proposition (screening validity).** With exact child certification on `A_K`, every evaluated child
bound is valid, and the selected action equals the full-strong-branching action whenever the true best
lies in `A_K`. When `A_K=A` the method reduces to full strong branching. *(The spectral/learned layer
changes only which candidates are inspected, never the child relaxation, pricing, or bound validity.)*

### 6. Empirical illustration (single-track timetabling)

On the faithful single-track B&B (8 sections, two directions, Erlang departures; meet-pass precedence
branching; crossing-conflict lower bound — validated to reproduce the exact optimum, with the LB
reducing nodes 7/15/25% at 6/8/10 trains):

- **Response low-rank & gradient cliff.** At a root node (6 trains) the response variances are
  `[2265,1083,264,86,40,0]` (95% energy at r=3), yet the *gradient-weighted* score noise only collapses
  at r=4 (`σ_score` 9.37→0.62, realized RMSE 3.82→0.25): a 2.3%-energy direction carries the
  score-relevant variance. The scalar bound `‖∇S‖√tail` overstates the realized error 5–20×.
- **Tie cluster.** At the energy rank the 2σ band exceeds all score gaps, so `K'=N` (all moves tied);
  raising the rank shrinks `K'` to ≈2. The best branch lives in a small tie cluster — captured by
  Containment@K (0.92 at K=5) though rarely top-1 (0.37).
- **Honest scope.** Screening reduces *ranking/validation effort* and contains the optimal branch in a
  small certified tie cluster; it does **not** reduce B&B node count on single-track, where the cheap
  earliest-conflict rule is already near-optimal. The node-count payoff is expected only where the
  candidate set is large and child evaluation expensive (N-track / route-choice instances).

### N-track LP: primal vs dual screening, with and without SVD (2026-06)
Head-to-head on the **same** node against the **same** Full-SB oracle (`nt_screen.py`, scores added:
`primalgw`, `dualgw`, `primalSVD`, `dualSVD`). Primal gradient = cell flow `H·x` (congestion, no duals);
dual gradient = `ρ`; SVD = compressed-response projection `b_a·(U_rU_rᵀ g)` at rank@95% energy.

| instance | metric | primalgw | dualgw | **primalSVD** | **dualSVD** | pseudocost |
|---|---|---|---|---|---|---|
| toy (3 cand) | Contain@1 / Spearman | 0 / −0.87 | 1 / +0.50 | 0 / −1.0 | **1 / +1.0** | 0 / 0 |
| ds3 (20 cand, 112 frac) | Contain@1 | 0 | 0 | **1** | 0 | 0 |
| ds3 | Contain@5 | 0 | 0 | **1** | **1** | 0 |
| ds3 | Spearman | −0.07 | +0.39 | +0.28 | **+0.70** | −0.64 |
| ds3 | RankGap (↓) | 6 | 12 | **0** | 4 | 13 |

**Findings.**
- **SVD compression is the enabler.** Raw gradient-weighted scores are weak on the realistic instance
  (`primalgw` Spearman −0.07, `dualgw` +0.39); projecting the response field onto its rank-9/20 (95%-energy)
  subspace lifts them sharply — **`primalSVD` hits Contain@1=1 (RankGap 0)** and **`dualSVD` gives the best
  rank correlation (+0.70)**. The compressed-response screen extracts a signal the raw scores miss.
- **Primal and dual are complementary**: `primalSVD` pins the single best branch (top-1), `dualSVD` ranks
  the whole candidate set best. Both contain the Full-SB best within K=5 → **4× fewer child LP solves**
  (40 → 10) at K=5; pseudocost contains it in neither.
- **Toy is too small/degenerate** (3 candidates; primal even anti-correlates) — the payoff shows on the
  realistic ds3 root, consistent with "large candidate set / expensive child" being the right regime.
- **Honest caveats**: these are single root nodes (n=1 per instance) — robustness needs many nodes ×
  instances; and the **tensor** layer (CP/HOSVD) is so far validated on single-track (`tensor_response.py`:
  multilinear rank 3×3×1, transfer Spearman +0.27) — extending it to the N-track response field is the next
  step.

### N-track MULTI-NODE: the single-root signal does NOT replicate (2026-06, `nt_tensor.py`)
Walking 6 ds3 B&P nodes (DFS by Full-SB best, max_cand=8, 48 candidate rows, 96 child solves) and adding the
TENSOR scores (R[action, cell, channel={primal,dual}], `tensorHOSVD`=Tucker-projected fused reconstruction,
`tensorCP`=CP-ALS), the comparison **reverses the optimistic root-node read**:

| method | Contain@1 | Contain@5 | Spearman |
|---|---|---|---|
| primalgw | 0.33 | 0.83 | +0.08 |
| dualgw | 0.17 | 0.50 | +0.08 |
| primalSVD | 0.17 | 0.67 | +0.02 |
| dualSVD | 0.33 | 0.67 | +0.12 |
| tensorHOSVD | 0.17 | 0.67 | −0.02 |
| tensorCP | 0.17 | 0.83 | +0.06 |
| pseudocost | **0.67** | 0.67 | −0.47 |
| random | 0.33 | 0.83 | +0.11 |

- **No screening method robustly beats the baselines.** `random` already scores 0.33/0.83 (8-candidate sets
  make top-1 a 1-in-8 shot and top-5 cover most); `pseudocost` even wins Contain@1 (0.67) — though with a
  *negative* Spearman (nails top-1, ranks the rest inversely). All SVD/tensor Spearmans are ≈0 (+0.02…+0.12).
- **The single-root ds3 win (primalSVD Contain@1=1, dualSVD Spearman +0.70) was an n=1 artifact** — it does
  not hold across nodes. Low power too (6 nodes × 8 candidates is thin) → this is *weak evidence against*,
  not a clean refutation, but it clearly does **not** support "unsupervised SVD/tensor screening works on
  N-track."
- **Consistent with single-track**: there too the *unsupervised* spectral score failed and only the
  *learned/gradient-aware* transfer model worked. **Conclusion: the robust N-track screen must be a LEARNED
  (transfer) model over these primal+dual+tensor features — not an unsupervised compressed score.** That is
  the next experiment (port `tensor_response.py`'s T3 transfer-screening to the N-track features, train on
  some nodes/instances, test on unseen ones, measure out-of-sample Containment@K vs Full-SB).

### Contribution statement (revised)
> We introduce a **gradient-weighted compressed-response screen** for branching in time-space railway
> scheduling. Branch candidates are mapped to low-rank response coordinates; a learned, gradient-aware
> score predicts the exact branch bound; and the **statistical tie cluster** `K'={a:Ŝ_a−Ŝ_best≤2σ_score}`
> is certified exactly. The screen never alters the child relaxation, pricing, or bound validity. On
> single-track instances the learned screen dominates unsupervised spectral and pseudo-cost scores on
> Containment@K and contains the optimal branch in a tie cluster of ≈5; the framework reduces exact
> evaluation effort, not branch-and-bound tree size.
