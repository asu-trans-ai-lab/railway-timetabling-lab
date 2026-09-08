from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from adapters.ras_adapter import dataset_path, load_node_positions


def plot_space_time(dataset: str | Path, schedule_csv: Path, output: Path,
                    validator_report: Path | None = None) -> Path:
    import matplotlib.pyplot as plt

    positions = load_node_positions(dataset)
    rows = list(csv.DictReader(schedule_csv.open(newline="", encoding="utf-8")))
    trains = sorted({row["train_id"] for row in rows})
    colors = plt.get_cmap("tab20", max(1, len(trains)))
    color = {train_id: colors(index) for index, train_id in enumerate(trains)}

    figure, axis = plt.subplots(figsize=(11, 7))
    for train_id in trains:
        train_rows = sorted(
            (row for row in rows if row["train_id"] == train_id),
            key=lambda row: int(row["leg_index"]),
        )
        previous = None
        for row in train_rows:
            start = (positions[int(row["from_node"])], float(row["entry_min"]))
            end = (positions[int(row["to_node"])], float(row["exit_min"]))
            if previous is not None and start[1] > previous[1]:
                axis.plot([previous[0], start[0]], [previous[1], start[1]],
                          color=color[train_id], linewidth=1.4, linestyle=":")
            axis.plot([start[0], end[0]], [start[1], end[1]],
                      color=color[train_id], linewidth=2.0,
                      linestyle="--" if row["track_type"] == "S" else "-",
                      label=train_id if int(row["leg_index"]) == 0 else None)
            previous = end

    status = "NOT_RUN"
    conflicts = 0
    if validator_report and validator_report.exists():
        report = json.loads(validator_report.read_text())
        status = str(report.get("status", "FAIL"))
        conflicts = len(report.get("conflicts") or [])
    axis.set_title(
        f"{dataset_path(dataset).name} Space-Time Diagram | Validator {status} | Conflicts {conflicts}"
    )
    axis.set_xlabel("Network position")
    axis.set_ylabel("Time (minutes)")
    axis.grid(True, alpha=0.25)
    if trains:
        axis.legend(ncol=min(4, len(trains)), fontsize=8)
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=180)
    plt.close(figure)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot a solver schedule as a space-time diagram.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--validator-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plot_space_time(args.dataset, args.schedule, args.output, args.validator_report)
    print(args.output)


if __name__ == "__main__":
    main()
