"""
record_schema.py — the COMMON REPORTING RECORD every solver in the benchmark lab must emit.

Without this record there are attractive timetables but no rigorous comparison. Wrap existing engines
(fasttrain, jtv, ras_flatland DP) so their runs land here too; new solvers emit it natively.

Usage:
    from record_schema import RunRecord, append_record
    rec = RunRecord(instance_id="C1_s12_seed7", method="B3_precedence_bnb", objective=412.0, ...)
    append_record("results/records.csv", rec)
"""
from __future__ import annotations
import csv, os, json
from dataclasses import dataclass, field, asdict


@dataclass
class RunRecord:
    # identity
    instance_id: str
    method: str                       # B0..B7, P1..P3 (+ variant suffix, e.g. "B3_segment")
    # objective & bounds (bounds are FIRST-CLASS: every method reports what it can certify)
    objective: float = float("nan")   # value of the returned (feasible) solution, NaN if none
    best_lower_bound: float = float("-inf")
    best_upper_bound: float = float("inf")
    relative_gap: float = float("nan")     # (UB-LB)/max(1,|UB|)
    # status
    feasible: bool = False
    optimality_proven: bool = False
    # effort
    time_to_first_feasible: float = float("nan")
    total_runtime: float = float("nan")
    nodes: int = 0                    # B&B/B&P nodes processed (0 for node-free methods)
    columns: int = 0                  # columns generated (CG/B&P)
    pricing_calls: int = 0
    dp_labels: int = 0                # DP states/labels generated (joint DP, pricing DP)
    hard_conflicts: int = 0           # remaining hard conflicts in the returned solution
    # provenance
    seed: int = -1
    config: str = ""                  # JSON string of solver parameters
    notes: str = ""

    def finalize(self):
        ub, lb = self.best_upper_bound, self.best_lower_bound
        if ub < float("inf") and lb > float("-inf"):
            self.relative_gap = (ub - lb) / max(1.0, abs(ub))
        return self


# B&B/B&P extension — one row per run in a side table keyed by (instance_id, method)
@dataclass
class BnbStats:
    instance_id: str
    method: str
    root_lower_bound: float = float("nan")
    initial_upper_bound: float = float("nan")
    nodes_generated: int = 0
    nodes_processed: int = 0
    nodes_pruned_bound: int = 0
    nodes_pruned_infeasible: int = 0
    max_tree_depth: int = 0
    time_to_first_feasible: float = float("nan")
    time_to_optimum: float = float("nan")
    final_gap: float = float("nan")
    # trajectories as JSON lists [(time, value), ...]
    best_bound_trajectory: str = "[]"
    incumbent_trajectory: str = "[]"


def append_record(path: str, rec) -> None:
    rec = rec.finalize() if hasattr(rec, "finalize") else rec
    row = asdict(rec)
    exists = os.path.exists(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists:
            w.writeheader()
        w.writerow(row)


def load_records(path: str):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))
