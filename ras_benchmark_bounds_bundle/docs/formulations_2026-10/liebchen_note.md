# Our timetabling MIPs seen from PESP (for the Oct 14 meeting with Prof. Liebchen)

Sources: Serafini & Ukovich (1989); Liebchen & Möhring (2004/2007, event-activity networks); Liebchen (2003, 2008)
and Liebchen & Peeters (2009, integral cycle bases); Liebchen (ATMOS 2020, flip inequalities); Lindner & Masing (2023,
split closure = flip inequalities); Liebchen, Lübbecke, Möhring & Stiller (2009, recoverable robustness).
Code: `solver/python/mip_formulations.py` (`build_bigm`, options `share`, `cycles`); experiment
`experiments/formulation_compare.py --forms bigm,bigm_shared,bigm_cycles,bigm_shared_cycles` -> `liebchen.json`.

## 1. The mapping

PESP (incidence form): events i with times pi_i, activities a = (i, j) with tension x_a = pi_j - pi_i + T p_a in
[l_a, u_a], p_a integer.

Our big-M model is the same object without a period:

| PESP | our model |
|---|---|
| event i | entry of train k into leg i, s[k, i] (s[k, L] = arrival) |
| running / dwell activity, [l, u] | s[k, i+1] - s[k, i] in [p_ki, p_ki] (no standing) or [p_ki, slack] (may stand) |
| headway activity with offset p_a in {0, 1} | the disjunction of two holds a, b on a single-track resource: start_b >= end_a + H or start_a >= end_b + H, chosen by z |
| period T | the big-M of the pair, the smallest value the window allows |
| cycle periodicity (Liebchen & Peeters 2009): the tension around every cycle is a multiple of T | the two headway rows of a pair, with the two holds, form a 4-cycle end_a -> start_b -> end_b -> start_a -> end_a whose offsets add to exactly 1: one of the two orders, never both |
| with all p fixed, a network matrix (TU) | with all z fixed, the timing problem is a longest-path problem (Zhou & Zhong 2007) |

Where it does not carry over: PESP assumes each activity duration in a fixed interval. A train that may stand
makes a hold [entry, exit + H) of variable length, so one headway decision touches four events, not two; this is why
the pair needs two rows sharing one integer (the 4-cycle above) instead of one PESP arc.

## 2. Questions he is likely to ask, and what we measured

**Which integer variables?** Liebchen showed for PESP that the choice of integers (an integral cycle basis instead
of one per arc) changes solve time a lot. Our analogue: one z per pair of holds per resource (`bigm`) or one z per
pair of trains per shared run (`bigm_shared`): the single-track legs two trains share consecutively, adjacent in both
paths, without standing and without a multi-track leg or siding in between. On single track their order cannot change inside such a run (H >= 1), so this is exact; it
is also the decision E1 (block-pair CP-SAT) and the E3 branching rule use. Between runs the two trains may pass (on
parallel tracks or a double-track link), so runs must not be merged: earlier versions that shared z over whole blocks,
or across double-track links, were exact on the single-line instances but cut off the optimum on the real-speed
models and on a full corridor; both were caught by comparing bigm and bigm_shared.

**Cycle inequalities?** For three trains on one single-track resource the orders must be transitive:
z_ab + z_bc - z_ac in [0, 1] (`bigm_cycles`), the triangle case of PESP's cycle inequalities.

CPLEX, 2 threads, same window (DP value), the MIP objective min sum_k [alpha (s[k,0] - R_k) + sum_i p_ki + beta
(s[k,L_k] - s[k,0] - sum_i p_ki)] (results/formulations/README.md, Objective); every proven value equal in all variants:

| set (instances) | integers bigm / shared | nodes bigm / cycles | seconds bigm / shared / cycles / both |
|---|---|---|---|
| toy (4) | 254 / 165 | 1,176 / 320 | 0.5 / 0.1 / 0.1 / 0.1 |
| mixed speeds (13) | 493 / 447 | 1,706 / 2,498 | 0.5 / 0.4 / 0.4 / 0.4 |
| bottleneck segments (12) | 1,246 / 780 | 18,064 / 13,880 | 2.0 / 1.8 / 3.1 / 2.2 |
| RAS ideal D1-D2 (6) | 7,357 / 4,771 | 166,002 / 64,978 | 109 / 109 / 146 / 135 |

| RAS ideal | integers bigm / shared | nodes bigm / shared / cycles | seconds bigm / shared / cycles |
|---|---|---|---|
| D2 whole | 1,449 / 1,449 | 152,075 / 152,075 / 53,417 | 90 / 95 / 104 |
| D2 cells10 | 1,946 / 1,325 | 10,517 / 7,142 / 8,363 | 14 / 6 / 19 |
| D2 cells5 | 2,842 / 1,252 | 3,410 / 4,221 / 3,182 | 5 / 6 / 22 |
| D3 whole | 1,598 / 1,598 | 246,950 / 246,950 / 185,166 | 563 / 285 / cap 900 (not proven) |

RAS ideal D3 with cells, none proven within 900 s (optimum 2,012 / 1,960 from the 1-hour cumulative-flow runs):

| case | integers bigm / shared | incumbent bigm / shared / cycles | bound bigm / shared / cycles |
|---|---|---|---|
| D3 cells10 | 2,403 / 1,598 | 2,029 / **2,012** / 2,033 | 1,888.6 / **1,957.2** / 1,828.6 |
| D3 cells5 | 3,653 / 1,598 | 1,972 / **1,960** / 1,983 | 1,850.6 / **1,875.8** / 1,793.0 |

RAS real speeds (trains fixed to tracks, parallel tracks on part of the line), window from our DP value, 1,800 s:

| case | optimum | cumulative flow (time-indexed) | bigm | bigm_shared | bigm_shared + cycles |
|---|---:|---|---|---|---|
| real D1 | 2,220 | proven, 648 s, 0.81 M binaries | proven, 6 s, 3,102 z | proven, 0.3 s, 554 z | proven, 1.5 s |
| real D2 | 4,127 | not solved in 2 h, bound 3,203, 1.67 M binaries | 4,134, bound 3,950 | **proven, 452 s**, 1,167 z | 4,127, bound 4,082 |
| real D3 | 4,056 | not solved in 2 h, bound 3,353, 1.58 M binaries | 4,056, bound 3,845 | **proven, 788 s**, 1,226 z | proven, 1,747 s |

The integer choice decides it here: with one z per shared run the compact model proves both optima, the first MIP
certificates of these two values (before, CP-SAT only).

("integers" in `bigm_shared` also counts pairs that only one order fits, which presolve fixes; D2/D3 whole have
no cells, so both variants are the same model and the time difference there is machine noise.)

Full ANL corridors (10-40 trains, double-track links and wait points): the compact model proves 11 of the 12
corridors CP-SAT had proved (same values, 0.2 s to 24 min) and bounds the other 7 within 0.4-5.5 %
(`results/formulations/README.md`); the 88-train full day is out of reach in 30 minutes.

Reading:
1. One z per shared run removes 30-56 % of the integers on cell models (D2 cells5: 2,842 -> 1,252; D3 cells5:
   3,653 -> 1,598) and about 82 % on the real-speed models (D2: 6,419 -> 1,167); on D3 cells10 / cells5 it is the only
   variant that reaches the optimum within 900 s, and on real-speed D2 / D3 it proves the optimum, which neither the
   per-resource big-M nor the time-indexed MIP does.
2. The triangle inequalities cut the tree by up to 3 times (D2 whole: 152,075 -> 53,417 nodes) but cost more time
   than they save (D3 whole is no longer proven within 900 s, and the bounds on D3 cells are lower): CPLEX's own cuts already capture most of it, and the big-M LP stays at TT0 (a fractional z
   satisfies every triangle).
3. The big-M LP is weak (TT0, 30-40 % below the optimum) and the time-indexed LP strong (0.2-9 % gap, but 10^5-10^6
   variables). On the single-line cases with whole blocks the strong LP wins; once the instances grow (cells, real
   speeds, parallel tracks) the compact model with the right integers wins. The question for him: can flip
   inequalities (Liebchen 2020) lift the compact LP above TT0 and close the remaining gap faster?

## 3. What to bring up in the meeting

- Daily freight timetables repeat: with T = 1,440 minutes a corridor timetable is a PESP instance, and his cycle-basis
  machinery applies directly (the waiting at sidings is the part that needs the 4-cycle form above).
- Flip inequalities for the disjunctive pairs: can they raise the big-M LP above TT0 without time indexing?
- Recoverable robustness (2009) for delays: our objective (origin waiting + running + standing, weights alpha, beta)
  has no delay scenarios.
