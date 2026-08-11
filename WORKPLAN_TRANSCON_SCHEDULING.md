# Work Plan — Train Scheduling on the ANL Transcon Corridor (Belen–Clovis)

**To:** Wenlin Li
**From:** Simon Zhou
**Date:** 2026-08-11
**Scope:** ~6 weeks, one milestone + one named artifact per week.
**Companion:** my 8-step methodology message (single-train routing → incremental loading →
conflict diagnostics → priority dispatching → beam search/branching → exact comparison →
validated slides). This plan grounds those steps in the ANL corridor data and the existing code
so you extend working tools instead of starting from a blank page.

---

## The corridor and the starter kit (already built — start by running it)

`timetabling_benchmark_lab\train_scheduling\FTT_handoff\data\transcon_clovis_native\` is a
**real-data instance of the BNSF Southern Transcon, Belen–Clovis (Clovis Subdivision)**:
236.6 mi extracted from RAS 2026 PUBLIC v5.2.2 L3 (`make_instance.py`, frozen
`path_manifest.csv` so you never need the 73 MB link.csv), aggregated to **25 controlling
blocks** with **4 genuine single-track pinches** (the 8-mi one at mp 95.5–103.6 west of Vaughn
is the corridor's controlling bottleneck) and the Abo Canyon slow zone (28–39 mph). Traffic
(`make_trains.py`): freight-only — Z-intermodal ×1.25, manifest ×1.0, grain ×0.85 — with
**peaked, non-uniform entries** (bunched intermodal fleets + a manifest/grain band).
Two scenarios: **starter** (32 trains) and **fullday** (88 trains ≈ the ~90+/day Belen–Clovis
evidence in `v0_pipeline\output\corridor_kb.csv`), the latter in the sibling dir
`transcon_clovis_native_fullday\`.

Verified baselines on the starter (2026-08-11, final semantics `MonotoneRouting=1` +
cap-weighted LB): B&B **LB 204.4 / UB 904** (900 s); CP-SAT **645** = best known,
**validator exit 0**; B0 best rule (edd) **1379**, validator exit 0.
Space-time diagram: `internal_timetable\incumbent_spacetime.png`.

**The corridor comparison set (2026-08-11).** Eight more real corridors were extracted the
same way — see `timetabling_benchmark_lab\train_scheduling\CORRIDOR_COMPARISON.md` for the
instance table, results, and three solver defects found and fixed along the way. For your
milestones: W3's incremental study is most instructive on `hiline_native`; W4's acceptance
test is now concrete — **route all trains on `gulf_native`, `moffat_native`, and
`cpkc_midcon_native`, the three corridors where no current method produces a complete
schedule**; W5 compares your beam search against the B&B rows in that report.

Tools you will use (all exist):
- `FTT_handoff\n_track\fasttrain.exe` — LR + **branch-and-bound with valid Lagrangian lower
  bounds** (`--bnb --branch dual --sp dijkstra`). This is the *exhaustive/bounded* reference.
- `benchmark_lab\solvers\priority_heuristics.py` — B0 sequential dispatching, 6 rules +
  random best-of-N; exports schedules.
- `benchmark_lab\solvers\cp_sat_timetabling.py` (B4), `time_indexed_milp.py` (B2, UB-only) —
  exact comparison methods for small cases.
- `benchmark_lab\validate_schedule.py` — the independent validator (8 checks). Exit 0 or it
  doesn't count.
- `FTT_handoff\n_track\plot_incumbent.py` — space-time (Marey) diagram of any B&B incumbent.
- `benchmark_lab\record_schema.py` — the reporting record every run must emit.

---

## Week 1 — Single-train routing (methodology step 1)

Route ONE train of each class (Z/M/G, each direction) over the starter instance with B0.
Verify the five checks from my message: route connectivity, feasible track use, minimum
running time per block, correct event sequence, end-to-end transit time = the free-running
times printed by `make_trains.py` (**Z 256 / M 318 / G 371 min**; deviation must be 0).
Export each schedule and pass `validate_schedule.py`.
**Artifact:** one-train space-time diagram per class + a validator log, in slides.

## Week 2 — Frequency scenario (step 2) — UPDATED per the auditability memo

Your Week-2 deliverable is now the **assumption ledger**, not just a scenario table.
Three audit tools ship in the package (`FTT_handoff\data\`): `make_provenance.py`
(provenance labels per element), `blocking_layer.py` (the simplified blocking chain:
public L3 demand → corridor screenline → cars-per-train → derived trains/day), and
`audit_instance.py` (the memo's three audit levels → `AUDIT_REPORT.md`). Run them, read
the reports, then: (a) convert **your existing transit-time / service-schedule traffic
estimates** into rows of `INPUT_ASSUMPTIONS.md` with explicit evidence labels — that work
is exactly the "externally supported" tier the audits need; (b) where your estimates and
the demand-derived mix disagree, write the explanation into the audit rather than picking
silently; (c) extend — don't replace — the audit checks as you learn corridor facts.

Reproduce the starter and fullday scenarios; write down the headway arithmetic explicitly
(90/day combined = 16-min average combined interval ≈ 32-min per direction — never confuse
combined vs per-direction). Study the throughput ceiling we hit while building the kit: the
8-mi cap-1 pinch imposes ~13 min exclusive occupancy per train ⇒ ~4.6 trains/h maximum;
the first starter draft offered 4.8/h and **no sequential rule could route all 32 trains** —
that is a physical infeasibility signal, not a solver bug. Design one additional
peak-clustered variant of your own (e.g., harder bunching, asymmetric directional peaks).
**Artifact:** a scenario table (trains/day, class mix, peak structure, pinch utilization %).

## Week 3 — Incremental loading + conflict diagnostics (steps 3–4)

Run 1 / 2 / 4 / 8 / 16 / 32 (then 88) trains. For every run record the `record_schema.py`
fields (objective, bounds, gap, `time_to_first_feasible`, `hard_conflicts`). Count conflicts
at all four levels from my message: conflicting resource-time **cells** → distinct train-**pair**
conflicts → distinct **meet/pass events** → **connected conflict groups**. Evidence that the
distinction matters: in the N-track B&B, branching on whole meet/pass *segments* solved the toy
in 19 nodes vs 51 nodes for single-*cell* branching (`FTT_handoff\BRANCH_AND_BOUND_PLAN.md`, B3).
**Artifact:** complexity-growth table + an *unresolved* timetable diagram with conflicts marked
(extend `plot_incumbent.py` to draw the relaxed LR solution and highlight overloaded cells —
small, contained change).

## Week 4 — Event-based dispatcher (step 5 — this is new code, your first contribution)

Build `benchmark_lab\solvers\event_dispatcher.py`: a discrete-event dispatcher processing
arrival / resource-entry / resource-release events, starting FIFO, then adding the
delay-escalation rule from my message (a train held at 2–3 consecutive locations gets a
priority boost). Requirements:
- must route **all** trains on starter and fullday (no 1e6 unroutable penalties — B0's
  sequential insertion currently fails on over-saturated variants; an event dispatcher that
  holds trains at origin should not),
- run it under several tie-breaking orders / random seeds and keep the best (the
  order-sensitivity lesson from `harrod_bnsf_native\README.md`),
- emit `record_schema.py` rows + schedule exports that pass the validator.
**Artifact:** the dispatcher + a B0-vs-event-dispatcher table on both scenarios.

## Week 5 — Beam search over priority disjunctions (step 6)

On top of the Week-4 dispatcher: at each detected conflict create the two-way disjunction
(A before B / B before A), keep the best **K** states by the score in my message (reference-line
deviation, accumulated waiting, consecutive holds, remaining travel, unresolved downstream
conflicts). Compare against `fasttrain --bnb` on the starter scenario. **Naming discipline:**
your method is *beam search* (no bounds, not exhaustive); fasttrain's is *branch-and-bound*
(valid LR bounds, exhaustive). Do not blur them on slides.
**Artifact:** a small search-tree figure (K=2–3, few levels) + beam-vs-B&B objective/time table.

## Week 6 — Exact comparison, validation, ANL slides (steps 7–8)

- Run B4 CP-SAT and B2 MILP on ≤8-train slices of the starter; confirm no bound crossings
  against fasttrain's LR LB.
- Extend `validate_schedule.py` with checks 9–10: **siding/pinch length vs train length** and
  **train-type restrictions** (the `TOB`/`length`/`hazmat` columns exist in every
  `input_train_info.csv` but are read by nothing today).
- Assemble the slide set from my message: one-train routing, two-train meet/pass, unresolved
  timetable with conflicts, conflict table vs volume, beam-search tree, before/after
  space-time diagrams, independent validation report.
**Artifact:** the deck + a one-page validation report for the next ANL status update.

---

## How this feeds the ANL engagement

`ANL_git_internal\ANL_STATUS_REPORT.md` lists the data gaps this corridor work turns into
evidence: (1) **siding locations** — the 4 extracted pinches show what the national network is
missing on the other 8,151 single-track segments; (2) **run times** — posted `free_speed` on
this path yields 256–371-min transits, a defensible substitute for missing observed times;
(3) **capacity** — the block-level min-track capacity replaces the uniform 700000 placeholder
and demonstrably binds (the pinch throughput ceiling). Each weekly artifact doubles as status-
report material for Bijal and Natalia.

## Ground rules (repeated from the 8-step message)

Feasibility before optimality. Conflict counts are diagnostics, not the model. Every schedule
that goes on a slide has passed the independent validator. Never call beam search
branch-and-bound. If a run fails, the failure mode (which trains, where, why) IS the result —
report it.
