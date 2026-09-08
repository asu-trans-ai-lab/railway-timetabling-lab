# Mini RAS-Format Datasets

These six small instances are exported from the existing
`resource_lr_bb_clean/verification/toy_instances.py` definitions. Every folder
uses the same CSV schema as RAS Dataset 1–3 and can be loaded directly with
`dp.load_dataset()`.

| Folder | Trains | Purpose |
|---|---:|---|
| `case01_one_train` | 1 | Root-feasible case with no conflict or branching. |
| `case02_two_trains_no_conflict` | 2 | Two well-separated trains; root closes directly. |
| `case03_two_trains_conflict` | 2 | Same-direction conflict requiring one B&B split. |
| `case05_three_trains_bottleneck` | 3 | Three-way bottleneck requiring recursive branching. |
| `case06_siding` | 2 | Merge conflict with main-track/siding alternatives. |
| `case07_opposing_siding` | 2 | Opposing movements with a parallel siding. |

`manifest.json` records the current test configuration and expected certified
result. The retained network/train data are historical, but the expected values
are for the current `SEGMENT_CLEARANCE_V1` model with a three-minute headway.
Consequently, `case06_siding` has objective 62 under the current clearance rule;
the older legacy-entry report recorded 52 under different headway semantics.
