from pathlib import Path
from tempfile import TemporaryDirectory

from branch_and_bound import ConflictBB
from dp import Arc, BranchRestrictions, DPConfig, NetworkDP, Train, load_dataset
from dp.headway_model import HEADWAY_MODEL_SEGMENT_CLEARANCE_V1
from lagrangian_relaxation import evaluate_at_lambda


ROOT = Path(__file__).resolve().parent
REQUIRED_DATA_FILES = {
    "input_MOW.csv",
    "input_rail_arc.csv",
    "input_rail_node.csv",
    "input_track_type.csv",
    "input_train_info.csv",
    "input_train_schedule_arrival.csv",
}


def verify_datasets() -> None:
    expected_trains = {1: 12, 2: 18, 3: 20}
    for index in (1, 2, 3):
        directory = ROOT / "dataset" / f"RAS_data-set_{index}"
        missing = REQUIRED_DATA_FILES - {path.name for path in directory.iterdir()}
        assert not missing, f"RAS dataset {index} is missing: {sorted(missing)}"
        arcs, trains, mow = load_dataset(directory)
        assert arcs and len(trains) == expected_trains[index]
        assert all(train.origin != train.destination for train in trains)
        assert isinstance(mow, list)


def run_smoke_test() -> None:
    arcs = {1: Arc(1, 0, 1, 1.0, False, "0", 60.0, 60.0)}
    trains = [
        Train("A", 0.0, 0, 1, 1.0, None),
        Train("B", 0.0, 0, 1, 1.0, None),
    ]
    config = DPConfig(
        time_step=1.0,
        horizon=8.0,
        max_wait=0.0,
        wait_step=1.0,
        departure_slack=3.0,
        departure_step=1.0,
        safety_headway=1.0,
        headway_model=HEADWAY_MODEL_SEGMENT_CLEARANCE_V1,
    )
    restrictions = BranchRestrictions()

    with TemporaryDirectory(prefix="ras_bounds_bundle_") as temporary:
        dp = NetworkDP(ROOT / "dp", Path(temporary) / "dp_calls")
        paths = dp.solve(arcs, trains, [], {}, restrictions, config)
        assert set(paths) == {"A", "B"}
        assert all(path.feasible for path in paths.values())

        lr = evaluate_at_lambda(
            dp,
            node_id="smoke",
            iteration=0,
            arcs=arcs,
            trains=trains,
            mow=[],
            lambdas={},
            restrictions=restrictions,
            config=config,
        )
        assert lr is not None
        assert lr.dual_value == sum(path.physical_cost for path in lr.paths.values())

        result = ConflictBB(
            dp,
            arcs=arcs,
            trains=trains,
            mow=[],
            config=config,
            max_nodes=100,
            max_depth=10,
        ).solve()
        assert result.status == "PROVEN_OPTIMAL"
        assert result.global_lb == result.incumbent_ub
        assert result.best_validator_report["status"] == "PASS"


if __name__ == "__main__":
    verify_datasets()
    run_smoke_test()
    print("PASS: datasets, native DP, Lagrangian relaxation, and conflict B&B")
