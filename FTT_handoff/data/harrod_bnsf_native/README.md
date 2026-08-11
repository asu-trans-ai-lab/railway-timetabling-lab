# harrod_bnsf_native — hypothetical instance from Harrod (2011), Transportation Science 45(1)

A realistic mixed-traffic instance in native FastTrain format, built from the practical-application
section (§6) of Steven Harrod, *"Modeling Network Transition Constraints with Hypergraphs"* — the
BNSF-style busy North American transcontinental double-track mainline.

## What the paper provides (and what we copied)

| Paper element | Value | Where used here |
|---|---|---|
| Segment | 54-mile double-track mainline, ~100 trains/day, 1 passenger | topology |
| Blocks | 6 controlling blocks: 12.1, 10.1, 4.5, 11.7, 6.3, 11.5 mi (Table 4) | `input_link.csv` lengths |
| Block capacity | b=2 (two mains consolidated), crossovers at block ends only | `capacity=2, bidir=1` |
| Freight timings | WB 18,17,6,25,16,26 / EB 13,10,5,12,10,17 min (Table 4; grade asymmetry) | link speeds back-solved so `max(1,int(L*60/v+1))` reproduces them **exactly** (WB 108, EB 67 total) |
| Intermodal timings | WB 78 / EB 60 total (Table 7) | `speed_mult=1.25` → WB 87 / EB 55 (single-scalar approximation) |
| Passenger timings | WB 61 / EB 51 total (Table 4) | `speed_mult=1.5` → WB 72 / EB 46 (approximation) |
| Traffic volume | 10 freight round trips (20 trains), dispatch headway 26 min (Table 5) | 10 WB + 10 EB, 26-min spacing, EB staggered 13 min |
| Traffic mix | every 3rd freight departure is intermodal (§6.4) | trains *3I, *6I, *9I |
| Passenger disruption | 1 passenger train each direction (§6.2) | WBP (t=120), EBP (t=150) |
| Separation | min train separation 0 blocks on double track (Table 5) | `SafetyHeadway=1` |
| Schedule slack | flexible-dispatch scenarios | `MaxSlackTimeAtDeparture=120`, `MaxTrainWaitingTime=120` |

Regenerate trains: `python make_trains.py` (reproduces the solver's travel-time formula, so
`intended_arrival = entry + class free-running time` and the free-flow baseline deviation is 0).

## Known modeling gaps vs the paper
- One scalar `speed_mult` per class cannot reproduce Harrod's direction-dependent class ratios
  (grades slow heavy freight far more than passenger stock): intermodal/passenger free-runs are
  ~10-20% off Table 4/7; general freight is exact.
- fasttrain's objective is unweighted Σ|arrival − intended|; Harrod's $-utility priority classes
  (passenger > intermodal > freight) are not expressible — priority emerges only from the UB
  heuristic's relative-delay ordering.
- Harrod's transition-window (ε/δ) conflicts have no counterpart; block occupancy + headway only.

## Result (2026-08-11, final semantics: MonotoneRouting=1 + cap-weighted LB)
`./fasttrain.exe ../data/harrod_bnsf_native --sp dijkstra --bnb --branch dual --time 300`
→ **LB 160.3 / UB 316, gap 49.3%** (5000 nodes). Two earlier headline numbers are
**superseded**: "proven optimal 251" relied on (a) hold-and-reverse trajectories, now
forbidden by `MonotoneRouting=1`, and (b) a Lagrangian term that omitted the ×capacity
factor on cap-2 blocks, which inflated the bound (exposed as LB 318 > UB 313). With valid
semantics this instance is bound-limited like the real corridors — see
`train_scheduling\CORRIDOR_COMPARISON.md` (finding F4).

**Caveat — SP engine**: with the default `--sp dag`, LR tie-breaking yields a relaxed solution
whose greedy-UB train ordering locks 3 WB trains out of residual capacity (UB contaminated by the
1e6 unroutable penalty). `--sp dijkstra` orders differently and schedules all 22. Use `dijkstra`
on this instance; the order-sensitivity of `priority_ub` is a known open issue.
