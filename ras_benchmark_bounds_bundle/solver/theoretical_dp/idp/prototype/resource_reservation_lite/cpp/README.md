# C++17 port of the lite reservation B&B

This is a compact C++ port of the verified Python reference kernel.  It uses the same fixed-chain assumptions and resource-reservation branch.

Export a JSON case to TSV and compile:

```bash
PYTHONPATH=. python -m prototype.resource_reservation_lite.export_tsv \
  --instance prototype/resource_reservation_lite/cases/case03_three_trains_one_resource.json \
  --output /tmp/case03.tsv

make -C prototype/resource_reservation_lite/cpp

prototype/resource_reservation_lite/cpp/reservation_bb \
  --input /tmp/case03.tsv \
  --output /tmp/case03_cpp.json \
  --tree /tmp/case03_cpp_tree.txt
```

The C++ port intentionally has no RAS parser and no route DP.  The existing native network DP remains the path-extraction engine for the RAS bridge; this C++ program is only the clean task/resource scheduling kernel that can be inspected and debugged independently.
