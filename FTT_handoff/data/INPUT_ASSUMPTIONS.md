# Input-assumption ledger — corridor service levels

Per the auditability memo: every train-frequency assumption with its evidence. "Derived"
columns come from the simplified blocking chain (`blocking_layer.py`: public L3 demand ->
corridor screenline -> cars-per-train). **Known limitation** (stated in every audit): the
public L3 demand release is volume-sampled (~3.33M cars/70d nationally), so derived
absolute trains/day are a FLOOR; the usable derived evidence is the class mix and the
directional balance. Absolute levels lean on the corridor knowledge base and published
sources listed per row. Cars-per-train constants {IM 150, manifest 100, auto 90,
grain/coal 120} are externally supported (Wenlin `s1_block_to_train.py`).

| instance | trains | assumed/day* | assumed mix | derived/day | derived mix | frequency evidence |
|---|---|---|---|---|---|---|
| cn_icmain_native | 20 | 45 | Z:40%/M:40%/G:20% | 1.2 | Z:62%/M:34%/G:4% | corridor_kb: IC main Chicago-New Orleans spine; mixed traffic |
| cpkc_midcon_native | 12 | 21 | Z:33%/M:50%/G:17% | 0.1 | Z:47%/M:38%/G:15% | synthetic: single-line KC-Gulf spine; deliberate OPEN stress case |
| gulf_native | 12 | 20 | Z:33%/M:50%/G:17% | 0.5 | Z:98%/M:0%/G:2% | synthetic: low-density secondary main; deliberate OPEN stress case at ~45% ceiling |
| hiline_native | 16 | 26 | Z:38%/M:38%/G:25% | 3.6 | Z:36%/M:56%/G:8% | synthetic: no published count found; rate set to ~70% of single-track meet capacity |
| moffat_native | 10 | 14 | Z:20%/M:40%/G:40% | 4.7 | Z:35%/M:63%/G:1% | synthetic: mountain single track, sparse service; deliberate OPEN stress case |
| overland_native | 40 | 116 | Z:50%/M:35%/G:15% | 1.9 | Z:33%/M:65%/G:2% | corridor_kb: 'high-capacity double/triple-track intermodal spine'; intermodal-heavy mix |
| panhandle_native | 32 | 80 | Z:44%/M:44%/G:12% | 8.3 | Z:50%/M:41%/G:9% | corridor_kb: Transcon 'almost entirely double-tracked'; volume scaled from Belen-Clovis |
| pocahontas_native | 16 | 27 | Z:12%/M:25%/G:62% | 2.8 | Z:52%/M:46%/G:1% | corridor_kb: ex-N&W coal network; mix set coal-heavy |
| prb_joint_native | 24 | 51 | Z:8%/M:17%/G:75% | 1.5 | Z:44%/M:47%/G:9% | corridor_kb: Joint Line triple-track coal corridor; mix set coal-heavy (G 75%) |
| transcon_clovis_native | 32 | 96 | Z:44%/M:38%/G:19% | 4.9 | Z:53%/M:44%/G:3% | corridor_kb: '~90+ trains/day Belen-Clovis' (trains.com-sourced); Harrod (2011) class timings |
| transcon_clovis_native_fullday | 88 | 122 | Z:45%/M:39%/G:16% | 4.9 | Z:53%/M:44%/G:3% | same as starter; 88 trains ~ the 90+/day headline (deliberate stress case) |

*assumed/day = scenario trains normalized by entry span; scenarios are deliberately
peaked, so this is the within-window rate, not a calendar-day average.

Maintained by `make_assumptions.py`; Wenlin: add rows/columns from your transit-time and
service-schedule estimates (Week 2 of the work plan) rather than editing by hand.
