# Joint-Transition Visibility Python Prototype

This package is the minimum integrated prototype corresponding to the paper draft
**From Dual-Price Coordination to Joint-Transition Visibility**.

## Implemented mechanisms

- ordered compressed completion/service state;
- time-space-state single-agent DP;
- synchronous pair/small-group joint DP;
- node, edge-swap, and exclusive resource feasibility;
- arc-time Lagrangian relaxation baseline;
- individual trajectory restricted master and column generation;
- disjoint group supercolumn master;
- donor-aware quadratic companion transition costs;
- integer master solved by SciPy/HiGHS branch-and-bound;
- controlled experiments for dual oscillation, fractional convexification, and group size.

## Run

```bash
python -m pip install -r requirements.txt
python run_prototype.py
```

Tests:

```bash
python -m pytest -q
```

## Experiment outputs

- `E2`: two opposing trains, LR price history, and joint-DP resolution;
- `E3`: odd-cycle individual-column LP versus group supercolumns and integer B&B;
- `E4`: group-size trade-off on a plus-shaped grid;
- `M1-M3`: individual CG, hard-feasible group CG, and quadratic companion group CG.

## Scientific boundary

The implementation is a finite-instance mechanism prototype. The active groups are
disjoint. The first-order oracle is the pricing certificate for the represented state
space. The quadratic oracle is a companion candidate selector. A general certified
state-corridor lower-bound rule and global enumeration of all possible group partitions
remain future extensions.
