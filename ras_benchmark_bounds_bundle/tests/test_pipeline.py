from __future__ import annotations

from tempfile import TemporaryDirectory
from pathlib import Path

from adapters.ras_adapter import list_datasets
from experiments.common import mini_cases, run_pipeline
from validator.validate_schedule import validate_solution_file


def assert_artifacts(output: Path, expected_validator: str) -> None:
    for name in (
        "schedule.json", "schedule.csv", "validator_report.json",
        "metrics.json", "bb_trace.json", "space_time_diagram.png",
    ):
        assert (output / name).is_file(), f"missing {output / name}"
    assert validate_solution_file(output.name, output / "schedule.json")["status"] == expected_validator


def main() -> None:
    expected = {f"RAS_data-set_{index}" for index in (1, 2, 3)}
    expected.update(str(case["name"]) for case in mini_cases())
    assert expected <= set(list_datasets())

    with TemporaryDirectory(prefix="ras_pipeline_tests_") as temporary:
        root = Path(temporary)
        for case in mini_cases():
            name = str(case["name"])
            output = root / name
            metrics = run_pipeline(name, output, max_nodes=500, max_depth=30)
            assert metrics["solver_status"] == case["expected_status"]
            assert metrics["incumbent_ub"] == case["expected_objective"]
            assert metrics["validator_status"] == "PASS"
            assert_artifacts(output, "PASS")

        for index, train_count in ((1, 12), (2, 18), (3, 20)):
            name = f"RAS_data-set_{index}"
            output = root / name
            metrics = run_pipeline(name, output, max_nodes=3, max_depth=1)
            assert metrics["train_count"] == train_count
            assert metrics["nodes_generated"] == 3
            assert metrics["solver_status"] == "DEPTH_BUDGET_EXHAUSTED"
            assert metrics["validator_status"] == "FAIL"
            assert_artifacts(output, "FAIL")

    print("PASS: mini + RAS adapter/DP/LR/B&B/validator/visualization pipeline")


if __name__ == "__main__":
    main()
