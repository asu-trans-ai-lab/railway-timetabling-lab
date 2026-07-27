# Train Scheduling — Handoff Package

Self-contained snapshot of the **single-track** and **N-track** train-timetabling code:
the faithful solvers, the spectral / gradient-weighted branching-screening research, and the
reproduction of Meng & Zhou (2014). The next milestone — **branch-and-bound on the N-track LR to
close the optimality gap** — is specified in [`BRANCH_AND_BOUND_PLAN.md`](BRANCH_AND_BOUND_PLAN.md).

## Layout
```
FTT_handoff/
  single_track/   single-track B&B + spectral/tensor branching-screening research (Python)
  n_track/        N-track LR solver (clean C++ + Python)
  data/           all instances (native + arc format) + Meng-Zhou published results (see data/README.md)
  docs/           plans, theory<->evidence, dataset inventory, single-track paper draft
  README.md, BRANCH_AND_BOUND_PLAN.md
```

## N-track (the foundation to extend) — `n_track/`
| file | what |
|---|---|
| `fasttrain.cpp` | **Clean, std-only C++17 reimplementation** of Meng-Zhou FastTrain LR (no MFC / windows.h). Per-train time-dependent shortest path + dualized link capacity + subgradient (LB) + priority-rule (UB). This is the canonical solver to add B&B to. |
| `mz_lr.py` | Python port of the same LR (slower; cross-checks the C++). |
| `nt_spacetime.py` | N-track space-time **LP** master (path columns + cell-time capacity); yields LP duals. |
| `nt_screen.py` | Single-node B&P screening on the LP: primal-gw, dual-gw, **primal-SVD, dual-SVD** (compressed-response) vs Full-SB. |
| `nt_tensor.py` | **Multi-node** screening walk: per-node response **tensor** `R[action,cell,{primal,dual}]`, HOSVD/CP scores, primal/dual/SVD/tensor vs Full-SB across nodes. |
| `tensor_lib.py` | CP-ALS / HOSVD / reduced-rank regression / containment metrics (shared). |

All datasets live in **`data/`** (see [`data/README.md`](data/README.md)).

**Build & run the C++ (portable):**
```
cd n_track
g++ -O2 -std=c++17 -static -o fasttrain.exe fasttrain.cpp
./fasttrain.exe ../data/RAS_set3_native    # LB/UB trajectory + ../data/RAS_set3_native/summary_log/Summary*.csv
./fasttrain.exe ../data/native_testbed_small   # smaller instance (no train_info -> 0 trains; topology test)
./fasttrain.exe ../data/RAS_set3_native --selftest   # B1 unit test: verify time-windowed forbidding in tdsp()
./fasttrain.exe ../data/toy_native --bnb --gap 0     # B&B -> PROVEN OPTIMAL (gap 0%, dev 12) on the toy
./fasttrain.exe ../data/RAS_set3_native --bnb --nodes 200 --gap 0.05   # B&B on RAS (node/gap budget)
```
The branch step has two swappable parts (neither affects correctness, only tree size / speed):
- `--split segment|cell` — **how to split a conflict.** `segment` *(default)* is the **Zhou & Zhong 2005**
  enumeration: commit one train's segment on the contested link, the other yields past it (LR offsets the
  rest). `cell` forbids one train from just the conflict cell.
- `--branch most-violated|earliest|latest|dual|strong` — **which conflict to fix first** (the selection
  criterion). `dual` = gradient-weighted screening score `ρ_{l,t}·violation` (B6); `strong` = top-K
  dual-screened look-ahead (the oracle `dual` is judged against). `--strong-k K` sets the screen width.
- **B5 speed knobs:** `--root-iters N` (root LR depth, default = ini MaxIter), `--child-iters N` (per-child
  LR depth, default 3 — children warm-start from the parent so they re-tighten cheaply), `--time S`
  (wall-clock budget; stops cleanly with a final gap report). The UB heuristic now runs once per node
  (not per LR iteration). Together these cut per-node cost several-fold so the tree goes deeper in fixed time.
- **Shortest-path engine:** `--sp dag|dijkstra`. The time-expanded graph is a DAG, so the heap-free
  time-layered DP (`dag`, *default*) is exact and **~2.9× faster** than the classical Dijkstra (RAS root LR
  7.3 s → 2.5 s); `dijkstra` is kept for verification / non-DAG model changes. (Per-SP costs are identical;
  on optimal-path ties the two may pick different equal-cost paths, so LR trajectories can differ slightly —
  both valid. An *approximate* SP is **not** allowed: it would break the LR lower-bound validity.)
**B&B progress (see `BRANCH_AND_BOUND_PLAN.md`):** B1 (per-train time-windowed `Restrictions` in `tdsp()`,
`--selftest` PASS), B2 (`bound_node()` — warm-started LR bound under node restrictions; root reproduces LB/UB
exactly), B3 (`find_conflict()` + provably-complete single-cell 2-way branching), and B4 (`run_bnb()`
best-first driver: warm-started nodes, fathom by bound/feasibility, global gap, `--bnb`) are **done and
validated** — the native toy closes to **gap 0.00% (LB=UB=12, optimum)** with the tree exhausted. Next: B5
(scale/tune on RAS Set 3), B6 (screening-guided branch selection).
Reproduced result (10 LR iterations): **LB 1785 → 2279, UB 4787 → 3803, gap 62.7% → 40.1%** — matches the
original C++ (LB 1844→2359, UB 4153) and the paper's `summary.xlsx` (LB ≈ 1923, best UB ≈ 3471, gap ≈ 45%).
Conventions: objective = `Σ|arrival − intended|`; run time `max(1,int(length·60/(speed·mult)+1))`; free
departure slack 1200; dwell only on siding links (type 4); headway 3 inflates occupancy; `LB = ΣtripPrice −
Σprice`. The original MFC source + its MinGW shim build live in
`../../Meng_Zhou_RAS_network_timetabling/FastTrain/FastTrain/{*.cpp, build_port/}` (reference only; the
clean rewrite supersedes the shim).

## Single-track (validated engine + research) — `single_track/`
| file | what |
|---|---|
| `ttbl_bnb.py` | Faithful single-track B&B (Zhou-Zhong 2007): meet-pass precedence branching, crossing-conflict LB. Validated exact; CC-LB cuts nodes 7.5%→35.6% (6→14 trains). |
| `tt_lb_sweep.py` | LB node-reduction sweep across train counts. |
| `learn_branch.py` | Strong-branching conflict-resolution search + learned gradient-aware screen; Containment@K vs Full-SB / pseudo-cost (learned 0.92@5). |
| `screening_theory.py` | Compressed-response screening run on train scheduling (gradient-weighted tail, tie cluster K'). |
| `tensor_features.py`, `tensor_response.py`, `tensor_lib.py` | Rich 5-layer feature + multi-metric response tensor; CP/Tucker/HOSVD; transfer screening. |
| `lowrank_probe.py` | Low-rank probe of the trajectory/action response field. |

Run examples: `python ttbl_bnb.py --trains 8`; `python tt_lb_sweep.py 6,8,10,12`;
`python learn_branch.py --trains 6`. (Python 3.10+, numpy/scipy/scikit-learn.)

## Key findings carried in `docs/`
- **`SPECTRAL_BRANCHING_PLAN.md`** — Stages A-C (the single-track B&B, screening, results).
- **`SCREENING_THEORY_AND_EVIDENCE.md`** — gradient-weighted screening: theory ↔ real numbers; the
  *unsupervised* spectral score fails, the *learned/gradient-aware* one works; screen a tie cluster K',
  certify exactly. No node-count speedup on single-track (small |A|) — the payoff regime is N-track.
- **`TENSOR_RESPONSE_LEARNING_PLAN.md`** — response-tensor framing; structure + transfer confirmed,
  low-rank = parsimony (not accuracy); the *trajectory* layer drives screening, not the dual proxy
  (single-track) — true LP/LR duals (N-track) are where the dual layer should bite.
- **`DATASET_INVENTORY.md`** — data inventory.
- **`single_track_paper/`** — the single-track screening paper (`main.tex` + `main.pdf`).
- **`ntrack_bnb_paper/`** — the **N-track paper** (`main.tex` + `main.pdf`): LR reproduction, LR-based
  B&B (segment enumeration), DAG shortest path, and the **honest** primal/dual/SVD/tensor screening study
  (single-node success that does not replicate multi-node) + the bound-limited-gap finding.

## Status & next step
- Single-track: exact B&B + screening research **done and validated** (`single_track_paper/`).
- N-track: LR **reproduced**; **LR-based B&B (B1–B6) built and validated** (toy proven optimal; RAS gap
  40%→~31%), with a **~2.9× faster exact DAG shortest path**. Screening: an SVD compressed-response score
  shone at a single root node but **did not replicate across nodes** — so the robust screen must be
  *learned*, not unsupervised (`ntrack_bnb_paper/`, `BRANCH_AND_BOUND_PLAN.md §7`).
- **Key finding**: the optimality gap is **bound-limited, not compute-limited** — the global LB does not
  lift above the root LR. Next: a stronger relaxation (cuts / ADMM consensus bound) to raise the LB, and a
  learned transfer screen over the primal+dual+tensor features.
