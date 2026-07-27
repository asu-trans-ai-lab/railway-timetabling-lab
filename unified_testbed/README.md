# Unified Testbed — train scheduling engines, one instance schema, one results schema

Integrates our validated solvers with the three GPT-reference prototypes
(`Timetabling_project/GPT_references/`, kept pristine) into a single scalable testing environment.

## Engines (`engines/`, all verified on this machine)
| engine | language | role | verified |
|---|---|---|---|
| `../spectral_ttbl/fasttrain` | C++17 | M0 arc-time LR + exact LR-B&B (native format) — ground truth & exact fallback | RAS Set 3 LR reproduction; toy proven optimal |
| `jtv/` | Python | Minimum mechanism prototype of the frozen design: compressed state, pair joint DP, LR baseline, individual + group-supercolumn masters, quadratic companion, integer master; experiments E2/E3/E4 + M1–M3 | `pytest -q` → 2 passed |
| `ras_flatland/` | C++17 | Conflict-localized joint-state DP with quadratic corridor screening; scaling benchmark (RAS corridors 12/24/48, Flatland-like 40/80 trains) | MinGW build clean; `ras_corridor_12`: 6→0 conflicts, full-DP states **identical** to packaged reference (72,530/148,822) |
| `control_trajectory/` | C++17 | Coordination-agent (prices+precedence+corridor+accept/reject) over trajectory agents; corridors 8/16/32/64 | MinGW build clean |

Windows notes: the C++ solvers call `mkdir -p` via `system()` → harmless "syntax of the command is
incorrect" message; **pre-create the results dir**. Build: `g++ -O2 -std=c++17 -static -o <name>.exe src/<name>.cpp`.

## Unified instance schema (`instances/`)
Adopted from the GPT prototypes (already consumed by two C++ engines + generators + Flatland adapter):
`nodes.csv` (node_id,x,y,resource_id,kind) · `arcs.csv` (arc_id,from,to,base_cost,resource_id) ·
`agents.csv` (agent_id,start,target,earliest_departure,preferred_arrival,latest_arrival,move_interval,
train_class,wait_cost,tardiness_cost,prox_weight,spacing_weight,desired_spacing) · `metadata.json`.
Conventions: exclusive resources are unit cells (resource_id ≥ 0; −1 = uncapacitated); **per-agent
private_start/private_goal staging nodes** (one-way 0.5-cost arcs) so shared origins/terminals never
register as infrastructure conflicts (Flatland waiting/DONE_REMOVED semantics).

### `convert_native.py` — native FastTrain → unified
Expands each link into a chain of unit cells at directional travel time; one exclusive resource per cell
shared by both directions (meet/pass = cell contention); private staging per train; deviation objective →
tardiness_cost=1, wait_cost=0. **v0 caveats (in metadata.json):** class-average speed (per-train smult not
per-link-honored), cap≥2 → non-exclusive, MOW not converted.
Verified: `toy_native` → 16 nodes/24 arcs/3 agents; `joint_timetable_solver` resolves 30→**0** conflicts
with `--budget=40 --rho=0.05 --radius=3` (full joint DP 89,167 states, 63 ms).
**Lesson:** tight corridors (budget 8, radius 0) fail on dense same-direction components — exactly the
prototypes' documented boundary case; corridor parameters are per-instance tuning, not universal.

## Instance pool
| family | source | sizes | role |
|---|---|---|---|
| `toy_native` (+ `instances/toy_native_unified`) | ours | 3 trains | hand-checkable exactness (optimum 12) |
| `RAS_set3_native` | ours (FTT_handoff/data) | 40 trains | the real reproduction instance (native engines; conversion = next step) |
| `ras_corridor_12/24/48` | engines/ras_flatland | 12–48 trains | component-size scaling (Tier 2) |
| `flatland_like_20x20_40 / 30x30_80` | engines/ras_flatland | 40/80 trains | grid scaling + route choice (Tier 3) |
| `control_corridor_8/16/32/64` | engines/control_trajectory | 8–64 trains | control-loop scaling |
| G1-pair / G2-chain / G3-ras | `../group_supercolumn/CODE_DESIGN.md` | planned | planted-truth generator ladder |

## Unified results schema
Per run: `summary.csv` (one row: conflicts before/after, components, groups solved/failed, reduced vs full
states/transitions, walls, parameters) · `schedules.csv` (agent,time,node) · `components.csv` (per-group
reduced-vs-full exactness) · engine-specific extras (`iterations.csv`, CG histories). Aggregate with each
engine's `collect_results.py`; cross-engine comparison keys on (instance, engine, config).

## Experiment ladder (paper-facing; merged from the frozen design + the prototypes)
- **Mechanism (jtv, done):** E2 dual oscillation → joint DP resolves what LR oscillates on; E3 fractional
  master → group supercolumns close the integrality gap (0.5 → 0.0); E4 group-size cost growth; M1–M3 CG.
- **Scaling (ras_flatland, done on packaged cases):** Tier 1 exactness (reduced ≡ full DP), Tier 2
  component-size scaling (12→48 trains, complexity tracks component size not population), Tier 3 grids,
  corridor-budget sensitivity B∈{2..40}.
- **Ground truth (ours):** fasttrain exact B&B for optimal objectives on native instances; M0/M4 anchors.
- **Next:** RAS Set 3 through the unified pipeline; G1–G3 planted-truth ladder; control_trajectory
  accept/reject loop on the same pool.
