# Native C++ Engine

`network_dp.cpp` is the computational core. It solves each train's finite
time-dependent shortest-path/DP subproblem under MOW windows, train-domain
restrictions, exact B&B event exclusions, and optional Lagrangian prices.

`solver/python/dp_interface.py` writes `network.tsv`, `requests.tsv`, `mow.tsv`,
price tables, and branch restrictions; invokes the executable; then decodes the
returned complete trajectories. Users normally call the Python pipeline rather
than invoking the executable directly.

Build only the engine:

```bash
g++ -std=c++17 -O2 -Wall -Wextra -pedantic \
  solver/cpp/network_dp.cpp -o solver/cpp/build/network_dp
```
