# Benchmark Design and Paper Use

## Experimental ladder

### Tier 1 — exact mechanism comparison

Use `ras_corridor_12` and compare:

1. independent schedules;
2. full pair joint DP;
3. quadratic joint DP without corridor;
4. quadratic joint DP with corridor;
5. budget sensitivity.

Report exact objective/path agreement, hard conflicts, states, transitions, and
wall time.

### Tier 2 — component-size scaling

Use the three RAS-style cases. Total agent count increases from 12 to 48 while the
largest active component remains three. This tests the core hypothesis that
complexity depends more strongly on conflict-component size than total population.

### Tier 3 — Flatland-like grid scaling

Use the 40- and 80-train switch-grid cases. These test route alternatives,
heterogeneous speed intervals, synchronous occupancy, and many independent
interaction communities.

### Tier 4 — native Flatland export

Install current `flatland-rl`, build or load a `RailEnv`, and invoke
`export_rail_env`. Compare the C++ optimizer with Flatland baselines under the same
rail grid, agents, releases, and latest arrivals.

## Core metrics

- hard conflicts before/after;
- largest interaction component;
- number of solved hyper-agent groups;
- full and reduced states;
- full and reduced transitions;
- candidate states screened;
- full/reduced objective difference;
- full/reduced trajectory identity under deterministic tie-breaking;
- pricing/runtime reduction;
- sensitivity to corridor budget and proximal weight.

## Interpretation

The large reductions arise because the benchmark has sparse, localized conflict
structure. This is deliberate and corresponds to the method's intended operating
regime. Dense network-wide conflict cliques remain difficult and should be used as
boundary cases rather than hidden.
