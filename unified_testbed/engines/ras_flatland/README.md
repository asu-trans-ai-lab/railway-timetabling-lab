# RAS/Flatland Conflict-Localized Joint Timetabling Benchmark

A larger-case prototype for **conflict-localized quadratic joint-state dynamic
programming** on railway time-space-state networks.

The package combines:

```text
Python benchmark generation and plotting
    +
C++17 sparse joint-state DP
    +
RAS-style single-track/network timetabling cases
    +
Flatland-like switch-grid cases and an optional RailEnv adapter
```

## What is implemented

1. Independent train schedules on a directed rail network.
2. Cell/resource and edge-swap conflict detection.
3. Interaction components built from active conflicts.
4. Temporary pair/triple hyper-agent lifting.
5. Synchronous time-layered joint-state DP.
6. Quadratic proximal deviation and soft spacing terms.
7. Hard non-overlap and resource-capacity constraints.
8. Ellipsoidal corridor screening.
9. Full-versus-reduced DP comparison.
10. Iterative conflict-localized repair for large populations.

The quadratic corridor does **not** reduce the nominal Cartesian dimension. It
reduces the effective explored region around the incumbent reference schedule.

## Benchmark families

### RAS-style network timetabling

- `ras_corridor_12`: six opposing-train meet/pass components.
- `ras_corridor_24`: eight local three-train components.
- `ras_corridor_48`: sixteen local three-train components.

These are synthetic railway-timetabling cases inspired by the network-based
scheduling and capacity themes commonly presented in the INFORMS Railway
Applications Section. They are **not** official RAS competition datasets. The
current 2026 RAS competition concerns railroad blocking rather than timetabling.

### Flatland-like switch grids

- `flatland_like_20x20_40`: 40 trains in localized switch communities.
- `flatland_like_30x30_80`: 80 trains in localized switch communities.

The cases reproduce central Flatland concepts: a 2-D rail grid, exclusive cells,
synchronous actions, switches, heterogeneous speed intervals, release times, and
arrival windows. They are generated locally and do not require Flatland.

`python/flatland_adapter.py` can export an installed Flatland `RailEnv` into the
same CSV format. Oriented Flatland configurations become network nodes because
allowed movements depend on cell **and orientation**.

## Build and run

```bash
make
python python/generate_instances.py

./joint_timetable_solver \
    instances/ras_corridor_24 \
    results/my_run \
    --budget=6 \
    --rho=0.18 \
    --radius=0 \
    --max-group=3 \
    --full-limit=3 \
    --max-rounds=10
```

Reproduce the packaged benchmark:

```bash
bash run_benchmarks.sh
python python/collect_results.py
python python/plot_results.py
```

## Input format

Each instance directory contains:

- `nodes.csv`: coordinates, resource ID, and node type;
- `arcs.csv`: directed transitions and base costs;
- `agents.csv`: origins, targets, time windows, speed intervals, and weights;
- `metadata.json`: benchmark description.

Private pre-departure and post-arrival nodes reproduce Flatland-style off-network
waiting and removal after completion, preventing artificial terminal conflicts.

## Packaged benchmark results

| Case | Trains | Initial conflicts | Final conflicts | Full states | Corridor states | State reduction | Speedup |
|---|---:|---:|---:|---:|---:|---:|---:|
| RAS corridor 12 | 12 | 6 | 0 | 72,530 | 12,066 | 83.36% | 5.24x |
| RAS corridor 24 | 24 | 16 | 0 | 1,908,083 | 117,915 | 93.82% | 13.95x |
| RAS corridor 48 | 48 | 32 | 0 | 5,323,508 | 341,498 | 93.59% | 13.22x |
| Flatland-like 20x20 | 40 | 20 | 0 | 233,407 | 86,145 | 63.09% | 2.36x |
| Flatland-like 30x30 | 80 | 40 | 0 | 461,532 | 169,620 | 63.25% | 2.18x |

All timings are single-process, single-thread runs in the current container and
are mechanism benchmarks rather than production claims.

## Important limitations

- The current C++ solver searches a shortest-path spatial DAG plus waiting; it is
  not yet a complete Flatland controller with acceleration, braking, malfunction,
  and action-state replay.
- Interaction communities are pairs or triples. Larger conflict components are
  repaired iteratively using highest-conflict subgroups.
- Corridor screening is a hard local trust region. The package verifies agreement
  against full DP on the reported cases, but does not yet implement the general
  admissible lower-bound theorem needed for universally certified screening.
- The master-level column-generation/atom layer remains separate from this larger
  timetable benchmark. The next integration should treat each solved group
  trajectory as a deterministic or probabilistic hyper-agent column.
