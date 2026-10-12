# Timetable feedback to the RAS 2026 blocking solver (Oct 10)

The blocking plan is made by the blocking solver (`ras_release_solver.py`, Version A snapshot of 2026-09-25, run
unchanged from `../blocking_feedback/dev`). The timetable only returns feedback. Code: `experiments/blocking_feedback.py`.

## Loop

1. Blocking plan on the official L3 data (release v2.1) -> cars on every L3 link by direction and commodity type.
2. 18 ANL corridors (`path_manifest.csv` maps each corridor resource to its L3 links): mean cars over the corridor's
   links / 70 days / cars per train -> trains per day by direction and class (Z 150 cars 1.25x speed, M 100 / 90
   cars 1.0x, G 120 cars 0.85x; the constants of `FTT_handoff/data/blocking_layer.py`), released evenly over 24 h.
   13 corridors carry at least one train a day; cn_icmain, gulf, moffat, csx_bo and up_texas carry less.
3. The day's timetable: DP and E1 (CP-SAT, 60 s), validated.
4. Feedback written into a copy of `link.csv` (the solver reads these columns itself; no solver change):

| feedback | from the timetable | into `link.csv` |
|---|---|---|
| (1) capacity | largest multiple s* of today's trains with every train's delay <= the corridor's MaxTrainWaitingTime (DP, bisection) -> s* x cars, the lighter direction at least half the total | `capacity` (only lowered; published value 700,000 cars / 70 days) |
| (2) price | CPLEX marginals of the day's space-time LP: a train entering resource r adds mean_t lambda(r, t) x (p_r + H) minutes | `vdf_fftt` = free-flow minutes + price (TAPLite routing time) |
| (3) run time | scheduled / free minutes per resource and direction | `free_speed` / ratio |
| (4) cut | corridors where today's trains exceed the waiting limit | the capacity of (1) with the solver's C5 overflow penalty raised to $100,000 per car |

5. The solver runs on the feedback copy; its JSON is checked by the official validator v2.0 with the ORIGINAL inputs
   and settings ($2,500 per block); steps 2-3 run again on the new plan.

## Results (official cost; total delay over the 13 corridors with traffic)

| run | links changed | official cost | vs base | corridor delay (min) | max wait | corridors over the limit |
|---|---|---|---|---|---|---|
| base | - | 2,803,896,719 | - | 414 | 60 | 0 |
| capacity | 2,321 | 2,803,982,492 | +0.003 % | 414 | 60 | 0 |
| price | 250 | 2,803,896,719 | 0 | 414 | 60 | 0 |
| run time | 133 | 2,803,668,995 | -0.008 % | 414 | 60 | 0 |
| all four | 2,321 + 250 + 133 | 2,817,379,529 | +0.48 % | **379** | 71 | 0 |
| round 2 (all four again, from the all-four plan) | 910 + 334 + 134 | 2,816,974,945 | +0.47 % | 379 | 71 | 0 |

Every run fails only C4 (137 blocks under the minimum volume), the same 137 as the base: a known issue of the
blocking solver, unrelated to the feedback.

With all four feedbacks the solver moves blocks off ns_crescent (89 % single track, 16 -> 13 trains a day) onto the
parallel pocahontas (6 -> 9) and ns_heartland (7 -> 11); ns_crescent's delay falls 346 -> 301 minutes, the total
414 -> 379, for +0.48 % cost (longer routes). A second round, computed from the timetables of the all-four plan,
changes the cost by -0.014 % and no corridor delay: the loop has settled after one round. No corridor exceeds its
waiting limit (120 minutes) in any run, so the cut (4) never fires on this plan.

## Prices: CPLEX marginals against the Fluid Queue price

At today's traffic only prb_joint, cpkc_midcon and ns_crescent have queues, so most prices are 0. At the capacity
(stress test, min(s*, 150 trains)):

| measure | Spearman, fluid vs LP marginal |
|---|---|
| per resource x 30-minute interval | 0.03 - 0.41 (12 corridors) |
| per resource, day total | 0.24 - 0.83 |

The Fluid Queue ranks the congested resources reasonably but not the congested half-hours: it sees free-running
arrivals and an average service rate, while the marginals see the trains' actual meets and waits.

## Issues we identified

- Steps 3-5 of the blocking solver (candidate blocks, block design, hub routing) use only distances and yard
  capacities. Link capacity and link cost reach only step 6 (each block's physical route, TAPLite with BPR costs), so
  timetable feedback can reroute blocks but cannot change which blocks are built or how commodities are blocked. To
  act on the design, step 4 (LR + knapsack) would need the link price or capacity term.
- At the current plan most corridors carry far less than their timetable capacity (s* = 2.5 - 32), so (1) and (4)
  rarely bind; only ns_crescent is near its limit (s* = 1.08).
- The corridor trains are released evenly over the day; peaks would raise the prices and lower s*.
- The capacity search uses the DP timetable, which can be worse than E1's: in round 1 it flagged ns_crescent
  (s* = 0.95) although E1 runs today's 13 trains with a worst wait of 71 minutes. The cut now follows the E1 timetable
  (only when it exceeds the waiting limit); round 2 was run before this fix, with ns_crescent's capacity cut to 0.95x,
  and still reproduces the round-1 plan.

## Delay as the feedback, iterated (`experiments/delay_feedback.py`)

The timetable's delay itself is fed back: each round every corridor gets "equivalent miles"
W_c <- W_c + eta x (average delay per train, minutes) x 1 mile/minute, spread over its L3 links (both directions) and
added to `length`, which the solver reads in every step (candidate blocks, design, hub routing, routes). eta = 10.
Planning on equivalent miles lets some blocks detour more than 1.3 x the true shortest path (C6 on true miles: 1 to 21
of ~915 blocks); those blocks get their true shortest path back (`repair_c6`), the rest of the plan is kept.

| round | equivalent miles (ns_crescent / cpkc_midcon / pocahontas) | delay, raw plan | official cost, repaired | vs base | delay, repaired | C6 repaired |
|---|---|---|---|---|---|---|
| 0 (base) | - | 414 | 2,803,896,719 | - | 414 | - |
| 1 | 216 / 62 / 0 | 240 | 2,819,176,129 | +0.54 % | 240 | 1 |
| 2 | 366 / 181 / 8 | 141 | 2,821,224,398 | +0.62 % | 171 | 5 |
| 3 | 473 / 181 / 38 | 107 | 2,821,427,778 | +0.63 % | 171 | 12 |
| 4 | 607 / 181 / 63 | 67 | 2,824,529,105 | +0.74 % | **133 (-68 %)** | 20 |
| 5 | 672 / 181 / 90 | 95 | 2,824,610,861 | +0.74 % | 133 | 21 |

After the repair every plan fails only C4 (the base's 137 blocks). Delay feedback cuts the corridor delay by 68 % for
+0.74 % cost (ns_crescent 346 -> 58 minutes, cpkc_midcon 56 -> 30); prb_joint keeps its 12 minutes (no other route).
Without the repair the delay falls to 67 (-84 %), but 20 blocks then break the 1.3 circuity limit.
