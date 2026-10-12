# Formulations, marginals, prism, fluid queue, blocking MIP (Oct 8-9)

Prof. Zhou's Oct 8 items. Code: `solver/python/mip_formulations.py`, `solver/python/blocking_mip.py`; experiments in
`experiments/` (one script per section). Every timetable goes through `solver/python/siding_validate.py`; every
blocking plan through the official RAS 2026 validator v2.0. Solvers: HiGHS 1.x and CPLEX 22.2, 2 threads.

## Objective (the MIP's, `solver/python/mip_cumflow.py:169-180`)

k train, i leg (0..L_k-1), t minute, R_k release, p_ki running minutes; E[k,i,t] = 1 once train k has entered leg i
by minute t (E[k,L_k,t] = 1 once it has arrived):

    min  sum_k [ alpha * sum_{t >= R_k} (1 - E[k,0,t])                       origin waiting
                 + sum_i p_ki                                                running (constant)
                 + beta * ( sum_t (E[k,0,t] - E[k,L_k,t]) - sum_i p_ki ) ]   standing after departure

With alpha = beta = 1 this is sum_k (arrival_k - R_k), OBJ-E. The same objective in the other two formulations:
arc  sum of arc costs (depart at t: alpha (t - R_k) + p; stand: beta; run on: p);
big-M  sum_k [ alpha (s[k,0] - R_k) + sum_i p_ki + beta (s[k,L_k] - s[k,0] - sum_i p_ki) ].

## 1. Three formulations of the timetabling problem (`formulation_compare.py` -> `compare.json`)

| formulation | 0/1 variable | meaning |
|---|---|---|
| space-time arc | x_a | train uses arc a of its time-expanded network (depart / run on / stand at minute t) |
| cumulative flow (Meng & Zhou 2014 P3; Bertsimas & Stock Patterson 1998) | E[k,i,t] | train k has entered leg i by minute t; occupancy = E[k,i,t] - E[k,i+1,t-H] |
| big-M precedence | z_ab | hold a ends (+H) before hold b starts on a shared resource; event times s[k,i] |

Same Model, same window (each event within [earliest, earliest + U - TT0], U = our DP value), the objective above.
Unit case (2 trains, 1 resource, run 3 and 2, H = 1): all three give 8 = the hand optimum.

**H1 equivalence.** 93 instances (unit, toy, mixed speeds, 1-2 bottleneck segments, 54 corridor cases, RAS ideal
D1-D3 x 3 resolutions), 6 runs each. Every proven value equals the same optimum; every timetable PASS. Runs stopped at
the cap have bound <= optimum <= incumbent (RAS ideal D3 cells10 / cells5, optimum 2,012 / 1,960 from the earlier
1-hour CPLEX runs, are not proven by any run within 900 s).

**H2 relaxation.** The arc LP and the cumulative LP are equal on all 91 instances where both LPs finished (a change of variables). The big-M LP
is TT0 (any fractional z switches the disjunction off). Mean LP gap to the optimum:

| set | arc / cumulative | big-M |
|---|---:|---:|
| toy | 5.2 % | 36.6 % |
| mixed speeds | 2.2 % | 40.0 % |
| bottleneck segments | 7.9 % | 30.7 % |
| corridors (1-3 trains per direction) | 0.8 % | 2.0 % |

**H3 which one is faster depends on how tight the time-indexed LP is** (CPLEX seconds, RAS ideal):

| case | optimum | arc | cumulative | big-M (nodes) | time-indexed LP gap |
|---|---:|---:|---:|---:|---:|
| D1 (3 resolutions) | 1,124 | 3-6 | 8-17 | 0.1 | 0 % |
| D2 whole | 1,994 | 34 | 74 | 101 (152,075) | 0.2 % |
| D2 cells10 | 1,759 | cap 900 | 70 | 12 (10,517) | 4.5 % |
| D2 cells5 | 1,729 | cap 900 | 67 | 4 (3,410) | 3.7 % |
| D3 whole | 2,109 | 35 | 50 | 287 (246,950) | 0.8 % |
| D3 cells10 / cells5 | 2,012 / 1,960 | cap | cap | cap (finds 1,960 on cells5) | 8.0 / 8.7 % |

Whole blocks: the time-indexed LP is almost tight and the root closes; big-M has 2,000 variables but needs 150,000+
nodes. Cell models: the time-indexed LP gap grows to 4-9 % on 190,000-480,000 variables; big-M wins. On the small
sets big-M is fastest (median 0.02-0.06 s with CPLEX). HiGHS returns no solution for the two largest arc models.

**Real-speed RAS D1-D3: big-M proves the optima** (`bigm_real.json`, `bigm_real_shared_fixed.json`; window from our DP
value, no CP-SAT input; CPLEX, 2 threads, 1,800 s):

| case | optimum | cumulative flow (CPLEX) | big-M | big-M, one z per shared run |
|---|---:|---|---|---|
| real D1 | 2,220 | proven, 648 s (0.81 M binaries) | proven, 6 s (3,102 z) | proven, 0.3 s (554 z) |
| real D2 | 4,127 | not solved in 2 h even from the E1 timetable, bound 3,203 (1.67 M binaries) | 4,134, bound 3,950 | **proven, 452 s** (1,167 z) |
| real D3 | 4,056 | not solved in 2 h even from the E1 timetable, bound 3,353 (1.58 M binaries) | 4,056 found, bound 3,845 | **proven, 788 s** (1,226 z) |

These are the first MIP certificates of the real-speed D2 / D3 optima (until now CP-SAT only). "One z per shared run":
two trains get one precedence variable for each run of single-track legs they share consecutively (adjacent in both
paths, no standing, no multi-track leg or siding in between); between runs they may pass on parallel tracks or on a
double-track link, so runs must not be merged. Two earlier rules were wrong and were caught by cross-checks: sharing
over whole blocks cut off the optimum on the real-speed models (trains fixed to parallel tracks: 2,344 / 4,229 / 4,126
"proven"), and letting a run continue across a double-track link did so on the full corridors (moffat 4,610 against
4,604). With the final rule the shared-z counts and results of every single-line and real-speed instance above are
unchanged (checked), and `tests/test_formulations.py` now requires bigm = bigm_shared on a corridor and on real D1.

**Full ANL corridors (10-88 trains): first MIP results** (`experiments/corridor_bigm.py` -> `corridor_bigm.json`; big-M
and big-M with one z per shared run, CPLEX 1,800 s, window from our DP value; E1 = CP-SAT, results/corridors):

| corridor (trains) | E1 | big-M (shared z) | lower bound | seconds |
|---|---|---|---|---|
| moffat (10), gulf (12), cpkc_midcon (12), pocahontas (16), hiline (16), ns_chicago (24) | optimal | **proven, = E1** | | 0.2-1.9 |
| prb_joint (24), cn_icmain (20), up_sunset (24) | optimal | **proven, = E1** | | 26-39 |
| csx_bo (24), csx_waterlevel (24) | optimal | **proven, = E1** | | 528 / 1,414 |
| overland (40) | 7,156 optimal | 7,156 | 7,117 (0.5 %) | cap |
| bnsf_columbia (24) | 5,937 feasible | 5,937 | 5,914 (0.4 %) | cap |
| ns_heartland (24) | 11,207 feasible | 11,235 | 10,997 (1.9 %) | cap |
| transcon_clovis (32) | 10,004 feasible | 10,056 (per-resource big-M 10,032) | 9,780 (2.2 %) | cap |
| panhandle (32) | 8,494 feasible | **8,492 (better)** | 8,274 (2.6 %) | cap |
| up_texas (24) | 6,544 feasible | **6,543 (better)** | 6,313 (3.5 %) | cap |
| ns_crescent (24) | 11,273 feasible | 11,311 | 10,649 (5.5 %) | cap |
| transcon_clovis full day (88) | 32,156 feasible | no solution (95,700 z) | - | cap |

(gaps against the best known value.) The MIP proves 11 of the 12 corridors E1 had proved, with the same values; on
the other 7 it gives the first lower bounds (0.4-5.5 % below the best known) and two better timetables (panhandle,
up_texas, validated). Every value proven by both big-M forms agrees.

## 2. CPLEX marginals are Lagrange multipliers (`cplex_duals.py` -> `duals.json`)

CPLEX solves the arc LP; the marginal of every capacity row (r, t) gives lambda(r, t) = -dual >= 0. The kernel prices
every train by its own DP at these lambda (`siding_lr --mode price`): L(lambda_CPLEX) = LP value on all 39 instances
(unit, toy, mixed, bottleneck, RAS ideal D1-D3), to 6 digits; the same with HiGHS duals. The kernel's subgradient
(3,000 steps) is at most 0.36 % below. Large marginals mark the bottleneck: RAS ideal D2 whole, sum of lambda on T8 1,061, next
T4 80; D3 whole, T8 456 and T2 267.

## 3. Prism and minimum speed (`prism_speed.py` -> `prism.json`)

Prism of train k: [earliest_ki, earliest_ki + slack_k]. slack_k = U - TT0 is exact (objective budget). A minimum
average speed rho x free speed gives slack_k = free_k (1/rho - 1): a service rule, not a valid reduction.
rho* = slowest train's average speed / free speed in an optimal timetable.

| case | rho* | rho = 0.6 | 0.5 | 0.4 | 0.3 | unrestricted |
|---|---:|---|---|---|---|---|
| ideal D2 whole | 0.36 | infeasible | 2,030 (12 s) | **1,994 (62 s)** | 1,994 (125 s) | 1,994 (143 s, 179k bin.) |
| ideal D2 cells10 | 0.62 | **1,759 (16 s, 15k bin.)** | 1,759 (50 s) | 1,759 (94 s) | 1,759 (138 s) | 1,759 (132 s, 146k bin.) |
| ideal D3 whole | 0.38 | infeasible | infeasible | 2,119 (97 s) | **2,109 (98 s)** | 2,109 (91 s) |
| ideal D3 cells10 | 0.52 | infeasible | **2,012 proven (206 s)** | 2,022 at cap | 2,012 at cap | 2,082 at cap (600 s) |
| real D1 | 0.63 | - | **2,220 proven (1,385 s)** | 2,222 at cap | 2,220 at cap | (2,220, 648 s with U = 2,220) |
| real D2 / D3 | - | - | no solution in 1,800 s | no solution | no solution | no solution (2 h) |

rho >= 0.7 is infeasible everywhere: some trains must run at half their free speed or less to give way. For rho <= rho*
the restriction is free (same optimum) and shrinks the model up to 10 times; ideal D3 cells10 is proven within 600 s only
with the prism (rho = 0.5). For rho > rho* the optimum rises (D2 whole 2,030 > 1,994). Issues we
identified: on real D2/D3 the prism alone does not make the MIP solvable (145,000-347,000 free binaries, window from
the DP value 6,106 / 5,014).

## 4. Fluid queue, 3 hours with 1 congested hour (`fluid_queue.py` -> `fluid.json`)

Corridor A 10 min | B 20 min, s tracks | C 10 min, H = 3; releases 0.8 / 1.5 / 0.8 x capacity per hour. Fluid queue
per minute and per 30-minute interval (the framework's Q_after = max(0, Q + A - Cq)).

| s | trains | optimal waiting | fluid (1 min) | fluid (30 min) | MIP seconds | LR seconds |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 8 | 88 | +24 % | +8 % | 0.2 | 0.1 |
| 2 | 16 | 196 | +11 % | -3 % | 1 | 0.2 |
| 4 | 32 | 387 | +12 % | -2 % | 6 | 0.8 |
| 8 | 65 | 797 | +9 % | -4 % | 57 | 2.9 |
| 16 | 129 | 1,578 | +10 % | -4 % | 414 | 17 |
| 32 | 259 | (DP 3,173) | +10 % vs DP | -4 % vs DP | no solution in 600 s | 81 |

The 30-minute fluid queue is within 4 % of the optimum from 16 trains on, in under 1 ms; the exact MIP grows from
0.2 s to 7 min and gives no timetable at 259 trains. (On this one-bottleneck case the DP, FIFO, equals the optimum.)

## 5. RAS 2026 blocking-plan MIP (`blocking_compare.py` -> `results/blocking/compare.json`)

Block design y_b, block routes z_bp (all simple paths within 1.3 x shortest), route flows w_bp; commodity part as
arcs x_kb (flow conservation + MTZ) or as complete chains lambda_kc (all simple yard sequences). C1-C9 as in the
validator v2.0. Practice instance (TWI: 8 yards, 15 links, 50 commodities).

| case | arc (HiGHS / CPLEX) | chain (HiGHS / CPLEX) | LP | validator |
|---|---|---|---|---|
| as given | 2,336,573.55 proven (0.4 / 0.2 s) | same (26 / 4 s) | = optimum | PASS, cost equal |
| fixed cost x20 | 4,550,589.41 proven (0.7 / 0.3 s) | same (26 / 6 s) | 4,550,324 | PASS, cost equal |
| min volume x10, stress score | 4,422,956 / LB 4,370,067 (1 h) | **4,418,694 / LB 4,396,788 (1 h, 0.50 %)** | 4,288,969 | PASS, stress equal |

The sample solution shipped with the instance costs 3,385,443 and fails 3 v2.0 checks (link capacity, one commodity
type per block, direct rule). With min volume x10 some small direct-only commodities cannot form a block at all, so
the case is scored as the competition does (unserved car-miles x M = 5): 35 of 50 commodities served. The chain
formulation gives the better plan and bound in the same hour with 1/10 of the nodes.
