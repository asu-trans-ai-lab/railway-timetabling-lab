# IDP Lab v2 — Progressive Dynamic-Programming Train Scheduling Prototype

This folder implements the deliberately idealized progression discussed in the project:

- **IDP-0 — physical-chain DP**: one train/job, explicit state `(task index, time)`, actions WAIT or OCCUPY the next physical block under a fixed resource calendar.
- **IDP-1 — global resource-state DP**: tiny exact DP with state `(progress by train, ready times, resource-availability times)`. This is the transparent “full state” benchmark; it is expected to blow up as the case grows.
- **IDP-2 — resource-reservation branch-and-bound**: reuses the existing exact B&B kernel. Branches reserve the contested resource for task A before task B or vice versa; the forward longest-path DP propagates all downstream tasks.
- **IDP-3 — first-UB search experiments**: DFS, best-first, and beam search are compared on nodes/time to the first complete feasible timetable.
- **IDP-4 — LR/subgradient + pricing DP**: resource-time capacities are dualized. Each train solves an independent priced physical-chain DP and multipliers are updated by a subgradient rule.

The point is not yet railway realism. The point is a controlled **meta-ground** in which every added layer is inspectable, testable, and reversible.

## Canonical baseline assumptions

- single/unary resources only;
- fixed physical chain (`K=1`);
- waiting permitted between tasks;
- default time step = 1 minute;
- default protected headway = 3 minutes;
- objective = sum of train travel/flow times;
- report free-run time, total delay, per-train delay, max delay, p95 delay, safe frontier, B&B nodes, and time/nodes to first UB.

## Run

From the repository root:

```bash
python -m prototype.idp_lab.run_idp_lab
```

Results are written under `prototype/idp_lab/results/`.

Run one case:

```bash
python -m prototype.idp_lab.run_idp_lab \
  --case prototype/idp_lab/cases/case04_two_trains_three_resources.json
```

## Why three DPs?

1. **Conditional physical-chain DP** answers: “given the current resource calendar, how does one train move forward?”
2. **Global resource-state DP** answers: “what would a full Bellman state look like if we tried to schedule all jobs directly?”
3. **Pricing DP** answers: “given resource-time prices, what is each train’s minimum reduced/Lagrangian-cost chain?”

They are intentionally separate. B&B is the outer resource-reservation enumeration; it is not renamed as a DP.
