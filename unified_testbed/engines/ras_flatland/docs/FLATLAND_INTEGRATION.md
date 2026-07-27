# Flatland Integration Notes

Flatland provides a 2-D railway grid with orientation-dependent transitions,
exclusive cell capacity, synchronous actions, heterogeneous speeds, timetable
windows, and optional malfunctions. These properties make it a strong external
benchmark for the proposed framework.

## Mapping

| Flatland | This package |
|---|---|
| `(row, col, direction)` configuration | oriented network node |
| transition map | directed arc set |
| one agent per cell | hard resource capacity |
| earliest departure | `earliest_departure` |
| latest arrival | `latest_arrival` |
| max speed | integer `move_interval` approximation |
| waiting / removed states | private staging nodes |
| local collision cluster | interaction component |
| coordinated replanning | hyper-agent joint DP |

## Recommended evaluation

1. Generate or load a Flatland environment.
2. Export the environment with `flatland_adapter.py`.
3. Solve the deterministic timetable offline.
4. Replay the resulting schedule in Flatland.
5. Inject malfunctions and re-run only the affected interaction components in a
   rolling horizon.
6. Compare completion rate, normalized reward, lateness, conflicts, runtime, and
   number of replanned agents.

## Current adapter status

The adapter follows the current public Flatland API but was not executed in this
container because `flatland-rl` is not installed. It should therefore be treated
as an integration scaffold and tested against the exact installed Flatland
version before reporting native-environment results.
