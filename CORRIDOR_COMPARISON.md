# ANL Corridor Comparison — real-data timetabling benchmarks from the RAS L3 network

**Date:** 2026-08-11 · **Author:** Claude (rail-expert session for Simon Zhou)
**Scope:** 9 corridor instances extracted from RAS 2026 PUBLIC v5.2.2 L3, spanning the full
North American capacity spectrum, each with a calibrated peaked freight scenario and
solution baselines from priority dispatching (B0), LR-based branch-and-bound (fasttrain
C++), and CP-SAT spot checks. All instances live in
`FTT_handoff\data\<name>_native\` and are consumed unchanged by fasttrain and every
benchmark_lab solver.

## 1. How the set was built

1. **Scout** — three parallel agents probed 12 corridors from the ANL corridor knowledge
   base (`v0_pipeline\output\corridor_kb.csv`) against the L3 physical network with
   `data\corridor_scout.py` (anchor-yard Dijkstra + track-profile report). 11 proved
   extractable; 9 were selected for regime diversity (two homogeneous controls skipped:
   NS Chicago Line, UP Sunset short).
2. **Extract** — `data\extract_corridor.py`: BNSF/UP/NS/CN/CPKC-filtered Dijkstra between
   named yards, `tracks` healed via fwd∪rev union, aggregation to controlling blocks
   (track-count / yard / ≥10 mph boundaries; >12-mi runs split ~10 mi ⇒ synthesized siding
   spacing on long single-track; cap-1 pinches never merged), harmonic-mean block speeds,
   placeholder capacity ignored. A frozen `path_manifest.csv` per instance removes any
   further dependence on the 73 MB RAS file.
3. **Traffic** — peaked, class-mixed scenarios (Z ×1.25 / M ×1.00 / G ×0.85) with a
   **pinch-ceiling guard**: offered combined rate capped at 70% (later 45–50% for
   single-track-dominant lines) of the tightest cap-1 block's throughput ceiling.
   `intended_arrival = entry + free-run`, so objective = pure conflict delay.
4. **Solve** — B0 (6 rules + best-of-N), fasttrain `--bnb --branch dual --sp dijkstra`
   (valid LR bounds), CP-SAT spot check, independent validator on exports.

## 2. The instances

| instance | corridor (anchors) | mi | blocks (cap-1) | single % | speeds mph | trains | regime |
|---|---|---|---|---|---|---|---|
| `overland_native` | UP Overland, N Platte–Grand Island | 141.8 | 16 (0) | 0% | 21/49 | 40 | triple-track fleeting |
| `panhandle_native` | BNSF Transcon, Amarillo–Waynoka | 207.0 | 24 (2) | 1% | 14/49 | 32 | double track, twin 1.2-mi pinches |
| `transcon_clovis_native` | BNSF Transcon, Belen–Clovis | 236.6 | 25 (4) | 6% | 7/49 | 32 (+88 fullday) | double track, 8-mi Vaughn pinch |
| `harrod_bnsf_native` | Harrod (2011) §6 synthetic-real | 56.2 | 6 (0) | 0% | 24–64 | 22 | double track, literature setup |
| `prb_joint_native` | BNSF/UP PRB Joint Line, Gillette–Guernsey | 177.1 | 27 (8) | 17% | 7/42 | 24 | 77-mi triple core + pinches, coal |
| `pocahontas_native` | NS Pocahontas, Roanoke–Portsmouth | 308.7 | 40 (9) | 12% | 18/32 | 16 | slow coal grades, 20.8-mi pinch |
| `hiline_native` | BNSF Hi-Line, Havre–Wolf Point | 203.8 | 21 (13) | 73% | 14/55 | 16 | prairie single track, 10-mi sidings |
| `cn_icmain_native` | CN IC main, Champaign–Centralia | 130.3 | 22 (12) | 84% | 14/55 | 20 | fast single track, 3 passing islands |
| `gulf_native` | BNSF Gulf, Temple–Galveston | 216.9 | 25 (19) | 93% | 14/39 | 12 | single track, few siding islands |
| `moffat_native` | UP Moffat, Denver–Glenwood Spgs | 185.8 | 25 (21) | 96% | 14/49 | 10 | mountain single track |
| `cpkc_midcon_native` | CPKC Mid-Con, Pittsburg–Heavener | 209.8 | 20 (18) | 99% | 41 | 12 | flat uniform single track |

Sidings on long single-track runs are **synthesized at ~10-mi spacing** (block boundaries =
passing points) because L3 records no siding locations — this instantiates and quantifies
data gap #2 of `ANL_STATUS_REPORT.md`.

## 3. Results (monotone semantics, cap-weighted LB — see §5)

Objective = Σ|arrival − intended| (deviation-minutes). "B0 best" = best of 6 dispatching
rules + random best-of-5 (∞ = some trains unroutable under every rule). B&B budgets:
300–900 s wall-clock, 5000 nodes. Overland/Moffat rows reflect the 2026-08-11
intended-arrival quantization repair (`fix_intended.py` — caught by the operational
audit's baseline-zero check).

| instance | B0 best | B&B LB | best UB (method) | gap | status |
|---|---|---|---|---|---|
| `overland_native` | 260 | 85.8 | **86** (B&B, 202 s) | **0.19%** | near-proven |
| `panhandle_native` | 436 | 153.2 | **364** (B&B, 900 s) | 57.9% | feasible, bound-limited |
| `transcon_clovis_native` | 1379 | 204.4 | **645** (CP-SAT 180 s; B&B 904) | 68.3% | feasible, bound-limited |
| `harrod_bnsf_native` | — | 160.3 | **316** (B&B, 268 s) | 49.3% | feasible, bound-limited |
| `prb_joint_native` | 321 | 140.2 | **290** (B&B, 900 s) | 51.6% | feasible, bound-limited |
| `pocahontas_native` | 442 | 205.2 | **206** (B&B, 422 s) | **0.40%** | near-proven |
| `hiline_native` | ∞ | 175.7 | **2855** (B&B — only method to route all 16) | 93.9% | feasible, bound-limited |
| `cn_icmain_native` | 1910 | 139.3 | **1910** (B0 edd; B&B 2144) | 92.7% | feasible, bound-limited |
| `gulf_native` | ∞ | 230.8 | none (best: 2 unroutable) | — | **OPEN** |
| `moffat_native` | ∞ | 231.4 | none (best: 3 unroutable) | — | **OPEN** |
| `cpkc_midcon_native` | ∞ | 179.7 | none (best: 5 unroutable) | — | **OPEN** |

CP-SAT spot check (`transcon_clovis_native`, 180 s): **UB 645, validator exit 0** — the
best known Belen–Clovis schedule and monotone-clean.

## 4. Findings

**F1 — What closes and what doesn't.** The gap closes (<0.5%) exactly where the *binding
resource is unambiguous*: Overland (capacity so abundant the relaxed solution is
near-feasible) and Pocahontas (delay concentrated on cap-1 pinches, which the LR prices
directly). Consolidated cap-2 double-track corridors keep 49–68% Lagrangian duality gaps
even with good feasible schedules — pricing a 2-capacity cell is where this dualization is
weakest. Feasibility (all trains routed) follows the capacity spectrum: multi-track always,
single-track-dominant only via B&B (Hi-Line) or not at all (Gulf/Moffat/CPKC).

**F2 — Greedy dispatching has a feasibility cliff.** Sequential insertion (any rule)
routes every train on corridors up to ~20% single-track share and fails on ≥73% even at
45% of the physical throughput ceiling. On single track, feasibility itself — not delay —
is the hard part, which is why meet-planning lookahead (B&B, beam search, event
dispatching with planned holds) is not optional there.

**F3 — Passing-opportunity spacing, not single-track share, sets the difficulty.** CN
(84% single but three real passing islands + fast 55 mph running) stays greedy-feasible at
high delay, while Gulf (93%, islands ~60 mi apart) does not. The binding parameter is
sidings-per-train-hour, which is exactly the siding-location data ANL's L3 export lacks.

**F4 — Three defects found and fixed by cross-checking methods against each other:**
1. *Folded-trajectory double-count* — both SP engines "waited" by bouncing onto the link
   behind; the fold's occupancy was double-counted (validator caught 3 trains on a cap-2
   block). Fixed by per-train occupancy dedup (B0 commit, `priority_ub` residual,
   validator distinct-train counting).
2. *Fold-dependent bound semantics* — SP dual cost charged folds at multiset rates while
   feasibility deduped, making "proven optimal" labels unsound where folding paid off
   (Overland B0 177 vs "proven" 180). Fixed by **`MonotoneRouting=1`** (trains never
   reverse) on all corridor instances — physically right for freight and it makes cost ≡
   occupancy. Old fold-era numbers are superseded.
3. *Cap-unweighted Lagrangian term* — `LB = Σtrip − Σρ` instead of `Σρ·cap`: correct on
   Meng–Zhou's all-cap-1 networks, silently invalid on consolidated cap-2/3 blocks
   (exposed by LB 318 > UB 313 on harrod). Fixed; **RAS Set 3 reproduction re-verified
   bit-identical** (LB 2278.9 / UB 3803).

**F5 — The Lagrangian bound is the corridor bottleneck.** With the corrected (weaker but
valid) LB, single-track corridor gaps are large even where good feasible schedules exist.
This matches the RAS Set 3 finding (`BRANCH_AND_BOUND_PLAN.md` §7): the lever is a
stronger relaxation (cuts, LP master, ADMM consensus), not more nodes.

## 5. Semantics of record

All corridor results use: **monotone routing** (no reversing; `MonotoneRouting=1` in
`FTSettings.ini`, honored by fasttrain and `priority_heuristics.py`, enforced as validator
check 9), **per-train set occupancy** (one train = one occupancy per cell), and the
**cap-weighted LR bound**. General-network instances (RAS Set 3) keep the original
Meng–Zhou multiset semantics — see the open audit chip on LB validity there.

## 5b. Auditability (added 2026-08-11, per the realism/auditability memo)

Every corridor instance now carries three machine-generated evidence artifacts
(regenerate with the commands in §7):
- **`provenance.csv`** — every network/service element labeled
  `observed | externally_supported | inferred | synthetic` with an evidence string
  (`make_provenance.py`). Example: link lengths/speeds = observed (L3 export); block
  aggregation & capacities = inferred (documented rules); ~10-mi siding spacing and peak
  structure = synthetic; class multipliers & cars-per-train = externally supported
  (Harrod Tables 4/7; Wenlin `s1_block_to_train.py`).
- **`service_derivation.csv` + `block_summary.csv`** — the simplified blocking chain
  (`blocking_layer.py`): public L3 `demand.csv` (cars/70-day by block type) → corridor
  screenline assignment (two anchor Dijkstras, 2% tolerance) → cars-per-train → **derived
  trains/day by direction and class**. Known limitation, stated in every audit: the public
  demand release is volume-sampled, so derived absolute levels are a floor; the usable
  evidence is the **class mix** and **directional balance** (e.g. Belen–Clovis: derived
  53% intermodal vs assumed 44%; EB/WB split 47/53 vs symmetric scenario).
- **`AUDIT_REPORT.md`** — the memo's three audit levels (`audit_instance.py`):
  *network* (chaining/connectivity, length conservation vs the L3 manifest, capacity and
  speed plausibility, meet/overtake capability, provenance coverage), *service* (assumed
  vs derived mix and balance, class-weighted worst-pinch utilization, transit-time
  sanity), *operational* (free-flow baseline 0, finite LR bound, B0 routability or
  documented OPEN status, independent validator exit 0). Fleet-wide roll-up:
  `FTT_handoff\data\AUDIT_SUMMARY.md`; assumption ledger: `INPUT_ASSUMPTIONS.md`.

## 6. What Wenlin should do with this set

- **W1–W3** of `WORKPLAN_TRANSCON_SCHEDULING.md` run unchanged on any instance here; the
  incremental-loading study (W3) is most instructive on `hiline_native`.
- **W4 (event dispatcher):** acceptance = route all trains on `gulf_native`,
  `moffat_native`, `cpkc_midcon_native` — the three corridors where *no current method*
  produces a complete schedule. They are the benchmark frontier.
- **W5 (beam search):** compare against the B&B rows above at equal wall-clock.
- **W6:** extend CP-SAT to the multi-track corridors (its transcon result suggests it may
  beat the LR-B&B UB wherever the model fits in memory).

## 7. Reproduce

```bash
cd train_scheduling/FTT_handoff/data
python corridor_scout.py --railroad BNSF --from BELEN --to CLOVIS      # scout any corridor
python extract_corridor.py --name X_native --railroad RR --src ID --dst ID ...  # build instance
cd ../n_track
./fasttrain.exe ../data/X_native --sp dijkstra --bnb --branch dual --time 600   # solve
python plot_incumbent.py ../data/X_native                                        # diagram
cd ../../benchmark_lab
python solvers/priority_heuristics.py ../FTT_handoff/data/X_native --rand-n 5   # B0
python validate_schedule.py ../FTT_handoff/data/X_native results/schedules/X_native_B0.csv
```
