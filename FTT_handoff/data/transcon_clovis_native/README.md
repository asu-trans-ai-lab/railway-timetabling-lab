# transcon_clovis_native — BNSF Southern Transcon, Belen–Clovis (real ANL/RAS data)

The ANL-corridor starter instance for Wenlin's train-scheduling work plan
(`train_scheduling\WORKPLAN_TRANSCON_SCHEDULING.md`). A 236.6-mile slice of the BNSF
Southern Transcon (Clovis Subdivision, Belen NM → Clovis NM) extracted from the
**RAS 2026 PUBLIC v5.2.2 L3 national network** and expressed in the native FastTrain format.

## Provenance & extraction (`make_instance.py`)

- Backbone: `RAS_2026_PUBLIC\datasets\l3\{node,link}.csv`, BNSF-filtered Dijkstra between the
  named yards **BELEN (10130) → CLOVIS (10154)** — 101 physical links, 236.6 mi, **VAUGHN
  (10348)** on the path. The frozen `path_manifest.csv` lets `--from-manifest` rebuild the
  instance without any access to the 73 MB RAS file.
- Aggregation to **25 controlling blocks / 26 nodes**: boundaries at track-count changes,
  yards, and ≥10 mph speed jumps; >12-mi runs split near 10 mi; sub-0.5-mi same-capacity
  slivers merged (a cap-1 pinch never merges into a double block). Block speed =
  length-weighted harmonic mean of posted `free_speed`; block capacity = min member track
  count (fwd∪rev union — the L3 export populates `tracks` on one direction only).
- Data-gap handling per `ANL_STATUS_REPORT.md`: the uniform `capacity=700000` placeholder is
  **ignored**; posted free speeds substitute for missing observed run times; no siding
  synthesis is needed because the corridor's own single-track pinches are the constraint.

Result: 4 genuine **single-track pinches** — mp 24.9–25.2 (0.3 mi), **mp 95.5–103.6 (8.0 mi,
west of Vaughn — the controlling bottleneck, ~13 min exclusive occupancy per train ⇒ ~4.6
trains/h ceiling)**, mp 114.4–116.1 (1.7 mi), mp 139.0–142.4 (3.4 mi); plus the Abo Canyon /
Mountainair slow zone (28–39 mph, mp 20–40).

## Traffic (`make_trains.py`) — freight-only, peaked

Clovis Sub carries no Amtrak (Southwest Chief uses Raton Pass). Classes per
`corridor_kb.csv` (~90+ trains/day Belen–Clovis): **Z intermodal ×1.25** (~45%),
**M manifest ×1.00** (~40%), **G grain ×0.85** (~15%); free-running times **Z 256 / M 318 /
G 371 min**. Entries are non-uniform: bunched Z fleets + an M/G band, directions offset
15 min. `intended_arrival = entry + freerun`, so free-flow baseline deviation = 0.

- **starter** (this dir): 32 trains, T=960, ~3.0 trains/h combined — congested but solvable.
  (A first draft at 4.8 trains/h exceeded the pinch ceiling: NO sequential rule could route
  all 32 — kept as a documented lesson, see the work plan.)
- **fullday** (`..\transcon_clovis_native_fullday\`): 88 trains, T=1800 — the ~90/day case.
  Single-rule dispatchers currently fail ~25 trains here; making it feasible is the Week-4
  event-dispatcher target in the work plan.

## Baseline results (2026-08-11, all on starter)

| method | objective (dev-min) | bound | validated |
|---|---|---|---|
| B&B (`--bnb --branch dual --sp dijkstra`, 900 s, final semantics) | 904 | **LB 204.4** | export deduped |
| CP-SAT B4 (180 s) | **645** (best known) | its own LB 31 | **validator exit 0, monotone-clean** |
| B0 best rule (edd) | 1379 | — | **validator exit 0** |

*(Final semantics = `MonotoneRouting=1` + cap-weighted LR term; earlier LB 397.3 / UB 959
were pre-correction — see `CORRIDOR_COMPARISON.md` F4. Gap vs best UB: 68.3%.)*

No bound crossings (max LB 397.3 ≤ min UB 645). Holding in the B4 schedule concentrates on
blocks 10–13 — the double-track approaches flanking the Vaughn pinch — confirming the
aggregation put the capacity constraint in the right place. Space-time diagram:
`internal_timetable\incumbent_spacetime.png` (regenerate via `..\..\n_track\plot_incumbent.py`).

## Finding: folded ("hold-and-reverse") trajectories

Because dwell is charged to the *next* link, both SP engines legitimately "wait" by bouncing
onto the link behind (holding on the opposing main). The bug this exposed: a folded path
covers the same (link, minute) cell twice and was double-counted — caught by the independent
validator (3 > 2 on a cap-2 block). Fixed by **per-train occupancy dedup** in B0's commit
(`priority_heuristics.py`), fasttrain's `priority_ub` residual, and the validator's capacity
check (distinct trains per cell, not schedule rows). Toy (12) and harrod (251) regressions
still prove optimal.

## Known modeling gaps
- Speeds direction-symmetric (L3 has no grade data — unlike `harrod_bnsf_native`'s WB/EB split).
- cap-2 consolidation of double track; crossovers implicit at block boundaries.
- Placeholder L3 capacity column ignored by design; siding lengths / train lengths not modeled
  (validator checks 9–10 are Week-6 work in the plan).
