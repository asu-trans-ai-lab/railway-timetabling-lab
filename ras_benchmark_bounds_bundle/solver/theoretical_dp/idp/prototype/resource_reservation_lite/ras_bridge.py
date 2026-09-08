from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .model import Instance, JobSpec, TaskSpec


def build_fixed_chain_from_ras(
    bundle_root: str | Path,
    dataset: str | Path,
    *,
    work_root: str | Path,
    time_step_min: float = 1.0,
    safety_headway_min: float = 3.0,
    limit_trains: int | None = None,
    include_mow_for_path_extraction: bool = False,
) -> Instance:
    """Bridge the existing RAS parser + native C++ DP into the lite benchmark.

    The native DP is used once to choose each train's independent minimum-running
    physical route.  That arc sequence is then *frozen* as a job/task chain.  The
    lite prototype deliberately discards double-track/siding/MOW distinctions and
    treats each physical arc ID as a unary resource.  This is a debugging bridge,
    not a claim that the simplified instance has the same feasible set/objective
    as the full RAS benchmark.
    """

    bundle_root = Path(bundle_root).resolve()
    if not bundle_root.is_dir():
        raise FileNotFoundError(bundle_root)

    # Import only the auditable public boundary already present in the bundle.
    from adapters.ras_adapter import load_ras_dataset, dataset_path
    from solver.python.dp_interface import BranchRestrictions, DPConfig, NetworkDP

    arcs, trains, mow = load_ras_dataset(dataset)
    if limit_trains is not None:
        if limit_trains < 1:
            raise ValueError("limit_trains must be positive")
        trains = trains[:limit_trains]

    max_release = max(train.entry_min for train in trains)
    config = DPConfig(
        time_step=time_step_min,
        horizon=max(1440.0, max_release + 1440.0),
        max_wait=0.0,
        wait_step=time_step_min,
        departure_slack=0.0,
        departure_step=time_step_min,
        safety_headway=safety_headway_min,
        origin_wait_cost=1.0,
        running_cost=1.0,
        siding_wait_cost=1.0,
        early_cost=0.0,
        late_cost=0.0,
    )
    dp = NetworkDP(bundle_root / "solver" / "cpp", Path(work_root) / "route_dp")
    rows = dp.solve(
        arcs,
        trains,
        mow if include_mow_for_path_extraction else [],
        {},
        BranchRestrictions(),
        config,
        event_prices={},
    )

    jobs: list[JobSpec] = []
    path_metadata: dict[str, object] = {}
    for train in trains:
        result = rows[train.train_id]
        if not result.feasible or not result.legs:
            raise RuntimeError(f"independent native DP could not produce a route for {train.train_id}: {result.reason}")
        tasks: list[TaskSpec] = []
        for index, ((arc_id, start, end), ab) in enumerate(zip(result.legs, result.ab_flags)):
            duration = end - start
            if duration <= 0:
                raise RuntimeError(f"nonpositive native DP movement duration for {train.train_id} arc {arc_id}")
            tasks.append(TaskSpec(
                resource_id=arc_id,
                duration_min=duration,
                name=f"arc_{arc_id}_{'AB' if ab else 'BA'}",
            ))
        jobs.append(JobSpec(
            train_id=train.train_id,
            release_min=train.entry_min,
            tasks=tuple(tasks),
            train_type=f"smult={train.smult:g}",
        ))
        path_metadata[train.train_id] = {
            "arc_ids": list(result.arc_ids),
            "node_ids": list(result.node_ids),
            "directions": ["AB" if ab else "BA" for ab in result.ab_flags],
            "native_free_run_cost": result.physical_cost,
            "speed_multiplier": train.smult,
        }

    source_name = dataset_path(dataset).name
    return Instance(
        name=f"{source_name}_fixed_chain_lite",
        jobs=tuple(jobs),
        time_step_min=time_step_min,
        safety_headway_min=safety_headway_min,
        metadata={
            "source": source_name,
            "bridge": "existing RAS parser + native C++ DP independent route -> frozen unary-resource task chains",
            "simplifications": [
                "fixed independent route per train",
                "every arc is a unary resource",
                "waiting allowed between every task in the lite forward DP",
                "no distinction among single/double/siding/crossover resources",
                "MOW ignored after route extraction",
            ],
            "native_paths": path_metadata,
        },
    )
