# Coordination-Agent + Trajectory-Agent Benchmark Report

## Headline results

- Four cases with **120 agent-instance observations**.
- **60 initial hard conflicts reduced to 0**.
- **60/60 coordination actions accepted**; 0 rejected.
- **60/60** corridor/full joint-DP objective matches.
- Joint states: **83,520 -> 32,100**, a **61.57% reduction**.
- Joint transitions: **233,640 -> 101,400**, a **56.60% reduction**.
- Aggregate sampled joint-DP time: **0.029328s -> 0.012712s**, a **2.31x speedup**.

## Case table

| Case | Agents | Conflicts before/after | Actions accepted | Exact matches | State reduction | Transition reduction | Speedup |
|---|---:|---:|---:|---:|---:|---:|---:|
| control_corridor_16 | 16 | 8 / 0 | 8 / 8 | 8 / 8 | 61.57% | 56.60% | 2.42x |
| control_corridor_32 | 32 | 16 / 0 | 16 / 16 | 16 / 16 | 61.57% | 56.60% | 2.52x |
| control_corridor_64 | 64 | 32 / 0 | 32 / 32 | 32 / 32 | 61.57% | 56.60% | 2.00x |
| control_corridor_8 | 8 | 4 / 0 | 4 / 4 | 4 / 4 | 61.57% | 56.60% | 4.07x |

## Interpretation

The coordination agent does not replace physical trajectory optimization. It identifies conflict-connected groups, raises resource-time prices, chooses a local precedence bias, defines the proximal corridor, and accepts or rejects the hyper-agent response. The trajectory agents retain responsibility for physically feasible individual or joint movement under immutable hard safety constraints.

The current cases consist of independent meet/pass communities, so total work grows approximately linearly with the number of communities. Dense conflict components remain the intended boundary case.
