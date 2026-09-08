# RAS DP, Lagrangian Relaxation, and Branch-and-Bound Bundle

This directory reorganizes the existing verified solver components referenced by
the meeting report. The original solver remains unchanged.

## Layout

- `dp/`: native C++ dynamic program, Python interface, PATH-K/full-domain support,
  trajectory identity, and headway model metadata.
- `lagrangian_relaxation/`: legacy safe-capacity LR, physical cross-train LR,
  fluid-queue support, and physical coupling definitions.
- `branch_and_bound/`: current conflict-based exact B&B, exact conflict branches,
  history no-goods, lower-bound validation, and the trusted timetable validator.
- `dataset/`: complete copies of `RAS_data-set_1`, `RAS_data-set_2`, and
  `RAS_data-set_3`.

The report's term is interpreted as **Branch-and-Bound**. The current branch is
an exact exclusion/exclusion disjunction: for a selected A/B conflict, one child
excludes A's exact conflicting movement and the other excludes B's.

## Dependency Flow

```text
dataset
   |
   v
DP backend <--- Lagrangian relaxation
   |
   v
conflict-based Branch-and-Bound
```

The B&B node lower bound is the sum of independent unpriced DP minima. The
Lagrangian code is retained as a separate bound-strengthening component; it is
not silently substituted into the current exact B&B proof.

## Quick Integration Test

Run from this directory:

```bash
./run_tests.sh
```

The smoke test verifies all three RAS dataset directories, compiles and executes
the native DP, evaluates the Lagrangian relaxation at zero multipliers, and solves
a tiny two-train conflict case to `PROVEN_OPTIMAL` with validator `PASS`. It does
not run a full RAS experiment.

To verify that the complete D1/D2/D3 train sets enter the packaged B&B pipeline,
run the bounded root-node check:

```bash
./run_ras123_smoke.sh
```

This runs one FULL-domain branching layer for 12, 18, and 20 trains under
`SEGMENT_CLEARANCE_V1`, `h=3`. Its fixed limits are three generated nodes and
depth one. Each root must create and solve both exclusion children. A successful
check establishes dataset-to-DP-to-validator-to-B&B runtime compatibility; it
does not establish a finite UB or full-instance convergence.
