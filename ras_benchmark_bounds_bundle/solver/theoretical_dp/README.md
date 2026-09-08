# Parallel theoretical IDP framework

This directory contains Dr. Zhou's complete incremental DP-based theoretical/reference framework, preserved in parallel with the production solver:

- **IDP-0:** physical-chain DP.
- **IDP-1:** exact global resource-state DP.
- **IDP-2:** resource-reservation branch-and-bound.
- **IDP-3:** DFS, best-first, and beam search strategies.
- **IDP-4:** Lagrangian relaxation plus per-train pricing DP.

IDP-0 through IDP-4 form an incremental DP-based theoretical/reference framework. They are not five Bellman DPs: IDP-2 is the reservation B&B layer and IDP-3 is its search-policy experiment layer.

## Layout

```text
solver/theoretical_dp/
|-- README.md
|-- SOURCE.md
|-- CROSSWALK.md
|-- VERIFICATION.md
|-- PRESERVATION_MANIFEST.tsv
|-- run_all_idp.py
|-- run_tests.py
`-- idp/
    `-- prototype/
        |-- idp_lab/
        `-- resource_reservation_lite/
```

The contents below `idp/prototype/` are copied unchanged from the authoritative v2 ZIP, except for generated result directories and a compiled Linux executable documented in `SOURCE.md`. New outputs belong under `results/theoretical_dp/`, not inside the preserved source tree.

## Run

From the bundle root:

```bash
.venv/bin/python solver/theoretical_dp/run_all_idp.py
.venv/bin/python solver/theoretical_dp/run_tests.py
```

Run one original case without changing its definition:

```bash
.venv/bin/python solver/theoretical_dp/run_all_idp.py \
  --case case04_two_trains_three_resources.json
```

The default canonical output is `results/theoretical_dp/combined/`. See `VERIFICATION.md` for the reproduced objectives, bounds, state counts, search metrics, and exact commands.

## Scope

The Zhou track intentionally retains fixed-chain, unary-resource theoretical assumptions. Production PATH-K routing, full RAS physics, production conflict branching, validator semantics, and C++ network DP remain in their existing directories and are not silently equated with this model.
