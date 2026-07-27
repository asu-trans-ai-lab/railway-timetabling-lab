# Timetabling_project — Dataset Inventory & Prototyping Notes

Target algorithm: **Algorithm 1 of "Spectral Screening for (Approximate) Branching in Time–Space
Railway Scheduling"** — a branch-and-price screening-before-certification scheme. Master problem
(draft Eqs 2–5): pick one trajectory column per train (`Σ_{p∈P_i} x_p = 1`) subject to resource-time
capacity (`Σ_p H_{rp} x_p ≤ C_r`), `x∈{0,1}`. Screen branch candidates by the H–B spectral footprint,
certify the top-K_pool exactly.

---

## 1. Datasets

### D1 — Santiago Metro Line 1 (RAS-2023 based)  ⟵ PRIMARY, recommended
Path: `Train_Timetabling_with_Operational_Strategies-main/`  (paper: "Train timetabling with rolling
stock assignment, short-turning and skip-stop for a bidirectional metro line"; data from 2023 INFORMS
RAS Problem Solving Competition).

Network: 8 physical stations San Pablo↔Unión Latinoamericana, modeled as a **bidirectional line with
16 directional station-nodes** (up = 1..8, down = 9..16; pairs 1&16, 2&15, …); 7 inter-station
segments per direction.

| File | Sheets / contents |
|---|---|
| `data/Network Topology/Network_Topology.xlsx` | **Distances** (km per segment); **Operation_conditions**: max speed 80 km/h, accel 1.35 m/s², decel 1.85 m/s², min headway 90 s, turnaround/return 135 s |
| `data/All Parameters/Parameters.xlsx` | h_max=360 s, h_min=90 s, δ_min(turnaround)=135 s, train capacity C=250, load factor κ=0.8 (off-peak)/1.0 (peak), max station skips s_k=4; **Running_time** (sec, asymmetric per segment & direction, ~40–64 s); **Dwelling_time** (35–45 s, up/down) |
| `data/OD Matrices/OD_matrices.xlsx` | **Morning / Mid-Day / Evening** static OD, 8×8 station-level, sliced into 15-min windows (e.g. 7:30–7:45) |
| `data/Station Profile Data (Simulated)/time_dependent_16stations_{30,60}mint.xlsx` | **Arrival_profile_per_min** (16 stations × 30 or 60 one-min bins, φ, 30-50-20 peak pattern); **OD_share_per_bin** (time-dependent OD shares, 1680 / 3360 rows) |
| `data/Reproducibility Script/` | `Input_Parameters.xlsx` + `Table_7_Row_1.py` (regenerates one paper result) |
| `src/Algorithms/*.py` | Reference impls: classical ε-constraint; hybrid ε-constraint **logic-based Benders** (e-LBBD); **Queue-Integrated Fixed-Point Optimization (QFPO)** |
| `src/Algorithms/Input_Parameters.xlsx` | adds Pure_running / Acceleration / Deceleration time sheets + peak demand variant |

Concrete instance constants (from `queue_integrated_fixed_point_optimization.py`): S_U=8 up, S_D=16
down, K_U=6 / K_D=12 trains (RS=14 rolling stock), planning start H=27000 s (07:30), T_WINDOW=1800 s
(30 min), 60-s bins ⇒ **16 nodes, ~12–14 trains, 30 time steps**. Headway 90 s, capacity 250.

→ Fit: directly supplies running/dwell/headway/capacity/OD to **generate trajectory columns and
resource-time cells**. Spans the draft's *diagnostic-small* (take one direction, 6–8 trains, ~8 nodes —
Full-SB ground truth computable) up to *medium* (full bidirectional, 12–14+ trains).

### D2 — TrainTimetablingLite (single-track B&B)  ⟵ secondary / reference
Path: `TrainTimetablingLite-master/…/CPP_SourceCode/` (+ `SoftwareRelease/TrainTimetablingLite.exe`,
`Documents/Single-track train timetabling with guaranteed optimality_Branch-and-bound….pdf`).
Single-track meet–pass timetabling via B&B with enhanced lower bounds. The `Test*.dat` files are **run
logs only** (7-train tests: space/time/conflict/runtime), *not* importable instances; input format is
internal to the C++/exe. → Use as conceptual reference for the *diagnostic single-track* class and the
meet–pass branching alphabet; no clean data to import.

### D3 — Project's own TR Part B paper  ⟵ reference only
`network_timetabling_literature/part_b_third_submission/network_timetabling-TR_PartB_Feb_25_2014.pdf`
(simultaneous train routing + timetabling, Lagrangian). Plus a strong Lagrangian-relaxation /
column-generation reading list (Caprara, Toth, Ahuja, Keaton, …). Reference, not data.

### Not present locally
Raw RAS-2023 competition files and any larger / mainline networks — would need download for
*medium/stress* scale beyond the metro line.

---

## 2. Algorithm ↔ existing code reuse (compressed_bcp)
The draft's H–B + S₁/S₂/S₃ spectral screening is the **same machinery already implemented** for the
compressed-B&P network-design paper:
- `FORMULATION.md` — H matrix, major/minor SVD split, compressed screening, exact-vs-compressed gap.
- `src/scores.py` — L1/L2/L3 spectral scores (≈ draft S₁/S₃), `src/compressed_screening.py`,
  `src/branch_screen_eval.py` — top-K screen + exact strong-branching gap, Spearman/Hit@K metrics.
- `td_phase1*.py` — recent time-binned column-explosion + SVD-compression experiments.

Mapping: draft `S₁(a)=‖U_kᵀH̃B·a‖≈‖Σ_kV_kᵀB·a‖` ↔ L1/L2; draft `S₃` (left/right footprint
separation) ↔ compressed branch-gap; exact certification ↔ existing exact SB layer. Estimated ~70% of
the prototype is portable; the genuinely new pieces are the **time-space railway instance/column
builder** and the **B (branch-action) matrix** for railway branching (departure-time, meet–pass).

## 3. Honest caveat carried over from the compressed-TA experiments
Screening only helps if it preserves the branch-ranking signal (Containment@K / BoundRecovery@K). In
the compressed-assignment study, aggressive low-rank compression smeared exactly the small
branch-benefit signal (compression error > Δ signal). So for this prototype the SVD rank k, and the
W_R/W_P weighting that concentrates energy on congested/high-dual cells, are the levers to watch —
measure Containment@K vs k explicitly. Per the design dialogue, position as **approximate branching
screening before exact certification** (Lagrangian/ALM lower bound + maintained incumbent), not
"exact branching".
