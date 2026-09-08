# RAS Adapter

`ras_adapter.py` is the visible boundary between RAS CSV files and the common
solver representation used by both Python and C++.

| RAS field | Common object | C++ request field |
|---|---|---|
| `train_header` | `Train.train_id` | `train_id` |
| `entry_time` | `Train.entry_min` | `entry_min` |
| `origin_node_id` | `Train.origin` | `origin` |
| `destination_node_id` | `Train.destination` | `destination` |
| `speed_multiplier` | `Train.smult` | `smult` |
| `terminal_want_time` | `Train.terminal_want` | `terminal_want` |
| rail arc fields | `Arc` | `network.tsv` |
| maintenance windows | MOW dictionaries | `mow.tsv` |

Fast and slow trains use the same engine. Their speed adaptation is explicit:
`speed_multiplier` becomes `Train.smult`, which scales the directional arc speed
inside `network_dp.cpp` when travel time is computed.
