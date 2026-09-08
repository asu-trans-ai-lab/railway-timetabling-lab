from __future__ import annotations

import argparse
import json
from pathlib import Path

from adapters.ras_adapter import dataset_path
from experiments.common import ROOT, run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run dataset -> DP/LR/B&B -> validator -> visualization -> metrics."
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-nodes", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    name = dataset_path(args.dataset).name
    is_mini = name.startswith("case")
    output = args.output or ROOT / "results" / name
    metrics = run_pipeline(
        args.dataset,
        output,
        max_nodes=args.max_nodes if args.max_nodes is not None else (500 if is_mini else 3),
        max_depth=args.max_depth if args.max_depth is not None else (30 if is_mini else 1),
        make_plot=not args.no_plot,
    )
    print(json.dumps(metrics, indent=2, sort_keys=True))
    print(f"results: {output.resolve()}")


if __name__ == "__main__":
    main()
