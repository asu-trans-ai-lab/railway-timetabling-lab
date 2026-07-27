# Group-Supercolumn CG — Design Freeze (2026-06)

Paper identity (FROZEN): **dual-price coordination → joint-transition interaction visibility.**
Strong local interactions become explicit joint-state feasibility inside a group; weak and
system-level interactions remain in the LR/master layer.

## The ten frozen decisions
1. **Disjoint active-group partition** in the first implementation:
   ∪ G = K, G_a ∩ G_b = ∅; group convexity Σ_{p∈P_G} y_{Gp} = 1 per group.
2. **Overlapping groups only as temporary pricing neighborhoods** (never two selectable
   supercolumn pools covering the same train). On merge {1,2}+{3}→{1,2,3}, old pools become
   inactive archives; the new group gets a fresh pool.
3. **Hard coupling is logical; soft coupling is scored.** If H_ij^hard = 1 then (same exact
   group) OR (incompatibility cut in master) OR (exact conflict-resolution fallback) — never
   decided by a weighted score. Soft score:
   Γ_ij^soft = α2·P_persistent + α3·D_oscillation + α4·|Q_ij| + α5·F_future − α6·C_expansion.
4. **Compressed service state** q_{i,t} = (κ completed-prefix, f frontier-mask,
   a^active current task, τ^remain remaining service). State-sufficiency proposition:
   ψ(h¹)=ψ(h²) ⟹ same feasible actions and same transition costs ⟹ V(h¹)=V(h²)
   (formal proposition in the paper).
5. **First-order (FO) joint DP certifies**: RC_Gp^FO = C_Gp + λᵀA_Gp − π_G;
   restricted-master certification for a fixed partition.
6. **Donor-aware finite-step quadratic companion valuation selects** (NOT "quadratic reduced
   cost"): ΔF̂_Gp = ΔC_Gp + λᵀd_Gp + ∇Φ(U)ᵀd_Gp + ½ d_GpᵀH_Φ(U)d_Gp + ρ_G·D(p,p⁰),
   with d_Gp = A_Gp − A_{Gp⁰}, and transition-additive proximal distance
   D(p,p⁰) = Σ_t |S_{G,t}^p − S_{G,t}^{p⁰}|²_{W_t} (so it enters the Bellman transition cost).
   **FO pricing certifies; quadratic valuation selects.**
7. **Three certification levels**, reported explicitly:
   - L1 Restricted-search convergence (no improving column in searched groups/corridors/states).
   - L2 Exact pricing for a fixed partition (exact group DPs, no corridor/screening) → LP
     master certified for the current disjoint partition.
   - L3 Global interaction certification (no unresolved hard conflict; no improving merge;
     complete integer search). First paper claims L2 + anytime improvement + exact fallback —
     NOT unrestricted global optimality over all group formations.
8. **M0–M4 experimental ladder**: M0 arc-time LR · M1 individual-trajectory CG · M2
   hard-feasible group-supercolumn CG · M3 = M2 + quadratic companion valuation · M4 integer
   group master + exact fallback.
9. **Exact fallback for unresolved components** (local K_max increase → corridor removal →
   full joint DP → MILP/B&B).
10. **No SVD or learned grouping in the minimum integrated prototype.**
    (Empirically corroborated in this repo: the multi-node N-track screening test —
    `FTT_handoff/docs/ntrack_bnb_paper/` §5 — found no unsupervised SVD/tensor score beats
    pseudo-cost/random across nodes. The frozen prototype rightly excludes them.)

## Core invariant
One agent → one active group → one selected group supercolumn (per partition version).
Every active supercolumn belongs to exactly one active group in exactly one partition version.

## Primal visibility (definition to carry into §5 of the paper)
An interaction (i,j) is primal-visible in G when its feasibility/direct cost is evaluated as
F_ij(S_{G,t}, A_{G,t}, S_{G,t+1}) — not only through an external dual price.

## First-implementation scope (frozen)
Network core, single-agent DP, pair/small-group joint DP, LR baseline, restricted master,
quadratic companion score, and three controlled experiments: dual oscillation, fractional
relaxation, group size.

## Frozen data structures
See `include/gsc_types.h` (CompletionState, AgentState, JointState, GroupPartition,
GroupSuperColumn, FiniteMove, PricingCertificate with Level ∈ {RestrictedSpace,
ExactFixedPartition, GlobalExact}).
