from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.common import ROOT, mini_cases, run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run all verified 1–3 train mini datasets.")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "mini_cases")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    rows = []
    for case in mini_cases():
        name = str(case["name"])
        metrics = run_pipeline(
            name,
            args.output / name,
            max_nodes=500,
            max_depth=30,
            make_plot=not args.no_plot,
        )
        if metrics["solver_status"] != case["expected_status"]:
            raise AssertionError(f"{name}: unexpected solver status")
        if metrics["incumbent_ub"] != case["expected_objective"]:
            raise AssertionError(f"{name}: unexpected objective")
        rows.append(metrics)
        print(json.dumps(metrics, sort_keys=True), flush=True)
    (args.output / "summary.json").write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
