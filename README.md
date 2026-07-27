# Train Scheduling — dedicated project folder

All train-timetabling work lives here. Layout:

| folder | role |
|---|---|
| `spectral_ttbl/` | **Active dev workspace** — canonical sources: `fasttrain.cpp/.exe` (N-track LR + LR-B&B + DAG SP), `nt_spacetime/nt_screen/nt_tensor/mz_lr.py` (LP master + screening), single-track research (`ttbl_bnb`, `learn_branch`, `screening_theory`, `tensor_*`). Data auto-resolves to `../../Meng_Zhou_RAS_network_timetabling/` or `../FTT_handoff/data/`. |
| `group_supercolumn/` | **Next paper (frozen design)** — group-supercolumn CG with joint-transition visibility: `DESIGN_FREEZE.md` (10 frozen decisions), `CODE_DESIGN.md` (M0–M4 source design + dataset generator), `include/gsc_types.h` (frozen structs). |
| `unified_testbed/` | **Stage 0: mechanism verification** — the three GPT-reference prototypes vendored + verified on Windows (`jtv` Python mechanism prototype, `ras_flatland` corridor-DP scaling benchmark, `control_trajectory` control loop), one instance schema (+ `convert_native.py` from our native format). See its README. |
| `benchmark_lab/` | **Stage 1: the classical railway scheduling laboratory** (authoritative plan `PLAN.md`): B0–B7 classical solvers + P1–P3 proposed methods, feasible-first generators (C1/C2/CM/G families, canonical record (k,i,j,i′,j′,t,t′,s,s′)), common reporting record (`record_schema.py`), visualization views A–E, experiments A–F. RAS instances are the mandatory regression family. |
| `PAPER_FRAMEWORK.md` | The three-paper arc (A single-track ✓, B N-track B&B ✓, C joint-transition visibility = main) with the claim→engine→evidence map. |
| `FTT_handoff/` | **Frozen snapshot package** (self-contained: code + data + docs + both papers). Don't develop here; re-snapshot from `spectral_ttbl/` when needed. |
| `FTT_handoff.zip` | The shipped archive (995 KB) of the snapshot. |
| `single_track_paper/` | Original single-track paper working copy (canonical build also in `FTT_handoff/docs/`). |

Pristine external references: `../GPT_references/` (three prototype packages + paper draft + landscape
PDFs + M* paper) — vendored copies live in `unified_testbed/engines/`; never edit the originals.

Reference data (kept at `Timetabling_project/` root, shared with other efforts):
`../Meng_Zhou_RAS_network_timetabling/` (RAS instances + original FastTrain C++),
`../TrainTimetablingLite-master/` (Zhou–Zhong single-track C++).

Quick starts
```
cd spectral_ttbl
g++ -O2 -std=c++17 -static -o fasttrain.exe fasttrain.cpp
./fasttrain.exe ../FTT_handoff/data/RAS_set3_native            # LR reproduction
./fasttrain.exe ../FTT_handoff/data/toy_native --bnb --gap 0   # proven-optimal B&B
python nt_screen.py --inst toy                                  # LP screening
python nt_tensor.py --inst ds3 --max_cand 8 --max_nodes 6       # multi-node tensor screening
```
Status and findings: `FTT_handoff/README.md`, `FTT_handoff/BRANCH_AND_BOUND_PLAN.md`,
`FTT_handoff/docs/ntrack_bnb_paper/main.pdf`. Next work: `group_supercolumn/CODE_DESIGN.md`.
