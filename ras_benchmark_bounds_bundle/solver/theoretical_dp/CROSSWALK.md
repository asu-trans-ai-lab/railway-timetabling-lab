# Theoretical and production crosswalk

The two tracks coexist. This table is descriptive and does not merge their semantics.

| Concept | Zhou theoretical track | Current production track |
|---|---|---|
| Physical single-train DP | IDP-0: `idp_lab/physical_chain_dp.py` | `solver/cpp/network_dp.cpp` through `solver/python/dp_interface.py` |
| Full global exact DP | IDP-1: `idp_lab/global_resource_dp.py` | No direct equivalent; IDP-1 is a tiny exact oracle |
| B&B | IDP-2: `resource_reservation_lite/branch_and_bound.py` and C++ reference | `solver/python/branch_and_bound.py` |
| Search strategy | IDP-3: `idp_lab/search_strategies.py` and `resource_reservation_lite/search.py` | Production node/conflict selection |
| LR/pricing | IDP-4: `idp_lab/lagrangian.py` using `physical_chain_dp.py` pricing | Current LR plus production C++ train DP |
| Route choice | Simplified/fixed initially | PATH-K available |
| Siding/full physics | Deferred or simplified | Production implementation |
| Validator | Theoretical model checks and exact-oracle comparison | Independent production validator |

## IDP-2 semantic boundary

IDP-2 branches on a resource-specific temporal reservation disjunction: task A precedes task B on the contested resource, or task B precedes task A. The later task may still use that resource after the earlier protected reservation ends.

The production B&B uses its existing conflict/trajectory restrictions. **Semantic equivalence is NOT assumed.** IDP-2 reservations are not translated into production `PROHIBIT` or trajectory-nogood semantics.

## Complete IDP mapping

- **IDP-0:** `idp/prototype/idp_lab/physical_chain_dp.py`; calendar/action tests in `idp_lab/tests/test_idp_lab.py`.
- **IDP-1:** `idp/prototype/idp_lab/global_resource_dp.py`; exact comparison on all five canonical cases in `idp_lab/tests/test_idp_lab.py`.
- **IDP-2:** the complete `idp/prototype/resource_reservation_lite/` package, including model, scheduler, conflicts, constructive UB, B&B, cases, tests, trace I/O, runners, and C++ reference.
- **IDP-3:** `idp/prototype/idp_lab/search_strategies.py`, `resource_reservation_lite/search.py`, and `compare_strategies.py`; terminology remains `best-first`, not breadth-first.
- **IDP-4:** `idp/prototype/idp_lab/lagrangian.py` and its per-train pricing calls into `physical_chain_dp.py`.

Shared model, metrics, conflict-history, cases, tests, and documentation remain in their original v2 package locations rather than being split into invented IDP directories.
