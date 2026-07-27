# Paper Framework — the unified train-scheduling research line

Three papers, one arc: *dual-price coordination → joint-transition interaction visibility.*
Every claim maps to a runnable engine + instance in `unified_testbed/`.

## Paper A — Single-track screening (WRITTEN: `FTT_handoff/docs/single_track_paper/`)
Gradient-weighted compressed-response screening on the exact Zhou–Zhong single-track B&B.
Findings: CC-LB cuts nodes 7.5→35.6%; *learned* screen contains Full-SB@5 on 92% of nodes and
transfers; *unsupervised* spectral score fails; no node-count speedup at small |A|.

## Paper B — N-track LR-B&B + honest screening (WRITTEN: `FTT_handoff/docs/ntrack_bnb_paper/`)
Faithful Meng–Zhou LR reproduction; exact LR-B&B (2005 segment enumeration; disjunction ≠ selection);
DAG SP ~2.9×. Honest screening result: single-node SVD success is an n=1 artifact — across nodes no
unsupervised SVD/tensor score beats baselines. Key structural finding: **the gap is bound-limited, not
compute-limited** (global LB pinned at the root LR).

## Paper C — Joint-transition visibility / group supercolumns (MAIN, frozen: `group_supercolumn/DESIGN_FREEZE.md`)
Papers A+B are the motivation: (A/B) unsupervised screening cannot fix branch selection, and (B) the LR
bound itself is the binding constraint → make strong local interactions **primal-visible** in small joint
groups instead of pricing them; FO pricing certifies, quadratic companion selects; three certification
levels; claim L2 (exact fixed-partition) + anytime + exact fallback.

### Evidence map (what already exists, engine → claim)
| paper section | engine / instance | status | headline evidence |
|---|---|---|---|
| §motivation: dual oscillation | jtv E2 | **run** | LR leaves 1 conflict + oscillates (TV 39.2); pair joint DP: 0 conflicts, 879 states |
| §master: convexification | jtv E3 | **run** | individual-column LP gap 0.5 (100% fractional) → group supercolumn LP gap **0.0** |
| §group size cost | jtv E4 | **run** | states 756→90k for group 1→4: the K_max budget argument |
| §CG ladder M1–M3 | jtv | **run** | M2 hard-feasible group CG reaches joint optimum; M3 quadratic companion adds 8 candidates over 9 iters |
| §corridor screening | ras_flatland Tier 1/2 | **run** (verified on Windows) | reduced ≡ full DP on reported cases; 63–94% state reduction, 2–14× speedup; corridor 12: full-DP states bit-identical to reference |
| §scaling law | ras_flatland Tier 2/3 | **run** | 12→48 trains with component size fixed at 3: cost tracks component size, not population |
| §control loop variant | control_trajectory | built | prices+precedence+corridor+accept/reject; corridors 8–64 |
| §exact anchors / fallback | fasttrain (ours) | **validated** | toy proven optimum 12; RAS Set 3 LR reproduction; exact LR-B&B |
| §planted-truth ladder G1–G3 | group_supercolumn generator | planned | hand-checkable joint optima + hard-coupling manifests |

### Reference documents (`../GPT_references/`, pristine)
`joint_transition_visibility_draft.pdf` (paper C draft) · `Joint_Space_Quadratic_Pricing_Prototype_Report.pdf`
(prototype report) · `Conflict_Localized_Quadratic_Joint_Pricing_Research_Landscape.pdf` (related-work
landscape) · `Subdimensional expansion for multirobot path planning.pdf` (M* — the key MAPF anchor: joint
state space only where conflicts occur; cite and differentiate: we add prices/master/certificates).

### Honesty rules (carried from A/B)
Report boundary cases (dense components defeat tight corridors — seen on our converted toy); label
single-node results as n=1; corridor exactness is *verified per case*, not a general theorem (the
admissible lower-bound corridor rule stays future work); no SVD/learned grouping claims in the prototype.

### Writing order
1. Paper C core = frozen outline (§1–§11 in DESIGN_FREEZE assessment) with jtv numbers as the mechanism
   section and ras_flatland as the scaling section.
2. Then G1–G3 planted-truth + RAS Set 3 unified run replace/augment the synthetic corridors.
3. A/B papers stay standalone (already written) and are cited as the motivating negative results.
