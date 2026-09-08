# References

**The distinction is essential:** `fast_train_original/` is an original reference implementation preserved for provenance. The active, tested ANL/RAS implementation is in the bundle's top-level `adapters/`, `solver/`, `validator/`, `visualization/`, and `experiments/` directories.

- `fast_train_original/`: preserved public/local Fast Train release; do not edit or use as the active solver.
- `FAST_TRAIN_LINEAGE.md`: evidence-labelled relationship among the paper, original engine, RAS GUI release, and current package.
- `IMPLEMENTATION_CROSSWALK.md`: component-level comparison.
- `PAPER_IMPLEMENTATION_COMPARISON.md`: paper equation/algorithm to executable-code audit.

No source file under `fast_train_original/` is imported, compiled, or called by the current pipeline.
