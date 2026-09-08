# Fast Train Lineage

## Evidence-labelled Lineage

```text
Meng and Zhou (2014) Fast Train paper/formulation
  |  CONCEPTUAL DESCENDANT / paper describes the released architecture
  v
Original Visual C++ Fast Train engine
  |-- ShortestPath.cpp: time-dependent label correcting
  |-- Timetable.cpp: LR prices, lower bound, priority recovery
  |-- FastTrain.cpp: general CSV input and CSV/XML output
  |
  +-- RAS competition GUI release
        |  ADAPTATION confirmed by bundled RAS data and NEXTA guide
        |  source-level adapter RELATIONSHIP UNKNOWN (NEXTA.exe source absent)
        v
      input_rail_*.csv / input_train_*.csv / output_schedule.xml

Current ras_benchmark_bounds_bundle
  |  REIMPLEMENTATION FROM ALGORITHM/SEMANTICS, not copied source
  +-- adapters/ras_adapter.py
  +-- solver/cpp/network_dp.cpp
  +-- solver/python/dp_interface.py
  +-- solver/python/lagrangian.py
  +-- solver/python/branch_and_bound.py       INDEPENDENT NEW COMPONENT
  +-- validator/validate_schedule.py          INDEPENDENT NEW COMPONENT
  +-- visualization/plot_space_time.py        INDEPENDENT NEW COMPONENT
```

## What Is and Is Not Derived

- **Original paper -> original C++:** conceptual and architectural correspondence is supported by Eq. (4)-(7), Fig. 7, and the executable logic in `ShortestPath.cpp` and `Timetable.cpp`. Exact source history is not established by the release metadata.
- **Original C++ -> RAS GUI release:** the release bundles RAS-format data, `NEXTA.exe`, and a RAS user guide, so an adaptation exists. The adapter implementation is inside an unavailable binary; direct derivation of particular parser functions is unknown.
- **Original C++ -> current C++:** no copied-source identity was found. The current engine independently implements a train-specific time-dependent DP with predecessor reconstruction and resource prices. Relationship: **REIMPLEMENTATION FROM ALGORITHM/SEMANTICS**.
- **Current Python layers:** the RAS parser, Python/TSV bridge, independent validator, space-time plotter, and conflict B&B are **INDEPENDENT NEW COMPONENTS** around the reimplemented DP/LR architecture.

## RAS Format Adaptation

### Original release

The general Fast Train source reads `input_node.csv`, `input_link.csv`, `input_train_info.csv`, and `input_MOW.csv` in `FastTrain.cpp`. The RAS release uses `input_rail_node.csv`, `input_track_type.csv`, `input_rail_arc.csv`, `input_train_info.csv`, `input_train_schedule_arrival.csv`, and XML output, documented by `NEXTA Users Guide_for_RAS.pdf`. No source file in the public tree parses `input_rail_arc.csv`; the only located implementation is the opaque `NEXTA.exe`.

### Current package

`adapters/ras_adapter.py::load_ras_dataset` resolves shared dataset paths and delegates to `solver/python/dp_interface.py::load_dataset`. That loader converts RAS arc/train/MOW CSV rows into Python `Arc` and `Train` objects. `NetworkDP._write_inputs` then serializes the internal objects to normalized TSV files (`network.tsv`, `requests.tsv`, `lambda.tsv`, MOW and branch restrictions) consumed by `solver/cpp/network_dp.cpp`.

The current C++ engine therefore does **not** consume the original Fast Train `input_link.csv` representation or the RAS CSVs directly. The current adapter is best classified as **independently implemented / semantically reconstructed**; direct source derivation from the unavailable NEXTA adapter cannot be established.
