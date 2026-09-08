from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.common import ROOT, run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run bounded RAS Dataset 1–3 pipeline checks.")
    parser.add_argument("--dataset", choices=("1", "2", "3", "all"), default="all")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "ras_datasets")
    parser.add_argument("--max-nodes", type=int, default=3)
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    indices = (1, 2, 3) if args.dataset == "all" else (int(args.dataset),)
    rows = []
    for index in indices:
        name = f"RAS_data-set_{index}"
        metrics = run_pipeline(
            name,
            args.output / name,
            max_nodes=args.max_nodes,
            max_depth=args.max_depth,
            make_plot=not args.no_plot,
        )
        rows.append(metrics)
        print(json.dumps(metrics, sort_keys=True), flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
