# Independent Validator

`validate_schedule.py` independently reconstructs topology, timing, cost,
maintenance-window, branch-restriction, headway, opposing-movement, siding, and
overtaking checks from serialized trajectories.

The validator does not accept the solver's feasibility flag as proof. Only a
validator `PASS` schedule can become a certified finite upper bound.
