# Coordination Agent and Trajectory Agents

## Computational separation

The prototype contains two explicit solver roles.

### CoordinationAgent

The virtual coordination agent maintains the system-level representation:

1. detect hard time-space conflicts;
2. construct the interaction graph;
3. form pair/triple hyper-agent groups;
4. update resource-time prices;
5. select a local precedence priority;
6. define the quadratic/proximal corridor;
7. invoke trajectory-agent oracles;
8. accept or reject proposed joint moves;
9. split groups after conflicts disappear;
10. record iteration and comparison statistics.

The coordination state is represented computationally by

\[
c^n=(\lambda^n,\Pi^n,\mathcal G^n,\bar S^n,\Delta^n),
\]

where resource prices, precedence biases, active groups, reference trajectories,
and corridor budgets are updated by the control loop.

### TrajectoryAgent

A physical trajectory agent generates an individual schedule on the railway
network, subject to release time, movement interval, target, and deadline.

### HyperTrajectoryAgent

A temporary hyper-agent advances all agents in an interaction group synchronously
through a common time axis. Its joint-state DP evaluates movement, waiting,
resource prices, precedence penalties, quadratic spacing, cross-agent movement
coupling, and proximal deviation. Hard node/resource overlap and edge swaps are
never relaxed.

## Local objective

For a group `G`, the implemented joint transition objective has the form

\[
\sum_{k\in G} c_k(a_t^k)
+\sum_{k\in G}\lambda_{r(s_{t+1}^k),t+1}
+\sum_{k\in G}\pi_{k,r,t}
+\frac{\rho}{2}\sum_{k\in G}\|s_{t+1}^k-\bar s_{t+1}^k\|_{Q_k}^2
+\Phi_{\mathrm{spacing}}
+\Phi_{\mathrm{cross}}.
\]

The corridor test is

\[
\rho\sum_{k\in G}\|s_{t+1}^k-\bar s_{t+1}^k\|_{Q_k}^2\le B_G.
\]

This reduces the effective search region, not the nominal Cartesian dimension.

## Acceptance rule

A proposed hyper-agent trajectory is accepted when it reduces the population's
hard-conflict count, or when the conflict count is unchanged and the physical
schedule objective decreases. Safety is evaluated after applying the complete
joint response; rejected responses are rolled back.

## Current scientific boundary

Resource prices and precedence terms are operational coordination signals, not a
proof of global optimality. The corridor is verified against full joint DP for all
reported groups. A later certified version should replace the fixed corridor with
an admissible state lower bound.
