# Datasets

All instances + Meng & Zhou's published results, self-contained for the handoff. Two on-disk formats:

- **Native FastTrain format** — read by `n_track/fasttrain.cpp` (CLI dir arg) and `n_track/mz_lr.py`.
  Files: `input_node.csv`, `input_link.csv`, `input_train_info.csv`, `input_MOW.csv`, `FTSettings.ini`.
- **RAS arc format** — read by `n_track/nt_spacetime.py` / `nt_screen.py`.
  Files: `input_rail_node.csv`, `input_rail_arc.csv`, `input_track_type.csv`, `input_train_info.csv`,
  `input_train_schedule_arrival.csv`, `input_MOW.csv`.

| folder | format | size | use |
|---|---|---|---|
| `RAS_set3_native/` | native | 85 nodes / 97 links / 40 trains, 7 MOW | **Primary** instance — reproduces the paper (LB 1785→2279, UB 3803, gap 40%). `FTSettings.ini` has lowercase `[optimization]` + output dirs `internal_timetable/`, `summary_log/`. Run: `./fasttrain.exe ../data/RAS_set3_native`. |
| `native_testbed_small/` | native | 10 nodes / 11 links, no train_info | Small topology for `tdsp()` / B&B debugging. |
| `native_testbed_large/` | native | 85 nodes / 97 links, no train_info | Large topology (Meng-Zhou LR-C++ test bed). |
| `arc_format_RAS_Toy_problem/` | arc | 13 nodes / 14 arcs / 3 trains | Tiny instance for the LP master + B&P screening prototype; ideal first B&B validation (must reach gap 0). |
| `arc_format_RAS_data-set_3/` | arc | 76 nodes / 85 arcs / 20 trains | Arc-format RAS Set 3 for `nt_spacetime.py` / `nt_screen.py`. |
| `MengZhou_published_results/` | xlsx | `summary_medium.xlsx`, `summary_large.xlsx` | Meng & Zhou's **published LB/UB/gap** — the reproduction target (LB ≈ 1923, best UB ≈ 3471, gap ≈ 45%). |

Notes
- The native and arc RAS Set 3 differ in node/train counts because they were prepared by different
  pipelines (the native FastTrain export collapses sidings differently). The native one is the canonical
  reproduction; the arc one feeds the LP/screening experiments.
- `*_testbed_*` carry topology only (no `input_train_info.csv`) → the C++ reports 0 trains; they exist to
  exercise network parsing / shortest-path / forbidding logic at two scales.
