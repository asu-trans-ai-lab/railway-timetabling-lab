# Recommended Computational Section

## E1. Conflict localization

Demonstrate that noninteracting trains remain independent and only conflict-connected
pairs/triples enter the joint state space.

## E2. Quadratic corridor value

For fixed joint groups, compare the same C++ DP with and without corridor screening.
This isolates the state-reduction mechanism from grouping and implementation.

## E3. Exactness on finite instances

For each case where full DP is available, report:

- objective equality;
- joint-path equality after deterministic tie-breaking;
- zero hard conflicts;
- state and transition reduction.

## E4. Corridor sensitivity

Vary budget and proximal weight. Show the operating region where state reduction
improves without losing feasibility or changing the full-DP solution.

## E5. Total-agent versus component-size scaling

Keep component size fixed and increase the number of components. Then hold total
agents fixed and increase component density. This is the most important scaling
experiment for the paper.

## E6. Flatland transfer

Export native Flatland instances and compare against shortest-path priority,
large-neighborhood replanning, and a Flatland MAPF baseline. Include malfunctions
as a rolling-horizon stress test.
