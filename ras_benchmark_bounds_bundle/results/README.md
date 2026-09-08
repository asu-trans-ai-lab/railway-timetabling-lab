# Results

Pipeline runs create one subdirectory per dataset containing:

- `schedule.json`: complete serialized train trajectories.
- `schedule.csv`: one row per train movement for inspection and plotting.
- `validator_report.json`: independent physical feasibility result.
- `metrics.json`: LB, UB, gap, node counts, runtime, and model metadata.
- `space_time_diagram.png`: train trajectories over network position and time.

Generated run directories are intentionally ignored by Git.
