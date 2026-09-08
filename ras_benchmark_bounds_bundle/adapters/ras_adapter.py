from __future__ import annotations

import csv
from pathlib import Path

from solver.python.dp_interface import Arc, Train, load_dataset


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data"
MINI_ROOT = DATA_ROOT / "mini_cases"


def list_datasets() -> list[str]:
    ras = [f"RAS_data-set_{index}" for index in (1, 2, 3)]
    mini = sorted(path.name for path in MINI_ROOT.iterdir() if path.is_dir())
    return ras + mini


def dataset_path(name_or_path: str | Path) -> Path:
    candidate = Path(name_or_path)
    if candidate.is_dir():
        return candidate.resolve()
    for root in (DATA_ROOT, MINI_ROOT):
        path = root / str(name_or_path)
        if path.is_dir():
            return path.resolve()
    raise FileNotFoundError(
        f"unknown dataset {name_or_path!s}; choose one of: {', '.join(list_datasets())}"
    )


def load_ras_dataset(name_or_path: str | Path):
    """Map RAS CSV files to the solver's common Arc/Train/MOW representation."""
    return load_dataset(dataset_path(name_or_path))


def load_node_positions(name_or_path: str | Path) -> dict[int, float]:
    path = dataset_path(name_or_path) / "input_rail_node.csv"
    positions: dict[int, float] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            node = int(float(row["node_id"]))
            value = row.get("TSdiagram_x") or row.get("location_x") or node
            positions[node] = float(value)
    return positions


def arc_endpoints(arcs: dict[int, Arc]) -> dict[int, tuple[int, int]]:
    return {arc_id: (arc.a, arc.b) for arc_id, arc in arcs.items()}


def train_index(trains: list[Train]) -> dict[str, Train]:
    return {train.train_id: train for train in trains}
