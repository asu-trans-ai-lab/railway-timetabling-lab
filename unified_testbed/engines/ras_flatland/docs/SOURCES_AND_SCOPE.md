# Sources and Scope

## INFORMS Railway Applications Section

The benchmark family is inspired by the Railway Applications Section's use of
network-based OR models, public problem repositories, and applied competition
cases. It is not an official RAS timetabling dataset. The 2026 RAS Problem
Solving Competition concerns the static railroad blocking problem.

- RAS home and mission: https://connect.informs.org/railway-applications/
- RAS public problem repository: https://connect.informs.org/railway-applications/new-item3/problem-repository16
- 2026 RAS competition: https://connect.informs.org/railway-applications/new-item3/problem-solving-competition682

## Flatland

The Flatland-inspired family follows the public environment's core ideas:
exclusive rail cells, synchronous discrete actions, separate rail and schedule
generators, heterogeneous agent speed profiles, off-grid ready/done states, and
optional malfunctions. The packaged generated instances do not require Flatland.
The adapter can export a locally constructed RailEnv into oriented network nodes.

- Challenge page: https://www.aicrowd.com/challenges/flatland
- Current project documentation: https://flatland-association.github.io/flatland-book/
- Historical challenge documentation: https://flatlandrl-docs.aicrowd.com/

## Research boundary

The packaged results are reproducible synthetic mechanism benchmarks. They do
not constitute performance claims for a production dispatching system or a
native Flatland controller. Full Flatland action replay, malfunction recovery,
and certified lower-bound corridor screening are planned extensions.
