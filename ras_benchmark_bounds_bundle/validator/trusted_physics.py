#!/usr/bin/env python3
"""Fluid-Queue Soft Lagrangian outer loop for the RAS PATH-K benchmark.

The active architecture is deliberately small and explicit:

    fixed PATH-K routes -> C++ timing DP -> selected legs
    -> LR occupancy and Fluid-Queue arrivals -> queue/wait -> lambda -> repeat

``pricing_reference_capacity`` is the legacy 30-minute pricing reference and
is one in a fully open block.  It is a per-cell pricing/reference quantity,
not a physical rule limiting a resource to one train during the whole
30-minute window.  A separately supplied
``lr_relaxation_capacity`` map may replace that value only in the
Lagrangian ``-lambda^T C`` term.  ``queue_service_capacity_train`` is a
separate Fluid Queue quantity and is the
fraction of one service slot available in a block after MOW.  The default
fully-open value is also one, but the two quantities are never combined.

The default policy is ``fluid-queue``.  ``legacy-subgradient`` is retained for
regression and uses the old projected ``D-C`` update.  The C++ program remains
the inner fixed-route timing DP; Python owns the complete outer iteration.

Main-track same-direction order is enforced at the candidate-assignment layer:
an order reversal on a common main-track arc is rejected unless the train that
was ahead has a positive siding dwell before that arc overlapping the other
train's passage.  This keeps the relaxed pricing loop from returning a
same-direction overtake on the running line.

The active benchmark fixes ``bin_minutes=30``.  ``clearance_wait_min`` is a
queue-equivalent backlog clearance time computed from future service capacity;
it is not automatically the realized waiting time of each individual train.
"""

from __future__ import annotations

import argparse
from solver.python.headway_model import (
    HEADWAY_MODEL_LEGACY_ENTRY, MODELS, physics_metadata,
)
import csv
import heapq
import hashlib
import json
import math
import os
import subprocess
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping


EPS = 1e-9
FLUID_QUEUE = "fluid-queue"
LEGACY_SUBGRADIENT = "legacy-subgradient"
DUAL_SUBGRADIENT = "dual-subgradient"
DEFAULT_BIN_MINUTES = 30.0
DEFAULT_HORIZON_MINUTES = 1440.0
DEFAULT_DEPARTURE_SLACK_MINUTES = 240.0
DEFAULT_MAX_ITERATIONS = 30
DEFAULT_PATH_K = 4
DEFAULT_ALPHA = 1.0
DEFAULT_BETA = 4.0
DEFAULT_GAMMA = 0.15
DEFAULT_WAIT_REFERENCE_MINUTES = 30.0
DEFAULT_MAX_PRICE_WAIT_MINUTES = 180.0
DEFAULT_QUEUE_WAIT_COST_PER_MIN = 1.0
DEFAULT_SAFETY_HEADWAY_MINUTES = 3.0
DEFAULT_ORIGIN_WAIT_COST_PER_MIN = 1.0
DEFAULT_RUNNING_COST_PER_MIN = 1.0
DEFAULT_SIDING_WAIT_COST_PER_MIN = 1.0
DEFAULT_EARLY_ARRIVAL_COST_PER_MIN = 1.0
DEFAULT_LATE_ARRIVAL_COST_PER_MIN = 1.0
DEFAULT_ARRIVAL_AVERAGING_POLICY = "none"
DEFAULT_ARRIVAL_AVERAGING_RHO = 0.20
DEFAULT_PRICE_QUEUE_TOLERANCE = 0.05
BLOCK30_PRICING_REFERENCE_CAPACITY = 1.0
BLOCK30_SEMANTICS = (
    "30-minute block is an aggregation/pricing window only; it is not a "
    "whole-window one-train physical capacity constraint"
)
PHYSICAL_CAPACITY_SEMANTICS = (
    "physical use is checked by exact timetable intervals, safety headway, "
    "MOW, and native per-resource capacity semantics"
)
ARRIVAL_AVERAGING_POLICIES = ("none", "msa", "sqrt-msa", "constant")
LR_CAPACITY_MODELS = ("block30", "headway-relaxation")
DEFAULT_LR_CAPACITY_MODEL = "block30"
DEFAULT_TIME_STEP = 0.5
DEFAULT_WAIT_STEP = 5.0
DEFAULT_DEPARTURE_STEP = 5.0
DEFAULT_DUAL_THETA_INITIAL = 1.0
DEFAULT_DUAL_THETA_MIN = 0.1
DEFAULT_DUAL_STALL_ROUNDS = 5
DEFAULT_DUAL_ROOT_ITERATIONS = 30
DEFAULT_DUAL_NODE_ITERATIONS = 5
DEFAULT_BB_MAX_NODES = 500
DEFAULT_BB_TIME_LIMIT_SEC = 300.0
DEFAULT_BB_RELATIVE_GAP = 0.0
DEFAULT_BB_ABSOLUTE_GAP = 0.0
DEFAULT_BB_CONFLICT_SELECTION = "earliest"
DEFAULT_BB_STRONG_BRANCHING_CANDIDATES = 5
DEFAULT_BB_CHECKPOINT_INTERVAL = 0
DEFAULT_BB_MAX_BYPASS_PER_NODE = 10
DEFAULT_BB_SPLITTING = "standard"
DEFAULT_BB_PAIRWISE_MAX_PAIRS = 20
DEFAULT_BB_PAIRWISE_NODE_DEPTH = 0
DEFAULT_BB_PAIRWISE_TIME_LIMIT_SEC = 5.0
DEFAULT_BB_SEARCH_POLICY = "best_bound"
DEFAULT_BB_FOCAL_WEIGHT = 1.5
DEFAULT_BB_FOCAL_SECONDARY = "conflicts"
BB_TOLERANCE = 1e-7
BB_CHECKPOINT_VERSION = 2
BB_CONSTRAINT_SCHEMA_VERSION = "cbs-required-forbidden-v2"
PHYSICAL_MODEL_SCHEMA_VERSION = "continuous-headway-v1"
BB_ROOT_CERTIFICATE_VERSION = 1


@dataclass(frozen=True)
class Arc:
    arc_id: int
    a: int
    b: int
    length: float
    bidirectional: bool
    track_type: str
    speed_ab: float
    speed_ba: float


@dataclass(frozen=True)
class Train:
    train_id: str
    entry_min: float
    origin: int
    destination: int
    smult: float
    terminal_want: float | None


@dataclass(frozen=True)
class Edge:
    node: int
    arc_id: int
    ab: bool


@dataclass(frozen=True)
class PathCandidate:
    train_id: str
    route_index: int
    arc_ids: tuple[int, ...]
    node_ids: tuple[int, ...]
    ab_flags: tuple[bool, ...]
    track_ids: tuple[str, ...]


@dataclass
class ResourceCell:
    arc_id: int
    time_bin: int
    arrival_count: float
    lr_occupancy_usage: float
    lr_capacity: float
    mow_open_minutes: float
    queue_service_capacity_train: float
    mu_train_per_min: float
    queue_before_train: float
    queue_after_train: float
    clearance_wait_min: float
    unresolved_queue: int
    lambda_old: float = 0.0
    lambda_target: float = 0.0
    lambda_new: float = 0.0
    # Explicit price-state fields.  The compatibility fields above remain the
    # authoritative actual queue used by congestion episodes and physical
    # diagnostics; these fields are only for the MSA price-driving state.
    actual_arrival_count: float = 0.0
    averaged_arrival_count: float = 0.0
    price_queue_before_train: float = 0.0
    price_queue_after_train: float = 0.0
    price_clearance_wait_min: float = 0.0


@dataclass(frozen=True, order=True)
class ForbiddenAction:
    """Exact C++ DP transition removed by one CBS branch."""

    train_id: str
    route_index: int
    arc_position: int
    start_cell: int
    wait_tick: int


@dataclass(frozen=True, order=True)
class RequiredAction:
    """Exact C++ DP transition that a CBS positive child must use."""

    train_id: str
    route_index: int
    arc_position: int
    start_cell: int
    wait_tick: int


@dataclass(frozen=True)
class ActionSignature:
    """Human-readable action witness paired with a ForbiddenAction key."""

    train_id: str
    route_index: int
    arc_position: int
    arc_id: int
    direction: str
    start_cell: int
    wait_tick: int
    start_min: float
    exit_min: float

    @property
    def forbidden(self) -> ForbiddenAction:
        return ForbiddenAction(
            self.train_id, self.route_index, self.arc_position,
            self.start_cell, self.wait_tick)

    @property
    def required(self) -> RequiredAction:
        return RequiredAction(
            self.train_id, self.route_index, self.arc_position,
            self.start_cell, self.wait_tick)


@dataclass(frozen=True)
class PhysicalConflict:
    """A reproducible pair of exact actions in two current trajectories."""

    conflict_type: str
    arc_id: int
    time_min: float
    action_a: ActionSignature
    action_b: ActionSignature
    train_a: str
    train_b: str


@dataclass
class BBNode:
    node_id: int
    parent_id: int | None
    depth: int
    forbidden_actions: frozenset[ForbiddenAction]
    inherited_lower_bound: float
    lower_bound: float
    required_actions: frozenset[RequiredAction] = field(default_factory=frozenset)
    base_lower_bound: float = -math.inf
    dual_lower_bound: float = -math.inf
    best_lambda: dict[tuple[int, int], float] = field(default_factory=dict)
    base_paths: dict[str, dict[str, str]] = field(default_factory=dict)
    relaxed_paths: dict[str, dict[str, str]] = field(default_factory=dict)
    status: str = "OPEN"
    conflict: PhysicalConflict | None = None
    infeasible_train: str | None = None
    branch_action: ActionSignature | None = None
    branch_conflict_type: str = ""
    branch_conflict_arc: int | None = None
    branch_conflict_time: float | None = None
    dual_history: list[dict[str, object]] = field(default_factory=list)
    pair_lower_bound: float = -math.inf
    pair_bound_exact: bool = False
    pair_matching_edges: list[tuple[str, str, float]] = field(default_factory=list)
    candidate_conflicts_examined: int = 0
    selected_conflict_class: str = ""
    selected_delta_a: float = math.nan
    selected_delta_b: float = math.nan
    selected_conflict_score: float = math.nan
    strong_branching_dp_calls: int = 0
    strong_branching_cache_hits: int = 0
    bypass_attempts: int = 0
    bypass_successes: int = 0
    conflicts_before_bypass: int = 0
    conflicts_after_bypass: int = 0
    bypass_train_id: str = ""
    old_path_signature: str = ""
    new_path_signature: str = ""
    required_residual_movements: int = 0
    labels_pruned_required_headway: int = 0
    labels_pruned_required_opposing: int = 0
    labels_pruned_required_special: int = 0
    required_action_infeasibility: int = 0
    representative_conflict_count: int = 0
    representative_trajectory_signatures: list[str] = field(default_factory=list)
    branch_kind: str = ""


@dataclass
class DualEvaluation:
    lower_bound: float
    relaxed_selected: dict[str, dict[str, str]]
    by_train: dict[str, list[dict[str, str]]]
    occupancy: dict[tuple[int, int], float]
    gradient: dict[tuple[int, int], float]
    lambda_dot_capacity: float
    minimum_sum: float


def _float(value: str | None, default: float = 0.0) -> float:
    if value in (None, "", "NA", "NaN", "nan"):
        return default
    return float(value)


def _int(value: str | None, default: int = 0) -> int:
    if value in (None, ""):
        return default
    return int(float(value))


def load_lr_capacity_map(path: Path | None) -> dict[tuple[int, int], float]:
    """Load a headway relaxation map without changing Fluid Queue service."""
    if path is None:
        return {}
    capacities: dict[tuple[int, int], float] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            arc_id = _int(row.get("arc_id"))
            time_bin = _int(row.get("time_bin"))
            value = row.get("headway_relaxation_capacity", "")
            if value in (None, ""):
                value = row.get("local_max_compatible_train_count", "")
            if value in (None, ""):
                raise ValueError(
                    f"{path} has no headway relaxation capacity at {arc_id}:{time_bin}")
            capacity = float(value)
            if not math.isfinite(capacity) or capacity < 0.0:
                raise ValueError(
                    f"{path} has invalid relaxation capacity {capacity} at {arc_id}:{time_bin}")
            capacities[(arc_id, time_bin)] = capacity
    if not capacities:
        raise ValueError(f"{path} contains no relaxation-capacity rows")
    return capacities


def capacity_map_metadata(path: Path | None) -> dict[str, object]:
    """Read optional certificate metadata from a capacity-map CSV."""
    if path is None:
        return {}
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields = set(reader.fieldnames or ())
        first = next(reader, None)
    if first is None:
        return {}
    metadata: dict[str, object] = {}
    for key in ("candidate_fingerprint", "candidate_count", "time_step",
                "bin_minutes", "horizon", "physical_model_fingerprint"):
        if key not in fields or first.get(key, "") in (None, ""):
            continue
        value = first[key]
        if key in {"candidate_count"}:
            metadata[key] = int(float(value))
        elif key in {"time_step", "bin_minutes", "horizon"}:
            metadata[key] = float(value)
        else:
            metadata[key] = value
    return metadata


def lr_capacity_value(
    key: tuple[int, int], *, model: str, capacity_map: dict[tuple[int, int], float],
) -> float:
    """Return the LR/reference quantity; never use it for physical feasibility.

    ``block30`` deliberately returns one reference unit for the diagnostic
    ``-lambda^T C`` term.  That scalar describes the pricing normalization of
    an aggregated cell; it does not mean that the resource can be used by at
    most one train over the entire 30-minute window.
    """
    if model == "block30":
        return BLOCK30_PRICING_REFERENCE_CAPACITY
    if model == "headway-relaxation":
        # A missing cell is a schema error, never an implicit zero or one.
        if key not in capacity_map:
            raise ValueError(
                f"headway-relaxation capacity map is missing cell {key}")
        return max(0.0, float(capacity_map[key]))
    raise ValueError(f"unknown LR capacity model: {model}")


def capacity_value(
    key: tuple[int, int], *, model: str,
    capacity_map: dict[tuple[int, int], float],
) -> float:
    """Single capacity accessor shared by every LR calculation and diagnostic."""
    value = lr_capacity_value(key, model=model, capacity_map=capacity_map)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"invalid capacity {value} at {key}")
    return value


def lambda_dot_capacity(
    lambdas: dict[tuple[int, int], float], *, model: str,
    capacity_map: dict[tuple[int, int], float],
) -> float:
    """Evaluate the diagnostic λᵀC/reference term.

    The result is not a physical capacity audit.  Version A physical
    feasibility is checked separately on exact intervals and headways.
    """
    total = 0.0
    for key, value in lambdas.items():
        multiplier = float(value)
        if not math.isfinite(multiplier) or multiplier < -EPS:
            raise ValueError(f"invalid multiplier {multiplier} at {key}")
        total += max(0.0, multiplier) * capacity_value(
            key, model=model, capacity_map=capacity_map)
    return total


def capacity_excess(
    usage: dict[tuple[int, int], float], *, model: str,
    capacity_map: dict[tuple[int, int], float],
) -> dict[tuple[int, int], float]:
    """Return diagnostic reference overage, never a physical rejection test."""
    keys = (set(usage) | set(capacity_map) if model != "headway-relaxation"
            else set(usage) & set(capacity_map) | set(capacity_map))
    return {
        key: float(usage.get(key, 0.0)) - capacity_value(
            key, model=model, capacity_map=capacity_map)
        for key in keys
    }


def candidate_universe_fingerprint(
    candidates: dict[str, list[PathCandidate]],
) -> str:
    """Stable hash for the immutable PATH-K universe used by CBS."""
    payload = []
    for train_id in sorted(candidates):
        for candidate in sorted(candidates[train_id], key=lambda item: item.route_index):
            payload.append({
                "train_id": train_id,
                "route_index": candidate.route_index,
                "arc_ids": list(candidate.arc_ids),
                "node_ids": list(candidate.node_ids),
                "ab_flags": [int(flag) for flag in candidate.ab_flags],
                "track_ids": list(candidate.track_ids),
            })
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def physical_model_fingerprint(args: argparse.Namespace) -> str:
    """Hash every discretisation/physical setting used by CBS DP checks."""
    payload = {
        "schema": PHYSICAL_MODEL_SCHEMA_VERSION,
        "bin_minutes": float(getattr(args, "bin_minutes", DEFAULT_BIN_MINUTES)),
        "time_step": float(getattr(args, "time_step", DEFAULT_TIME_STEP)),
        "wait_step": float(getattr(args, "wait_step", DEFAULT_WAIT_STEP)),
        "departure_step": float(getattr(args, "departure_step", DEFAULT_DEPARTURE_STEP)),
        "departure_slack": float(getattr(
            args, "departure_slack_minutes", DEFAULT_DEPARTURE_SLACK_MINUTES)),
        "max_wait": float(getattr(args, "max_wait_minutes", 180.0)),
        "horizon": float(getattr(args, "horizon", DEFAULT_HORIZON_MINUTES)),
        "safety_headway_minutes": float(getattr(
            args, "safety_headway_minutes", DEFAULT_SAFETY_HEADWAY_MINUTES)),
        "main_track_types": ["0", "1", "2"],
        "conflict_rules": [
            "same-direction-main-entry-headway",
            "protected-interval-special-and-opposing",
            "illegal-main-track-overtake",
        ],
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def time_discretization_fingerprint(args: argparse.Namespace) -> str:
    """Hash every grid/domain setting that changes a DP action signature."""
    payload = {
        "schema": "path-k-discretized-time-v1",
        "bin_minutes": float(getattr(args, "bin_minutes", DEFAULT_BIN_MINUTES)),
        "time_step": float(getattr(args, "time_step", DEFAULT_TIME_STEP)),
        "wait_step": float(getattr(args, "wait_step", DEFAULT_WAIT_STEP)),
        "departure_step": float(getattr(args, "departure_step", DEFAULT_DEPARTURE_STEP)),
        "departure_slack_minutes": float(getattr(
            args, "departure_slack_minutes", DEFAULT_DEPARTURE_SLACK_MINUTES)),
        "max_wait_minutes": float(getattr(args, "max_wait_minutes", 180.0)),
        "horizon": float(getattr(args, "horizon", DEFAULT_HORIZON_MINUTES)),
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def root_domain_constraint_fingerprint(candidate_fingerprint: str) -> str:
    """Fingerprint the unconstrained root domain under the active schema."""
    payload = {
        "constraint_schema_version": BB_CONSTRAINT_SCHEMA_VERSION,
        "candidate_fingerprint": candidate_fingerprint,
        "required_actions": [], "forbidden_actions": [],
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _read_root_bound_certificate(
    path: Path, *, candidate_fingerprint: str, candidate_count: int,
    physical_fingerprint: str, time_fingerprint: str,
) -> dict[str, object]:
    """Read a root certificate and reject every identity mismatch."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("certificate_version") != BB_ROOT_CERTIFICATE_VERSION:
        raise ValueError("root bound certificate version is not supported")
    if data.get("candidate_fingerprint") != candidate_fingerprint:
        raise ValueError("root bound certificate candidate fingerprint mismatch")
    if int(data.get("candidate_count", -1)) != int(candidate_count):
        raise ValueError("root bound certificate candidate count mismatch")
    if data.get("physical_model_fingerprint") != physical_fingerprint:
        raise ValueError("root bound certificate physical model fingerprint mismatch")
    if data.get("time_discretization_fingerprint") != time_fingerprint:
        raise ValueError("root bound certificate time discretization mismatch")
    if data.get("root_domain_constraint_fingerprint") != root_domain_constraint_fingerprint(
            candidate_fingerprint):
        raise ValueError("root bound certificate root-domain fingerprint mismatch")
    if data.get("validation_status") != "PASS":
        raise ValueError("root bound certificate is not validated")
    certified = float(data.get("certified_root_lb", "nan"))
    if not math.isfinite(certified) or certified < -BB_TOLERANCE:
        raise ValueError("root bound certificate has invalid certified LB")
    for field in ("root_base_lb", "root_dual_lb", "root_pair_lb"):
        value = data.get(field)
        if value is not None and not math.isfinite(float(value)):
            raise ValueError(f"root bound certificate has invalid {field}")
    return data


def normalize_bb_constraints(
    forbidden_actions: Iterable[ForbiddenAction] = (),
    required_actions: Iterable[RequiredAction] = (),
) -> tuple[frozenset[ForbiddenAction], frozenset[RequiredAction], bool, dict[str, int]]:
    """Canonicalize CBS constraints and detect only explicit contradictions."""
    forbidden_list = list(forbidden_actions)
    required_list = list(required_actions)
    forbidden = set(forbidden_list)
    required = set(required_list)
    diagnostics = {
        "required_duplicates_removed": max(0, len(required_list) - len(required)),
        "forbidden_duplicates_removed": max(0, len(forbidden_list) - len(forbidden)),
        "redundant_forbidden_removed": 0,
        "constraint_contradictions": 0,
    }
    forbidden_keys = {
        (a.train_id, a.route_index, a.arc_position, a.start_cell, a.wait_tick)
        for a in forbidden
    }
    required_keys = {
        (a.train_id, a.route_index, a.arc_position, a.start_cell, a.wait_tick)
        for a in required
    }
    if forbidden_keys & required_keys:
        diagnostics["constraint_contradictions"] += 1
        return frozenset(forbidden), frozenset(required), True, diagnostics

    routes_by_train: dict[str, set[int]] = defaultdict(set)
    positions: dict[tuple[str, int, int], set[tuple[int, int]]] = defaultdict(set)
    for action in required:
        routes_by_train[action.train_id].add(action.route_index)
        positions[(action.train_id, action.route_index, action.arc_position)].add(
            (action.start_cell, action.wait_tick))
    if any(len(routes) > 1 for routes in routes_by_train.values()):
        diagnostics["constraint_contradictions"] += 1
        return frozenset(forbidden), frozenset(required), True, diagnostics
    if any(len(values) > 1 for values in positions.values()):
        diagnostics["constraint_contradictions"] += 1
        return frozenset(forbidden), frozenset(required), True, diagnostics

    filtered: set[ForbiddenAction] = set()
    for action in forbidden:
        required_route = routes_by_train.get(action.train_id)
        if required_route and action.route_index not in required_route:
            diagnostics["redundant_forbidden_removed"] += 1
            continue
        required_at_position = positions.get(
            (action.train_id, action.route_index, action.arc_position))
        if required_at_position and (
                action.start_cell, action.wait_tick) not in required_at_position:
            # At a required position the C++ DP permits only the exact
            # required transition; all other exact forbids are redundant.
            diagnostics["redundant_forbidden_removed"] += 1
            continue
        filtered.add(action)
    return frozenset(filtered), frozenset(required), False, diagnostics


def bb_constraint_key(
    forbidden_actions: Iterable[ForbiddenAction],
    required_actions: Iterable[RequiredAction],
    *, candidate_fingerprint: str,
) -> tuple[object, ...]:
    """Order-independent domain key including schema and immutable universe."""
    forbidden, required, infeasible, _ = normalize_bb_constraints(
        forbidden_actions, required_actions)
    return (
        BB_CONSTRAINT_SCHEMA_VERSION, candidate_fingerprint, int(infeasible),
        tuple(sorted(required)), tuple(sorted(forbidden)),
    )


def load_dataset(dataset: Path) -> tuple[dict[int, Arc], list[Train], list[dict[str, float | int]]]:
    arc_path = dataset / "input_rail_arc.csv"
    train_path = dataset / "input_train_info.csv"
    mow_path = dataset / "input_MOW.csv"
    if not arc_path.exists() or not train_path.exists():
        raise FileNotFoundError(
            f"{dataset} must contain input_rail_arc.csv and input_train_info.csv")

    arcs: dict[int, Arc] = {}
    with arc_path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            arc = Arc(
                arc_id=_int(row.get("arc_id")),
                a=_int(row.get("A_node_id")),
                b=_int(row.get("B_node_id")),
                length=_float(row.get("length")),
                bidirectional=_int(row.get("bidirectional_flag")) == 1,
                track_type=str(row.get("track_type", "")).strip(),
                speed_ab=_float(row.get("default_AB_speed_per_hour"), 1.0),
                speed_ba=_float(row.get("default_BA_speed_per_hour"), 1.0),
            )
            arcs[arc.arc_id] = arc

    trains: list[Train] = []
    with train_path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            wanted = row.get("terminal_want_time", "")
            trains.append(Train(
                train_id=str(row.get("train_header", "")).strip(),
                entry_min=_float(row.get("entry_time")),
                origin=_int(row.get("origin_node_id")),
                destination=_int(row.get("destination_node_id")),
                smult=_float(row.get("speed_multiplier"), 1.0),
                terminal_want=None if wanted in (None, "") else float(wanted),
            ))

    mow: list[dict[str, float | int]] = []
    if mow_path.exists():
        with mow_path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                mow.append({
                    "a": _int(row.get("A_node_id")),
                    "b": _int(row.get("B_node_id")),
                    "start": _float(row.get("start_time_in_min")),
                    "end": _float(row.get("end_time_in_min")),
                })
    return arcs, trains, mow


def build_graph(arcs: dict[int, Arc]) -> dict[int, list[Edge]]:
    graph: dict[int, list[Edge]] = defaultdict(list)
    for arc in arcs.values():
        graph[arc.a].append(Edge(arc.b, arc.arc_id, True))
        if arc.bidirectional:
            graph[arc.b].append(Edge(arc.a, arc.arc_id, False))
    for edges in graph.values():
        edges.sort(key=lambda edge: (edge.node, edge.arc_id, not edge.ab))
    return dict(graph)


def traversal_minutes(arc: Arc, smult: float, ab: bool) -> float:
    speed = (arc.speed_ab if ab else arc.speed_ba) * max(smult, 1e-6)
    return 1.0 if speed <= 0.0 else 60.0 * arc.length / speed


def _reverse_reachable(graph: dict[int, list[Edge]], destination: int) -> set[int]:
    reverse: dict[int, list[int]] = defaultdict(list)
    for source, edges in graph.items():
        for edge in edges:
            reverse[edge.node].append(source)
    seen = {destination}
    stack = [destination]
    while stack:
        node = stack.pop()
        for previous in reverse.get(node, []):
            if previous not in seen:
                seen.add(previous)
                stack.append(previous)
    return seen


def enumerate_candidates(
    train: Train,
    graph: dict[int, list[Edge]],
    arcs: dict[int, Arc],
    path_k: int,
    *,
    max_paths: int = 20_000,
) -> list[PathCandidate]:
    """Retain the cheapest K fixed directed routes without changing PATH-K."""
    reachable = _reverse_reachable(graph, train.destination)
    found: list[tuple[float, tuple[int, ...], tuple[int, ...], tuple[bool, ...]]] = []

    def visit(node: int, seen: set[int], nodes: list[int], edge_ids: list[int],
              flags: list[bool], cost: float) -> None:
        if len(found) >= max_paths:
            return
        if node == train.destination:
            found.append((cost, tuple(edge_ids), tuple(nodes), tuple(flags)))
            return
        if node not in reachable or len(nodes) > len(graph) + 2:
            return
        for edge in graph.get(node, []):
            if edge.node in seen or edge.node not in reachable:
                continue
            arc = arcs[edge.arc_id]
            visit(
                edge.node, seen | {edge.node}, nodes + [edge.node],
                edge_ids + [edge.arc_id], flags + [edge.ab],
                cost + traversal_minutes(arc, train.smult, edge.ab)
            )

    visit(train.origin, {train.origin}, [train.origin], [], [], 0.0)
    if not found:
        raise RuntimeError(
            f"no directed path for train {train.train_id}: "
            f"{train.origin}->{train.destination}")

    found.sort(key=lambda item: (item[0], len(item[1]), item[1]))
    unique: list[PathCandidate] = []
    signatures: set[tuple[int, ...]] = set()
    for _, edge_ids, nodes, flags in found:
        if edge_ids in signatures:
            continue
        signatures.add(edge_ids)
        unique.append(PathCandidate(
            train_id=train.train_id,
            route_index=len(unique),
            arc_ids=edge_ids,
            node_ids=nodes,
            ab_flags=flags,
            track_ids=tuple(arcs[arc_id].track_type for arc_id in edge_ids),
        ))
        if len(unique) >= path_k:
            break
    return unique


def all_candidates(
    trains: Iterable[Train], graph: dict[int, list[Edge]], arcs: dict[int, Arc], path_k: int
) -> dict[str, list[PathCandidate]]:
    cache: dict[tuple[int, int, float], list[PathCandidate]] = {}
    out: dict[str, list[PathCandidate]] = {}
    for train in trains:
        key = (train.origin, train.destination, train.smult)
        base = cache.get(key)
        if base is None:
            base = enumerate_candidates(train, graph, arcs, path_k)
            cache[key] = base
        out[train.train_id] = [PathCandidate(
            train_id=train.train_id, route_index=i, arc_ids=candidate.arc_ids,
            node_ids=candidate.node_ids, ab_flags=candidate.ab_flags,
            track_ids=candidate.track_ids,
        ) for i, candidate in enumerate(base)]
    return out


def write_tsv(path: Path, header: list[str], rows: Iterable[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def write_dp_inputs(
    root: Path,
    trains: list[Train],
    candidates: dict[str, list[PathCandidate]],
    lambdas: dict[tuple[int, int], float],
    mow: list[dict[str, float | int]],
    arcs: dict[int, Arc],
    forbidden_actions: Iterable[ForbiddenAction] = (),
    required_actions: Iterable[RequiredAction] = (),
    action_prices: dict[tuple[str, int, int, int, int], float] | None = None,
    branch_restriction_rows: Iterable[Mapping[str, object]] = (),
) -> tuple[Path, Path, Path, Path]:
    requests, routes = root / "requests.tsv", root / "routes.tsv"
    lambda_file, mow_file = root / "lambda.tsv", root / "mow.tsv"
    write_tsv(
        requests,
        ["train_id", "entry_min", "smult", "terminal_want"],
        [[t.train_id, t.entry_min, t.smult,
          "" if t.terminal_want is None else t.terminal_want] for t in trains],
    )
    route_rows: list[list[object]] = []
    for train in trains:
        for candidate in candidates[train.train_id]:
            route_rows.append([
                train.train_id, candidate.route_index,
                f"{train.train_id}_path_{candidate.route_index}",
                f"{train.train_id}_macro", "PATH_K",
                ",".join(str(x) for x in candidate.arc_ids),
                ",".join(str(x) for x in candidate.node_ids),
                ",".join("1" if x else "0" for x in candidate.ab_flags),
                ",".join(candidate.track_ids),
            ])
    write_tsv(
        routes,
        ["train_id", "route_index", "operational_route_id", "macro_route_id", "role",
         "arc_ids", "node_ids", "ab_flags", "track_ids"], route_rows,
    )
    write_tsv(lambda_file, ["arc_id", "time_bin", "price"],
              [[a, b, v] for (a, b), v in sorted(lambdas.items())])
    mow_rows: list[list[object]] = []
    for window in mow:
        a, b = int(window["a"]), int(window["b"])
        for arc in arcs.values():
            if {arc.a, arc.b} == {a, b}:
                mow_rows.append([arc.arc_id, window["start"], window["end"]])
    write_tsv(mow_file, ["arc_id", "start_min", "end_min"], mow_rows)
    write_tsv(
        root / "forbidden_actions.tsv",
        ["train_id", "route_index", "arc_position", "start_cell", "wait_tick"],
        [[action.train_id, action.route_index, action.arc_position,
          action.start_cell, action.wait_tick]
         for action in sorted(set(forbidden_actions))],
    )
    write_tsv(
        root / "required_actions.tsv",
        ["train_id", "route_index", "arc_position", "start_cell", "wait_tick"],
        [[action.train_id, action.route_index, action.arc_position,
          action.start_cell, action.wait_tick]
         for action in sorted(set(required_actions))],
    )
    write_tsv(
        root / "action_prices.tsv",
        ["train_id", "route_index", "arc_position", "start_cell", "wait_tick", "price"],
        [[train_id, route_index, arc_position, start_cell, wait_tick, price]
         for (train_id, route_index, arc_position, start_cell, wait_tick), price
         in sorted((action_prices or {}).items())],
    )
    write_tsv(
        root / "branch_restrictions.tsv",
        ["train_id", "relation", "resource_kind", "resource_id",
         "window_start", "window_end"],
        [[row.get("train_id", ""), row.get("relation", ""),
          row.get("resource_kind", "arc"), row.get("resource_id", ""),
          "" if row.get("window_start") is None else row.get("window_start"),
          "" if row.get("window_end") is None else row.get("window_end")]
         for row in branch_restriction_rows],
    )
    return requests, routes, lambda_file, mow_file


def ensure_cpp(source: Path, executable: Path) -> Path:
    executable = executable.resolve()
    if executable.exists() and executable.stat().st_mtime >= source.stat().st_mtime:
        return executable
    executable.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        os.environ.get("CXX", "c++"), "-std=c++17", "-O2", "-Wall", "-Wextra",
        "-pthread", str(source), "-o", str(executable),
    ], check=True)
    return executable


def run_cpp_batch(
    executable: Path, dataset: Path, trains: list[Train],
    candidates: dict[str, list[PathCandidate]], lambdas: dict[tuple[int, int], float],
    mow: list[dict[str, float | int]], arcs: dict[int, Arc], *,
    bin_minutes: float, horizon_minutes: float, output_root: Path, cfg: argparse.Namespace,
    minimum_departure: float | None = None,
    minimum_siding_wait: float | None = None,
    forbidden_actions: Iterable[ForbiddenAction] = (),
    required_actions: Iterable[RequiredAction] = (),
    action_prices: dict[tuple[str, int, int, int, int], float] | None = None,
    fixed_headway_rows: Iterable[dict[str, object]] = (),
    enable_headway_residual: bool = False,
    branch_restriction_rows: Iterable[Mapping[str, object]] = (),
) -> list[dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="fluid-lagrangian-", dir=output_root) as temp_dir:
        root = Path(temp_dir)
        requests, routes, lambda_file, mow_file = write_dp_inputs(
            root, trains, candidates, lambdas, mow, arcs, forbidden_actions,
            required_actions, action_prices, branch_restriction_rows)
        residual_headway_file = root / "residual_headway.tsv"
        write_tsv(
            residual_headway_file,
            ["arc_id", "direction", "entry", "exit", "protected_end",
             "train_id", "track_type"],
            [[row["arc_id"], row["direction"], row["entry"], row["exit"],
              row.get("protected_end", row["exit"]), row["train_id"],
              row.get("track_type", "")]
             for row in fixed_headway_rows],
        )
        result = root / "result.tsv"
        command = [
            str(executable), "--dataset", str(dataset), "--requests", str(requests),
            "--routes", str(routes), "--lambda", str(lambda_file),
            "--mow", str(mow_file), "--output", str(result),
            "--bin-minutes", str(bin_minutes), "--horizon", str(horizon_minutes),
            "--time-step", str(getattr(cfg, "time_step", DEFAULT_TIME_STEP)),
            "--max-wait", str(getattr(cfg, "max_wait_minutes", 180.0)),
            "--wait-step", str(getattr(cfg, "wait_step", DEFAULT_WAIT_STEP)),
            "--departure-slack", str(cfg.departure_slack_minutes),
            "--departure-step", str(getattr(cfg, "departure_step", DEFAULT_DEPARTURE_STEP)),
            "--safety-headway-minutes", str(cfg.safety_headway_minutes),
            "--origin-wait-cost-per-min", str(cfg.origin_wait_cost_per_min),
            "--running-cost-per-min", str(cfg.running_cost_per_min),
            "--siding-wait-cost-per-min", str(cfg.siding_wait_cost_per_min),
            "--early-arrival-cost-per-min", str(cfg.early_arrival_cost_per_min),
            "--late-arrival-cost-per-min", str(cfg.late_arrival_cost_per_min),
            "--enable-time-window-pruning",
            "1" if getattr(cfg, "enable_time_window_pruning", False) else "0",
            "--enable-gh-pruning",
            "1" if getattr(cfg, "enable_gh_pruning", False) else "0",
            "--pruning-stats", "1",
            "--forbidden-actions", str(root / "forbidden_actions.tsv"),
            "--required-actions", str(root / "required_actions.tsv"),
            "--action-prices", str(root / "action_prices.tsv"),
            "--branch-restrictions", str(root / "branch_restrictions.tsv"),
            "--residual-headway", str(residual_headway_file),
            "--enable-legacy-block-residual", "0",
            "--enable-headway-residual", "1" if enable_headway_residual else "0",
        ]
        if getattr(cfg, "native_minute_law", False):
            command.extend(["--native-minute-law", "1"])
        if minimum_departure is not None:
            command.extend(["--minimum-departure", str(minimum_departure)])
        if minimum_siding_wait is not None:
            command.extend(["--minimum-siding-wait", str(minimum_siding_wait)])
        threshold_arc = getattr(cfg, "timing_threshold_arc", None)
        if threshold_arc is not None and int(threshold_arc) >= 0:
            command.extend([
                "--timing-threshold-arc", str(int(threshold_arc)),
                "--timing-threshold", str(float(getattr(cfg, "timing_threshold", 0.0))),
                "--timing-threshold-side",
                "leq" if bool(getattr(cfg, "timing_threshold_leq", True)) else "gt",
            ])
        timeout = None
        deadline = getattr(cfg, "_bootstrap_deadline", None)
        if deadline is not None:
            timeout = max(0.1, float(deadline) - time.monotonic())
        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("DP call exceeded bounded bootstrap wall-clock deadline") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "C++ PATH-K batch failed:\n" + (exc.stderr or "<no stderr>")) from exc
        with result.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream, delimiter="\t"))


def parse_legs(value: str) -> list[tuple[int, float, float]]:
    if not value:
        return []
    legs: list[tuple[int, float, float]] = []
    for item in value.split(";"):
        parts = item.split(",")
        if len(parts) != 3:
            raise ValueError(f"invalid C++ leg field: {item!r}")
        legs.append((int(parts[0]), float(parts[1]), float(parts[2])))
    return legs


def parse_waits(value: str) -> list[float]:
    if not value:
        return []
    return [float(item) for item in value.split(",")]


def _main_track_type(track_type: str) -> bool:
    return str(track_type).strip() in {"0", "1", "2"}


def _movement_profile(
    row: dict[str, str], candidate: PathCandidate,
) -> dict[str, object]:
    """Decode a DP row for the pairwise same-direction-order check."""
    legs = parse_legs(row.get("legs", ""))
    waits = parse_waits(row.get("waits", ""))
    if len(legs) != len(candidate.arc_ids) or len(waits) != len(legs):
        raise RuntimeError(
            f"DP trajectory shape does not match PATH-K candidate "
            f"{candidate.route_index} for {candidate.train_id}")
    main: dict[int, list[dict[str, float | int | bool]]] = defaultdict(list)
    all_movements: dict[int, list[dict[str, float | int | bool]]] = defaultdict(list)
    siding_dwells: list[tuple[int, float, float]] = []
    for index, (arc_id, start, exit_) in enumerate(legs):
        track_type = str(candidate.track_ids[index])
        movement: dict[str, float | int | bool] = {
            "index": index,
            "arc_id": int(arc_id),
            "entry": start,
            "exit": exit_,
            "direction": bool(candidate.ab_flags[index]),
            "track_type": track_type,
        }
        if _main_track_type(track_type):
            main[arc_id].append(movement)
        all_movements[arc_id].append(movement)
        wait = max(0.0, waits[index])
        if track_type == "S" and wait > EPS:
            # The DP leg start is before the siding dwell and the traversal;
            # therefore [start, start+wait) is the actual siding hold.
            siding_dwells.append((index, start, start + wait))
    return {"main": dict(main), "all": dict(all_movements), "siding_dwells": siding_dwells}


def _siding_dwell_covers_pass(
    leader: dict[str, object], leader_index: int,
    follower: dict[str, float | int | bool],
) -> bool:
    follower_start = float(follower["entry"])
    follower_end = float(follower["exit"])
    for siding_index, dwell_start, dwell_end in leader["siding_dwells"]:
        if siding_index >= leader_index:
            continue
        if dwell_end > follower_start + EPS and follower_end > dwell_start + EPS:
            return True
    return False


def _forbidden_main_track_overtake(
    left: dict[str, object], right: dict[str, object],
) -> bool:
    """Return true when a same-direction main-track order reverses."""
    left_main = left["main"]
    right_main = right["main"]
    for arc_id in set(left_main) & set(right_main):
        for left_leg in left_main[arc_id]:
            for right_leg in right_main[arc_id]:
                if left_leg["direction"] != right_leg["direction"]:
                    continue
                left_entry, right_entry = float(left_leg["entry"]), float(right_leg["entry"])
                left_exit, right_exit = float(left_leg["exit"]), float(right_leg["exit"])
                if left_entry < right_entry - EPS and left_exit > right_exit + EPS:
                    if not _siding_dwell_covers_pass(left, int(left_leg["index"]), right_leg):
                        return True
                if right_entry < left_entry - EPS and right_exit > left_exit + EPS:
                    if not _siding_dwell_covers_pass(right, int(right_leg["index"]), left_leg):
                        return True
    return False


def stable_block_ratio(minute: float, bin_minutes: float) -> float:
    """Normalize round-off when a DP time is mathematically on a bin edge."""
    ratio = minute / bin_minutes
    nearest = round(ratio)
    return float(nearest) if abs(ratio - nearest) <= 1e-9 else ratio


def select_results(
    rows: list[dict[str, str]], trains: list[Train], candidates: dict[str, list[PathCandidate]],
    *, enforce_order: bool = True,
) -> tuple[dict[str, dict[str, str]], dict[str, list[dict[str, str]]]]:
    by_train: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_train[str(row["train_id"])].append(row)
    options_by_train: dict[str, list[tuple[dict[str, str], dict[str, object]]]] = {}
    for train in trains:
        options = [r for r in by_train.get(train.train_id, []) if r.get("feasible") == "1"]
        if not options:
            raise RuntimeError(
                f"C++ DP found no feasible PATH-K timing for train {train.train_id}; "
                f"candidates={len(candidates[train.train_id])}")
        options.sort(key=lambda r: (
            float(r.get("total_priced_cost", "inf")),
            float(r.get("arrival_min") or "inf"), int(r.get("route_index", 0)),
        ))
        candidate_by_index = {candidate.route_index: candidate
                              for candidate in candidates[train.train_id]}
        options_by_train[train.train_id] = []
        for option in options:
            route_index = int(option.get("route_index", 0))
            candidate = candidate_by_index.get(route_index)
            if candidate is None:
                raise RuntimeError(
                    f"C++ DP returned unknown route index {route_index} for "
                    f"train {train.train_id}")
            options_by_train[train.train_id].append(
                (option, _movement_profile(option, candidate)))

    if not enforce_order:
        return {
            train.train_id: options_by_train[train.train_id][0][0]
            for train in trains
        }, dict(by_train)

    # Earlier-entry trains are assigned first.  DFS backtracking is bounded so
    # a pathological PATH-K instance fails explicitly rather than relaxing the
    # safety rule and silently returning an overtake.
    ordered_trains = sorted(trains, key=lambda train: (train.entry_min, train.train_id))
    selected: dict[str, dict[str, str]] = {}
    selected_profiles: dict[str, dict[str, object]] = {}
    search_nodes = 0
    max_search_nodes = 200_000

    def assign(position: int) -> bool:
        nonlocal search_nodes
        if position >= len(ordered_trains):
            return True
        if search_nodes >= max_search_nodes:
            return False
        train = ordered_trains[position]
        for option, profile in options_by_train[train.train_id]:
            search_nodes += 1
            if any(_forbidden_main_track_overtake(profile, previous_profile)
                   for previous_profile in selected_profiles.values()):
                continue
            selected[train.train_id] = option
            selected_profiles[train.train_id] = profile
            if assign(position + 1):
                return True
            selected.pop(train.train_id, None)
            selected_profiles.pop(train.train_id, None)
        return False

    if not assign(0):
        raise RuntimeError(
            "PATH-K/Lagrangian candidates cannot satisfy the hard same-direction "
            "main-track order rule; no safe overtake was returned "
            f"(search_nodes={search_nodes})")
    return selected, dict(by_train)


def select_relaxed_argmins(
    rows: list[dict[str, str]], trains: list[Train],
    candidates: dict[str, list[PathCandidate]],
) -> tuple[dict[str, dict[str, str]], dict[str, list[dict[str, str]]]]:
    """Select independent priced argmins; never applies physical recovery."""
    selected, by_train = select_results(
        rows, trains, candidates, enforce_order=False)
    return {train_id: dict(row) for train_id, row in selected.items()}, by_train


def evaluate_dual_bound(
    relaxed_selected: dict[str, dict[str, str]],
    by_train: dict[str, list[dict[str, str]]], trains: list[Train],
    lambdas: dict[tuple[int, int], float], *,
    bin_minutes: float, safety_headway_minutes: float,
    capacity_model: str, capacity_map: dict[tuple[int, int], float],
) -> DualEvaluation:
    """Evaluate the valid dual function at exact independent argmins."""
    minimum_sum = 0.0
    for train in trains:
        options = [row for row in by_train.get(train.train_id, [])
                   if row.get("feasible") == "1"]
        if not options:
            raise RuntimeError(f"no feasible relaxed trajectory for {train.train_id}")
        minimum_sum += min(float(row["total_priced_cost"]) for row in options)
    occupancy = resource_occupancy(
        relaxed_selected, bin_minutes, safety_headway_minutes)
    dot_capacity = lambda_dot_capacity(
        lambdas, model=capacity_model, capacity_map=capacity_map)
    return DualEvaluation(
        lower_bound=minimum_sum - dot_capacity,
        relaxed_selected=relaxed_selected, by_train=by_train,
        occupancy=occupancy,
        gradient={
            key: float(occupancy.get(key, 0.0)) - capacity_value(
                key, model=capacity_model, capacity_map=capacity_map)
            for key in (
                (set(occupancy) & set(capacity_map)) | set(lambdas)
                if capacity_model == "headway-relaxation"
                else set(occupancy) | set(lambdas)
            )
        },
        lambda_dot_capacity=dot_capacity,
        minimum_sum=minimum_sum,
    )


def _profile_for_row(
    train: Train, row: dict[str, str], candidates: dict[str, list[PathCandidate]],
) -> dict[str, object]:
    route_index = int(row.get("route_index", 0))
    candidate = next(
        (candidate for candidate in candidates[train.train_id]
         if candidate.route_index == route_index),
        None,
    )
    if candidate is None:
        raise RuntimeError(
            f"selected row has unknown route index {route_index} for train {train.train_id}")
    return _movement_profile(row, candidate)


def _candidate_for_row(
    train_id: str, row: dict[str, str],
    candidates: dict[str, list[PathCandidate]],
) -> PathCandidate:
    route_index = int(row.get("route_index", 0))
    for candidate in candidates[train_id]:
        if candidate.route_index == route_index:
            return candidate
    raise RuntimeError(
        f"selected row has unknown route index {route_index} for train {train_id}")


def _discrete_cell(minute: float, time_step: float) -> int:
    """Mirror C++ cell_of; callers pass the exact DP time step."""
    ratio = float(minute) / time_step
    nearest = round(ratio)
    if abs(ratio - nearest) <= 1e-9:
        ratio = float(nearest)
    return int(math.floor(ratio))


def action_signatures_for_row(
    train_id: str, row: dict[str, str], candidates: dict[str, list[PathCandidate]],
    *, time_step: float = DEFAULT_TIME_STEP, wait_step: float = DEFAULT_WAIT_STEP,
) -> tuple[ActionSignature, ...]:
    """Decode every transition in one DP trajectory into a branch key."""
    candidate = _candidate_for_row(train_id, row, candidates)
    legs = parse_legs(row.get("legs", ""))
    waits = parse_waits(row.get("waits", ""))
    if len(legs) != len(candidate.arc_ids) or len(waits) != len(legs):
        raise RuntimeError(f"trajectory shape mismatch for {train_id}")
    actions: list[ActionSignature] = []
    for position, (arc_id, start, exit_) in enumerate(legs):
        wait = float(waits[position])
        wait_tick = int(round(wait / wait_step)) if wait_step > 0 else 0
        actions.append(ActionSignature(
            train_id=train_id, route_index=candidate.route_index,
            arc_position=position, arc_id=int(arc_id),
            direction="AB" if candidate.ab_flags[position] else "BA",
            start_cell=_discrete_cell(start, time_step), wait_tick=wait_tick,
            start_min=float(start), exit_min=float(exit_),
        ))
    return tuple(actions)


def _profile_leg_pairs(
    left_profile: dict[str, object], right_profile: dict[str, object],
) -> Iterable[tuple[dict[str, object], dict[str, object]]]:
    left_all = left_profile.get("all", left_profile["main"])
    right_all = right_profile.get("all", right_profile["main"])
    for arc_id in sorted(set(left_all) & set(right_all)):
        for left_leg in left_all[arc_id]:
            for right_leg in right_all[arc_id]:
                yield left_leg, right_leg


def _physical_leg_pair_conflict(
    left_leg: dict[str, object], right_leg: dict[str, object],
    safety_headway_minutes: float,
    *, headway_model: str = HEADWAY_MODEL_LEGACY_ENTRY,
) -> tuple[bool, str]:
    """Authoritative versioned pair rule; callers supply one physical resource."""
    if headway_model not in MODELS:
        raise ValueError(f'unknown headway model: {headway_model}')
    if ('arc_id' in left_leg and 'arc_id' in right_leg and
            int(left_leg['arc_id']) != int(right_leg['arc_id'])):
        return False, ''
    left_start, left_exit = float(left_leg["entry"]), float(left_leg["exit"])
    right_start, right_exit = float(right_leg["entry"]), float(right_leg["exit"])
    left_track = str(left_leg.get("track_type", ""))
    right_track = str(right_leg.get("track_type", left_track))
    same_direction = bool(left_leg["direction"]) == bool(right_leg["direction"])
    main = _main_track_type(left_track) and _main_track_type(right_track)
    if same_direction and main and headway_model == HEADWAY_MODEL_LEGACY_ENTRY:
        return (
            abs(left_start - right_start) <
            max(0.0, safety_headway_minutes) - EPS,
            "same-direction-main-headway",
        )
    left_protected = (left_start, left_exit + max(0.0, safety_headway_minutes))
    right_protected = (right_start, right_exit + max(0.0, safety_headway_minutes))
    if (left_protected[0] < right_protected[1] - EPS and
            right_protected[0] < left_protected[1] - EPS):
        if same_direction and main:
            return True, 'same-direction-main-segment-clearance'
        return True, "same-direction-special-overlap" if same_direction else "opposing-protected-overlap"
    return False, ""


def _make_conflict(
    conflict_type: str, arc_id: int, action_a: ActionSignature,
    action_b: ActionSignature,
) -> PhysicalConflict:
    return PhysicalConflict(
        conflict_type=conflict_type, arc_id=int(arc_id),
        time_min=min(action_a.start_min, action_b.start_min),
        action_a=action_a, action_b=action_b,
        train_a=action_a.train_id, train_b=action_b.train_id,
    )


def _mow_free(
    windows: dict[int, list[tuple[float, float]]],
    arc_id: int, start: float, exit_: float,
) -> bool:
    return not any(
        start < window_end - EPS and exit_ > window_start + EPS
        for window_start, window_end in windows.get(arc_id, [])
    )


def all_physical_conflicts(
    selected: dict[str, dict[str, str]], trains: list[Train],
    candidates: dict[str, list[PathCandidate]], *,
    safety_headway_minutes: float,
    time_step: float = DEFAULT_TIME_STEP, wait_step: float = DEFAULT_WAIT_STEP,
    headway_model: str = HEADWAY_MODEL_LEGACY_ENTRY,
) -> list[PhysicalConflict]:
    """Return deterministic exact-action conflicts in a selected timetable."""
    train_ids = sorted(selected)
    profiles = {
        train_id: _profile_for_row(
            next(train for train in trains if train.train_id == train_id),
            selected[train_id], candidates)
        for train_id in train_ids
    }
    actions = {
        train_id: action_signatures_for_row(
            train_id, selected[train_id], candidates,
            time_step=time_step, wait_step=wait_step)
        for train_id in train_ids
    }
    by_position = {
        train_id: {(action.arc_id, action.arc_position): action
                   for action in action_list}
        for train_id, action_list in actions.items()
    }
    conflicts: list[PhysicalConflict] = []
    for pos, left_id in enumerate(train_ids):
        for right_id in train_ids[pos + 1:]:
            left_profile, right_profile = profiles[left_id], profiles[right_id]
            witness = _overtake_witness(left_profile, right_profile)
            if witness is not None:
                leader, follower, leader_leg, follower_leg = witness
                leader_id = left_id if leader is left_profile else right_id
                follower_id = left_id if follower is left_profile else right_id
                leader_action = by_position[leader_id][
                    (int(leader_leg.get("arc_id", 0)), int(leader_leg["index"]))]
                follower_action = by_position[follower_id][
                    (int(follower_leg.get("arc_id", 0)), int(follower_leg["index"]))]
                conflicts.append(_make_conflict(
                    "illegal-main-track-overtake", leader_action.arc_id,
                    leader_action, follower_action))
            for left_leg, right_leg in _profile_leg_pairs(left_profile, right_profile):
                conflict, conflict_type = _physical_leg_pair_conflict(
                    left_leg, right_leg, safety_headway_minutes, headway_model=headway_model)
                if not conflict:
                    continue
                left_action = by_position[left_id][
                    (int(left_leg.get("arc_id", 0)), int(left_leg["index"]))]
                right_action = by_position[right_id][
                    (int(right_leg.get("arc_id", 0)), int(right_leg["index"]))]
                conflicts.append(_make_conflict(
                    conflict_type, left_action.arc_id, left_action, right_action))
    conflicts.sort(key=lambda item: (
        item.time_min, item.conflict_type, item.train_a, item.train_b,
        item.arc_id, item.action_a.arc_position, item.action_b.arc_position,
    ))
    return conflicts


def propagate_required_action_conflicts(
    required_action: ActionSignature,
    representative_paths: dict[str, dict[str, str]],
    trains: list[Train], candidates: dict[str, list[PathCandidate]], *,
    safety_headway_minutes: float,
    time_step: float = DEFAULT_TIME_STEP,
    wait_step: float = DEFAULT_WAIT_STEP,
    existing_required: Iterable[RequiredAction] = (),
) -> tuple[frozenset[ForbiddenAction], bool, dict[str, int]]:
    """Propagate only exact actions that physically conflict with a pivot.

    This is deliberately narrower than a time-window propagation rule: every
    returned restriction is the exact discretized transition witnessed in a
    current representative trajectory.  Such an action cannot coexist with
    ``required_action`` in any physical timetable because the same continuous
    headway predicate is evaluated on the two exact intervals.  The helper is
    also usable in unit tests without invoking the C++ backend.
    """
    train_by_id = {train.train_id: train for train in trains}
    required_set = set(existing_required)
    propagated: set[ForbiddenAction] = set()
    diagnostics = {
        "required_residual_movements": 0,
        "labels_pruned_required_headway": 0,
        "labels_pruned_required_opposing": 0,
        "labels_pruned_required_special": 0,
        "required_action_infeasibility": 0,
    }
    pivot_candidate = _candidate_for_row(
        required_action.train_id,
        representative_paths[required_action.train_id], candidates)
    if required_action.arc_position < 0 or required_action.arc_position >= len(
            pivot_candidate.arc_ids):
        diagnostics["required_action_infeasibility"] += 1
        return frozenset(), True, diagnostics
    if pivot_candidate.route_index != required_action.route_index:
        diagnostics["required_action_infeasibility"] += 1
        return frozenset(), True, diagnostics
    pivot_track = pivot_candidate.track_ids[required_action.arc_position]
    pivot_leg = {
        "entry": required_action.start_min,
        "exit": required_action.exit_min,
        "direction": required_action.direction == "AB",
        "track_type": pivot_track,
    }
    pivot_key = required_action.required
    for train_id in sorted(representative_paths):
        if train_id == required_action.train_id:
            continue
        other_actions = action_signatures_for_row(
            train_id, representative_paths[train_id], candidates,
            time_step=time_step, wait_step=wait_step)
        other_candidate = _candidate_for_row(
            train_id, representative_paths[train_id], candidates)
        for other in other_actions:
            if other.arc_id != required_action.arc_id:
                continue
            other_leg = {
                "entry": other.start_min, "exit": other.exit_min,
                "direction": other.direction == "AB",
                "track_type": other_candidate.track_ids[other.arc_position],
            }
            conflict, conflict_type = _physical_leg_pair_conflict(
                pivot_leg, other_leg, safety_headway_minutes)
            if not conflict:
                continue
            diagnostics["required_residual_movements"] += 1
            if conflict_type == "same-direction-main-headway":
                diagnostics["labels_pruned_required_headway"] += 1
            elif conflict_type == "opposing-protected-overlap":
                diagnostics["labels_pruned_required_opposing"] += 1
            else:
                diagnostics["labels_pruned_required_special"] += 1
            if other.required in required_set:
                # A positive child that requires two mutually incompatible
                # exact actions is explicitly infeasible.  Returning the
                # action as both required and forbidden lets the canonical
                # constraint normalizer preserve that proof.
                diagnostics["required_action_infeasibility"] += 1
                return frozenset({other.forbidden}), True, diagnostics
            if other.forbidden != pivot_key:
                propagated.add(other.forbidden)
    return frozenset(propagated), False, diagnostics


def first_physical_conflict(*args, **kwargs) -> PhysicalConflict | None:
    conflicts = all_physical_conflicts(*args, **kwargs)
    return conflicts[0] if conflicts else None


def validate_physical_schedule(
    selected: dict[str, dict[str, str]], trains: list[Train],
    candidates: dict[str, list[PathCandidate]], arcs: dict[int, Arc],
    mow: list[dict[str, float | int]], *, horizon_minutes: float,
    safety_headway_minutes: float, time_step: float = DEFAULT_TIME_STEP,
    wait_step: float = DEFAULT_WAIT_STEP, require_complete: bool = True,
    headway_model: str = HEADWAY_MODEL_LEGACY_ENTRY,
) -> dict[str, object]:
    """Validate a complete incumbent or a physically valid partial schedule.

    The default remains the strict complete-incumbent validator used for UBs.
    Bootstrap recovery uses ``require_complete=False`` only to validate the
    affected partial timetable before more trains are inserted; it does not
    relax any interval, headway, MOW, route, or physical conflict rule.
    """
    metadata = physics_metadata(headway_model, safety_headway_minutes)
    train_by_id = {train.train_id: train for train in trains}
    errors: list[str] = []
    if require_complete and set(selected) != set(train_by_id):
        errors.append("schedule does not contain exactly one row per train")
    windows = mow_windows_by_arc(arcs, mow)
    for train_id, train in train_by_id.items():
        row = selected.get(train_id)
        if row is None:
            continue
        try:
            candidate = _candidate_for_row(train_id, row, candidates)
            legs = parse_legs(row.get("legs", ""))
            waits = parse_waits(row.get("waits", ""))
            if len(legs) != len(candidate.arc_ids) or len(waits) != len(legs):
                errors.append(f"{train_id}: trajectory shape mismatch")
                continue
            previous = float(row.get("departure_min", train.entry_min))
            if previous < train.entry_min - EPS:
                errors.append(f"{train_id}: departure before entry")
            for arc_id, start, exit_ in legs:
                if int(arc_id) not in arcs:
                    errors.append(f"{train_id}: unknown arc {arc_id}")
                if abs(float(start) - previous) > 1e-6:
                    errors.append(f"{train_id}: route discontinuity at arc {arc_id}")
                if not _mow_free(windows, int(arc_id), float(start), float(exit_)):
                    errors.append(f"{train_id}: MOW overlap at arc {arc_id}")
                if exit_ < start - EPS or exit_ > horizon_minutes + EPS:
                    errors.append(f"{train_id}: invalid leg interval")
                previous = float(exit_)
        except (KeyError, RuntimeError, ValueError) as exc:
            errors.append(f"{train_id}: {exc}")
    conflicts = all_physical_conflicts(
        selected, trains, candidates,
        safety_headway_minutes=safety_headway_minutes,
        time_step=time_step, wait_step=wait_step,
        headway_model=headway_model,
    ) if not errors else []
    return {
        **metadata,
        "status": "PASS" if not errors and not conflicts else "FAIL",
        "errors": errors,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "train_count": len(selected),
        "exactly_one_per_train": set(selected) == set(train_by_id),
        "require_complete": require_complete,
    }


def _overtake_witness(
    left: dict[str, object], right: dict[str, object],
) -> tuple[dict[str, object], dict[str, object],
           dict[str, float | int | bool], dict[str, float | int | bool]] | None:
    """Return (leader, follower, leader_leg, follower_leg) for a forbidden pass."""
    left_main = left["main"]
    right_main = right["main"]
    for arc_id in set(left_main) & set(right_main):
        for left_leg in left_main[arc_id]:
            for right_leg in right_main[arc_id]:
                if left_leg["direction"] != right_leg["direction"]:
                    continue
                left_entry, right_entry = float(left_leg["entry"]), float(right_leg["entry"])
                left_exit, right_exit = float(left_leg["exit"]), float(right_leg["exit"])
                if left_entry < right_entry - EPS and left_exit > right_exit + EPS:
                    if not _siding_dwell_covers_pass(left, int(left_leg["index"]), right_leg):
                        return left, right, left_leg, right_leg
                if right_entry < left_entry - EPS and right_exit > left_exit + EPS:
                    if not _siding_dwell_covers_pass(right, int(right_leg["index"]), left_leg):
                        return right, left, right_leg, left_leg
    return None


def repair_main_track_order(
    selected: dict[str, dict[str, str]], trains: list[Train],
    candidates: dict[str, list[PathCandidate]], executable: Path, dataset: Path,
    lambdas: dict[tuple[int, int], float], mow: list[dict[str, float | int]],
    arcs: dict[int, Arc], *, bin_minutes: float, horizon_minutes: float,
    output_root: Path, cfg: argparse.Namespace,
    allow_candidate_expansion: bool = True,
) -> dict[str, dict[str, str]]:
    """Repair forbidden passes using DP delay, then a real siding dwell."""
    train_by_id = {train.train_id: train for train in trains}
    graph = build_graph(arcs)
    minimum_departures: dict[str, float] = {}
    siding_attempted: set[tuple[str, str]] = set()
    max_repairs = 2_000

    def ensure_siding_candidates(train_id: str) -> None:
        if any("S" in candidate.track_ids for candidate in candidates[train_id]):
            return
        if not allow_candidate_expansion:
            raise RuntimeError(
                f"CBS candidate universe has no siding route for {train_id}; "
                "dynamic candidate expansion is disabled")
        train = train_by_id[train_id]
        candidates[train_id] = enumerate_candidates(
            train, graph, arcs, max(50, len(candidates[train_id]))
        )

    def try_siding_recovery(
        leader_id: str, follower_profile: dict[str, object],
    ) -> dict[str, str] | None:
        ensure_siding_candidates(leader_id)
        leader_train = train_by_id[leader_id]
        leader_min_departure = minimum_departures.get(leader_id)
        for minimum_wait in range(5, 181, 5):
            rerun_rows = run_cpp_batch(
                executable, dataset, [leader_train],
                {leader_id: candidates[leader_id]}, lambdas, mow, arcs,
                bin_minutes=bin_minutes, horizon_minutes=horizon_minutes,
                output_root=output_root, cfg=cfg,
                minimum_departure=leader_min_departure,
                minimum_siding_wait=float(minimum_wait),
            )
            options = [row for row in rerun_rows if row.get("feasible") == "1"]
            options.sort(key=lambda row: (
                float(row.get("total_priced_cost", "inf")),
                float(row.get("arrival_min") or "inf"),
                int(row.get("route_index", 0)),
            ))
            for option in options:
                profile = _profile_for_row(leader_train, option, candidates)
                # The selected leader must actually be the train that waits;
                # a route with an unrelated siding dwell does not authorize
                # the original pass.
                if _forbidden_main_track_overtake(profile, follower_profile):
                    continue
                return option
        return None

    for _ in range(max_repairs):
        profiles = {
            train_id: _profile_for_row(train_by_id[train_id], row, candidates)
            for train_id, row in selected.items()
        }
        witness = None
        for left_id, left_profile in profiles.items():
            for right_id, right_profile in profiles.items():
                if left_id >= right_id:
                    continue
                found = _overtake_witness(left_profile, right_profile)
                if found is not None:
                    witness = (left_id, right_id, found)
                    break
            if witness is not None:
                break
        if witness is None:
            return selected

        left_id, right_id, (leader, follower, leader_leg, follower_leg) = witness
        leader_id = left_id if leader is profiles[left_id] else right_id
        follower_id = left_id if follower is profiles[left_id] else right_id

        leader_exit = float(leader_leg["exit"])
        follower_exit = float(follower_leg["exit"])
        leader_entry = float(leader_leg["entry"])
        follower_entry = float(follower_leg["entry"])
        current_departure = float(selected[follower_id].get("departure_min") or
                                  train_by_id[follower_id].entry_min)
        required = current_departure + max(
            0.0, leader_exit - follower_exit, leader_entry - follower_entry,
        ) + 1e-6
        minimum_departures[follower_id] = max(
            minimum_departures.get(follower_id, train_by_id[follower_id].entry_min),
            required,
        )
        follower_train = train_by_id[follower_id]
        rerun_rows = run_cpp_batch(
            executable, dataset, [follower_train],
            {follower_id: candidates[follower_id]}, lambdas, mow, arcs,
            bin_minutes=bin_minutes, horizon_minutes=horizon_minutes,
            output_root=output_root, cfg=cfg,
            minimum_departure=minimum_departures[follower_id],
        )
        rerun_options = [row for row in rerun_rows if row.get("feasible") == "1"]
        if not rerun_options:
            pair = (leader_id, follower_id)
            if pair not in siding_attempted:
                siding_attempted.add(pair)
                siding_option = try_siding_recovery(
                    leader_id, profiles[follower_id])
                if siding_option is not None:
                    selected[leader_id] = siding_option
                    continue
            raise RuntimeError(
                f"C++ DP cannot delay train {follower_id} enough to preserve "
                "same-direction main-track order")
        rerun_options.sort(key=lambda row: (
            float(row.get("total_priced_cost", "inf")),
            float(row.get("arrival_min") or "inf"),
            int(row.get("route_index", 0)),
        ))
        other_profiles = {
            train_id: profile for train_id, profile in profiles.items()
            if train_id != follower_id
        }
        replacement = None
        for option in rerun_options:
            profile = _profile_for_row(follower_train, option, candidates)
            if not any(_forbidden_main_track_overtake(profile, other)
                       for other in other_profiles.values()):
                replacement = option
                break
        if replacement is None:
            pair = (leader_id, follower_id)
            if pair not in siding_attempted:
                siding_attempted.add(pair)
                siding_option = try_siding_recovery(
                    leader_id, profiles[follower_id])
                if siding_option is not None:
                    selected[leader_id] = siding_option
                    continue
        selected[follower_id] = replacement or rerun_options[0]
    raise RuntimeError(
        "C++ DP could not repair same-direction main-track order within "
        f"{max_repairs} delay repairs")


def protected_resource_blocks(
    arc_id: int, start: float, exit_: float,
    bin_minutes: float, safety_headway: float,
) -> set[tuple[int, int]]:
    protected_end = exit_ + max(0.0, safety_headway)
    first = max(0, int(math.floor(stable_block_ratio(start, bin_minutes))))
    last = max(first, int(math.ceil(
        stable_block_ratio(protected_end, bin_minutes)) - 1))
    blocks: set[tuple[int, int]] = set()
    for time_bin in range(first, last + 1):
        block_start = time_bin * bin_minutes
        block_end = (time_bin + 1) * bin_minutes
        if min(protected_end, block_end) > max(start, block_start) + EPS:
            blocks.add((arc_id, time_bin))
    return blocks


def resource_occupancy_train_ids(
    selected: dict[str, dict[str, str]], bin_minutes: float, safety_headway: float,
) -> dict[tuple[int, int], set[str]]:
    """Train IDs touching each aggregate cell for pricing diagnostics.

    Multiple train IDs in one cell are expected and are not, by themselves, a
    physical conflict.  Exact interval/headway checks decide feasibility.
    """
    trains_by_cell: dict[tuple[int, int], set[str]] = defaultdict(set)
    for row in selected.values():
        train_id = str(row.get("train_id", ""))
        occupied_by_train: set[tuple[int, int]] = set()
        for arc_id, start, exit_ in parse_legs(row.get("legs", "")):
            occupied_by_train |= protected_resource_blocks(
                arc_id, start, exit_, bin_minutes, safety_headway)
        for key in occupied_by_train:
            trains_by_cell[key].add(train_id)
    return dict(trains_by_cell)


def resource_occupancy(
    selected: dict[str, dict[str, str]], bin_minutes: float, safety_headway: float
) -> dict[tuple[int, int], float]:
    """Count train contributions to aggregate cells, one per train/cell.

    This is an LR/Fluid-Queue feature map only.  It is not a hard one-train
    per 30-minute-window constraint.
    """
    trains_by_cell = resource_occupancy_train_ids(selected, bin_minutes, safety_headway)
    return {key: float(len(train_ids)) for key, train_ids in trains_by_cell.items()}


def resource_arrivals(
    selected: dict[str, dict[str, str]], bin_minutes: float
) -> dict[tuple[int, int], float]:
    """Fluid Queue entry events: exactly one arrival at each leg's entry bin."""
    arrivals: dict[tuple[int, int], float] = defaultdict(float)
    for row in selected.values():
        for arc_id, start, _ in parse_legs(row.get("legs", "")):
            arrivals[(arc_id, max(0, int(math.floor(
                stable_block_ratio(start, bin_minutes)))))] += 1.0
    return dict(arrivals)


def arrival_averaging_rho(
    policy: str, iteration: int, rho_constant: float,
) -> float:
    """Return the input-state averaging weight for zero-based iteration k.

    The first iteration is initialized with the current actual arrival state,
    so ``rho(0)=1`` for all policies.  Thereafter the schedules are the
    standard one-based MSA weights evaluated as ``1/(k+1)`` and
    ``1/sqrt(k+1)`` when the Python iteration index is zero-based.
    """
    if policy == "none":
        return 1.0
    if policy == "msa":
        return 1.0 / (iteration + 1.0)
    if policy == "sqrt-msa":
        return 1.0 / math.sqrt(iteration + 1.0)
    if policy == "constant":
        return float(rho_constant)
    raise ValueError(f"unknown arrival averaging policy: {policy}")


def update_arrival_average(
    previous: dict[tuple[int, int], float] | None,
    actual: dict[tuple[int, int], float],
    *, policy: str, rho: float,
) -> dict[tuple[int, int], float]:
    """Mix the aggregate arrival state without changing actual arrivals."""
    if policy == "none" or previous is None:
        return {key: float(value) for key, value in actual.items()
                if abs(float(value)) > EPS}
    averaged: dict[tuple[int, int], float] = {}
    for key in set(previous) | set(actual):
        value = ((1.0 - rho) * float(previous.get(key, 0.0)) +
                 rho * float(actual.get(key, 0.0)))
        if abs(value) > EPS:
            averaged[key] = value
    return averaged


def arrival_map_metrics(
    previous: dict[tuple[int, int], float] | None,
    current: dict[tuple[int, int], float],
    actual: dict[tuple[int, int], float],
) -> dict[str, float]:
    """Return state-change and actual-vs-price-state arrival diagnostics."""
    previous = previous or {}
    keys = set(previous) | set(current)
    changes = [abs(float(current.get(key, 0.0)) - float(previous.get(key, 0.0)))
               for key in keys]
    actual_difference = [abs(float(actual.get(key, 0.0)) -
                             float(current.get(key, 0.0)))
                         for key in set(actual) | set(current)]
    return {
        "arrival_average_l1_change": sum(changes),
        "arrival_average_linf_change": max(changes, default=0.0),
        "actual_vs_average_arrival_l1": sum(actual_difference),
        "actual_vs_average_arrival_linf": max(actual_difference, default=0.0),
    }


def resource_arrival_train_ids(
    selected: dict[str, dict[str, str]], bin_minutes: float,
) -> dict[tuple[int, int], set[str]]:
    arriving: dict[tuple[int, int], set[str]] = defaultdict(set)
    for row in selected.values():
        train_id = str(row.get("train_id", ""))
        for arc_id, start, _ in parse_legs(row.get("legs", "")):
            key = (arc_id, max(0, int(math.floor(
                stable_block_ratio(start, bin_minutes)))))
            arriving[key].add(train_id)
    return dict(arriving)


def mow_windows_by_arc(arcs: dict[int, Arc], mow: list[dict[str, float | int]]) -> dict[int, list[tuple[float, float]]]:
    out: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for window in mow:
        a, b = int(window["a"]), int(window["b"])
        for arc in arcs.values():
            if {arc.a, arc.b} == {a, b}:
                out[arc.arc_id].append((float(window["start"]), float(window["end"])))
    return dict(out)


def open_minutes_for_block(
    windows: dict[int, list[tuple[float, float]]], arc_id: int,
    block_start: float, block_end: float
) -> float:
    intervals: list[tuple[float, float]] = []
    for start, end in windows.get(arc_id, []):
        left = max(block_start, start)
        right = min(block_end, end)
        if right - left > EPS:
            intervals.append((left, right))
    if not intervals:
        return block_end - block_start
    intervals.sort()
    closed = 0.0
    merged_left, merged_right = intervals[0]
    for left, right in intervals[1:]:
        if left <= merged_right + EPS:
            merged_right = max(merged_right, right)
        else:
            closed += merged_right - merged_left
            merged_left, merged_right = left, right
    closed += merged_right - merged_left
    open_minutes = block_end - block_start - closed
    if abs(open_minutes) <= EPS:
        return 0.0
    return min(block_end - block_start, max(0.0, open_minutes))


def clearance_wait_minutes(
    queue_after: float, current_bin: int, service: list[float], bin_minutes: float
) -> tuple[float, int]:
    """Backlog clearance time from block end, including future MOW gaps.

    This ignores future arrivals and measures the time needed for future
    service to process the current backlog; it is not an individual train's
    realized waiting time.
    """
    remaining = max(0.0, queue_after)
    if remaining <= EPS:
        return 0.0, 0
    elapsed = 0.0
    for future in range(current_bin + 1, len(service)):
        capacity = max(0.0, service[future])
        if capacity <= EPS:
            elapsed += bin_minutes
            continue
        if remaining <= capacity + EPS:
            elapsed += remaining * bin_minutes / capacity
            return elapsed, 0
        remaining -= capacity
        elapsed += bin_minutes
    return math.inf, 1


def fluid_queue_cells(
    arrivals: dict[tuple[int, int], float], occupancy: dict[tuple[int, int], float],
    old_lambda: dict[tuple[int, int], float], arcs: dict[int, Arc],
    mow: list[dict[str, float | int]], *, bin_minutes: float, horizon: float,
    price_arrivals: dict[tuple[int, int], float] | None = None,
    lr_capacity_model: str = DEFAULT_LR_CAPACITY_MODEL,
    lr_capacity_map: dict[tuple[int, int], float] | None = None,
) -> dict[tuple[int, int], ResourceCell]:
    """Compute actual and price-driving queues over the complete horizon.

    ``arrivals`` is always the actual discrete timetable arrival state.  When
    ``price_arrivals`` is supplied, it is a separate averaged state used only
    for the price-driving queue.  Both queues share exactly the same service
    quantity and MOW treatment.  The 30-minute cell is only the Fluid-Queue
    aggregation/pricing window: the service quantity is not a hard physical
    one-train-per-window rule, and multiple physically headway-separated
    trains may touch the same cell.  Leaving ``price_arrivals`` as ``None``
    makes the price queue an exact copy of the actual queue for compatibility.
    """
    if price_arrivals is None:
        price_arrivals = arrivals
    lr_capacity_map = lr_capacity_map or {}
    windows = mow_windows_by_arc(arcs, mow)
    n_bins = max(1, int(math.ceil(horizon / bin_minutes)))
    resource_ids = (
        {a for a, _ in arrivals} | {a for a, _ in price_arrivals} |
        {a for a, _ in occupancy} | {a for a, _ in old_lambda}
    )
    cells: dict[tuple[int, int], ResourceCell] = {}
    for arc_id in sorted(resource_ids):
        service: list[float] = []
        open_by_bin: list[float] = []
        for b in range(n_bins):
            start, end = b * bin_minutes, (b + 1) * bin_minutes
            open_minutes = open_minutes_for_block(windows, arc_id, start, end)
            open_by_bin.append(open_minutes)
            service.append(open_minutes / bin_minutes)
        queue_before = 0.0
        price_queue_before = 0.0
        for b in range(n_bins):
            arrival = float(arrivals.get((arc_id, b), 0.0))
            after = max(0.0, queue_before + arrival - service[b])
            clearance, unresolved = clearance_wait_minutes(after, b, service, bin_minutes)
            averaged_arrival = float(price_arrivals.get((arc_id, b), 0.0))
            price_after = max(
                0.0, price_queue_before + averaged_arrival - service[b])
            price_clearance, _ = clearance_wait_minutes(
                price_after, b, service, bin_minutes)
            cells[(arc_id, b)] = ResourceCell(
                arc_id=arc_id, time_bin=b, arrival_count=arrival,
                lr_occupancy_usage=float(occupancy.get((arc_id, b), 0.0)),
                lr_capacity=capacity_value(
                    (arc_id, b), model=lr_capacity_model,
                    capacity_map=lr_capacity_map),
                mow_open_minutes=open_by_bin[b],
                queue_service_capacity_train=service[b],
                mu_train_per_min=service[b] / bin_minutes,
                queue_before_train=queue_before, queue_after_train=after,
                clearance_wait_min=clearance, unresolved_queue=unresolved,
                lambda_old=float(old_lambda.get((arc_id, b), 0.0)),
                actual_arrival_count=arrival,
                averaged_arrival_count=averaged_arrival,
                price_queue_before_train=price_queue_before,
                price_queue_after_train=price_after,
                price_clearance_wait_min=price_clearance,
            )
            queue_before = after
            price_queue_before = price_after
    return cells


def fluid_lambda_target(
    cell: ResourceCell, *, alpha: float, beta: float,
    wait_reference_minutes: float, queue_wait_cost_per_min: float,
    max_price_wait_minutes: float, use_price_queue: bool = False,
) -> float:
    queue_after = (cell.price_queue_after_train if use_price_queue
                   else cell.queue_after_train)
    clearance_wait = (cell.price_clearance_wait_min if use_price_queue
                      else cell.clearance_wait_min)
    if queue_after <= EPS:
        return 0.0
    wait = clearance_wait
    if not math.isfinite(wait):
        wait = max_price_wait_minutes
    wait = min(max(wait, 0.0), max_price_wait_minutes)
    return max(0.0, alpha * queue_wait_cost_per_min * wait_reference_minutes
               * (wait / wait_reference_minutes) ** beta)


def fluid_lambda_update(
    cells: dict[tuple[int, int], ResourceCell], old_lambda: dict[tuple[int, int], float],
    *, alpha: float, beta: float, gamma: float, wait_reference_minutes: float,
    queue_wait_cost_per_min: float, max_price_wait_minutes: float,
    use_price_queue: bool = False,
) -> tuple[dict[tuple[int, int], float], dict[tuple[int, int], float]]:
    target: dict[tuple[int, int], float] = {}
    new: dict[tuple[int, int], float] = {}
    for key in set(old_lambda) | set(cells):
        cell = cells.get(key)
        if cell is None:
            target[key] = 0.0
        else:
            target[key] = fluid_lambda_target(
                cell, alpha=alpha, beta=beta,
                wait_reference_minutes=wait_reference_minutes,
                queue_wait_cost_per_min=queue_wait_cost_per_min,
                max_price_wait_minutes=max_price_wait_minutes,
                use_price_queue=use_price_queue,
            )
        old = max(0.0, float(old_lambda.get(key, 0.0)))
        new[key] = max(0.0, (1.0 - gamma) * old + gamma * target[key])
    return new, target


def legacy_lambda_update(
    old_lambda: dict[tuple[int, int], float], occupancy: dict[tuple[int, int], float],
    *, iteration: int, step_size_initial: float, minimum_step_size: float,
    gamma: float, lr_capacity_model: str = DEFAULT_LR_CAPACITY_MODEL,
    lr_capacity_map: dict[tuple[int, int], float] | None = None,
) -> tuple[dict[tuple[int, int], float], dict[tuple[int, int], float]]:
    lr_capacity_map = lr_capacity_map or {}
    step = max(step_size_initial / (iteration + 1.0), minimum_step_size)
    target: dict[tuple[int, int], float] = {}
    new: dict[tuple[int, int], float] = {}
    keys = set(old_lambda) | set(occupancy)
    if lr_capacity_model == "headway-relaxation":
        keys |= set(lr_capacity_map)
    for key in keys:
        old = max(0.0, float(old_lambda.get(key, 0.0)))
        raw = max(0.0, old + step * (
            float(occupancy.get(key, 0.0)) - capacity_value(
                key, model=lr_capacity_model, capacity_map=lr_capacity_map)))
        target[key] = raw
        new[key] = max(0.0, (1.0 - gamma) * old + gamma * raw)
    return new, target


def dual_subgradient_update(
    old_lambda: dict[tuple[int, int], float],
    occupancy: dict[tuple[int, int], float], *,
    iteration: int, dual_value: float, incumbent_ub: float,
    model: str, capacity_map: dict[tuple[int, int], float],
    theta: float, fallback_step: float, norm_tolerance: float = 1e-12,
) -> tuple[dict[tuple[int, int], float], dict[tuple[int, int], float], dict[str, object]]:
    """Projected Polyak ascent for a minimisation LR with a safe fallback.

    The returned gradient is always computed from the relaxed independent
    argmins.  A finite validated incumbent enables the Polyak step; otherwise
    a diminishing positive step is used and the result remains diagnostic.
    """
    keys = set(old_lambda) | (
        set(occupancy) & set(capacity_map)
        if model == "headway-relaxation" else set(occupancy)
    )
    if model == "headway-relaxation":
        keys |= set(capacity_map)
    gradient = {
        key: float(occupancy.get(key, 0.0)) - capacity_value(
            key, model=model, capacity_map=capacity_map)
        for key in keys
    }
    norm_sq = sum(value * value for value in gradient.values())
    if norm_sq <= norm_tolerance:
        return dict(old_lambda), gradient, {
            "step": 0.0, "norm_sq": norm_sq, "used_polyak": False,
            "stopped": True,
        }
    used_polyak = (
        math.isfinite(incumbent_ub) and math.isfinite(dual_value) and
        incumbent_ub > dual_value + EPS and theta > 0.0
    )
    if used_polyak:
        step = max(0.0, float(theta) * (incumbent_ub - dual_value) / norm_sq)
    else:
        step = max(0.0, float(fallback_step) / ((iteration + 1.0) ** 0.5))
    new = {
        key: max(0.0, float(old_lambda.get(key, 0.0)) + step * value)
        for key, value in gradient.items()
    }
    if any(not math.isfinite(value) for value in new.values()):
        raise FloatingPointError("dual update generated a non-finite multiplier")
    return new, gradient, {
        "step": step, "norm_sq": norm_sq, "used_polyak": used_polyak,
        "stopped": False,
    }


def congestion_episodes(
    cells: dict[tuple[int, int], ResourceCell], *, bin_minutes: float,
    queue_wait_cost_per_min: float, early_arrival_cost_per_min: float,
    late_arrival_cost_per_min: float,
) -> list[dict[str, object]]:
    """Extract block-level episodes using onset, peak, and clearance landmarks.

    T0 is the start of the first block with zero-before/positive-after queue.
    Tp is the end of the earliest block attaining the episode's maximum queue.
    Tc is the end of the first later block with positive-before/zero-after queue.
    These are 30-minute block-level approximations, not train preferred times.
    """
    by_arc: dict[int, list[ResourceCell]] = defaultdict(list)
    for cell in cells.values():
        by_arc[cell.arc_id].append(cell)
    episodes: list[dict[str, object]] = []
    for arc_id, series in by_arc.items():
        series.sort(key=lambda c: c.time_bin)
        i = 0
        episode_id = 0
        while i < len(series):
            if not (series[i].queue_before_train <= EPS and
                    series[i].queue_after_train > EPS):
                i += 1
                continue
            start = i
            positive_end = start
            while (positive_end + 1 < len(series) and
                   series[positive_end + 1].queue_after_train > EPS):
                positive_end += 1
            peak = max(
                range(start, positive_end + 1),
                key=lambda j: series[j].queue_after_train,
            )
            qmax = series[peak].queue_after_train
            widx = max(range(start, positive_end + 1),
                       key=lambda j: ((series[j].clearance_wait_min
                                       if math.isfinite(series[j].clearance_wait_min)
                                       else math.inf), -series[j].time_bin))
            d_plus = sum(series[j].arrival_count for j in range(start, peak + 1))
            c_plus = sum(series[j].queue_service_capacity_train for j in range(start, peak + 1))
            omega = d_plus - c_plus
            clear_bin = positive_end + 1
            cleared = (
                clear_bin < len(series) and
                series[clear_bin].queue_before_train > EPS and
                series[clear_bin].queue_after_train <= EPS
            )
            t0 = series[start].time_bin * bin_minutes
            tp = (series[peak].time_bin + 1) * bin_minutes
            if cleared:
                tc = (series[clear_bin].time_bin + 1) * bin_minutes
                duration = tc - t0
            else:
                tc, duration = "", ""
            wmax = series[widx].clearance_wait_min
            if not math.isfinite(wmax):
                wmax = math.inf
            symmetric = abs(early_arrival_cost_per_min - late_arrival_cost_per_min) <= EPS
            symmetric_reference = (late_arrival_cost_per_min * duration / 2.0
                                   if symmetric and cleared else "")
            symmetric_balance = (
                queue_wait_cost_per_min * wmax - symmetric_reference
                if symmetric and cleared and math.isfinite(wmax) else "")
            symmetric_implied = (
                2.0 * queue_wait_cost_per_min * wmax / duration
                if symmetric and cleared and duration > EPS and math.isfinite(wmax)
                else "")
            episodes.append({
                "arc_id": arc_id, "episode_id": episode_id,
                "T0_onset_min": t0, "Tp_peak_min": tp, "Tc_clear_min": tc,
                "cleared": int(cleared), "D_plus_train": d_plus,
                "C_plus_train": c_plus, "omega_train": omega,
                "omega_minus_qmax": omega - qmax,
                "Qmax_train": qmax, "qmax_time_min": tp,
                "wmax_min": wmax,
                "wmax_time_min": (series[widx].time_bin + 1) * bin_minutes,
                "duration_P_min": duration,
                "symmetric_schedule_reference_cost": symmetric_reference,
                "symmetric_balance_residual": symmetric_balance,
                "symmetric_implied_theta_schedule": symmetric_implied,
            })
            episode_id += 1
            i = clear_bin + 1 if cleared else len(series)
    return episodes


def write_resource_state(
    output: Path, cells: dict[tuple[int, int], ResourceCell],
    lambdas: dict[tuple[int, int], float], bin_minutes: float, *,
    lr_capacity_model: str = DEFAULT_LR_CAPACITY_MODEL,
    lr_capacity_map: dict[tuple[int, int], float] | None = None,
) -> None:
    lr_capacity_map = lr_capacity_map or {}
    fields = [
        "arc_id", "time_bin", "time_start", "time_end", "arrival_count",
        "actual_arrival_count", "averaged_arrival_count",
        "lr_occupancy_usage", "pricing_reference_capacity",
        "lr_capacity", "lr_excess", "lr_relaxation_capacity",
        "lr_relaxation_excess", "mow_open_minutes",
        "queue_service_capacity_train", "mu_train_per_min", "queue_before_train",
        "queue_after_train", "clearance_wait_min", "backlog_clearance_min",
        "price_queue_before_train", "price_queue_after_train",
        "price_clearance_wait_min", "price_backlog_clearance_min",
        "unresolved_queue",
        "lambda_old", "lambda_target", "lambda_new",
    ]
    with (output / "resource_state.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        keys = set(cells) | set(lambdas)
        for key in sorted(keys):
            cell = cells.get(key)
            if cell is None:
                arc, b = key
                cell = ResourceCell(arc, b, 0.0, 0.0, 1.0, bin_minutes,
                                    1.0, 1.0 / bin_minutes, 0.0, 0.0, 0.0, 0)
            writer.writerow({
                "arc_id": cell.arc_id, "time_bin": cell.time_bin,
                "time_start": cell.time_bin * bin_minutes,
                "time_end": (cell.time_bin + 1) * bin_minutes,
                "arrival_count": cell.arrival_count,
                "actual_arrival_count": cell.actual_arrival_count or cell.arrival_count,
                "averaged_arrival_count": cell.averaged_arrival_count,
                "lr_occupancy_usage": cell.lr_occupancy_usage,
                "pricing_reference_capacity": 1.0,
                "lr_capacity": cell.lr_capacity,
                "lr_excess": max(0.0, cell.lr_occupancy_usage - cell.lr_capacity),
                "lr_relaxation_capacity": lr_capacity_value(
                    key, model=lr_capacity_model, capacity_map=lr_capacity_map),
                "lr_relaxation_excess": max(
                    0.0, cell.lr_occupancy_usage - lr_capacity_value(
                        key, model=lr_capacity_model, capacity_map=lr_capacity_map)),
                "mow_open_minutes": cell.mow_open_minutes,
                "queue_service_capacity_train": cell.queue_service_capacity_train,
                "mu_train_per_min": cell.mu_train_per_min,
                "queue_before_train": cell.queue_before_train,
                "queue_after_train": cell.queue_after_train,
                "clearance_wait_min": cell.clearance_wait_min,
                "backlog_clearance_min": cell.clearance_wait_min,
                "price_queue_before_train": cell.price_queue_before_train,
                "price_queue_after_train": cell.price_queue_after_train,
                "price_clearance_wait_min": cell.price_clearance_wait_min,
                "price_backlog_clearance_min": cell.price_clearance_wait_min,
                "unresolved_queue": cell.unresolved_queue,
                "lambda_old": cell.lambda_old,
                "lambda_target": cell.lambda_target,
                "lambda_new": lambdas.get(key, cell.lambda_new),
            })


def write_congestion_episodes(output: Path, episodes: list[dict[str, object]]) -> None:
    fields = ["arc_id", "episode_id", "T0_onset_min", "Tp_peak_min", "Tc_clear_min", "cleared",
              "D_plus_train", "C_plus_train", "omega_train", "Qmax_train",
              "omega_minus_qmax", "qmax_time_min", "wmax_min", "wmax_time_min",
              "duration_P_min", "symmetric_schedule_reference_cost",
              "symmetric_balance_residual", "symmetric_implied_theta_schedule"]
    with (output / "congestion_episodes.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(episodes)


def route_signature_digest(selected: dict[str, dict[str, str]]) -> str:
    signature = sorted(route_signature_map(selected).items())
    payload = json.dumps(signature, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def route_signature_map(
    selected: dict[str, dict[str, str]],
) -> dict[str, tuple[str, str]]:
    return {
        train_id: (row.get("route_index", ""), row.get("arc_ids", ""))
        for train_id, row in selected.items()
    }


def trajectory_signature_map(
    selected: dict[str, dict[str, str]],
) -> dict[str, tuple[str, str, str, str, str]]:
    return {
        train_id: (
            row.get("route_index", ""), row.get("departure_min", ""),
            row.get("arrival_min", ""), row.get("waits", ""),
            row.get("legs", ""),
        )
        for train_id, row in selected.items()
    }


def trajectory_signature_digest(selected: dict[str, dict[str, str]]) -> str:
    signature = sorted(trajectory_signature_map(selected).items())
    payload = json.dumps(signature, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def changed_train_counts(
    previous: dict[str, dict[str, str]] | None,
    current: dict[str, dict[str, str]],
    *, tolerance: float = 1e-6,
) -> tuple[int, int, int, dict[str, tuple[bool, bool]]]:
    """Return changed-train, route-change, timing-change counts and flags."""
    if previous is None:
        flags = {train_id: (False, False) for train_id in current}
        return 0, 0, 0, flags
    route_changes = timing_changes = 0
    flags: dict[str, tuple[bool, bool]] = {}
    for train_id in sorted(set(previous) | set(current)):
        old = previous.get(train_id, {})
        new = current.get(train_id, {})
        route_changed = (
            old.get("route_index", "") != new.get("route_index", "") or
            old.get("arc_ids", "") != new.get("arc_ids", "")
        )
        try:
            timing_changed = (
                abs(float(old.get("departure_min", "nan")) -
                    float(new.get("departure_min", "nan"))) > tolerance or
                abs(float(old.get("arrival_min", "nan")) -
                    float(new.get("arrival_min", "nan"))) > tolerance
            )
        except (TypeError, ValueError):
            timing_changed = old.get("departure_min", "") != new.get("departure_min", "")
        flags[train_id] = (route_changed, timing_changed)
        route_changes += int(route_changed)
        timing_changes += int(timing_changed)
    return route_changes + timing_changes - sum(
        int(route and timing) for route, timing in flags.values()), route_changes, timing_changes, flags


def cycle_observation(
    route_hashes: list[str], trajectory_hashes: list[str],
    route_counters: dict[int, int], trajectory_counters: dict[int, int],
) -> dict[str, object]:
    """Observe periods 2--4 and require three consecutive confirmations."""
    index = len(route_hashes) - 1
    observation: dict[str, object] = {}
    for kind, hashes, counters in (
        ("route", route_hashes, route_counters),
        ("trajectory", trajectory_hashes, trajectory_counters),
    ):
        for period in (2, 3, 4):
            confirmed = (
                index >= period and hashes[index] == hashes[index - period] and
                hashes[index] != hashes[index - 1]
            )
            counters[period] = counters.get(period, 0) + 1 if confirmed else 0
            observation[f"period{period}_{kind}_cycle"] = bool(confirmed)
            observation[f"persistent_period{period}_{kind}_cycles"] = counters[period]
    detected_period = 0
    for kind, counters in (("trajectory", trajectory_counters), ("route", route_counters)):
        periods = [p for p in (2, 3, 4) if counters.get(p, 0) >= 3]
        if periods:
            detected_period = min(periods)
            observation["detected_cycle_signature_type"] = kind
            break
    observation["detected_persistent_cycle"] = detected_period != 0
    observation["detected_cycle_period"] = detected_period
    return observation


def _approx_equal(a: float, b: float, tolerance: float = 1e-6) -> bool:
    return abs(a - b) <= tolerance


def detect_cycle_cells(
    cell_history: dict[tuple[int, int], list[dict[str, object]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for (arc_id, time_bin), series in cell_history.items():
        confirmations = 0
        best_confirmations = 0
        best_index = -1
        for index in range(2, len(series)):
            a, b, c = series[index - 2], series[index - 1], series[index]
            q_alt = (_approx_equal(float(c["queue_after_train"]), float(a["queue_after_train"])) and
                     not _approx_equal(float(c["queue_after_train"]), float(b["queue_after_train"])))
            price_q_alt = (
                _approx_equal(float(c.get("price_queue_after_train", 0.0)),
                              float(a.get("price_queue_after_train", 0.0)))
                and not _approx_equal(float(c.get("price_queue_after_train", 0.0)),
                                      float(b.get("price_queue_after_train", 0.0)))
            )
            target_alt = (_approx_equal(float(c["lambda_target"]), float(a["lambda_target"])) and
                          not _approx_equal(float(c["lambda_target"]), float(b["lambda_target"])))
            lambda_alt = (_approx_equal(float(c["lambda_new"]), float(a["lambda_new"])) and
                          not _approx_equal(float(c["lambda_new"]), float(b["lambda_new"])))
            if q_alt or price_q_alt or target_alt or lambda_alt:
                confirmations += 1
                if confirmations > best_confirmations:
                    best_confirmations = confirmations
                    best_index = index
            else:
                confirmations = 0
        if best_confirmations < 3 or best_index < 1:
            continue
        a, b = series[best_index], series[best_index - 1]
        involved = set(a["selected_train_ids"]) | set(b["selected_train_ids"])
        rows.append({
            "arc_id": arc_id, "time_bin": time_bin, "detected_period": 2,
            "confirmations": best_confirmations,
            "q_state_a": a["queue_after_train"], "q_state_b": b["queue_after_train"],
            "price_q_state_a": a.get("price_queue_after_train", 0.0),
            "price_q_state_b": b.get("price_queue_after_train", 0.0),
            "lambda_target_state_a": a["lambda_target"],
            "lambda_target_state_b": b["lambda_target"],
            "lambda_state_a": a["lambda_new"], "lambda_state_b": b["lambda_new"],
            "involved_train_ids": ";".join(sorted(involved)),
            "cycle_queue_amplitude": abs(float(a["queue_after_train"]) -
                                          float(b["queue_after_train"])),
            "cycle_price_queue_amplitude": abs(
                float(a.get("price_queue_after_train", 0.0)) -
                float(b.get("price_queue_after_train", 0.0))),
            "cycle_lambda_amplitude": abs(float(a["lambda_new"]) -
                                           float(b["lambda_new"])),
        })
    rows.sort(key=lambda row: (-int(row["confirmations"]),
                               -float(row["cycle_lambda_amplitude"]),
                               int(row["arc_id"]), int(row["time_bin"])))
    return rows


def tail_iteration_indices(history_length: int, tail_window: int) -> list[int]:
    """Return the final post-initial iteration transitions used by tail metrics."""
    if history_length <= 1 or tail_window <= 0:
        return []
    start = max(1, history_length - tail_window)
    return list(range(start, history_length))


def detect_cycle_cells_window(
    cell_history: dict[tuple[int, int], list[dict[str, object]]],
    iteration_indices: list[int],
) -> list[dict[str, object]]:
    """Detect persistent period-2 behavior using only a final window."""
    rows: list[dict[str, object]] = []
    if not iteration_indices:
        return rows
    for (arc_id, time_bin), series in cell_history.items():
        observations = [
            (iteration, series[iteration])
            for iteration in iteration_indices if iteration < len(series)
        ]
        confirmations = 0
        best_confirmations = 0
        best_position = -1
        for position in range(2, len(observations)):
            (i_a, a), (i_b, b), (i_c, c) = observations[position - 2:position + 1]
            if not (i_b == i_a + 1 and i_c == i_b + 1):
                confirmations = 0
                continue
            q_alt = (
                _approx_equal(float(c["queue_after_train"]), float(a["queue_after_train"]))
                and not _approx_equal(float(c["queue_after_train"]), float(b["queue_after_train"]))
            )
            price_q_alt = (
                _approx_equal(float(c.get("price_queue_after_train", 0.0)),
                              float(a.get("price_queue_after_train", 0.0)))
                and not _approx_equal(float(c.get("price_queue_after_train", 0.0)),
                                      float(b.get("price_queue_after_train", 0.0)))
            )
            target_alt = (
                _approx_equal(float(c["lambda_target"]), float(a["lambda_target"]))
                and not _approx_equal(float(c["lambda_target"]), float(b["lambda_target"]))
            )
            lambda_alt = (
                _approx_equal(float(c["lambda_new"]), float(a["lambda_new"]))
                and not _approx_equal(float(c["lambda_new"]), float(b["lambda_new"]))
            )
            if q_alt or price_q_alt or target_alt or lambda_alt:
                confirmations += 1
                if confirmations > best_confirmations:
                    best_confirmations = confirmations
                    best_position = position
            else:
                confirmations = 0
        if best_confirmations < 3 or best_position < 1:
            continue
        _, a = observations[best_position]
        _, b = observations[best_position - 1]
        involved = set(a["selected_train_ids"]) | set(b["selected_train_ids"])
        rows.append({
            "arc_id": arc_id, "time_bin": time_bin,
            "detected_period": 2,
            "confirmations_in_tail": best_confirmations,
            "q_state_a": a["queue_after_train"],
            "q_state_b": b["queue_after_train"],
            "price_q_state_a": a.get("price_queue_after_train", 0.0),
            "price_q_state_b": b.get("price_queue_after_train", 0.0),
            "lambda_target_state_a": a["lambda_target"],
            "lambda_target_state_b": b["lambda_target"],
            "lambda_state_a": a["lambda_new"],
            "lambda_state_b": b["lambda_new"],
            "cycle_queue_amplitude": abs(
                float(a["queue_after_train"]) - float(b["queue_after_train"])),
            "cycle_price_queue_amplitude": abs(
                float(a.get("price_queue_after_train", 0.0)) -
                float(b.get("price_queue_after_train", 0.0))),
            "cycle_lambda_amplitude": abs(
                float(a["lambda_new"]) - float(b["lambda_new"])),
            "involved_train_ids": ";".join(sorted(involved)),
        })
    rows.sort(key=lambda row: (
        -int(row["confirmations_in_tail"]),
        -float(row["cycle_lambda_amplitude"]),
        int(row["arc_id"]), int(row["time_bin"]),
    ))
    return rows


def tail_signature_diagnostics(
    route_hashes: list[str], trajectory_hashes: list[str],
    iteration_indices: list[int],
) -> dict[str, object]:
    if not iteration_indices:
        return {
            "tail_detected_signature_cycle": False,
            "tail_detected_cycle_period": 0,
            "tail_detected_cycle_signature_type": "none",
        }
    start, end = iteration_indices[0], iteration_indices[-1] + 1
    route_tail = route_hashes[start:end]
    trajectory_tail = trajectory_hashes[start:end]
    route_counters: dict[int, int] = {}
    trajectory_counters: dict[int, int] = {}
    observation: dict[str, object] = {}
    for position in range(len(route_tail)):
        observation = cycle_observation(
            route_tail[:position + 1], trajectory_tail[:position + 1],
            route_counters, trajectory_counters,
        )
    return {
        "tail_detected_signature_cycle": bool(
            observation.get("detected_persistent_cycle", False)),
        "tail_detected_cycle_period": int(
            observation.get("detected_cycle_period", 0)),
        "tail_detected_cycle_signature_type": observation.get(
            "detected_cycle_signature_type", "none"),
    }


def stale_price_count_from_cells(
    cells: dict[tuple[int, int], ResourceCell],
) -> int:
    return sum(
        1 for cell in cells.values()
        if cell.lambda_new > EPS
        and cell.queue_after_train <= EPS
        and cell.lambda_target <= EPS
    )


def stale_price_stats_from_snapshots(
    snapshots: Iterable[dict[str, object]],
) -> dict[str, float]:
    stale = [snapshot for snapshot in snapshots
             if float(snapshot.get("lambda_new", 0.0)) > EPS
             and float(snapshot.get("queue_after_train", 0.0)) <= EPS
             and float(snapshot.get("lambda_target", 0.0)) <= EPS]
    values = [float(snapshot.get("lambda_new", 0.0)) for snapshot in stale]
    return {
        "count": float(len(values)),
        "mass": sum(values),
        "max": max(values, default=0.0),
        "count_gt_1": float(sum(value > 1.0 for value in values)),
        "count_gt_10": float(sum(value > 10.0 for value in values)),
    }


def stale_price_stats_from_cells(
    cells: dict[tuple[int, int], ResourceCell],
) -> dict[str, float]:
    return stale_price_stats_from_snapshots({
        key: {
            "lambda_new": cell.lambda_new,
            "queue_after_train": cell.queue_after_train,
            "lambda_target": cell.lambda_target,
        }
        for key, cell in cells.items()
    }.values())


def linear_trend(values: list[float], x_values: list[float]) -> float:
    if len(values) < 2 or len(values) != len(x_values):
        return 0.0
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(values) / len(values)
    denominator = sum((x - x_mean) ** 2 for x in x_values)
    if denominator <= EPS:
        return 0.0
    return sum((x - x_mean) * (y - y_mean)
               for x, y in zip(x_values, values)) / denominator


def compute_tail_diagnostics(
    history: list[dict[str, object]],
    cell_history: dict[tuple[int, int], list[dict[str, object]]],
    route_hashes: list[str], trajectory_hashes: list[str],
    final_cells: dict[tuple[int, int], ResourceCell],
    *, tail_window: int, train_count: int, max_price_wait_minutes: float,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    indices = tail_iteration_indices(len(history), tail_window)
    count = len(indices)
    denominator = float(count * train_count) if count and train_count else 1.0

    def mean_history(field: str) -> float:
        if not indices:
            return 0.0
        return sum(float(history[i].get(field, 0.0)) for i in indices) / count

    def max_history(field: str) -> float:
        return max((float(history[i].get(field, 0.0)) for i in indices), default=0.0)

    def min_history(field: str) -> float:
        return min((float(history[i].get(field, 0.0)) for i in indices), default=0.0)

    tail_cycle_rows = detect_cycle_cells_window(cell_history, indices)
    tail_signature = tail_signature_diagnostics(
        route_hashes, trajectory_hashes, indices)
    tail_active_counts: list[float] = []
    tail_positive_queue_counts: list[float] = []
    tail_stale_counts: list[float] = []
    tail_stale_masses: list[float] = []
    tail_stale_maxima: list[float] = []
    tail_stale_gt_1: list[float] = []
    tail_stale_gt_10: list[float] = []
    tail_cap_cell_sets: list[set[tuple[int, int]]] = []
    for iteration in indices:
        snapshots = [
            series[iteration] for series in cell_history.values()
            if iteration < len(series)
        ]
        tail_active_counts.append(sum(
            1 for snapshot in snapshots
            if float(snapshot.get("lambda_new", 0.0)) > EPS
        ))
        tail_positive_queue_counts.append(sum(
            1 for snapshot in snapshots
            if float(snapshot.get("queue_after_train", 0.0)) > EPS
        ))
        stale_stats = stale_price_stats_from_snapshots(snapshots)
        tail_stale_counts.append(stale_stats["count"])
        tail_stale_masses.append(stale_stats["mass"])
        tail_stale_maxima.append(stale_stats["max"])
        tail_stale_gt_1.append(stale_stats["count_gt_1"])
        tail_stale_gt_10.append(stale_stats["count_gt_10"])
        cap_cells = {
            (int(snapshot["arc_id"]), int(snapshot["time_bin"]))
            for snapshot in snapshots
            if float(snapshot.get("queue_after_train", 0.0)) > EPS
            and float(snapshot.get("backlog_clearance_min", 0.0)) >= max_price_wait_minutes
        }
        tail_cap_cell_sets.append(cap_cells)

    relative_values = [
        float(history[i].get("relative_lambda_linf_change", 0.0))
        for i in indices
    ]
    linf_values = [
        float(history[i].get("lambda_linf_change", 0.0))
        for i in indices
    ]
    tail_cap_iterations = sum(
        1 for i in indices
        if int(history[i].get("price_wait_cap_hit_count", 0)) > 0
    )
    tail_unique_cap_cells = set().union(*tail_cap_cell_sets) if tail_cap_cell_sets else set()
    tail_mean_cycle_lambda = (
        sum(float(row["cycle_lambda_amplitude"]) for row in tail_cycle_rows)
        / len(tail_cycle_rows) if tail_cycle_rows else 0.0
    )
    tail_mean_cycle_queue = (
        sum(float(row["cycle_queue_amplitude"]) for row in tail_cycle_rows)
        / len(tail_cycle_rows) if tail_cycle_rows else 0.0
    )
    tail_cycle_lambda_mass = sum(
        float(row["cycle_lambda_amplitude"]) for row in tail_cycle_rows)
    tail_cycle_queue_mass = sum(
        float(row["cycle_queue_amplitude"]) for row in tail_cycle_rows)
    diagnostics: dict[str, object] = {
        "tail_window": tail_window,
        "tail_transition_count": count,
        "tail_changed_train_rate": (
            sum(float(history[i].get("changed_train_count", 0.0)) for i in indices)
            / denominator),
        "tail_changed_route_rate": (
            sum(float(history[i].get("changed_route_count", 0.0)) for i in indices)
            / denominator),
        "tail_changed_timing_rate": (
            sum(float(history[i].get("changed_timing_count", 0.0)) for i in indices)
            / denominator),
        "tail_route_stable_fraction": (
            sum(bool(history[i].get("route_stable", False)) for i in indices) / count
            if count else 0.0),
        "tail_trajectory_stable_fraction": (
            sum(bool(history[i].get("trajectory_stable", False)) for i in indices) / count
            if count else 0.0),
        "tail_price_stable_fraction": (
            sum(bool(history[i].get("price_stable", False)) for i in indices) / count
            if count else 0.0),
        "tail_joint_stable_fraction": (
            sum(bool(history[i].get("trajectory_stable", False)) and
                bool(history[i].get("price_stable", False)) for i in indices) / count
            if count else 0.0),
        "tail_mean_relative_lambda_linf": mean_history("relative_lambda_linf_change"),
        "tail_max_relative_lambda_linf": max_history("relative_lambda_linf_change"),
        "tail_min_relative_lambda_linf": min_history("relative_lambda_linf_change"),
        "tail_relative_linf_trend": linear_trend(
            relative_values, [float(i) for i in indices]),
        "tail_mean_lambda_linf_change": mean_history("lambda_linf_change"),
        "tail_max_lambda_linf_change": max_history("lambda_linf_change"),
        "tail_mean_max_lambda": mean_history("max_lambda"),
        "tail_max_max_lambda": max_history("max_lambda"),
        "tail_mean_max_queue": mean_history("max_queue_train"),
        "tail_max_max_queue": max_history("max_queue_train"),
        "tail_mean_max_lr_excess": mean_history("max_lr_excess"),
        "tail_max_max_lr_excess": max_history("max_lr_excess"),
        "tail_mean_total_queue": mean_history("total_queue_train"),
        **tail_signature,
        "tail_cycle_cell_count": len(tail_cycle_rows),
        "tail_cycle_lambda_mass": tail_cycle_lambda_mass,
        "tail_cycle_queue_mass": tail_cycle_queue_mass,
        "tail_cycle_cells_lambda_amp_gt_1": sum(
            float(row["cycle_lambda_amplitude"]) > 1.0
            for row in tail_cycle_rows),
        "tail_cycle_cells_lambda_amp_gt_10": sum(
            float(row["cycle_lambda_amplitude"]) > 10.0
            for row in tail_cycle_rows),
        "tail_mean_cycle_lambda_amplitude": tail_mean_cycle_lambda,
        "tail_max_cycle_lambda_amplitude": max((
            float(row["cycle_lambda_amplitude"]) for row in tail_cycle_rows), default=0.0),
        "tail_mean_cycle_queue_amplitude": tail_mean_cycle_queue,
        "tail_max_cycle_queue_amplitude": max((
            float(row["cycle_queue_amplitude"]) for row in tail_cycle_rows), default=0.0),
        "tail_iterations_with_cap_hit": tail_cap_iterations,
        "tail_fraction_iterations_with_cap_hit": (
            tail_cap_iterations / count if count else 0.0),
        "tail_unique_cells_hitting_cap": len(tail_unique_cap_cells),
        "tail_mean_active_positive_lambda_cells": (
            sum(tail_active_counts) / count if count else 0.0),
        "tail_final_active_positive_lambda_cells": (
            tail_active_counts[-1] if tail_active_counts else 0.0),
        "tail_mean_positive_queue_cells": (
            sum(tail_positive_queue_counts) / count if count else 0.0),
        "tail_mean_stale_price_cell_count": (
            sum(tail_stale_counts) / count if count else 0.0),
        "tail_mean_stale_price_mass": (
            sum(tail_stale_masses) / count if count else 0.0),
        "tail_max_stale_price_mass": max(tail_stale_masses, default=0.0),
        "tail_mean_max_stale_price": (
            sum(tail_stale_maxima) / count if count else 0.0),
        "tail_mean_stale_price_count_gt_1": (
            sum(tail_stale_gt_1) / count if count else 0.0),
        "tail_mean_stale_price_count_gt_10": (
            sum(tail_stale_gt_10) / count if count else 0.0),
        "tail_final_stale_price_cell_count": (
            tail_stale_counts[-1] if tail_stale_counts else 0.0),
        "final_stale_price_cell_count": stale_price_count_from_cells(final_cells),
    }
    return diagnostics, tail_cycle_rows


def snapshot_cells(
    cells: dict[tuple[int, int], ResourceCell],
    selected_ids: dict[tuple[int, int], set[str]],
    arriving_ids: dict[tuple[int, int], set[str]],
) -> dict[tuple[int, int], dict[str, object]]:
    snapshots: dict[tuple[int, int], dict[str, object]] = {}
    for key, cell in cells.items():
        snapshots[key] = {
            "arc_id": cell.arc_id, "time_bin": cell.time_bin,
            "arrival_count": cell.arrival_count,
            "actual_arrival_count": cell.actual_arrival_count,
            "averaged_arrival_count": cell.averaged_arrival_count,
            "lr_occupancy_usage": cell.lr_occupancy_usage,
            "lr_excess": max(0.0, cell.lr_occupancy_usage - cell.lr_capacity),
            "queue_before_train": cell.queue_before_train,
            "queue_after_train": cell.queue_after_train,
            "backlog_clearance_min": cell.clearance_wait_min,
            "price_queue_before_train": cell.price_queue_before_train,
            "price_queue_after_train": cell.price_queue_after_train,
            "price_backlog_clearance_min": cell.price_clearance_wait_min,
            "lambda_old": cell.lambda_old, "lambda_target": cell.lambda_target,
            "lambda_new": cell.lambda_new,
            "selected_train_ids": set(selected_ids.get(key, set())),
            "arriving_train_ids": set(arriving_ids.get(key, set())),
        }
    return snapshots


def write_price_map(output: Path, beta: float, *, alpha: float,
                    queue_wait_cost_per_min: float, tau0: float,
                    max_price_wait_minutes: float) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for queue_train in range(7):
        wait = 30.0 * queue_train
        ratio = wait / tau0 if tau0 > 0 else 0.0
        unclipped = (alpha * queue_wait_cost_per_min * tau0 * ratio ** beta
                     if queue_train > 0 else 0.0)
        clipped_wait = min(max(wait, 0.0), max_price_wait_minutes)
        clipped_ratio = clipped_wait / tau0 if tau0 > 0 else 0.0
        clipped_target = (alpha * queue_wait_cost_per_min * tau0 * clipped_ratio ** beta
                          if queue_train > 0 else 0.0)
        rows.append({
            "beta": beta, "queue_train": queue_train,
            "clearance_wait_min": wait,
            "unclipped_lambda_target": unclipped,
            "clipped_wait_min": clipped_wait,
            "lambda_target_after_wait_cap": clipped_target,
        })
    with (output / "price_map.csv").open("w", encoding="utf-8", newline="") as stream:
        fields = list(rows[0])
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def write_resource_iteration_trace(
    output: Path, rows: list[dict[str, object]],
) -> None:
    fields = [
        "iteration", "arc_id", "time_bin", "time_start_min", "time_end_min",
        "arrival_count", "actual_arrival_count", "averaged_arrival_count",
        "lr_occupancy_usage", "lr_excess",
        "queue_before_train", "queue_after_train", "backlog_clearance_min",
        "actual_queue_before", "actual_queue_after",
        "actual_backlog_clearance_min", "price_queue_before",
        "price_queue_after", "price_backlog_clearance_min", "rho",
        "lambda_old", "lambda_target", "lambda_new", "lambda_change",
        "wait_to_reference_ratio", "price_wait_cap_hit",
        "selected_train_ids", "arriving_train_ids",
    ]
    with (output / "resource_iteration_trace.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_arrival_averaging_trace(
    output: Path, rows: list[dict[str, object]],
) -> None:
    fields = [
        "iteration", "arc_id", "time_bin", "rho", "actual_arrival",
        "previous_average_arrival", "new_average_arrival",
        "arrival_difference", "actual_queue", "price_queue",
        "lambda_target", "lambda_new", "selected_train_ids",
    ]
    with (output / "arrival_averaging_trace.csv").open(
            "w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_train_iteration_trace(output: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "iteration", "train_id", "route_index", "arc_ids", "departure_min", "arrival_min",
        "departure_delay_min", "origin_wait_cost", "siding_wait_min", "enroute_wait_min",
        "running_cost", "waiting_cost", "arrival_schedule_cost", "lambda_cost",
        "physical_cost", "total_priced_cost", "route_changed_from_previous",
        "timing_changed_from_previous",
    ]
    with (output / "train_iteration_trace.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_cycle_cells(output: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "arc_id", "time_bin", "detected_period", "confirmations",
        "q_state_a", "q_state_b", "price_q_state_a", "price_q_state_b",
        "lambda_target_state_a",
        "lambda_target_state_b", "lambda_state_a", "lambda_state_b",
        "involved_train_ids", "cycle_queue_amplitude",
        "cycle_price_queue_amplitude", "cycle_lambda_amplitude",
    ]
    with (output / "cycle_cells.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_tail_cycle_cells(output: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "arc_id", "time_bin", "detected_period", "confirmations_in_tail",
        "q_state_a", "q_state_b", "price_q_state_a", "price_q_state_b",
        "lambda_target_state_a",
        "lambda_target_state_b", "lambda_state_a", "lambda_state_b",
        "cycle_queue_amplitude", "cycle_price_queue_amplitude",
        "cycle_lambda_amplitude", "involved_train_ids",
    ]
    with (output / "tail_cycle_cells.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_bottleneck_trace(
    output: Path, cell_history: dict[tuple[int, int], list[dict[str, object]]],
    cycle_rows: list[dict[str, object]], iterations: int,
) -> None:
    max_lambda = sorted(
        cell_history,
        key=lambda key: max((float(row["lambda_new"]) for row in cell_history[key]), default=0.0),
        reverse=True,
    )[:5]
    max_queue = sorted(
        cell_history,
        key=lambda key: max((float(row["queue_after_train"]) for row in cell_history[key]), default=0.0),
        reverse=True,
    )[:5]
    cycle_ranked = [
        (int(row["arc_id"]), int(row["time_bin"]))
        for row in cycle_rows[:5]
    ]
    reasons: list[tuple[str, list[tuple[int, int]]]] = [
        ("max_lambda", max_lambda), ("max_queue", max_queue),
        ("cycle_confirmations", cycle_ranked),
    ]
    rows: list[dict[str, object]] = []
    for reason, keys in reasons:
        for key in keys:
            series = cell_history.get(key, [])
            for iteration in range(iterations):
                snapshot = series[iteration] if iteration < len(series) else {}
                rows.append({
                    "iteration": iteration, "rank_reason": reason,
                    "arc_id": key[0], "time_bin": key[1],
                    "arrival_count": snapshot.get("arrival_count", 0.0),
                    "occupancy": snapshot.get("lr_occupancy_usage", 0.0),
                    "queue": snapshot.get("queue_after_train", 0.0),
                    "clearance_wait": snapshot.get("backlog_clearance_min", 0.0),
                    "lambda_target": snapshot.get("lambda_target", 0.0),
                    "lambda_old": snapshot.get("lambda_old", 0.0),
                    "lambda_new": snapshot.get("lambda_new", 0.0),
                    "selected_train_ids": ";".join(sorted(snapshot.get("selected_train_ids", set()))),
                })
    fields = ["iteration", "rank_reason", "arc_id", "time_bin", "arrival_count",
              "occupancy", "queue", "clearance_wait", "lambda_target", "lambda_old",
              "lambda_new", "selected_train_ids"]
    with (output / "bottleneck_trace.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_routes_file(path: Path, selected: dict[str, dict[str, str]]) -> None:
    fields = ["train_id", "route_index", "arc_ids", "legs", "departure_min", "arrival_min",
              "waits",
              "departure_delay_min", "origin_wait_cost", "running_time_min",
              "running_cost", "enroute_wait_min", "siding_wait_min", "total_wait_min",
              "waiting_cost", "early_arrival_min", "late_arrival_min",
              "arrival_schedule_cost", "lambda_cost", "physical_cost", "total_priced_cost"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for train_id, row in sorted(selected.items()):
            writer.writerow({field: (train_id if field == "train_id" else row.get(field, ""))
                             for field in fields})


def write_final_routes(output: Path, selected: dict[str, dict[str, str]]) -> None:
    _write_routes_file(output / "final_routes.tsv", selected)


def _bb_action_dict(action: ForbiddenAction) -> dict[str, object]:
    return {
        "train_id": action.train_id, "route_index": action.route_index,
        "arc_position": action.arc_position, "start_cell": action.start_cell,
        "wait_tick": action.wait_tick,
    }


def _bb_required_dict(action: RequiredAction) -> dict[str, object]:
    return {
        "train_id": action.train_id, "route_index": action.route_index,
        "arc_position": action.arc_position, "start_cell": action.start_cell,
        "wait_tick": action.wait_tick,
    }


def _bb_signature_dict(action: ActionSignature) -> dict[str, object]:
    return {
        "train_id": action.train_id, "route_index": action.route_index,
        "arc_position": action.arc_position, "arc_id": action.arc_id,
        "direction": action.direction, "start_cell": action.start_cell,
        "wait_tick": action.wait_tick, "start_min": action.start_min,
        "exit_min": action.exit_min,
    }


def _bb_conflict_dict(conflict: PhysicalConflict | None) -> dict[str, object] | None:
    if conflict is None:
        return None
    return {
        "conflict_type": conflict.conflict_type, "arc_id": conflict.arc_id,
        "time_min": conflict.time_min, "train_a": conflict.train_a,
        "train_b": conflict.train_b,
        "action_a": _bb_signature_dict(conflict.action_a),
        "action_b": _bb_signature_dict(conflict.action_b),
    }


def _bb_node_spec(node: BBNode) -> dict[str, object]:
    return {
        "constraint_schema_version": BB_CONSTRAINT_SCHEMA_VERSION,
        "node_id": node.node_id, "parent_id": node.parent_id,
        "depth": node.depth,
        "required_actions": [
            _bb_required_dict(action) for action in sorted(node.required_actions)
        ],
        "forbidden_actions": [
            _bb_action_dict(action) for action in sorted(node.forbidden_actions)
        ],
        "inherited_lower_bound": node.inherited_lower_bound,
        "lower_bound": node.lower_bound,
        "base_lower_bound": node.base_lower_bound,
        "dual_lower_bound": node.dual_lower_bound,
        "best_lambda": {
            f"{key[0]}:{key[1]}": value for key, value in node.best_lambda.items()
        },
        "branch_action": (
            _bb_signature_dict(node.branch_action)
            if node.branch_action is not None else None
        ),
        "branch_conflict_type": node.branch_conflict_type,
        "branch_conflict_arc": node.branch_conflict_arc,
        "branch_conflict_time": node.branch_conflict_time,
        "dual_history": node.dual_history,
        "required_residual_movements": node.required_residual_movements,
        "labels_pruned_required_headway": node.labels_pruned_required_headway,
        "labels_pruned_required_opposing": node.labels_pruned_required_opposing,
        "labels_pruned_required_special": node.labels_pruned_required_special,
        "required_action_infeasibility": node.required_action_infeasibility,
        "representative_trajectory_signatures": list(
            node.representative_trajectory_signatures),
    }


def _write_bb_checkpoint(
    path: Path, *, fingerprint: str, open_nodes: Iterable[BBNode],
    incumbent_ub: float, incumbent_routes: dict[str, dict[str, str]],
    next_node_id: int, global_lb_history: list[float], termination_reason: str,
    candidate_count: int, physical_fingerprint: str,
    root_bound_certificate_path: Path | None = None,
) -> None:
    payload = {
        "version": BB_CHECKPOINT_VERSION,
        "constraint_schema_version": BB_CONSTRAINT_SCHEMA_VERSION,
        "candidate_fingerprint": fingerprint, "candidate_count": candidate_count,
        "physical_model_fingerprint": physical_fingerprint,
        "root_bound_certificate_path": (
            str(root_bound_certificate_path.resolve())
            if root_bound_certificate_path is not None else None),
        "open_nodes": [_bb_node_spec(node) for node in open_nodes],
        "incumbent_ub": incumbent_ub,
        "incumbent_routes": incumbent_routes,
        "next_node_id": next_node_id,
        "global_lb_history": global_lb_history,
        "termination_reason": termination_reason,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _bb_read_checkpoint(
    path: Path, fingerprint: str, *, candidate_count: int,
    physical_fingerprint: str,
) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != BB_CHECKPOINT_VERSION or data.get(
            "constraint_schema_version") != BB_CONSTRAINT_SCHEMA_VERSION:
        raise ValueError(
            "CBS checkpoint schema is not compatible with required/forbidden "
            "branch constraints; old v2 checkpoints are not restored silently")
    if data.get("candidate_fingerprint") != fingerprint:
        raise ValueError(
            "CBS checkpoint candidate fingerprint does not match the immutable "
            "candidate universe")
    if int(data.get("candidate_count", -1)) != candidate_count:
        raise ValueError("CBS checkpoint candidate count does not match candidate universe")
    if data.get("physical_model_fingerprint") != physical_fingerprint:
        raise ValueError("CBS checkpoint physical model fingerprint does not match")
    return data


def _bb_restore_node(spec: dict[str, object]) -> BBNode:
    actions = frozenset(
        ForbiddenAction(
            str(item["train_id"]), int(item["route_index"]),
            int(item["arc_position"]), int(item["start_cell"]),
            int(item["wait_tick"]),
        ) for item in spec.get("forbidden_actions", [])
    )
    required = frozenset(
        RequiredAction(
            str(item["train_id"]), int(item["route_index"]),
            int(item["arc_position"]), int(item["start_cell"]),
            int(item["wait_tick"]),
        ) for item in spec.get("required_actions", [])
    )
    forbidden, required, infeasible, _ = normalize_bb_constraints(actions, required)
    if infeasible:
        raise ValueError(f"checkpoint node {spec.get('node_id')} has contradictory constraints")
    return BBNode(
        node_id=int(spec["node_id"]), parent_id=(
            None if spec.get("parent_id") is None else int(spec["parent_id"])),
        depth=int(spec["depth"]), forbidden_actions=forbidden,
        required_actions=required,
        inherited_lower_bound=float(spec.get("inherited_lower_bound", 0.0)),
        lower_bound=float(spec.get("lower_bound", -math.inf)),
        base_lower_bound=float(spec.get("base_lower_bound", -math.inf)),
        dual_lower_bound=float(spec.get("dual_lower_bound", -math.inf)),
        best_lambda={
            tuple(int(part) for part in str(key).split(":", 1)): float(value)
            for key, value in dict(spec.get("best_lambda", {})).items()
        },
        branch_conflict_type=str(spec.get("branch_conflict_type", "")),
        branch_conflict_arc=(
            None if spec.get("branch_conflict_arc") is None
            else int(spec["branch_conflict_arc"])),
        branch_conflict_time=(
            None if spec.get("branch_conflict_time") is None
            else float(spec["branch_conflict_time"])),
        dual_history=[dict(row) for row in spec.get("dual_history", [])],
        required_residual_movements=int(spec.get("required_residual_movements", 0)),
        labels_pruned_required_headway=int(
            spec.get("labels_pruned_required_headway", 0)),
        labels_pruned_required_opposing=int(
            spec.get("labels_pruned_required_opposing", 0)),
        labels_pruned_required_special=int(
            spec.get("labels_pruned_required_special", 0)),
        required_action_infeasibility=int(
            spec.get("required_action_infeasibility", 0)),
        representative_trajectory_signatures=[
            str(value) for value in spec.get("representative_trajectory_signatures", [])
        ],
    )


def _bb_gap(
    lower_bound: float, incumbent_ub: float,
) -> tuple[float | None, float | None]:
    if not math.isfinite(lower_bound) or not math.isfinite(incumbent_ub):
        return None, None
    absolute = max(0.0, incumbent_ub - lower_bound)
    # This is the conventional UB-normalised gap used by this project.
    relative_ub = absolute / max(abs(incumbent_ub), 1.0)
    relative_lb = absolute / max(abs(lower_bound), 1.0)
    return relative_ub, relative_lb


def _bb_open_global_lb(
    heap: Iterable[tuple[float, int, int, BBNode]], incumbent_ub: float,
) -> float | None:
    """Return the best-bound lower bound over open leaves only."""
    if heap:
        return float(min(item[0] for item in heap))
    return incumbent_ub if math.isfinite(incumbent_ub) else None


def _bb_missing_train(
    rows: list[dict[str, str]], trains: list[Train],
) -> str | None:
    feasible = {str(row.get("train_id")) for row in rows if row.get("feasible") == "1"}
    for train in trains:
        if train.train_id not in feasible:
            return train.train_id
    return None


def _bb_dp_cache_key(
    train_id: str, forbidden_actions: Iterable[ForbiddenAction],
    required_actions: Iterable[RequiredAction], *, candidate_fingerprint: str,
    physical_fingerprint: str,
) -> tuple[object, ...]:
    forbidden, required, infeasible, _ = normalize_bb_constraints(
        forbidden_actions, required_actions)
    # A node branch normally changes one train.  Projecting the canonical
    # domain to this train is what makes the cache genuinely incremental;
    # including unrelated trains here would turn every child into a miss.
    forbidden = frozenset(action for action in forbidden
                          if action.train_id == train_id)
    required = frozenset(action for action in required
                         if action.train_id == train_id)
    return (
        "lambda-zero-dp", candidate_fingerprint, physical_fingerprint, train_id,
        int(infeasible), tuple(sorted(required)), tuple(sorted(forbidden)),
    )


def _bb_get_lambda_zero_rows(
    *, executable: Path, dataset: Path, train: Train,
    candidates: dict[str, list[PathCandidate]], mow: list[dict[str, float | int]],
    arcs: dict[int, Arc], args: argparse.Namespace, output: Path,
    forbidden_actions: Iterable[ForbiddenAction],
    required_actions: Iterable[RequiredAction], candidate_fingerprint: str,
    physical_fingerprint: str, dp_cache: dict[tuple[object, ...], list[dict[str, str]]],
    cache_stats: dict[str, int],
) -> list[dict[str, str]]:
    key = _bb_dp_cache_key(
        train.train_id, forbidden_actions, required_actions,
        candidate_fingerprint=candidate_fingerprint,
        physical_fingerprint=physical_fingerprint,
    )
    cached = dp_cache.get(key)
    if cached is not None:
        cache_stats["dp_cache_hits"] = cache_stats.get("dp_cache_hits", 0) + 1
        return [dict(row) for row in cached]
    cache_stats["dp_cache_misses"] = cache_stats.get("dp_cache_misses", 0) + 1
    rows = run_cpp_batch(
        executable, dataset, [train], {train.train_id: candidates[train.train_id]},
        {}, mow, arcs, bin_minutes=args.bin_minutes,
        horizon_minutes=args.horizon, output_root=output, cfg=args,
        forbidden_actions=forbidden_actions, required_actions=required_actions,
    )
    dp_cache[key] = [dict(row) for row in rows]
    return rows


def _bb_min_physical_row(rows: Iterable[dict[str, str]]) -> dict[str, str] | None:
    feasible = [row for row in rows if row.get("feasible") == "1"]
    if not feasible:
        return None
    return min(feasible, key=lambda row: (
        float(row.get("physical_cost", "inf")),
        float(row.get("arrival_min") or "inf"),
        int(row.get("route_index", 0)),
    ))


def _bb_trajectory_signature(
    selected: dict[str, dict[str, str]],
) -> str:
    """Stable representative signature, independent of CSV column ordering."""
    payload = []
    for train_id in sorted(selected):
        row = selected[train_id]
        payload.append({
            "train_id": train_id,
            "route_index": int(row.get("route_index", 0)),
            "departure_min": row.get("departure_min", ""),
            "arrival_min": row.get("arrival_min", ""),
            "waits": row.get("waits", ""),
            "legs": row.get("legs", ""),
        })
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _bb_row_signature(train_id: str, row: dict[str, str]) -> str:
    return _bb_trajectory_signature({train_id: row})


def _bb_classify_deltas(
    delta_a: float, delta_b: float, tolerance: float = BB_TOLERANCE,
) -> str:
    positive_a = math.isinf(delta_a) or delta_a > tolerance
    positive_b = math.isinf(delta_b) or delta_b > tolerance
    if positive_a and positive_b:
        return "cardinal"
    if positive_a or positive_b:
        return "semi-cardinal"
    return "non-cardinal"


def _bb_certified_pair_delta(
    delta_a: float, delta_b: float,
) -> float:
    """Certified two-train surcharge implied by one exact conflict witness."""
    value = min(delta_a, delta_b)
    return max(0.0, value)


def _bb_row_in_domain(
    train_id: str, row: dict[str, str], *,
    forbidden_actions: Iterable[ForbiddenAction],
    required_actions: Iterable[RequiredAction],
    candidates: dict[str, list[PathCandidate]],
    time_step: float, wait_step: float,
) -> bool:
    """Check an alternate trajectory against the exact parent action domain."""
    try:
        actions = action_signatures_for_row(
            train_id, row, candidates, time_step=time_step, wait_step=wait_step)
    except (KeyError, RuntimeError, ValueError):
        return False
    action_keys = {action.forbidden for action in actions}
    forbidden = set(forbidden_actions)
    required = set(required_actions)
    if action_keys & forbidden:
        return False
    if any(action.train_id == train_id and action not in action_keys
           for action in required):
        return False
    route_index = int(row.get("route_index", -1))
    return all(action.route_index == route_index for action in required
               if action.train_id == train_id)


def _bb_eval_required_variant(
    node: BBNode, pivot: ActionSignature, other: ActionSignature, *,
    executable: Path, dataset: Path, trains: list[Train],
    candidates: dict[str, list[PathCandidate]],
    mow: list[dict[str, float | int]], arcs: dict[int, Arc],
    args: argparse.Namespace, output: Path, candidate_fingerprint: str,
    physical_fingerprint: str,
    dp_cache: dict[tuple[object, ...], list[dict[str, str]]],
    cache_stats: dict[str, int],
    variant_cache: dict[tuple[object, ...], float],
) -> tuple[float, bool]:
    """Evaluate a disjoint positive child using only affected train DPs."""
    temp_forbidden = set(node.forbidden_actions)
    temp_forbidden.add(other.forbidden)
    temp_required = set(node.required_actions)
    temp_required.add(pivot.required)
    forbidden, required, infeasible, _ = normalize_bb_constraints(
        temp_forbidden, temp_required)
    key = ("positive-branch", candidate_fingerprint, physical_fingerprint,
           bb_constraint_key(forbidden, required,
                             candidate_fingerprint=candidate_fingerprint))
    cached = variant_cache.get(key)
    if cached is not None:
        cache_stats["strong_branching_cache_hits"] = cache_stats.get(
            "strong_branching_cache_hits", 0) + 1
        return cached, True
    cache_stats["strong_branching_cache_misses"] = cache_stats.get(
        "strong_branching_cache_misses", 0) + 1
    if infeasible:
        variant_cache[key] = math.inf
        return math.inf, False
    train_by_id = {train.train_id: train for train in trains}
    total_delta = 0.0
    for train_id in sorted({pivot.train_id, other.train_id}):
        rows = _bb_get_lambda_zero_rows(
            executable=executable, dataset=dataset, train=train_by_id[train_id],
            candidates=candidates, mow=mow, arcs=arcs, args=args, output=output,
            forbidden_actions=forbidden, required_actions=required,
            candidate_fingerprint=candidate_fingerprint,
            physical_fingerprint=physical_fingerprint,
            dp_cache=dp_cache, cache_stats=cache_stats)
        selected = _bb_min_physical_row(rows)
        if selected is None:
            total_delta = math.inf
            break
        old = float(node.base_paths[train_id].get("physical_cost", "inf"))
        total_delta += max(0.0, float(selected.get("physical_cost", "inf")) - old)
    variant_cache[key] = total_delta
    return total_delta, False


def _bb_eval_forbidden_action(
    node: BBNode, action: ForbiddenAction, *, executable: Path, dataset: Path,
    trains: list[Train], candidates: dict[str, list[PathCandidate]],
    mow: list[dict[str, float | int]], arcs: dict[int, Arc], args: argparse.Namespace,
    output: Path, candidate_fingerprint: str, physical_fingerprint: str,
    dp_cache: dict[tuple[object, ...], list[dict[str, str]]],
    strong_cache: dict[tuple[object, ...], tuple[float, dict[str, str] | None]],
    cache_stats: dict[str, int],
) -> tuple[float, dict[str, str] | None, bool]:
    """Return exact lambda=0 cost increase and an alternate row, if any."""
    temp_forbidden = set(node.forbidden_actions)
    temp_forbidden.add(action)
    forbidden, required, infeasible, _ = normalize_bb_constraints(
        temp_forbidden, node.required_actions)
    key = bb_constraint_key(
        forbidden, required, candidate_fingerprint=candidate_fingerprint)
    cache_key = ("strong-branch", action.train_id, key)
    cached = strong_cache.get(cache_key)
    if cached is not None:
        cache_stats["strong_branching_cache_hits"] = cache_stats.get(
            "strong_branching_cache_hits", 0) + 1
        return cached[0], (dict(cached[1]) if cached[1] is not None else None), True
    cache_stats["strong_branching_cache_misses"] = cache_stats.get(
        "strong_branching_cache_misses", 0) + 1
    if infeasible:
        value, selected = math.inf, None
    else:
        train = next(train for train in trains if train.train_id == action.train_id)
        rows = _bb_get_lambda_zero_rows(
            executable=executable, dataset=dataset, train=train, candidates=candidates,
            mow=mow, arcs=arcs, args=args, output=output,
            forbidden_actions=forbidden, required_actions=required,
            candidate_fingerprint=candidate_fingerprint,
            physical_fingerprint=physical_fingerprint,
            dp_cache=dp_cache, cache_stats=cache_stats,
        )
        selected = _bb_min_physical_row(rows)
        if selected is None:
            value = math.inf
        else:
            old = float(node.base_paths[action.train_id]["physical_cost"])
            value = float(selected.get("physical_cost", "inf")) - old
            if value < 0.0 and value > -BB_TOLERANCE:
                value = 0.0
            value = max(0.0, value)
    strong_cache[cache_key] = (value, dict(selected) if selected is not None else None)
    return value, (dict(selected) if selected is not None else None), False


def _bb_matching(
    train_ids: Iterable[str], edges: dict[tuple[str, str], float],
) -> tuple[float, list[tuple[str, str, float]], int]:
    """Exact deterministic maximum-weight matching by component subset DP."""
    ids = sorted(set(train_ids))
    adjacency: dict[str, set[str]] = defaultdict(set)
    for (left, right), weight in edges.items():
        if weight < -BB_TOLERANCE or not math.isfinite(weight):
            if weight != math.inf:
                raise ValueError("matching accepts only finite nonnegative certified weights")
        adjacency[left].add(right)
        adjacency[right].add(left)
    components: list[list[str]] = []
    seen: set[str] = set()
    for train_id in ids:
        if train_id in seen or not adjacency.get(train_id):
            continue
        stack = [train_id]
        component: list[str] = []
        seen.add(train_id)
        while stack:
            current = stack.pop()
            component.append(current)
            for other in sorted(adjacency[current], reverse=True):
                if other not in seen:
                    seen.add(other)
                    stack.append(other)
        components.append(sorted(component))

    total = 0.0
    selected: list[tuple[str, str, float]] = []
    states = 0
    for component in components:
        index = {train_id: i for i, train_id in enumerate(component)}
        local_edges = {
            (min(left, right), max(left, right)): weight
            for (left, right), weight in edges.items()
            if left in index and right in index
        }
        memo: dict[int, tuple[float, tuple[tuple[str, str], ...]]] = {}

        def solve(mask: int) -> tuple[float, tuple[tuple[str, str], ...]]:
            nonlocal states
            states += 1
            if mask == 0:
                return 0.0, ()
            if mask in memo:
                return memo[mask]
            lowest = (mask & -mask).bit_length() - 1
            rest = mask & ~(1 << lowest)
            best_value, best_pairs = solve(rest)
            left = component[lowest]
            for j in range(lowest + 1, len(component)):
                if not (rest & (1 << j)):
                    continue
                right = component[j]
                weight = local_edges.get((min(left, right), max(left, right)))
                if weight is None:
                    continue
                sub_value, sub_pairs = solve(rest & ~(1 << j))
                candidate_value = sub_value + weight
                candidate_pairs = tuple(sorted(sub_pairs + ((min(left, right), max(left, right)),)))
                if (candidate_value > best_value + BB_TOLERANCE or
                        (abs(candidate_value - best_value) <= BB_TOLERANCE and
                         candidate_pairs < best_pairs)):
                    best_value, best_pairs = candidate_value, candidate_pairs
            memo[mask] = (best_value, best_pairs)
            return memo[mask]

        value, pairs = solve((1 << len(component)) - 1)
        total += value
        selected.extend((left, right, local_edges[(left, right)]) for left, right in pairs)
    selected.sort()
    return total, selected, states


def _bb_exact_pair_global_lb(
    node: BBNode, pair: tuple[str, str], *, executable: Path, dataset: Path,
    trains: list[Train], candidates: dict[str, list[PathCandidate]],
    mow: list[dict[str, float | int]], arcs: dict[int, Arc], args: argparse.Namespace,
    output: Path, candidate_fingerprint: str, physical_fingerprint: str,
    dp_cache: dict[tuple[object, ...], list[dict[str, str]]],
    pair_cache: dict[tuple[object, ...], tuple[float, bool]],
    cache_stats: dict[str, int],
) -> tuple[float, bool]:
    """Solve a projected two-train CBS subproblem exactly when possible.

    Each local leaf is bounded by the sum of the two exact lambda=0 DP minima.
    Standard exact-action splitting is complete for the two-train physical
    conflict detector.  If the local time limit expires, the minimum lower
    bound among its remaining open leaves is returned; the incumbent pair cost
    is used only to prune, never as a lower bound.
    """
    train_ids = tuple(sorted(pair))
    projected_forbidden = frozenset(
        action for action in node.forbidden_actions
        if action.train_id in train_ids)
    projected_required = frozenset(
        action for action in node.required_actions
        if action.train_id in train_ids)
    projected_forbidden, projected_required, infeasible, _ = (
        normalize_bb_constraints(projected_forbidden, projected_required))
    cache_key = (
        "pair-exact", candidate_fingerprint, physical_fingerprint, train_ids,
        tuple(sorted(projected_required)), tuple(sorted(projected_forbidden)),
        float(getattr(args, "time_step", DEFAULT_TIME_STEP)),
        float(getattr(args, "wait_step", DEFAULT_WAIT_STEP)),
        float(getattr(args, "bin_minutes", DEFAULT_BIN_MINUTES)),
        float(getattr(args, "horizon", DEFAULT_HORIZON_MINUTES)),
        float(getattr(
            args, "bb_pairwise_time_limit_sec", DEFAULT_BB_PAIRWISE_TIME_LIMIT_SEC)),
    )
    cached = pair_cache.get(cache_key)
    if cached is not None:
        cache_stats["pair_exact_cache_hits"] = cache_stats.get(
            "pair_exact_cache_hits", 0) + 1
        return cached
    cache_stats["pair_exact_cache_misses"] = cache_stats.get(
        "pair_exact_cache_misses", 0) + 1
    cache_stats["pair_subproblems_solved"] = cache_stats.get(
        "pair_subproblems_solved", 0) + 1
    if infeasible:
        pair_cache[cache_key] = (math.inf, True)
        return math.inf, True

    train_by_id = {train.train_id: train for train in trains}
    pair_trains = [train_by_id[train_id] for train_id in train_ids]
    started = time.monotonic()
    time_limit = max(0.0, float(getattr(
        args, "bb_pairwise_time_limit_sec", DEFAULT_BB_PAIRWISE_TIME_LIMIT_SEC)))
    local_counter = 0
    local_heap: list[tuple[float, int, int, frozenset[ForbiddenAction],
                           frozenset[RequiredAction],
                           dict[str, dict[str, str]]]] = []
    local_seen: set[tuple[object, ...]] = set()

    def solve_projected_domain(
        forbidden: frozenset[ForbiddenAction],
        required: frozenset[RequiredAction],
    ) -> tuple[float, dict[str, dict[str, str]] | None]:
        selected: dict[str, dict[str, str]] = {}
        total = 0.0
        for train in pair_trains:
            rows = _bb_get_lambda_zero_rows(
                executable=executable, dataset=dataset, train=train,
                candidates=candidates, mow=mow, arcs=arcs, args=args, output=output,
                forbidden_actions=forbidden, required_actions=required,
                candidate_fingerprint=candidate_fingerprint,
                physical_fingerprint=physical_fingerprint,
                dp_cache=dp_cache, cache_stats=cache_stats)
            selected_row = _bb_min_physical_row(rows)
            if selected_row is None:
                return math.inf, None
            selected[train.train_id] = selected_row
            total += float(selected_row.get("physical_cost", "inf"))
        return total, selected

    root_lb, root_paths = solve_projected_domain(
        projected_forbidden, projected_required)
    if root_paths is None:
        pair_cache[cache_key] = (math.inf, True)
        return math.inf, True
    root_key = bb_constraint_key(
        projected_forbidden, projected_required,
        candidate_fingerprint=candidate_fingerprint)
    local_seen.add(root_key)
    heapq.heappush(local_heap, (
        root_lb, 0, local_counter, projected_forbidden, projected_required,
        root_paths))
    best_feasible = math.inf
    local_expanded = 0
    timed_out = False
    while local_heap:
        if local_expanded > 0 and time.monotonic() - started >= time_limit:
            timed_out = True
            break
        lower_bound, depth, _, forbidden, required, selected = heapq.heappop(local_heap)
        if lower_bound >= best_feasible - BB_TOLERANCE:
            continue
        local_expanded += 1
        conflicts = all_physical_conflicts(
            selected, pair_trains, candidates,
            safety_headway_minutes=args.safety_headway_minutes,
            time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
            wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
        )
        if not conflicts:
            best_feasible = min(best_feasible, lower_bound)
            continue
        conflict = conflicts[0]
        for action in (conflict.action_a.forbidden, conflict.action_b.forbidden):
            child_forbidden, child_required, child_infeasible, _ = (
                normalize_bb_constraints(set(forbidden) | {action}, required))
            if child_infeasible:
                continue
            child_key = bb_constraint_key(
                child_forbidden, child_required,
                candidate_fingerprint=candidate_fingerprint)
            if child_key in local_seen:
                continue
            local_seen.add(child_key)
            child_lb, child_selected = solve_projected_domain(
                child_forbidden, child_required)
            if child_selected is None:
                continue
            if child_lb >= best_feasible - BB_TOLERANCE:
                continue
            local_counter += 1
            heapq.heappush(local_heap, (
                child_lb, depth + 1, local_counter, child_forbidden,
                child_required, child_selected))
    cache_stats["pair_exact_nodes_expanded"] = cache_stats.get(
        "pair_exact_nodes_expanded", 0) + local_expanded
    if timed_out:
        cache_stats["pair_exact_timeouts"] = cache_stats.get(
            "pair_exact_timeouts", 0) + 1
        if local_heap:
            result = min(item[0] for item in local_heap)
        else:
            # This can only occur when all remaining leaves were certified by
            # the incumbent pair cost; in that case the optimum is exact.
            result = best_feasible
        exact = not local_heap
    else:
        result = best_feasible if not local_heap else min(
            item[0] for item in local_heap)
        exact = not local_heap
    if not math.isfinite(result) and not exact:
        # A non-exact search with open nodes always has a finite DP lower bound
        # unless the projected domain is already infeasible.
        result = min(item[0] for item in local_heap)
    pair_cache[cache_key] = (result, exact)
    return result, exact


def _bb_pairwise_bound(
    node: BBNode, conflicts: list[PhysicalConflict], *, executable: Path,
    dataset: Path, trains: list[Train], candidates: dict[str, list[PathCandidate]],
    mow: list[dict[str, float | int]], arcs: dict[int, Arc], args: argparse.Namespace,
    output: Path, candidate_fingerprint: str, physical_fingerprint: str,
    dp_cache: dict[tuple[object, ...], list[dict[str, str]]],
    strong_cache: dict[tuple[object, ...], tuple[float, dict[str, str] | None]],
    pair_cache: dict[tuple[object, ...], tuple[float, bool]],
    cache_stats: dict[str, int],
) -> tuple[float, list[tuple[str, str, float]], bool]:
    """Compute a certified matching bound from projected two-train searches."""
    if not getattr(args, "bb_enable_pairwise_bound", False):
        return node.base_lower_bound, [], False
    max_depth = getattr(args, "bb_pairwise_node_depth", DEFAULT_BB_PAIRWISE_NODE_DEPTH)
    if node.depth > max_depth and node.depth != 0:
        return node.base_lower_bound, [], False
    pair_conflicts: dict[tuple[str, str], list[PhysicalConflict]] = defaultdict(list)
    for conflict in conflicts:
        key = tuple(sorted((conflict.train_a, conflict.train_b)))
        pair_conflicts[key].append(conflict)
    if not pair_conflicts:
        return node.base_lower_bound, [], False
    weights: dict[tuple[str, str], float] = {}
    pair_limit = max(0, int(getattr(
        args, "bb_pairwise_max_pairs", DEFAULT_BB_PAIRWISE_MAX_PAIRS)))
    edge_exact: list[bool] = []
    train_by_id = {train.train_id: train for train in trains}
    for pair in sorted(pair_conflicts)[:pair_limit]:
        pair_lb, exact = _bb_exact_pair_global_lb(
            node, pair, executable=executable, dataset=dataset, trains=trains,
            candidates=candidates, mow=mow, arcs=arcs, args=args, output=output,
            candidate_fingerprint=candidate_fingerprint,
            physical_fingerprint=physical_fingerprint, dp_cache=dp_cache,
            pair_cache=pair_cache, cache_stats=cache_stats)
        independent = sum(
            float(node.base_paths[train_id].get("physical_cost", "inf"))
            for train_id in pair)
        pair_delta = max(0.0, pair_lb - independent)
        edge_exact.append(exact)
        if math.isfinite(pair_delta) and pair_delta > BB_TOLERANCE:
            weights[pair] = pair_delta
        elif pair_delta == math.inf:
            weights[pair] = pair_delta
    matching_weight, selected, _ = _bb_matching(
        [train.train_id for train in trains], weights)
    return node.base_lower_bound + matching_weight, selected, bool(edge_exact) and all(edge_exact)


def _bb_physical_cost(selected: dict[str, dict[str, str]]) -> float:
    return sum(float(row.get("physical_cost", 0.0) or 0.0)
               for row in selected.values())


def _bb_recompute_physical_cost(
    selected: dict[str, dict[str, str]], trains: list[Train],
    candidates: dict[str, list[PathCandidate]], arcs: dict[int, Arc],
    args: argparse.Namespace,
) -> float:
    """Recompute the incumbent objective from the actual route/timing fields."""
    train_by_id = {train.train_id: train for train in trains}
    total = 0.0
    for train_id, train in train_by_id.items():
        row = selected.get(train_id)
        if row is None:
            raise ValueError(f"incumbent is missing train {train_id}")
        candidate = _candidate_for_row(train_id, row, candidates)
        legs = parse_legs(row.get("legs", ""))
        waits = parse_waits(row.get("waits", ""))
        if len(legs) != len(candidate.arc_ids) or len(waits) != len(legs):
            raise ValueError(f"incumbent trajectory shape mismatch for {train_id}")
        departure = float(row.get("departure_min") or train.entry_min)
        running = 0.0
        waiting = 0.0
        previous = departure
        for position, (arc_id, start, exit_) in enumerate(legs):
            if abs(start - previous) > 1e-6:
                raise ValueError(f"incumbent discontinuity for {train_id}")
            running += traversal_minutes(
                arcs[int(arc_id)], train.smult, candidate.ab_flags[position])
            if candidate.track_ids[position] == "S":
                waiting += max(0.0, float(waits[position]))
            previous = float(exit_)
        arrival = float(legs[-1][2]) if legs else departure
        if train.terminal_want is None:
            early = late = 0.0
        else:
            early = max(0.0, train.terminal_want - arrival)
            late = max(0.0, arrival - train.terminal_want)
        schedule = (
            args.early_arrival_cost_per_min * early +
            args.late_arrival_cost_per_min * late)
        total += (
            args.origin_wait_cost_per_min * (departure - train.entry_min) +
            args.running_cost_per_min * running +
            args.siding_wait_cost_per_min * waiting + schedule)
    return total


def _fixed_headway_rows(
    selected: dict[str, dict[str, str]], trains: list[Train],
    candidates: dict[str, list[PathCandidate]],
    *, safety_headway_minutes: float,
) -> list[dict[str, object]]:
    """Convert accepted DP trajectories to C++ residual-headway records."""
    rows: list[dict[str, object]] = []
    for train_id in sorted(selected):
        candidate = _candidate_for_row(train_id, selected[train_id], candidates)
        for position, (arc_id, start, exit_) in enumerate(
                parse_legs(selected[train_id].get("legs", ""))):
            rows.append({
                "arc_id": int(arc_id),
                "direction": "AB" if candidate.ab_flags[position] else "BA",
                "entry": float(start), "exit": float(exit_),
                "protected_end": float(exit_) + max(0.0, safety_headway_minutes),
                "train_id": train_id,
                "track_type": candidate.track_ids[position],
            })
    return rows


def _bootstrap_conflict_priority(conflict_type: str) -> int:
    """Return the deterministic bootstrap repair priority."""
    return {
        "opposing-protected-overlap": 1,
        "illegal-main-track-overtake": 2,
        "same-direction-main-headway": 3,
        "same-direction-special-overlap": 4,
        "siding-occupancy-conflict": 5,
    }.get(str(conflict_type), 6)


def _bootstrap_conflict_overlap(
    conflict: PhysicalConflict, safety_headway_minutes: float,
) -> float:
    """Measure the protected temporal overlap for deterministic tie-breaking."""
    left = conflict.action_a
    right = conflict.action_b
    left_end = float(left.exit_min) + max(0.0, safety_headway_minutes)
    right_end = float(right.exit_min) + max(0.0, safety_headway_minutes)
    return max(0.0, min(left_end, right_end) - max(
        float(left.start_min), float(right.start_min)))


def _bootstrap_conflict_counts(
    selected: dict[str, dict[str, str]], trains: list[Train],
    candidates: dict[str, list[PathCandidate]], *,
    safety_headway_minutes: float, time_step: float, wait_step: float,
) -> dict[str, int]:
    conflicts = all_physical_conflicts(
        selected, trains, candidates,
        safety_headway_minutes=safety_headway_minutes,
        time_step=time_step, wait_step=wait_step,
    )
    counts = defaultdict(int)
    for conflict in conflicts:
        counts[conflict.conflict_type] += 1
    return {
        "total": len(conflicts),
        "headway": sum(value for key, value in counts.items() if "headway" in key),
        "opposing": sum(value for key, value in counts.items() if key.startswith("opposing")),
        "same_direction": sum(
            value for key, value in counts.items()
            if key.startswith("same-direction") or key == "illegal-main-track-overtake"),
        "illegal_overtakes": counts["illegal-main-track-overtake"],
        "siding": sum(value for key, value in counts.items() if "siding" in key),
        "MOW": 0,
    }


def _bootstrap_partial_objective(
    selected: dict[str, dict[str, str]], trains: list[Train],
) -> float:
    """Return the objective contribution of the trains currently inserted."""
    train_by_id = {train.train_id: train for train in trains}
    total = 0.0
    for train_id, row in selected.items():
        train = train_by_id[train_id]
        if train.terminal_want is None:
            continue
        legs = parse_legs(row.get("legs", ""))
        if legs:
            total += abs(float(legs[-1][2]) - float(train.terminal_want))
    return total


def _bootstrap_pair_signature(
    selected: dict[str, dict[str, str]], pair: tuple[str, str],
    trains: list[Train], candidates: dict[str, list[PathCandidate]],
) -> tuple[tuple[object, ...], ...]:
    """Encode route, track, direction, and interval order for a pair."""
    if any(train_id not in selected for train_id in pair):
        return ()
    train_by_id = {train.train_id: train for train in trains}
    profiles = {
        train_id: _profile_for_row(train_by_id[train_id], selected[train_id], candidates)
        for train_id in pair
    }
    left, right = profiles[pair[0]], profiles[pair[1]]
    signature: list[tuple[object, ...]] = []
    for arc_id in sorted(set(left.get("all", {})) & set(right.get("all", {}))):
        for left_leg in left["all"][arc_id]:
            for right_leg in right["all"][arc_id]:
                signature.append((
                    int(arc_id), int(left_leg["index"]), int(right_leg["index"]),
                    bool(left_leg["direction"]), bool(right_leg["direction"]),
                    str(left_leg.get("track_type", "")),
                    str(right_leg.get("track_type", "")),
                    round(float(left_leg["entry"]), 6),
                    round(float(left_leg["exit"]), 6),
                    round(float(right_leg["entry"]), 6),
                    round(float(right_leg["exit"]), 6),
                ))
    return tuple(signature)


def _bootstrap_pair_resources(
    selected: dict[str, dict[str, str]], pair: tuple[str, str],
    trains: list[Train], candidates: dict[str, list[PathCandidate]],
) -> list[int]:
    if any(train_id not in selected for train_id in pair):
        return []
    train_by_id = {train.train_id: train for train in trains}
    left = _profile_for_row(train_by_id[pair[0]], selected[pair[0]], candidates)
    right = _profile_for_row(train_by_id[pair[1]], selected[pair[1]], candidates)
    return sorted(set(left.get("main", {})) & set(right.get("main", {})))


def _write_bootstrap_audits(
    output: Path, history: list[dict[str, object]],
    pair_calls: list[dict[str, object]],
) -> None:
    """Persist detailed recovery traces for every bootstrap attempt."""
    history_fields = [
        "step", "train_inserted", "partial_train_count", "objective",
        "total_conflicts", "headway_conflicts", "opposing_conflicts",
        "same_direction_conflicts", "illegal_overtakes", "siding_conflicts",
        "repair_triggered", "repair_pair_train_1", "repair_pair_train_2",
        "pair_deep_attempted", "pair_deep_success", "route_changed",
        "timing_changed", "meet_pass_changed", "objective_after_repair",
        "conflicts_after_repair", "wall_time_sec",
        "adaptive_probe_round", "adaptive_probed_train_count",
    ]
    with (output / "bootstrap_history.csv").open(
            "w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=history_fields)
        writer.writeheader()
        writer.writerows(history)

    pair_fields = [
        "step", "failed_train", "pair_train_1", "pair_train_2",
        "conflict_type", "failed_resource", "conflict_overlap_minutes",
        "candidate_route", "candidate_entry_time", "fixed_train_count",
        "pair_deep_attempted", "pair_deep_success", "pair_deep_status",
        "pair_deep_value", "dp_calls", "combination_cap", "route_changed",
        "timing_changed", "meet_pass_changed", "common_main_resources",
        "baseline_pair_signature", "repaired_pair_signature",
        "local_conflict_count", "reason", "wall_time_sec",
    ]
    with (output / "pair_deep_bootstrap_calls.csv").open(
            "w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=pair_fields)
        writer.writeheader()
        writer.writerows(pair_calls)


def recover_feasible_with_dp(
    trains: list[Train], candidates: dict[str, list[PathCandidate]],
    executable: Path, dataset: Path, arcs: dict[int, Arc],
    mow: list[dict[str, float | int]], args: argparse.Namespace,
    output: Path, forbidden_actions: frozenset[ForbiddenAction] = frozenset(),
    initial_selected: dict[str, dict[str, str]] | None = None,
) -> tuple[dict[str, dict[str, str]] | None, dict[str, object]]:
    """Greedy primal recovery with bootstrap-only existing pair-deep repair.

    Each accepted train becomes a fixed residual movement for the next DP
    call.  If the next train has no compatible insertion, the existing
    ``gap30_campaign._evaluate_joint`` pair evaluator is called on the new
    train and one deterministic blocking train while all other inserted
    trains remain fixed.  This routine is heuristic UB construction only; it
    never supplies a subgradient or raises a dual bound.
    """
    output.mkdir(parents=True, exist_ok=True)
    selected: dict[str, dict[str, str]] = {
        train_id: dict(row) for train_id, row in (initial_selected or {}).items()
    }
    train_by_id = {train.train_id: train for train in trains}
    order_policy = str(getattr(args, "bootstrap_order", "earliest"))
    if order_policy == "most_constrained":
        ordered = sorted(trains, key=lambda train: (
            -sum(track == "S" for candidate in candidates[train.train_id]
                 for track in candidate.track_ids) / max(1, len(candidates[train.train_id])),
            train.entry_min, train.origin, -train.smult, train.train_id))
    elif order_policy == "opposing_bottleneck":
        # Deterministic direction alternation prioritizes an opposing train
        # near the same bottleneck entry window without hand-picking IDs.
        by_entry = sorted(trains, key=lambda train: (
            train.entry_min, train.origin, -train.smult, train.train_id))
        eb = [train for train in by_entry if train.origin < train.destination]
        wb = [train for train in by_entry if train.origin > train.destination]
        ordered = []
        while eb or wb:
            if eb:
                ordered.append(eb.pop(0))
            if wb:
                ordered.append(wb.pop(0))
    elif order_policy == "directional_batch":
        # Alternative deterministic order for the recovery audit: commit
        # the same-direction stream first, then insert the opposing stream.
        # This changes only primal insertion order; physical validation and
        # every residual headway constraint remain unchanged.
        ordered = sorted(trains, key=lambda train: (
            train.origin > train.destination, train.entry_min,
            train.origin, -train.smult, train.train_id))
    else:
        # ``earliest`` and ``earliest_conflict`` both retain the existing
        # deterministic baseline order; the latter is a named audit policy
        # so future conflict-priority ordering can be added without changing
        # the default benchmark silently.
        ordered = sorted(trains, key=lambda train: (
            train.entry_min, train.origin, -train.smult, train.train_id))
    ordered = [train for train in ordered if train.train_id not in selected]
    history: list[dict[str, object]] = []
    pair_calls: list[dict[str, object]] = []
    pair_deep_attempts = 0
    pair_deep_successes = 0
    retimed_train_ids: set[str] = set()
    route_change_count = 0
    timing_change_count = 0
    meet_pass_change_count = 0
    bootstrap_start = time.monotonic()
    bootstrap_deadline = bootstrap_start + float(getattr(
        args, "bootstrap_time_limit_sec", math.inf))
    # run_cpp_batch reads this shared deadline and applies it to every nested
    # C++ call, including conflict probes and pair/triple recovery scans.
    args._bootstrap_deadline = bootstrap_deadline

    def deadline_exceeded() -> bool:
        return time.monotonic() >= bootstrap_deadline

    def time_limit_result(train_id: str) -> dict[str, object]:
        return {
            "status": "TIME_LIMIT", "failed_train": train_id,
            "failed_insertion_index": next(
                (index for index, item in enumerate(ordered, start=1)
                 if item.train_id == train_id), len(ordered)),
            "reason": "bounded bootstrap wall-clock deadline exceeded",
            "bootstrap_history": history, "pair_deep_calls": pair_calls,
            "pair_deep_attempts": pair_deep_attempts,
            "pair_deep_successes": pair_deep_successes,
            "retimed_train_count": len(retimed_train_ids),
            "route_change_count": route_change_count,
            "timing_change_count": timing_change_count,
            "meet_pass_change_count": meet_pass_change_count,
        }

    def append_history(
        step: int, train: Train, before: dict[str, dict[str, str]],
        after: dict[str, dict[str, str]], *, repair_triggered: bool = False,
        pair: tuple[str, str] = (), pair_attempted: bool = False,
        pair_success: bool = False, route_changed: bool = False,
        timing_changed: bool = False, meet_pass_changed: bool = False,
    ) -> None:
        counts = _bootstrap_conflict_counts(
            after, trains, candidates,
            safety_headway_minutes=args.safety_headway_minutes,
            time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
            wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
        )
        history.append({
            "step": step, "train_inserted": train.train_id,
            "partial_train_count": len(after),
            "objective": _bootstrap_partial_objective(after, trains),
            "total_conflicts": counts["total"],
            "headway_conflicts": counts["headway"],
            "opposing_conflicts": counts["opposing"],
            "same_direction_conflicts": counts["same_direction"],
            "illegal_overtakes": counts["illegal_overtakes"],
            "siding_conflicts": counts["siding"],
            "repair_triggered": int(repair_triggered),
            "repair_pair_train_1": pair[0] if len(pair) > 0 else "",
            "repair_pair_train_2": pair[1] if len(pair) > 1 else "",
            "pair_deep_attempted": int(pair_attempted),
            "pair_deep_success": int(pair_success),
            "route_changed": int(route_changed),
            "timing_changed": int(timing_changed),
            "meet_pass_changed": int(meet_pass_changed),
            "objective_after_repair": _bootstrap_partial_objective(after, trains),
            "conflicts_after_repair": counts["total"],
            "wall_time_sec": time.monotonic() - bootstrap_start,
        })

    # A warm start is allowed only as a current-pipeline continuation.  It is
    # checked before any new train is inserted and is never a historical
    # schedule import.  This keeps the recovery audit honest while avoiding a
    # second full 16-train bootstrap when the already-generated current
    # directional prefix is extended to 32 trains.
    if selected:
        unknown = sorted(set(selected) - set(train_by_id))
        if unknown:
            _write_bootstrap_audits(output, history, pair_calls)
            return None, {
                "status": "FAIL", "reason": f"warm start has unknown trains: {unknown}",
                "bootstrap_history": history, "pair_deep_calls": pair_calls,
            }
        warm_validation = validate_physical_schedule(
            selected, trains, candidates, arcs, mow,
            horizon_minutes=args.horizon,
            safety_headway_minutes=args.safety_headway_minutes,
            time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
            wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
            require_complete=False,
        )
        if warm_validation["status"] != "PASS":
            _write_bootstrap_audits(output, history, pair_calls)
            return None, {
                **warm_validation, "reason": "invalid current-pipeline warm start",
                "bootstrap_history": history, "pair_deep_calls": pair_calls,
            }

    def local_row_signatures(
        state: dict[str, dict[str, str]], ids: tuple[str, ...]
    ) -> dict[str, tuple[str, str, str, str]]:
        return {
            train_id: (
                str(state[train_id].get("route_index", "")),
                str(state[train_id].get("departure_min", "")),
                str(state[train_id].get("waits", "")),
                str(state[train_id].get("legs", "")),
            )
            for train_id in ids if train_id in state
        }

    for step, train in enumerate(ordered, start=1):
        if deadline_exceeded():
            _write_bootstrap_audits(output, history, pair_calls)
            return None, time_limit_result(train.train_id)
        before = {train_id: dict(row) for train_id, row in selected.items()}
        fixed = _fixed_headway_rows(
            selected, trains, candidates,
            safety_headway_minutes=args.safety_headway_minutes)
        try:
            rows = run_cpp_batch(
                executable, dataset, [train], {train.train_id: candidates[train.train_id]},
                {}, mow, arcs, bin_minutes=args.bin_minutes,
                horizon_minutes=args.horizon, output_root=output, cfg=args,
                forbidden_actions=forbidden_actions, fixed_headway_rows=fixed,
                enable_headway_residual=True,
            )
        except RuntimeError as exc:
            if deadline_exceeded():
                _write_bootstrap_audits(output, history, pair_calls)
                return None, {**time_limit_result(train.train_id),
                              "reason": str(exc)}
            raise
        options = [row for row in rows if row.get("feasible") == "1"]
        options.sort(key=lambda row: (
            float(row.get("physical_cost", "inf")),
            float(row.get("arrival_min") or "inf"),
            int(row.get("route_index", 0)),
        ))
        chosen: dict[str, str] | None = None
        for option in options:
            profile = _profile_for_row(train, option, candidates)
            if any(_forbidden_main_track_overtake(
                    profile, _profile_for_row(
                        train_by_id[train_id], fixed_row, candidates))
                   for train_id, fixed_row in selected.items()):
                continue
            chosen = option
            break
        if chosen is None:
            # Re-run without residual rejection to obtain exact conflict
            # witnesses for the attempted insertion.  This is diagnostic
            # input to the existing pair evaluator, not a relaxed acceptance.
            conflict_probe_output = output / "bootstrap_conflict_probe"
            conflict_probe_output.mkdir(parents=True, exist_ok=True)
            if deadline_exceeded():
                _write_bootstrap_audits(output, history, pair_calls)
                return None, time_limit_result(train.train_id)
            try:
                unreserved_rows = run_cpp_batch(
                    executable, dataset, [train],
                    {train.train_id: candidates[train.train_id]}, {}, mow, arcs,
                    bin_minutes=args.bin_minutes, horizon_minutes=args.horizon,
                    output_root=conflict_probe_output, cfg=args,
                    forbidden_actions=forbidden_actions,
                    enable_headway_residual=False,
                )
            except RuntimeError as exc:
                if deadline_exceeded():
                    _write_bootstrap_audits(output, history, pair_calls)
                    return None, {**time_limit_result(train.train_id),
                                  "reason": str(exc)}
                raise
            unreserved_options = [
                row for row in unreserved_rows if row.get("feasible") == "1"
            ]
            # The C++ probe has already scanned the complete frozen route
            # universe.  Conflict-witness extraction is a recovery heuristic,
            # not a second exhaustive optimization problem; keep a
            # deterministic route-diverse sample so a late diagnosed copy
            # route remains visible without an O(131k * |selected|) Python
            # sweep at every blocked insertion.
            probe_cap = int(getattr(args, "bootstrap_probe_option_cap", 16384))
            if len(unreserved_options) > probe_cap:
                head = min(1024, max(1, probe_cap // 4))
                indices = set(range(head))
                remaining = max(1, probe_cap - len(indices))
                stride = max(1, (len(unreserved_options) - 1) // remaining)
                indices.update(range(0, len(unreserved_options), stride))
                indices.add(len(unreserved_options) - 1)
                unreserved_options = [
                    unreserved_options[index]
                    for index in sorted(indices)[:probe_cap]
                ]
            pair_representatives: dict[tuple[str, str], tuple[dict[str, str], PhysicalConflict]] = {}
            for probe_index, candidate_row in enumerate(unreserved_options):
                if probe_index % 128 == 0 and deadline_exceeded():
                    _write_bootstrap_audits(output, history, pair_calls)
                    return None, time_limit_result(train.train_id)
                partial = {**selected, train.train_id: candidate_row}
                conflicts = all_physical_conflicts(
                    partial, trains, candidates,
                    safety_headway_minutes=args.safety_headway_minutes,
                    time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
                    wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
                )
                for conflict in conflicts:
                    if train.train_id not in {conflict.train_a, conflict.train_b}:
                        continue
                    other = (conflict.train_b if conflict.train_a == train.train_id
                             else conflict.train_a)
                    pair = tuple(sorted((train.train_id, other)))
                    rank = (
                        _bootstrap_conflict_priority(conflict.conflict_type),
                        -_bootstrap_conflict_overlap(
                            conflict, args.safety_headway_minutes),
                        conflict.time_min, conflict.arc_id,
                        int(candidate_row.get("route_index", 0)),
                    )
                    previous = pair_representatives.get(pair)
                    if previous is None:
                        pair_representatives[pair] = (candidate_row, conflict)
                    else:
                        old_rank = (
                            _bootstrap_conflict_priority(previous[1].conflict_type),
                            -_bootstrap_conflict_overlap(
                                previous[1], args.safety_headway_minutes),
                            previous[1].time_min, previous[1].arc_id,
                            int(previous[0].get("route_index", 0)),
                        )
                        if rank < old_rank:
                            pair_representatives[pair] = (candidate_row, conflict)
            ordered_pairs = sorted(
                pair_representatives,
                key=lambda pair: (
                    _bootstrap_conflict_priority(
                        pair_representatives[pair][1].conflict_type),
                    -_bootstrap_conflict_overlap(
                        pair_representatives[pair][1], args.safety_headway_minutes),
                    pair_representatives[pair][1].time_min,
                    pair_representatives[pair][1].arc_id, pair,
                ),
            )
            pair_output = output / "pair_deep_bootstrap"
            pair_output.mkdir(parents=True, exist_ok=True)
            pair_repair_succeeded = False
            successful_pair: tuple[str, str] = ()
            baseline_row = (
                pair_representatives[ordered_pairs[0]][0]
                if ordered_pairs else None
            )
            for pair in ordered_pairs:
                if deadline_exceeded():
                    _write_bootstrap_audits(output, history, pair_calls)
                    return None, time_limit_result(train.train_id)
                candidate_row, conflict = pair_representatives[pair]
                pair_deep_attempts += 1
                pair_started = time.monotonic()
                pair_ids = tuple(pair)
                baseline = {**selected, train.train_id: candidate_row}
                before_signature = _bootstrap_pair_signature(
                    baseline, pair_ids, trains, candidates)
                call: dict[str, object] = {
                    "step": step, "failed_train": train.train_id,
                    "pair_train_1": pair_ids[0], "pair_train_2": pair_ids[1],
                    "conflict_type": conflict.conflict_type,
                    "failed_resource": conflict.arc_id,
                    "conflict_overlap_minutes": _bootstrap_conflict_overlap(
                        conflict, args.safety_headway_minutes),
                    "candidate_route": candidate_row.get("route_index", ""),
                    "candidate_entry_time": (
                        parse_legs(candidate_row.get("legs", ""))[0][1]
                        if parse_legs(candidate_row.get("legs", "")) else ""),
                    "fixed_train_count": len(
                        {train_id for train_id in selected if train_id not in pair_ids}),
                    "pair_deep_attempted": 1,
                    "pair_deep_success": 0,
                    "pair_deep_status": "NOT_RUN",
                    "pair_deep_value": math.inf,
                    "dp_calls": 0, "combination_cap": 100000,
                    "route_changed": 0, "timing_changed": 0,
                    "meet_pass_changed": 0,
                    "common_main_resources": json.dumps(
                        _bootstrap_pair_resources(
                            baseline, pair_ids, trains, candidates)),
                    "baseline_pair_signature": json.dumps(before_signature),
                    "repaired_pair_signature": "",
                    "local_conflict_count": "",
                    "reason": "",
                }
                try:
                    # Lazy import avoids a module cycle: gap30_campaign imports
                    # soft_lagrangian for its existing pair-deep machinery.
                    from gap30_campaign import _evaluate_joint

                    local, value, info = _evaluate_joint(
                        pair_ids, selected, dataset=dataset, trains=trains,
                        candidates=candidates, executable=executable, arcs=arcs,
                        mow=mow, cfg=args, output=pair_output,
                        headway=args.safety_headway_minutes, top_k=None,
                        combination_cap=100000,
                    )
                    local_validation = (
                        validate_physical_schedule(
                            local, trains, candidates, arcs, mow,
                            horizon_minutes=args.horizon,
                            safety_headway_minutes=args.safety_headway_minutes,
                            time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
                            wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
                            require_complete=False,
                        ) if local is not None else {
                            "status": "FAIL", "conflicts": [],
                            "conflict_count": 0,
                        }
                    )
                    local_ok = local is not None and local_validation["status"] == "PASS"
                    info_status = str(info.get("status", "UNKNOWN"))
                    call.update({
                        "pair_deep_status": info_status,
                        "pair_deep_value": value,
                        "dp_calls": info.get("dp_calls", 0),
                        "combination_cap": info.get("combination_cap", 100000),
                        "local_conflict_count": local_validation.get("conflict_count", 0),
                        "reason": "accepted partial repair" if local_ok else
                        "returned schedule failed local physical validation",
                    })
                    if local_ok:
                        after_signature = _bootstrap_pair_signature(
                            local, pair_ids, trains, candidates)
                        route_changed = any(
                            baseline.get(train_id, {}).get("route_index") !=
                            local.get(train_id, {}).get("route_index")
                            for train_id in pair_ids)
                        timing_changed = any(
                            (baseline.get(train_id, {}).get("legs") !=
                             local.get(train_id, {}).get("legs") or
                             baseline.get(train_id, {}).get("waits") !=
                             local.get(train_id, {}).get("waits") or
                             baseline.get(train_id, {}).get("departure_min") !=
                             local.get(train_id, {}).get("departure_min"))
                            for train_id in pair_ids)
                        meet_pass_changed = before_signature != after_signature
                        call.update({
                            "pair_deep_success": 1,
                            "route_changed": int(route_changed),
                            "timing_changed": int(timing_changed),
                            "meet_pass_changed": int(meet_pass_changed),
                            "repaired_pair_signature": json.dumps(after_signature),
                        })
                        selected = {train_id: dict(row) for train_id, row in local.items()}
                        pair_deep_successes += 1
                        successful_pair = pair_ids
                        pair_repair_succeeded = True
                        retimed_train_ids.update(
                            train_id for train_id in pair_ids
                            if timing_changed and train_id in before and
                            baseline.get(train_id, {}).get("legs") !=
                            local.get(train_id, {}).get("legs"))
                        route_change_count += int(route_changed)
                        timing_change_count += int(timing_changed)
                        meet_pass_change_count += int(meet_pass_changed)
                except Exception as exc:  # preserve deterministic next-pair fallback
                    call.update({
                        "pair_deep_status": "ERROR",
                        "reason": f"{type(exc).__name__}: {exc}",
                    })
                    if deadline_exceeded():
                        _write_bootstrap_audits(output, history, pair_calls)
                        return None, time_limit_result(train.train_id)
                call["wall_time_sec"] = time.monotonic() - pair_started
                pair_calls.append(call)
                if pair_repair_succeeded:
                    break
            if pair_repair_succeeded:
                append_history(
                    step, train, before, selected, repair_triggered=True,
                    pair=successful_pair, pair_attempted=True, pair_success=True,
                    route_changed=bool(pair_calls[-1]["route_changed"]),
                    timing_changed=bool(pair_calls[-1]["timing_changed"]),
                    meet_pass_changed=bool(pair_calls[-1]["meet_pass_changed"]),
                )
                continue

            # Bounded deterministic backtracking: release the failed train
            # together with the most recently committed one or two trains and
            # reuse the existing joint DP evaluator.  This is still primal
            # recovery; every accepted partial state is physically validated
            # before insertion continues.  The two-train attempt above is
            # kept separate so the audit can distinguish pair-deep from the
            # triple/backtracking fallback.
            recent_ids = [item.train_id for item in ordered[:step - 1]][-2:]
            local_attempts = [
                tuple([train.train_id, recent_ids[-1]])
                for _ in [0] if recent_ids
            ]
            if len(recent_ids) >= 2:
                local_attempts.append(tuple([train.train_id, *recent_ids[-2:]]))
            local_repair_succeeded = False
            for local_ids in local_attempts:
                if deadline_exceeded():
                    _write_bootstrap_audits(output, history, pair_calls)
                    return None, time_limit_result(train.train_id)
                if len(set(local_ids)) != len(local_ids):
                    continue
                try:
                    from gap30_campaign import _evaluate_joint
                    local, value, info = _evaluate_joint(
                        tuple(local_ids), selected, dataset=dataset, trains=trains,
                        candidates=candidates, executable=executable, arcs=arcs,
                        mow=mow, cfg=args, output=pair_output, headway=args.safety_headway_minutes,
                        top_k=20 if len(local_ids) > 2 else None,
                        combination_cap=20000 if len(local_ids) > 2 else 100000,
                    )
                    local_validation = (
                        validate_physical_schedule(
                            local, trains, candidates, arcs, mow,
                            horizon_minutes=args.horizon,
                            safety_headway_minutes=args.safety_headway_minutes,
                            time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
                            wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
                            require_complete=False,
                        ) if local is not None else {
                            "status": "FAIL", "conflicts": [], "conflict_count": 0,
                        }
                    )
                    if local is None or local_validation["status"] != "PASS":
                        continue
                    before_signature = local_row_signatures(selected, tuple(local_ids))
                    selected = {train_id: dict(row) for train_id, row in local.items()}
                    after_signature = local_row_signatures(selected, tuple(local_ids))
                    route_changed = any(
                        before_signature.get(train_id, ("",))[0] !=
                        after_signature.get(train_id, ("",))[0]
                        for train_id in before_signature)
                    timing_changed = any(
                        before_signature.get(train_id, ("", "", "",))[1:] !=
                        after_signature.get(train_id, ("", "", "",))[1:]
                        for train_id in before_signature)
                    pair_calls.append({
                        "step": step, "failed_train": train.train_id,
                        "pair_train_1": local_ids[0],
                        "pair_train_2": local_ids[1] if len(local_ids) > 1 else "",
                        "conflict_type": "bounded-backtracking",
                        "failed_resource": "",
                        "conflict_overlap_minutes": "",
                        "candidate_route": "",
                        "candidate_entry_time": "",
                        "fixed_train_count": len(selected) - len(local_ids),
                        "pair_deep_attempted": 0,
                        "pair_deep_success": 0,
                        "pair_deep_status": "TRIPLE_BACKTRACK_PASS" if len(local_ids) > 2 else "PAIR_BACKTRACK_PASS",
                        "pair_deep_value": value,
                        "dp_calls": info.get("dp_calls", 0),
                        "combination_cap": info.get("combination_cap", 0),
                        "route_changed": int(route_changed),
                        "timing_changed": int(timing_changed),
                        "meet_pass_changed": 0,
                        "common_main_resources": "",
                        "baseline_pair_signature": "",
                        "repaired_pair_signature": "",
                        "local_conflict_count": local_validation.get("conflict_count", 0),
                        "reason": "accepted bounded local backtracking",
                        "wall_time_sec": time.monotonic() - bootstrap_start,
                    })
                    append_history(
                        step, train, before, selected, repair_triggered=True,
                        pair=tuple(local_ids[:2]), pair_attempted=True,
                        pair_success=True, route_changed=route_changed,
                        timing_changed=timing_changed, meet_pass_changed=False,
                    )
                    pair_deep_successes += 1
                    route_change_count += int(route_changed)
                    timing_change_count += int(timing_changed)
                    local_repair_succeeded = True
                    break
                except Exception as exc:
                    pair_calls.append({
                        "step": step, "failed_train": train.train_id,
                        "pair_train_1": local_ids[0],
                        "pair_train_2": local_ids[1] if len(local_ids) > 1 else "",
                        "conflict_type": "bounded-backtracking",
                        "failed_resource": "", "conflict_overlap_minutes": "",
                        "candidate_route": "", "candidate_entry_time": "",
                        "fixed_train_count": len(selected) - len(local_ids),
                        "pair_deep_attempted": 0, "pair_deep_success": 0,
                        "pair_deep_status": "ERROR",
                        "pair_deep_value": math.inf, "dp_calls": 0,
                        "combination_cap": 20000 if len(local_ids) > 2 else 100000,
                        "route_changed": 0, "timing_changed": 0,
                        "meet_pass_changed": 0, "common_main_resources": "",
                        "baseline_pair_signature": "", "repaired_pair_signature": "",
                        "local_conflict_count": "",
                        "reason": f"{type(exc).__name__}: {exc}",
                        "wall_time_sec": time.monotonic() - bootstrap_start,
                    })
                    if deadline_exceeded():
                        _write_bootstrap_audits(output, history, pair_calls)
                        return None, time_limit_result(train.train_id)
            if local_repair_succeeded:
                continue

            # Final bounded bootstrap fallback: reuse the existing campaign
            # 4--8 train beam neighborhood on the failed train plus the seven
            # most recently committed trains.  This is still primal recovery
            # only.  The full PATH-K/route-augmentation scan is sampled after
            # the C++ call, and the returned partial state is validated before
            # any train is committed.
            neighborhood_ids = [item.train_id for item in ordered[:step - 1]][-7:]
            neighborhood_ids.append(train.train_id)
            neighborhood_ids = list(dict.fromkeys(neighborhood_ids))
            if len(neighborhood_ids) >= 4:
                if deadline_exceeded():
                    _write_bootstrap_audits(output, history, pair_calls)
                    return None, time_limit_result(train.train_id)
                try:
                    from gap30_campaign import _beam_neighborhood
                    neighborhood, neighborhood_info = _beam_neighborhood(
                        tuple(neighborhood_ids), selected, dataset=dataset,
                        trains=trains, candidates=candidates,
                        executable=executable, arcs=arcs, mow=mow, cfg=args,
                        output=pair_output, headway=args.safety_headway_minutes,
                        beam_width=8, option_width=9000)
                    neighborhood_validation = (
                        validate_physical_schedule(
                            neighborhood, trains, candidates, arcs, mow,
                            horizon_minutes=args.horizon,
                            safety_headway_minutes=args.safety_headway_minutes,
                            time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
                            wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
                            require_complete=False)
                        if neighborhood is not None else {
                            "status": "FAIL", "conflicts": [], "conflict_count": 0,
                        }
                    )
                    if neighborhood is not None and neighborhood_validation["status"] == "PASS":
                        old_signatures = local_row_signatures(
                            selected, tuple(neighborhood_ids))
                        selected = {train_id: dict(row)
                                    for train_id, row in neighborhood.items()}
                        new_signatures = local_row_signatures(
                            selected, tuple(neighborhood_ids))
                        route_changed = any(
                            old_signatures.get(train_id, ("",))[0] !=
                            new_signatures.get(train_id, ("",))[0]
                            for train_id in old_signatures)
                        timing_changed = any(
                            old_signatures.get(train_id, ("", "", "",))[1:] !=
                            new_signatures.get(train_id, ("", "", "",))[1:]
                            for train_id in old_signatures)
                        pair_calls.append({
                            "step": step, "failed_train": train.train_id,
                            "pair_train_1": neighborhood_ids[0],
                            "pair_train_2": neighborhood_ids[1] if len(neighborhood_ids) > 1 else "",
                            "conflict_type": "bounded-8-train-beam",
                            "failed_resource": "",
                            "conflict_overlap_minutes": "",
                            "candidate_route": "",
                            "candidate_entry_time": "",
                            "fixed_train_count": len(selected) - len(neighborhood_ids),
                            "pair_deep_attempted": 0, "pair_deep_success": 0,
                            "pair_deep_status": "BEAM_BACKTRACK_PASS",
                            "pair_deep_value": neighborhood_info.get("best_objective", math.inf),
                            "dp_calls": neighborhood_info.get("dp_calls", 0),
                            "combination_cap": "beam_width=8;option_width=9000",
                            "route_changed": int(route_changed),
                            "timing_changed": int(timing_changed),
                            "meet_pass_changed": 0,
                            "common_main_resources": "",
                            "baseline_pair_signature": "",
                            "repaired_pair_signature": "",
                            "local_conflict_count": neighborhood_validation.get("conflict_count", 0),
                            "reason": "accepted bounded 4-8 train beam backtracking",
                            "wall_time_sec": time.monotonic() - bootstrap_start,
                        })
                        pair_deep_successes += 1
                        route_change_count += int(route_changed)
                        timing_change_count += int(timing_changed)
                        append_history(
                            step, train, before, selected, repair_triggered=True,
                            pair=tuple(neighborhood_ids[:2]), pair_attempted=True,
                            pair_success=True, route_changed=route_changed,
                            timing_changed=timing_changed, meet_pass_changed=False,
                        )
                        continue
                except Exception as exc:
                    pair_calls.append({
                        "step": step, "failed_train": train.train_id,
                        "pair_train_1": neighborhood_ids[0],
                        "pair_train_2": neighborhood_ids[1] if len(neighborhood_ids) > 1 else "",
                        "conflict_type": "bounded-8-train-beam",
                        "failed_resource": "", "conflict_overlap_minutes": "",
                        "candidate_route": "", "candidate_entry_time": "",
                        "fixed_train_count": len(selected) - len(neighborhood_ids),
                        "pair_deep_attempted": 0, "pair_deep_success": 0,
                        "pair_deep_status": "ERROR",
                        "pair_deep_value": math.inf, "dp_calls": 0,
                        "combination_cap": "beam_width=8;option_width=9000",
                        "route_changed": 0, "timing_changed": 0,
                        "meet_pass_changed": 0, "common_main_resources": "",
                        "baseline_pair_signature": "", "repaired_pair_signature": "",
                        "local_conflict_count": "",
                        "reason": f"{type(exc).__name__}: {exc}",
                        "wall_time_sec": time.monotonic() - bootstrap_start,
                    })
                    if deadline_exceeded():
                        _write_bootstrap_audits(output, history, pair_calls)
                        return None, time_limit_result(train.train_id)
            failed_partial = (
                {**selected, train.train_id: baseline_row}
                if baseline_row is not None else selected
            )
            append_history(
                step, train, before, failed_partial, repair_triggered=True,
                pair=tuple(ordered_pairs[0]) if ordered_pairs else (),
                pair_attempted=bool(ordered_pairs), pair_success=False,
            )
            _write_bootstrap_audits(output, history, pair_calls)
            if deadline_exceeded():
                return None, time_limit_result(train.train_id)
            return None, {
                "status": "FAIL", "failed_train": train.train_id,
                "failed_insertion_index": step,
                "reason": "no DP trajectory compatible with fixed residual headways "
                          "and siding-only overtake rule; existing pair_deep "
                          "repair options exhausted",
                "bootstrap_history": history,
                "pair_deep_calls": pair_calls,
                "pair_deep_attempts": pair_deep_attempts,
                "pair_deep_successes": pair_deep_successes,
                "retimed_train_count": len(retimed_train_ids),
                "route_change_count": route_change_count,
                "timing_change_count": timing_change_count,
                "meet_pass_change_count": meet_pass_change_count,
            }
        selected[train.train_id] = chosen
        append_history(step, train, before, selected)
    validation = validate_physical_schedule(
        selected, trains, candidates, arcs, mow,
        horizon_minutes=args.horizon,
        safety_headway_minutes=args.safety_headway_minutes,
        time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
        wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
    )
    _write_bootstrap_audits(output, history, pair_calls)
    if validation["status"] != "PASS":
        return None, {
            **validation,
            "bootstrap_history": history,
            "pair_deep_calls": pair_calls,
            "pair_deep_attempts": pair_deep_attempts,
            "pair_deep_successes": pair_deep_successes,
            "retimed_train_count": len(retimed_train_ids),
            "route_change_count": route_change_count,
            "timing_change_count": timing_change_count,
            "meet_pass_change_count": meet_pass_change_count,
        }
    return selected, {
        **validation,
        "bootstrap_history": history,
        "pair_deep_calls": pair_calls,
        "pair_deep_attempts": pair_deep_attempts,
        "pair_deep_successes": pair_deep_successes,
        "retimed_train_count": len(retimed_train_ids),
        "route_change_count": route_change_count,
        "timing_change_count": timing_change_count,
        "meet_pass_change_count": meet_pass_change_count,
    }


def recover_feasible_adaptive(
    trains: list[Train], candidates: dict[str, list[PathCandidate]],
    executable: Path, dataset: Path, arcs: dict[int, Arc],
    mow: list[dict[str, float | int]], args: argparse.Namespace,
    output: Path, initial_selected: dict[str, dict[str, str]] | None = None,
) -> tuple[dict[str, dict[str, str]] | None, dict[str, object]]:
    """Current-pipeline feasibility-first insertion with adaptive order.

    This is deliberately a small extension of the residual PATH-K recovery:
    a train whose current residual DP has no compatible trajectory is skipped
    for this round, and another deterministic pending train is tried.  Only a
    trajectory returned feasible by the residual DP and the Python physical
    validator is committed.  No objective value from a partial state is used
    as a bound, and no block-30 aggregate quota is introduced.
    """
    output.mkdir(parents=True, exist_ok=True)
    selected = {
        train_id: dict(row) for train_id, row in (initial_selected or {}).items()
    }
    train_by_id = {train.train_id: train for train in trains}
    unknown = sorted(set(selected) - set(train_by_id))
    if unknown:
        return None, {"status": "FAIL", "reason": f"unknown warm-start trains: {unknown}"}
    warm_validation = validate_physical_schedule(
        selected, trains, candidates, arcs, mow,
        horizon_minutes=args.horizon,
        safety_headway_minutes=args.safety_headway_minutes,
        time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
        wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
        require_complete=False,
    )
    if warm_validation["status"] != "PASS":
        return None, {**warm_validation, "reason": "invalid warm start"}

    by_entry = sorted(trains, key=lambda train: (
        train.entry_min, train.origin, -train.smult, train.train_id))
    eb = [train for train in by_entry if train.origin < train.destination]
    wb = [train for train in by_entry if train.origin > train.destination]
    adaptive_order = str(getattr(args, "adaptive_order", "alternating"))
    if adaptive_order == "directional":
        priority = eb + wb
    elif adaptive_order == "wb_first":
        priority = wb + eb
    elif adaptive_order == "reverse_directional":
        priority = list(reversed(eb + wb))
    else:
        priority = []
        while eb or wb:
            if eb:
                priority.append(eb.pop(0))
            if wb:
                priority.append(wb.pop(0))
    pending = [train for train in priority if train.train_id not in selected]
    initial_ids = set(initial_selected or {})
    accepted_order = [
        train.train_id for train in priority
        if train.train_id in selected
    ]
    history: list[dict[str, object]] = []
    pair_calls: list[dict[str, object]] = []
    start = time.monotonic()
    deadline = start + float(getattr(args, "bootstrap_time_limit_sec", math.inf))
    args._bootstrap_deadline = deadline
    probe_rounds = 0
    dp_calls = 0
    backtrack_attempts = 0
    # A released train must not be forced back onto the identical trajectory
    # that caused the dead end.  The key deliberately includes route and
    # timing, because the same topology may have several feasible departure
    # / dwell realizations under different residual reservations.
    rejected_rows: dict[str, set[tuple[object, ...]]] = defaultdict(set)
    retime_variant: dict[str, int] = defaultdict(int)
    retime_step = float(getattr(args, "adaptive_retime_step", 5.0))

    def row_key(row: dict[str, str]) -> tuple[object, ...]:
        return (
            int(row.get("route_index", 0)),
            float(row.get("departure_min", "nan")),
            float(row.get("arrival_min", "nan")),
            row.get("legs", ""),
            row.get("waits", ""),
        )

    def probe_subset(items: list[PathCandidate]) -> list[PathCandidate]:
        """Keep adaptive probes bounded while retaining route diversity.

        The certified LB still evaluates the complete native chain universe.
        This cap applies only to the primal recovery probe, where a single
        train's 8k PATH-K route batch is otherwise repeated for every
        residual state.  Always retaining the tail preserves deterministic
        capacity-coverage routes appended by the native adapter.
        """
        cap = int(getattr(args, "adaptive_candidate_cap", 0))
        if cap <= 0 or len(items) <= cap:
            return items
        keep: set[int] = set(range(min(cap // 2, len(items))))
        keep.update(range(max(0, len(items) - min(256, cap)), len(items)))
        if len(keep) < cap:
            stride = max(1, len(items) // max(1, cap - len(keep)))
            for index in range(0, len(items), stride):
                keep.add(index)
                if len(keep) >= cap:
                    break
        return [items[index] for index in sorted(keep)[:cap]]

    def counts(state: dict[str, dict[str, str]]) -> dict[str, object]:
        return _bootstrap_conflict_counts(
            state, trains, candidates,
            safety_headway_minutes=args.safety_headway_minutes,
            time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
            wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
        )

    def result(status: str, reason: str, failed: str = "") -> dict[str, object]:
        return {
            "status": status, "reason": reason, "failed_train": failed,
            "train_count": len(selected), "initial_train_count": len(initial_selected or {}),
            "adaptive_probe_rounds": probe_rounds, "adaptive_dp_calls": dp_calls,
            "adaptive_backtrack_attempts": backtrack_attempts,
            "bootstrap_history": history, "pair_deep_calls": pair_calls,
            "wall_time_sec": time.monotonic() - start,
        }

    while pending:
        if time.monotonic() >= deadline:
            _write_bootstrap_audits(output, history, pair_calls)
            return None, result("TIME_LIMIT", "bounded adaptive bootstrap deadline exceeded")
        probe_rounds += 1
        fixed = _fixed_headway_rows(
            selected, trains, candidates,
            safety_headway_minutes=args.safety_headway_minutes)
        chosen_train: Train | None = None
        chosen_row: dict[str, str] | None = None
        next_pending: list[Train] = []
        for train in pending:
            if time.monotonic() >= deadline:
                _write_bootstrap_audits(output, history, pair_calls)
                return None, result("TIME_LIMIT", "bounded adaptive bootstrap deadline exceeded",
                                    train.train_id)
            dp_calls += 1
            probe_candidates = {train.train_id: probe_subset(
                candidates[train.train_id])}
            minimum_siding_wait = None
            if retime_variant[train.train_id] > 0:
                minimum_siding_wait = retime_step * retime_variant[train.train_id]
            rows = run_cpp_batch(
                executable, dataset, [train],
                probe_candidates, {}, mow, arcs,
                bin_minutes=args.bin_minutes, horizon_minutes=args.horizon,
                output_root=output, cfg=args,
                forbidden_actions=frozenset(), fixed_headway_rows=fixed,
                enable_headway_residual=True,
                minimum_siding_wait=minimum_siding_wait,
            )
            options = [row for row in rows if row.get("feasible") == "1"]
            if str(getattr(args, "adaptive_option_policy", "objective")) == "siding_first":
                options.sort(key=lambda row: (
                    -float(row.get("siding_wait_min", 0.0) or 0.0),
                    -float(row.get("enroute_wait_min", 0.0) or 0.0),
                    float(row.get("physical_cost", "inf")),
                    float(row.get("arrival_min") or "inf"),
                    int(row.get("route_index", 0)),
                ))
            elif str(getattr(args, "adaptive_option_policy", "objective")) == "latest":
                options.sort(key=lambda row: (
                    -float(row.get("arrival_min") or "-inf"),
                    float(row.get("physical_cost", "inf")),
                    int(row.get("route_index", 0)),
                ))
            else:
                options.sort(key=lambda row: (
                    float(row.get("physical_cost", "inf")),
                    float(row.get("arrival_min") or "inf"),
                    int(row.get("route_index", 0)),
                ))
            for option in options:
                if row_key(option) in rejected_rows[train.train_id]:
                    continue
                profile = _profile_for_row(train, option, candidates)
                if any(_forbidden_main_track_overtake(
                        profile, _profile_for_row(
                            train_by_id[train_id], fixed_row, candidates))
                       for train_id, fixed_row in selected.items()):
                    continue
                chosen_train, chosen_row = train, option
                break
            if chosen_train is not None:
                next_pending = [item for item in pending if item.train_id != train.train_id]
                break
        if chosen_train is None or chosen_row is None:
            # Bounded deterministic recovery from a dead end: release the
            # latest non-warm-start committed trains and retry the pending
            # stream before declaring bootstrap failure.  This is the same
            # current residual-DP construction, not a copied timetable.
            release_depth = int(getattr(args, "adaptive_backtrack_depth", 4))
            max_backtracks = int(getattr(args, "adaptive_backtrack_max", 12))
            allow_warm_release = bool(
                getattr(args, "adaptive_allow_warm_release", False))
            releasable = [
                train_id for train_id in accepted_order
                if train_id in selected
                and (allow_warm_release or train_id not in initial_ids)
            ][-release_depth:]
            if releasable and backtrack_attempts < max_backtracks:
                backtrack_attempts += 1
                for train_id in releasable:
                    rejected_rows[train_id].add(row_key(selected[train_id]))
                    retime_variant[train_id] += 1
                    selected.pop(train_id, None)
                accepted_order = [
                    train_id for train_id in accepted_order
                    if train_id not in set(releasable)
                ]
                released = [train_by_id[train_id] for train_id in releasable]
                pending = pending + released
                c = counts(selected)
                history.append({
                    "step": len(history) + 1,
                    "train_inserted": "BACKTRACK_RELEASE",
                    "partial_train_count": len(selected),
                    "objective": _bootstrap_partial_objective(selected, trains),
                    "total_conflicts": c["total"],
                    "headway_conflicts": c["headway"],
                    "opposing_conflicts": c["opposing"],
                    "same_direction_conflicts": c["same_direction"],
                    "illegal_overtakes": c["illegal_overtakes"],
                    "siding_conflicts": c["siding"],
                    "repair_triggered": 1,
                    "repair_pair_train_1": releasable[0] if releasable else "",
                    "repair_pair_train_2": releasable[1] if len(releasable) > 1 else "",
                    "pair_deep_attempted": 0, "pair_deep_success": 0,
                    "route_changed": 0, "timing_changed": 0,
                    "meet_pass_changed": 0,
                    "objective_after_repair": _bootstrap_partial_objective(selected, trains),
                    "conflicts_after_repair": c["total"],
                    "wall_time_sec": time.monotonic() - start,
                    "adaptive_probe_round": probe_rounds,
                    "adaptive_probed_train_count": dp_calls,
                })
                continue
            failed = pending[0].train_id if pending else ""
            _write_bootstrap_audits(output, history, pair_calls)
            return None, result(
                "FAIL", "no pending train has a residual-DP trajectory compatible "
                "with the committed physical reservations after bounded adaptive "
                "backtracking", failed)
        selected[chosen_train.train_id] = chosen_row
        accepted_order.append(chosen_train.train_id)
        c = counts(selected)
        history.append({
            "step": len(history) + 1,
            "train_inserted": chosen_train.train_id,
            "partial_train_count": len(selected),
            "objective": _bootstrap_partial_objective(selected, trains),
            "total_conflicts": c["total"], "headway_conflicts": c["headway"],
            "opposing_conflicts": c["opposing"],
            "same_direction_conflicts": c["same_direction"],
            "illegal_overtakes": c["illegal_overtakes"],
            "siding_conflicts": c["siding"],
            "repair_triggered": 0, "repair_pair_train_1": "",
            "repair_pair_train_2": "", "pair_deep_attempted": 0,
            "pair_deep_success": 0, "route_changed": 0, "timing_changed": 0,
            "meet_pass_changed": 0,
            "objective_after_repair": _bootstrap_partial_objective(selected, trains),
            "conflicts_after_repair": c["total"],
            "wall_time_sec": time.monotonic() - start,
            "adaptive_probe_round": probe_rounds,
            "adaptive_probed_train_count": dp_calls,
        })
        pending = next_pending
        # This check is intentionally redundant with the commit-time count:
        # it documents that adaptive order is feasibility-first, not an
        # objective-driven partial schedule heuristic.
        if c["total"] != 0:
            _write_bootstrap_audits(output, history, pair_calls)
            return None, result("FAIL", "adaptive insertion produced a physical conflict",
                                chosen_train.train_id)

    validation = validate_physical_schedule(
        selected, trains, candidates, arcs, mow,
        horizon_minutes=args.horizon,
        safety_headway_minutes=args.safety_headway_minutes,
        time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
        wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
    )
    _write_bootstrap_audits(output, history, pair_calls)
    if validation["status"] != "PASS":
        return None, result("FAIL", "final adaptive physical validation failed")
    return selected, {
        **validation, "adaptive_probe_rounds": probe_rounds,
        "adaptive_dp_calls": dp_calls, "bootstrap_history": history,
        "pair_deep_calls": pair_calls,
        "adaptive_backtrack_attempts": backtrack_attempts,
        "wall_time_sec": time.monotonic() - start,
    }


def _bb_solve_node(
    node: BBNode, *, executable: Path, dataset: Path, trains: list[Train],
    candidates: dict[str, list[PathCandidate]], lambdas: dict[tuple[int, int], float],
    mow: list[dict[str, float | int]], arcs: dict[int, Arc], args: argparse.Namespace,
    output: Path, capacity_certified: bool,
    capacity_model: str, capacity_map: dict[tuple[int, int], float],
    incumbent_ub: float, dual_iterations: int,
    candidate_fingerprint: str, physical_fingerprint: str,
    dp_cache: dict[tuple[object, ...], list[dict[str, str]]] | None = None,
    strong_cache: dict[tuple[object, ...], tuple[float, dict[str, str] | None]] | None = None,
    pair_cache: dict[tuple[object, ...], tuple[float, bool]] | None = None,
    cache_stats: dict[str, int] | None = None,
) -> tuple[BBNode, dict[str, dict[str, str]] | None, dict[str, object]]:
    """Solve one fixed-domain CBS node and optionally recover a validated UB."""
    dp_cache = dp_cache if dp_cache is not None else {}
    strong_cache = strong_cache if strong_cache is not None else {}
    pair_cache = pair_cache if pair_cache is not None else {}
    cache_stats = cache_stats if cache_stats is not None else {}
    forbidden, required, infeasible, _ = normalize_bb_constraints(
        node.forbidden_actions, node.required_actions)
    node.forbidden_actions, node.required_actions = forbidden, required
    if infeasible:
        node.status = "INFEASIBLE"
        node.infeasible_train = "constraint-set"
        node.base_lower_bound = math.inf
        node.dual_lower_bound = math.inf
        node.lower_bound = math.inf
        return node, None, {"status": "FAIL", "errors": ["contradictory CBS constraints"]}

    rows_by_train: dict[str, list[dict[str, str]]] = {}
    missing_trains: list[Train] = []
    for train in trains:
        key = _bb_dp_cache_key(
            train.train_id, node.forbidden_actions, node.required_actions,
            candidate_fingerprint=candidate_fingerprint,
            physical_fingerprint=physical_fingerprint,
        )
        cached = dp_cache.get(key)
        if cached is None:
            missing_trains.append(train)
        else:
            cache_stats["dp_cache_hits"] = cache_stats.get("dp_cache_hits", 0) + 1
            rows_by_train[train.train_id] = [dict(row) for row in cached]
    if missing_trains:
        cache_stats["dp_cache_misses"] = cache_stats.get(
            "dp_cache_misses", 0) + len(missing_trains)
        missing_candidates = {
            train.train_id: candidates[train.train_id] for train in missing_trains
        }
        missing_rows = run_cpp_batch(
            executable, dataset, missing_trains, missing_candidates, {}, mow, arcs,
            bin_minutes=args.bin_minutes, horizon_minutes=args.horizon,
            output_root=output, cfg=args,
            forbidden_actions=node.forbidden_actions,
            required_actions=node.required_actions,
        )
        for train in missing_trains:
            rows = [row for row in missing_rows if row.get("train_id") == train.train_id]
            key = _bb_dp_cache_key(
                train.train_id, node.forbidden_actions, node.required_actions,
                candidate_fingerprint=candidate_fingerprint,
                physical_fingerprint=physical_fingerprint,
            )
            dp_cache[key] = [dict(row) for row in rows]
            rows_by_train[train.train_id] = rows
    base_rows = [row for train in trains for row in rows_by_train.get(train.train_id, [])]
    missing = _bb_missing_train(base_rows, trains)
    if missing is not None:
        node.status = "INFEASIBLE"
        node.infeasible_train = missing
        node.base_lower_bound = math.inf
        node.dual_lower_bound = math.inf
        node.lower_bound = math.inf
        return node, None, {"status": "FAIL", "errors": [f"infeasible train {missing}"]}

    base_selected, _ = select_relaxed_argmins(base_rows, trains, candidates)
    node.base_paths = base_selected
    node.base_lower_bound = _bb_physical_cost(base_selected)
    node.relaxed_paths = base_selected
    node.dual_lower_bound = node.base_lower_bound
    node.best_lambda = dict(lambdas)

    ub_candidate: dict[str, dict[str, str]] | None = None
    ub_validation: dict[str, object] = {"status": "NOT_ATTEMPTED", "conflicts": []}
    if ub_candidate is None and not math.isfinite(incumbent_ub) and node.depth <= 1:
        greedy, greedy_validation = recover_feasible_with_dp(
            trains, candidates, executable, dataset, arcs, mow, args, output,
            node.forbidden_actions,
        )
        if greedy is not None:
            ub_candidate = greedy
            ub_validation = greedy_validation

    if capacity_certified and dual_iterations > 0:
        current_lambda = dict(lambdas)
        best_dual = -math.inf
        best_dual_lambda: dict[tuple[int, int], float] = dict(current_lambda)
        for iteration in range(dual_iterations):
            dual_rows = run_cpp_batch(
                executable, dataset, trains, candidates, current_lambda, mow, arcs,
                bin_minutes=args.bin_minutes, horizon_minutes=args.horizon,
                output_root=output, cfg=args,
                forbidden_actions=node.forbidden_actions,
                required_actions=node.required_actions,
            )
            missing = _bb_missing_train(dual_rows, trains)
            if missing is not None:
                node.status = "INFEASIBLE"
                node.infeasible_train = missing
                node.base_lower_bound = math.inf
                node.dual_lower_bound = math.inf
                node.lower_bound = math.inf
                return node, ub_candidate, ub_validation
            relaxed, by_train = select_relaxed_argmins(dual_rows, trains, candidates)
            evaluation = evaluate_dual_bound(
                relaxed, by_train, trains, current_lambda,
                bin_minutes=args.bin_minutes,
                safety_headway_minutes=args.safety_headway_minutes,
                capacity_model=capacity_model, capacity_map=capacity_map,
            )
            node.relaxed_paths = relaxed
            if evaluation.lower_bound > best_dual + EPS:
                best_dual = evaluation.lower_bound
                best_dual_lambda = dict(current_lambda)
            updated, gradient, step_info = dual_subgradient_update(
                current_lambda, evaluation.occupancy, iteration=iteration,
                dual_value=evaluation.lower_bound,
                incumbent_ub=incumbent_ub,
                model=capacity_model, capacity_map=capacity_map,
                theta=args.dual_theta_initial,
                fallback_step=max(args.step_size_initial, 1e-3),
                norm_tolerance=args.dual_norm_tolerance,
            )
            node.dual_history.append({
                "iteration": iteration, "dual_value": evaluation.lower_bound,
                "best_dual_bound": best_dual,
                "gradient_norm_sq": step_info["norm_sq"],
                "step": step_info["step"], "used_polyak": step_info["used_polyak"],
                "max_gradient": max(gradient.values(), default=0.0),
                "capacity_violation_l1": sum(
                    max(0.0, value) for value in gradient.values()),
            })
            current_lambda = updated
            if step_info["stopped"]:
                break
        node.dual_lower_bound = max(node.base_lower_bound, best_dual)
        node.best_lambda = best_dual_lambda
    pair_conflicts = all_physical_conflicts(
        node.base_paths, trains, candidates,
        safety_headway_minutes=args.safety_headway_minutes,
        time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
        wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
    )
    node.pair_lower_bound, node.pair_matching_edges, node.pair_bound_exact = (
        _bb_pairwise_bound(
            node, pair_conflicts, executable=executable, dataset=dataset,
            trains=trains, candidates=candidates, mow=mow, arcs=arcs, args=args,
            output=output, candidate_fingerprint=candidate_fingerprint,
            physical_fingerprint=physical_fingerprint, dp_cache=dp_cache,
            strong_cache=strong_cache, pair_cache=pair_cache,
            cache_stats=cache_stats))
    if math.isinf(node.pair_lower_bound) and node.pair_lower_bound > 0:
        # Both sides of a witnessed pair conflict are infeasible under the
        # current single-train domains.  This is a certified empty node, not a
        # finite incumbent-like objective value.
        node.status = "INFEASIBLE"
        node.infeasible_train = "pairwise-conflict"
        node.lower_bound = math.inf
        return node, ub_candidate, ub_validation
    if (math.isfinite(incumbent_ub) and math.isfinite(node.pair_lower_bound) and
            node.pair_lower_bound > incumbent_ub + BB_TOLERANCE):
        raise AssertionError("certified pairwise lower bound exceeded validated UB")
    node.lower_bound = max(
        node.inherited_lower_bound, node.base_lower_bound, node.dual_lower_bound,
        node.pair_lower_bound)
    if node.lower_bound < node.inherited_lower_bound - BB_TOLERANCE:
        raise AssertionError("CBS child bound fell below inherited valid bound")
    return node, ub_candidate, ub_validation


def _prepare_bb_candidates(
    trains: list[Train], graph: dict[int, list[Edge]], arcs: dict[int, Arc],
    candidates: dict[str, list[PathCandidate]],
) -> bool:
    """Complete siding-containing candidate generation before root creation."""
    expanded = False
    for train in trains:
        if any("S" in candidate.track_ids for candidate in candidates[train.train_id]):
            continue
        enlarged = enumerate_candidates(
            train, graph, arcs, max(50, len(candidates[train.train_id]))
        )
        if any("S" in candidate.track_ids for candidate in enlarged):
            candidates[train.train_id] = enlarged
            expanded = True
    return expanded


def _bb_load_incumbent(
    path: Path, trains: list[Train], candidates: dict[str, list[PathCandidate]],
    arcs: dict[int, Arc], mow: list[dict[str, float | int]], args: argparse.Namespace,
) -> tuple[float, dict[str, dict[str, str]], dict[str, object]]:
    selected: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as probe:
        header_line = probe.readline()
    delimiter = "\t" if "\t" in header_line else ","
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter=delimiter):
            train_id = str(row.get("train_id", ""))
            if train_id:
                selected[train_id] = dict(row)
    validation = validate_physical_schedule(
        selected, trains, candidates, arcs, mow,
        horizon_minutes=args.horizon,
        safety_headway_minutes=args.safety_headway_minutes,
        time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
        wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
    )
    if validation["status"] != "PASS":
        raise ValueError(f"initial incumbent failed physical validation: {validation}")
    recorded_cost = _bb_physical_cost(selected)
    recomputed_cost = _bb_recompute_physical_cost(
        selected, trains, candidates, arcs, args)
    validation["recorded_objective"] = recorded_cost
    validation["recomputed_objective"] = recomputed_cost
    validation["objective_difference_recomputed_minus_recorded"] = (
        recomputed_cost - recorded_cost)
    return recomputed_cost, selected, validation


def _bb_node_log_row(
    node: BBNode, *, incumbent_ub: float, global_lb: float | None,
    prune_reason: str = "", candidate_fingerprint: str = "",
) -> dict[str, object]:
    action = node.branch_action
    rel_ub, rel_lb = _bb_gap(
        global_lb if global_lb is not None else math.inf, incumbent_ub)
    constraint_payload = {
        "constraint_schema_version": BB_CONSTRAINT_SCHEMA_VERSION,
        "candidate_fingerprint": candidate_fingerprint,
        "required_actions": [
            _bb_required_dict(item) for item in sorted(node.required_actions)
        ],
        "forbidden_actions": [
            _bb_action_dict(item) for item in sorted(node.forbidden_actions)
        ],
    }
    constraint_json = json.dumps(
        constraint_payload, sort_keys=True, separators=(",", ":"))
    constraint_hash = hashlib.sha256(
        constraint_json.encode("utf-8")).hexdigest()
    return {
        "node_id": node.node_id, "parent_id": node.parent_id,
        "depth": node.depth, "status": node.status,
        "branch_train_id": action.train_id if action else "",
        "branch_route_index": action.route_index if action else "",
        "branch_arc_position": action.arc_position if action else "",
        "branch_start_cell": action.start_cell if action else "",
        "branch_wait_tick": action.wait_tick if action else "",
        "base_lower_bound": node.base_lower_bound,
        "dual_lower_bound": node.dual_lower_bound,
        "inherited_lower_bound": node.inherited_lower_bound,
        "node_lower_bound": node.lower_bound,
        "incumbent_ub_at_solve": incumbent_ub,
        "global_lb_after_processing": global_lb,
        "absolute_gap": (
            max(0.0, incumbent_ub - global_lb)
            if global_lb is not None and math.isfinite(incumbent_ub) else None),
        "relative_gap_ub_normalized": rel_ub,
        "relative_gap_lb_normalized": rel_lb,
        "dual_iterations": len(node.dual_history),
        "conflict_count": node.representative_conflict_count,
        "selected_conflict_type": node.branch_conflict_type,
        "selected_conflict_arc": node.branch_conflict_arc,
        "selected_conflict_time": node.branch_conflict_time,
        "candidate_conflicts_examined": node.candidate_conflicts_examined,
        "selected_conflict_class": node.selected_conflict_class,
        "selected_delta_a": node.selected_delta_a,
        "selected_delta_b": node.selected_delta_b,
        "selected_conflict_score": node.selected_conflict_score,
        "strong_branching_dp_calls": node.strong_branching_dp_calls,
        "strong_branching_cache_hits": node.strong_branching_cache_hits,
        "bypass_attempts": node.bypass_attempts,
        "bypass_successes": node.bypass_successes,
        "conflicts_before_bypass": node.conflicts_before_bypass,
        "conflicts_after_bypass": node.conflicts_after_bypass,
        "bypass_train_id": node.bypass_train_id,
        "old_path_signature": node.old_path_signature,
        "new_path_signature": node.new_path_signature,
        "required_residual_movements": node.required_residual_movements,
        "labels_pruned_required_headway": node.labels_pruned_required_headway,
        "labels_pruned_required_opposing": node.labels_pruned_required_opposing,
        "labels_pruned_required_special": node.labels_pruned_required_special,
        "required_action_infeasibility": node.required_action_infeasibility,
        "pair_lower_bound": node.pair_lower_bound,
        "pair_bound_exact": node.pair_bound_exact,
        "pair_matching_edges": json.dumps(node.pair_matching_edges, sort_keys=True),
        "required_action_count": len(node.required_actions),
        "forbidden_action_count": len(node.forbidden_actions),
        "branch_kind": node.branch_kind,
        "prune_reason": prune_reason,
        "constraint_schema_version": BB_CONSTRAINT_SCHEMA_VERSION,
        "candidate_fingerprint": candidate_fingerprint,
        "constraint_set_hash": constraint_hash,
        "constraint_set_json": constraint_json,
        "runtime_sec": 0.0,
    }


def _bb_audit_capacity_map(
    path: Path | None, capacity_map: dict[tuple[int, int], float],
    metadata: dict[str, object], candidates: dict[str, list[PathCandidate]],
    arcs: dict[int, Arc], args: argparse.Namespace,
) -> dict[str, object]:
    """Audit a headway LR map against this exact candidate universe.

    The LR capacity is an occupancy upper bound, so a map is certificate-safe
    only when every possible DP resource cell is present and its capacity is at
    least the number of distinct trains that can touch that arc.  A failed
    check disables the dual rather than assigning an implicit value to a cell.
    """
    expected = {
        (arc_id, time_bin)
        for arc_id in arcs
        for time_bin in range(int(math.ceil(args.horizon / args.bin_minutes)))
    }
    touch_counts: dict[int, int] = {}
    for arc_id in arcs:
        touch_counts[arc_id] = len({
            train_id for train_id, train_candidates in candidates.items()
            if any(arc_id in candidate.arc_ids for candidate in train_candidates)
        })
    missing = sorted(expected - set(capacity_map))
    bad_capacity = sorted(
        (key, capacity_map[key], touch_counts[key[0]])
        for key in expected & set(capacity_map)
        if (not math.isfinite(capacity_map[key]) or
            capacity_map[key] < touch_counts[key[0]] - BB_TOLERANCE)
    )
    metadata_checks = {
        "candidate_fingerprint": metadata.get("candidate_fingerprint") ==
        candidate_universe_fingerprint(candidates),
        "candidate_count": metadata.get("candidate_count") ==
        sum(len(values) for values in candidates.values()),
        "physical_model_fingerprint": metadata.get("physical_model_fingerprint") ==
        physical_model_fingerprint(args),
        "bin_minutes": (metadata.get("bin_minutes") is not None and
                        abs(float(metadata["bin_minutes"]) - args.bin_minutes) <= EPS),
        "horizon": (metadata.get("horizon") is not None and
                    abs(float(metadata["horizon"]) - args.horizon) <= EPS),
    }
    certified = bool(path is not None and not missing and not bad_capacity and
                     all(metadata_checks.values()))
    reasons: list[str] = []
    if path is None:
        reasons.append("no capacity map supplied")
    if missing:
        reasons.append(f"missing resource cells: {len(missing)}")
    if bad_capacity:
        reasons.append(f"capacity below candidate-touch upper bound: {len(bad_capacity)}")
    for name, passed in metadata_checks.items():
        if not passed:
            reasons.append(f"capacity metadata mismatch: {name}")
    return {
        "path": str(path) if path is not None else None,
        "certified": certified,
        "expected_cell_count": len(expected),
        "map_cell_count": len(capacity_map),
        "missing_cell_count": len(missing),
        "missing_cells_sample": [list(key) for key in missing[:20]],
        "invalid_capacity_count": len(bad_capacity),
        "invalid_capacity_sample": [
            {"cell": list(key), "capacity": value, "required_upper_bound": required}
            for key, value, required in bad_capacity[:20]
        ],
        "metadata": metadata,
        "metadata_checks": metadata_checks,
        "candidate_touch_upper_bound_by_arc": {
            str(key): value for key, value in sorted(touch_counts.items())
        },
        "missing_cell_semantics": "schema-error-not-zero",
        "reasons": reasons,
    }


def _bb_serializable_validation(validation: dict[str, object]) -> dict[str, object]:
    """Remove PhysicalConflict objects before writing a JSON summary."""
    output: dict[str, object] = {}
    for key, value in validation.items():
        if key == "conflicts":
            output[key] = [
                _bb_conflict_dict(conflict) if isinstance(conflict, PhysicalConflict)
                else conflict for conflict in value
            ]
        else:
            output[key] = value
    return output


def solve_conflict_bb_v2(args: argparse.Namespace) -> dict[str, object]:
    """Run the schema-v2 fixed-domain CBS/B&B implementation.

    The old solver is retained below as a source-level rollback reference, but
    all CLI B&B runs enter this implementation.  The search domain is frozen
    before the root is created; every bound and cache key thereafter carries
    the resulting candidate and physical-model fingerprints.
    """
    started = time.monotonic()
    dataset = Path(args.dataset).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).with_name("path_k_dp.cpp")
    executable = (Path(args.cpp_exe).resolve() if args.cpp_exe
                  else Path(__file__).with_name("build") / "path_k_dp")
    executable = ensure_cpp(source, executable)
    arcs, trains, mow = load_dataset(dataset)
    graph = build_graph(arcs)

    # This is the only candidate-generation point in the tree search.  In
    # particular, a supplied capacity map is never allowed to decide whether
    # siding alternatives are added.
    candidates = all_candidates(trains, graph, arcs, args.path_k)
    candidate_expanded = _prepare_bb_candidates(trains, graph, arcs, candidates)
    candidate_count = sum(len(values) for values in candidates.values())
    fingerprint = candidate_universe_fingerprint(candidates)
    physical_fingerprint = physical_model_fingerprint(args)
    time_fingerprint = time_discretization_fingerprint(args)
    root_certificate: dict[str, object] | None = None
    root_certificate_lb = 0.0
    if getattr(args, "bb_root_bound_certificate", None):
        root_certificate = _read_root_bound_certificate(
            Path(args.bb_root_bound_certificate).resolve(),
            candidate_fingerprint=fingerprint, candidate_count=candidate_count,
            physical_fingerprint=physical_fingerprint,
            time_fingerprint=time_fingerprint,
        )
        root_certificate_lb = float(root_certificate["certified_root_lb"])

    capacity_model = str(getattr(args, "lr_capacity_model", DEFAULT_LR_CAPACITY_MODEL))
    capacity_path_value = getattr(args, "lr_capacity_map", None)
    capacity_path = Path(capacity_path_value).resolve() if capacity_path_value else None
    capacity_map: dict[tuple[int, int], float] = {}
    capacity_metadata: dict[str, object] = {}
    capacity_load_error: str | None = None
    if capacity_model == "headway-relaxation" and capacity_path is not None:
        try:
            capacity_map = load_lr_capacity_map(capacity_path)
            capacity_metadata = capacity_map_metadata(capacity_path)
        except (OSError, ValueError) as exc:
            capacity_load_error = str(exc)
    if capacity_model == "headway-relaxation":
        capacity_audit = _bb_audit_capacity_map(
            capacity_path, capacity_map, capacity_metadata, candidates, arcs, args)
        if capacity_load_error is not None:
            capacity_audit["certified"] = False
            capacity_audit.setdefault("reasons", []).append(capacity_load_error)
    else:
        capacity_audit = {
            "path": str(capacity_path) if capacity_path is not None else None,
            "certified": False,
            "reason": (
                "block30 C_ref=1 is a diagnostic aggregate reference term, not "
                "a proven physical relaxation RHS for continuous headway"
            ),
            "missing_cell_semantics": "not-applicable",
        }
    capacity_certified = bool(capacity_audit.get("certified", False))
    capacity_complete = capacity_certified
    dual_note = (
        "certified same-domain headway-relaxation capacity map"
        if capacity_certified else
        "dual disabled for certificate; only lambda=0 and other certified "
        "bounds are used because the capacity map audit did not pass"
    )

    # The incumbent is accepted only after reading its route/timing rows,
    # recomputing its objective, and running the complete physical validator.
    incumbent_ub = math.inf
    incumbent_routes: dict[str, dict[str, str]] = {}
    incumbent_validation: dict[str, object] = {
        "status": "NOT_ATTEMPTED", "conflicts": []}
    initial_ub = math.inf
    incumbent_path = getattr(args, "bb_incumbent_tsv", None)
    if incumbent_path:
        initial_ub, incumbent_routes, incumbent_validation = _bb_load_incumbent(
            Path(incumbent_path).resolve(), trains, candidates, arcs, mow, args)
        incumbent_ub = initial_ub

    resume_checkpoint_path = (
        Path(args.bb_resume_checkpoint).resolve()
        if getattr(args, "bb_resume_checkpoint", None) else None)
    # A resume source is read-only.  All subsequent checkpoints belong to the
    # new output run, so continuing a run cannot mutate the source artifact.
    checkpoint_path = output / "bb_open_nodes_checkpoint.json"
    restored = None
    if resume_checkpoint_path is not None:
        restored = _bb_read_checkpoint(
            resume_checkpoint_path, fingerprint, candidate_count=candidate_count,
            physical_fingerprint=physical_fingerprint)

    dp_cache: dict[tuple[object, ...], list[dict[str, str]]] = {}
    strong_cache: dict[tuple[object, ...], tuple[float, dict[str, str] | None]] = {}
    pair_cache: dict[tuple[object, ...], tuple[float, bool]] = {}
    variant_cache: dict[tuple[object, ...], float] = {}
    cache_stats: dict[str, int] = {}
    global_lb_history: list[float] = list(
        restored.get("global_lb_history", []) if restored else [])
    node_log: list[dict[str, object]] = []
    conflict_log: list[dict[str, object]] = []
    heap: list[tuple[float, int, int, BBNode]] = []
    root_base_lb: float | None = None
    root_dual_lb: float | None = None
    root_pair_lb: float | None = None
    root_pair_bound_exact: bool = False
    root_pair_matching_edges: list[tuple[str, str, float]] = []
    root_dual_history: list[dict[str, object]] = []
    root_action_dual_lb: float | None = None
    root_group_lb: float | None = None
    next_node_id = int(restored.get("next_node_id", 0)) if restored else 0

    def solve_node(node: BBNode, lambdas: dict[tuple[int, int], float],
                   dual_iterations: int) -> tuple[BBNode, dict[str, dict[str, str]] | None,
                                                    dict[str, object]]:
        node.inherited_lower_bound = max(
            float(node.inherited_lower_bound), root_certificate_lb)
        return _bb_solve_node(
            node, executable=executable, dataset=dataset, trains=trains,
            candidates=candidates, lambdas=lambdas, mow=mow, arcs=arcs, args=args,
            output=output, capacity_certified=capacity_certified,
            capacity_model=capacity_model, capacity_map=capacity_map,
            incumbent_ub=incumbent_ub, dual_iterations=dual_iterations,
            candidate_fingerprint=fingerprint, physical_fingerprint=physical_fingerprint,
            dp_cache=dp_cache, strong_cache=strong_cache, pair_cache=pair_cache,
            cache_stats=cache_stats)

    def accept_recovered(
        recovered: dict[str, dict[str, str]] | None,
        validation: dict[str, object],
    ) -> None:
        nonlocal incumbent_ub, incumbent_routes, incumbent_validation
        if recovered is None or validation.get("status") != "PASS":
            return
        cost = _bb_physical_cost(recovered)
        if cost < incumbent_ub - EPS or not incumbent_routes:
            incumbent_ub = cost
            incumbent_routes = {train_id: dict(row)
                                for train_id, row in recovered.items()}
            incumbent_validation = validation

    if restored:
        restored_routes = {
            str(tid): dict(row)
            for tid, row in dict(restored.get("incumbent_routes", {})).items()
        }
        if restored_routes:
            restored_validation = validate_physical_schedule(
                restored_routes, trains, candidates, arcs, mow,
                horizon_minutes=args.horizon,
                safety_headway_minutes=args.safety_headway_minutes,
                time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
                wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
            )
            if restored_validation["status"] != "PASS":
                raise ValueError(
                    "checkpoint incumbent failed physical validation: "
                    f"{restored_validation}")
            restored_cost = _bb_recompute_physical_cost(
                restored_routes, trains, candidates, arcs, args)
            if not incumbent_routes or restored_cost < incumbent_ub - EPS:
                incumbent_ub = restored_cost
                incumbent_routes = restored_routes
                incumbent_validation = restored_validation
        elif math.isfinite(float(restored.get("incumbent_ub", math.inf))):
            raise ValueError(
                "checkpoint contains a finite incumbent UB but no incumbent routes")
        for spec in restored.get("open_nodes", []):
            node = _bb_restore_node(spec)
            if root_certificate_lb > 0.0:
                node.inherited_lower_bound = max(
                    node.inherited_lower_bound, root_certificate_lb)
                node.lower_bound = max(node.lower_bound, root_certificate_lb)
            heapq.heappush(heap, (node.lower_bound, node.depth, node.node_id, node))
        root_candidates = [node for _, _, _, node in heap if node.depth == 0]
        if root_candidates:
            root_base_lb = root_candidates[0].base_lower_bound
            root_dual_lb = root_candidates[0].dual_lower_bound
            root_pair_lb = root_candidates[0].pair_lower_bound
            root_pair_bound_exact = root_candidates[0].pair_bound_exact
            root_pair_matching_edges = list(root_candidates[0].pair_matching_edges)
            root_dual_history = list(root_candidates[0].dual_history)
        elif root_certificate is not None:
            root_base_lb = root_certificate.get("root_base_lb")
            root_dual_lb = root_certificate.get("root_dual_lb")
            root_pair_lb = root_certificate.get("root_pair_lb")
            root_pair_bound_exact = bool(root_certificate.get("root_pair_bound_exact", False))
            root_pair_matching_edges = [tuple(item) for item in root_certificate.get(
                "selected_disjoint_matching", [])]
            root_dual_history = [dict(row) for row in root_certificate.get(
                "root_dual_history", [])]
            if root_certificate.get("root_action_dual_lb") is not None:
                root_action_dual_lb = float(root_certificate["root_action_dual_lb"])
            if root_certificate.get("root_group_lb") is not None:
                root_group_lb = float(root_certificate["root_group_lb"])
    else:
        root = BBNode(
            node_id=next_node_id, parent_id=None, depth=0,
            forbidden_actions=frozenset(), required_actions=frozenset(),
            inherited_lower_bound=0.0, lower_bound=0.0,
        )
        next_node_id += 1
        root, recovered, recovered_validation = solve_node(
            root, {}, args.bb_root_dual_iterations)
        accept_recovered(recovered, recovered_validation)
        root_base_lb, root_dual_lb = root.base_lower_bound, root.dual_lower_bound
        root_pair_lb = root.pair_lower_bound
        root_pair_bound_exact = root.pair_bound_exact
        root_pair_matching_edges = list(root.pair_matching_edges)
        root_dual_history = list(root.dual_history)
        if root.status != "INFEASIBLE":
            heapq.heappush(heap, (root.lower_bound, root.depth, root.node_id, root))
        else:
            node_log.append(_bb_node_log_row(
                root, incumbent_ub=incumbent_ub, global_lb=None,
                prune_reason="infeasible-train",
                candidate_fingerprint=fingerprint))

    nodes_created = next_node_id if restored else 1
    nodes_expanded = 0
    nodes_pruned_bound = 0
    nodes_pruned_infeasible = 0
    nodes_fathomed_feasible = 0
    duplicate_nodes_skipped = 0
    maximum_depth = max((item[1] for item in heap), default=0)
    seen_constraints: set[tuple[object, ...]] = set()
    seen_sources: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for _, _, _, node in heap:
        key = bb_constraint_key(
            node.forbidden_actions, node.required_actions,
            candidate_fingerprint=fingerprint)
        seen_constraints.add(key)
        seen_sources[key].append({
            "node_id": node.node_id, "parent_id": node.parent_id,
            "branch_kind": node.branch_kind,
        })

    duplicate_causes: dict[str, int] = {
        "same_constraint_set_reached_in_different_branch_order": 0,
        "standard_splitting_child_overlap": 0,
        "repeated_conflict_detection_on_equivalent_representative_paths": 0,
        "constraint_canonicalization_failure": 0,
        "required_forbidden_redundancy": 0,
    }
    duplicate_attempts: list[dict[str, object]] = []
    conflict_class_counts: dict[str, int] = {
        "cardinal": 0, "semi-cardinal": 0, "non-cardinal": 0,
        "not_evaluated": 0,
    }
    selected_conflict_class_counts: dict[str, int] = {
        "cardinal": 0, "semi-cardinal": 0, "non-cardinal": 0,
        "not_evaluated": 0,
    }
    delta_values: list[float] = []
    both_children_zero_delta_count = 0
    required_branches = 0
    forbidden_branches = 0
    bypass_totals = {"attempts": 0, "successes": 0}
    last_global_lb = global_lb_history[-1] if global_lb_history else -math.inf
    termination_reason = "OPEN_NODES"

    def pop_open_node() -> tuple[float, int, int, BBNode]:
        """Pop a node while preserving the best-bound LB invariant.

        Focal search changes only expansion order.  The certified global LB
        is still obtained from the minimum lower bound over *all* open leaves,
        so choosing a focal member can never turn a valid bound into a
        heuristic bound.
        """
        policy = str(getattr(args, "bb_search_policy", DEFAULT_BB_SEARCH_POLICY))
        if policy != "focal" or len(heap) <= 1:
            return heapq.heappop(heap)
        best_lb = min(item[0] for item in heap)
        weight = max(1.0, float(getattr(
            args, "bb_focal_weight", DEFAULT_BB_FOCAL_WEIGHT)))
        threshold = best_lb * weight if best_lb > 0.0 else best_lb + BB_TOLERANCE
        eligible = [
            (index, item) for index, item in enumerate(heap)
            if item[0] <= threshold + BB_TOLERANCE
        ]
        secondary = str(getattr(
            args, "bb_focal_secondary", DEFAULT_BB_FOCAL_SECONDARY))

        def representative_cost(node: BBNode) -> float:
            return sum(float(row.get("physical_cost", "inf"))
                       for row in node.base_paths.values())

        def focal_key(item: tuple[int, tuple[float, int, int, BBNode]]) -> tuple[object, ...]:
            index, (_, _, node_id, node) = item
            conflict_count = node.representative_conflict_count
            cost = representative_cost(node)
            if secondary == "excess_cost":
                # Smaller current representative cost is a deterministic
                # primal-oriented tie-break within the certified focal band.
                secondary_key = (cost, conflict_count)
            elif secondary == "hybrid":
                secondary_key = (conflict_count, cost)
            else:
                secondary_key = (conflict_count, cost)
            return (*secondary_key, node.depth, node_id, index)

        selected_index, _ = min(eligible, key=focal_key)
        item = heap[selected_index]
        heap[selected_index] = heap[-1]
        heap.pop()
        heapq.heapify(heap)
        return item

    def current_global_lb() -> float | None:
        return _bb_open_global_lb(heap, incumbent_ub)

    def append_global_lb() -> float | None:
        nonlocal last_global_lb
        value = current_global_lb()
        if (value is not None and math.isfinite(incumbent_ub) and
                value > incumbent_ub + BB_TOLERANCE):
            raise AssertionError("CBS global lower bound exceeded incumbent UB")
        if (value is not None and math.isfinite(last_global_lb) and
                value < last_global_lb - BB_TOLERANCE):
            raise AssertionError("CBS global lower bound decreased")
        if value is not None:
            last_global_lb = value
            global_lb_history.append(value)
        return value

    def evaluate_conflict(
        node: BBNode, conflicts: list[PhysicalConflict], *, force: bool = False,
    ) -> tuple[PhysicalConflict, float, float, dict[str, str] | None,
               dict[str, str] | None, str]:
        nonlocal both_children_zero_delta_count
        selection = str(getattr(args, "bb_conflict_selection", "earliest"))
        if selection == "earliest" and not force:
            conflict = conflicts[0]
            node.candidate_conflicts_examined = 1
            node.selected_conflict_class = "not_evaluated"
            node.selected_delta_a = math.nan
            node.selected_delta_b = math.nan
            node.selected_conflict_score = math.nan
            conflict_class_counts["not_evaluated"] += 1
            return conflict, math.nan, math.nan, None, None, "not_evaluated"
        limit = max(1, int(getattr(
            args, "bb_strong_branching_candidates", DEFAULT_BB_STRONG_BRANCHING_CANDIDATES)))
        ranked: list[tuple[tuple[object, ...], PhysicalConflict, float, float,
                           dict[str, str] | None, dict[str, str] | None]] = []
        for conflict in conflicts[:limit]:
            before_dp = cache_stats.get("dp_cache_misses", 0)
            before_hits = cache_stats.get("strong_branching_cache_hits", 0)
            delta_a, row_a, hit_a = _bb_eval_forbidden_action(
                node, conflict.action_a.forbidden, executable=executable,
                dataset=dataset, trains=trains, candidates=candidates, mow=mow,
                arcs=arcs, args=args, output=output, candidate_fingerprint=fingerprint,
                physical_fingerprint=physical_fingerprint, dp_cache=dp_cache,
                strong_cache=strong_cache, cache_stats=cache_stats)
            delta_b, row_b, hit_b = _bb_eval_forbidden_action(
                node, conflict.action_b.forbidden, executable=executable,
                dataset=dataset, trains=trains, candidates=candidates, mow=mow,
                arcs=arcs, args=args, output=output, candidate_fingerprint=fingerprint,
                physical_fingerprint=physical_fingerprint, dp_cache=dp_cache,
                strong_cache=strong_cache, cache_stats=cache_stats)
            node.strong_branching_dp_calls += (
                cache_stats.get("dp_cache_misses", 0) - before_dp)
            node.strong_branching_cache_hits += (
                cache_stats.get("strong_branching_cache_hits", 0) - before_hits)
            classification = _bb_classify_deltas(delta_a, delta_b)
            priority = {"non-cardinal": 0, "semi-cardinal": 1,
                        "cardinal": 2}[classification]
            score = min(delta_a, delta_b)
            max_delta = max(delta_a, delta_b)
            rank = (
                priority, score, max_delta, -conflict.time_min,
                conflict.train_a, conflict.train_b, conflict.arc_id,
                conflict.action_a.arc_position, conflict.action_b.arc_position,
            )
            ranked.append((rank, conflict, delta_a, delta_b, row_a, row_b))
        _, conflict, delta_a, delta_b, row_a, row_b = max(
            ranked, key=lambda item: item[0])
        classification = _bb_classify_deltas(delta_a, delta_b)
        node.candidate_conflicts_examined = len(ranked)
        node.selected_conflict_class = classification
        node.selected_delta_a, node.selected_delta_b = delta_a, delta_b
        node.selected_conflict_score = min(delta_a, delta_b)
        conflict_class_counts[classification] += 1
        delta_values.extend((delta_a, delta_b))
        if delta_a <= BB_TOLERANCE and delta_b <= BB_TOLERANCE:
            both_children_zero_delta_count += 1
        return conflict, delta_a, delta_b, row_a, row_b, classification

    def try_bypass(
        node: BBNode, conflicts: list[PhysicalConflict],
        conflict: PhysicalConflict, delta_a: float, delta_b: float,
        row_a: dict[str, str] | None, row_b: dict[str, str] | None,
        classification: str,
    ) -> bool:
        if not getattr(args, "bb_enable_bypass", False):
            return False
        if classification not in {"non-cardinal", "semi-cardinal"}:
            return False
        max_bypass = max(0, int(getattr(
            args, "bb_max_bypass_per_node", DEFAULT_BB_MAX_BYPASS_PER_NODE)))
        if node.bypass_attempts >= max_bypass:
            return False
        choices = [
            (conflict.action_a.train_id, conflict.action_a, delta_a, row_a),
            (conflict.action_b.train_id, conflict.action_b, delta_b, row_b),
        ]
        for train_id, action, delta, alternate in choices:
            if node.bypass_attempts >= max_bypass:
                break
            if delta > BB_TOLERANCE or alternate is None:
                continue
            node.bypass_attempts += 1
            bypass_totals["attempts"] += 1
            before_count = len(conflicts)
            node.conflicts_before_bypass = before_count
            old_row = node.base_paths[train_id]
            old_signature = _bb_row_signature(train_id, old_row)
            new_signature = _bb_row_signature(train_id, alternate)
            node.old_path_signature = old_signature
            node.new_path_signature = new_signature
            if new_signature in node.representative_trajectory_signatures:
                node.conflicts_after_bypass = before_count
                continue
            if not _bb_row_in_domain(
                    train_id, alternate,
                    forbidden_actions=node.forbidden_actions,
                    required_actions=node.required_actions, candidates=candidates,
                    time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
                    wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP)):
                node.conflicts_after_bypass = before_count
                continue
            old_cost = float(old_row.get("physical_cost", "inf"))
            new_cost = float(alternate.get("physical_cost", "inf"))
            if abs(new_cost - old_cost) > BB_TOLERANCE:
                node.conflicts_after_bypass = before_count
                continue
            replacement = {
                key: dict(value) for key, value in node.base_paths.items()
            }
            replacement[train_id] = dict(alternate)
            after = all_physical_conflicts(
                replacement, trains, candidates,
                safety_headway_minutes=args.safety_headway_minutes,
                time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
                wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
            )
            node.conflicts_after_bypass = len(after)
            if len(after) >= before_count:
                continue
            original_base = node.base_lower_bound
            node.base_paths = replacement
            node.relaxed_paths = {
                key: dict(value) for key, value in node.relaxed_paths.items()
            }
            node.relaxed_paths[train_id] = dict(alternate)
            if abs(_bb_physical_cost(node.base_paths) - original_base) > BB_TOLERANCE:
                raise AssertionError("ICBS bypass changed the node BaseLB")
            node.bypass_successes += 1
            bypass_totals["successes"] += 1
            node.bypass_train_id = train_id
            node.representative_trajectory_signatures.append(new_signature)
            node.representative_conflict_count = len(after)
            # The domain did not change.  Existing pair cuts remain valid; a
            # later node solve may add a stronger cut from the new witness.
            return True
        return False

    # The initial open-leaf bound is part of the monotonicity audit.
    append_global_lb()
    while heap:
        if time.monotonic() - started >= args.bb_time_limit_sec:
            termination_reason = "TIME_LIMIT"
            break
        global_lb = append_global_lb()
        if (global_lb is not None and math.isfinite(incumbent_ub) and
                ((args.bb_relative_gap > 0 and
                  (_bb_gap(global_lb, incumbent_ub)[0] or math.inf) <= args.bb_relative_gap) or
                 (args.bb_absolute_gap > 0 and
                  incumbent_ub - global_lb <= args.bb_absolute_gap + BB_TOLERANCE))):
            termination_reason = "GAP_REACHED"
            break
        if nodes_expanded >= args.bb_max_nodes:
            termination_reason = "NODE_LIMIT"
            break

        _, _, _, node = pop_open_node()
        if node.status == "INFEASIBLE":
            nodes_pruned_infeasible += 1
            node_log.append(_bb_node_log_row(
                node, incumbent_ub=incumbent_ub, global_lb=global_lb,
                prune_reason="infeasible-train",
                candidate_fingerprint=fingerprint))
            continue
        if math.isfinite(incumbent_ub) and node.lower_bound >= incumbent_ub - BB_TOLERANCE:
            nodes_pruned_bound += 1
            node.status = "PRUNED_BOUND"
            node_log.append(_bb_node_log_row(
                node, incumbent_ub=incumbent_ub, global_lb=current_global_lb(),
                prune_reason="node-lb>=incumbent-ub",
                candidate_fingerprint=fingerprint))
            continue

        nodes_expanded += 1
        if not node.base_paths:
            node, recovered, recovered_validation = solve_node(
                node, node.best_lambda, args.bb_node_dual_iterations)
            accept_recovered(recovered, recovered_validation)
            if node.status == "INFEASIBLE":
                nodes_pruned_infeasible += 1
                node_log.append(_bb_node_log_row(
                    node, incumbent_ub=incumbent_ub, global_lb=current_global_lb(),
                    prune_reason="infeasible-train",
                    candidate_fingerprint=fingerprint))
                append_global_lb()
                continue

        if not node.representative_trajectory_signatures:
            node.representative_trajectory_signatures = [
                f"{train_id}:{_bb_row_signature(train_id, row)}"
                for train_id, row in sorted(node.base_paths.items())
            ]

        # Bypass can update only the representative, never the domain.  Keep
        # re-detecting conflicts until no safe strict reduction remains.
        selected_info = None
        while True:
            conflicts = all_physical_conflicts(
                node.base_paths, trains, candidates,
                safety_headway_minutes=args.safety_headway_minutes,
                time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
                wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
            )
            node.representative_conflict_count = len(conflicts)
            if not conflicts:
                validation = validate_physical_schedule(
                    node.base_paths, trains, candidates, arcs, mow,
                    horizon_minutes=args.horizon,
                    safety_headway_minutes=args.safety_headway_minutes,
                    time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
                    wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
                )
                if validation["status"] != "PASS":
                    node.status = "FAILED_VALIDATION"
                    node_log.append(_bb_node_log_row(
                        node, incumbent_ub=incumbent_ub, global_lb=current_global_lb(),
                        prune_reason="base-path-validation-failed",
                        candidate_fingerprint=fingerprint))
                else:
                    accept_recovered(node.base_paths, validation)
                    nodes_fathomed_feasible += 1
                    node.status = "FATHOMED_FEASIBLE"
                    node_log.append(_bb_node_log_row(
                        node, incumbent_ub=incumbent_ub, global_lb=current_global_lb(),
                        prune_reason="conflict-free-base-paths",
                        candidate_fingerprint=fingerprint))
                append_global_lb()
                selected_info = None
                break

            force_selection = (
                str(getattr(args, "bb_conflict_selection", "earliest")) == "cardinal" or
                str(getattr(args, "bb_splitting", DEFAULT_BB_SPLITTING)) == "disjoint")
            selected_info = evaluate_conflict(node, conflicts, force=force_selection)
            conflict, delta_a, delta_b, row_a, row_b, classification = selected_info
            if try_bypass(node, conflicts, conflict, delta_a, delta_b,
                          row_a, row_b, classification):
                continue
            break

        if selected_info is None:
            continue
        conflict, delta_a, delta_b, row_a, row_b, classification = selected_info
        node.conflict = conflict
        node.branch_conflict_type = conflict.conflict_type
        node.branch_conflict_arc = conflict.arc_id
        node.branch_conflict_time = conflict.time_min
        conflict_log.append(_bb_conflict_dict(conflict) | {
            "node_id": node.node_id, "depth": node.depth,
            "selected_conflict_class": classification,
            "selected_delta_a": delta_a, "selected_delta_b": delta_b,
            "selected_conflict_score": node.selected_conflict_score,
            "candidate_conflicts_examined": node.candidate_conflicts_examined,
            "strong_branching_dp_calls": node.strong_branching_dp_calls,
            "strong_branching_cache_hits": node.strong_branching_cache_hits,
        })
        selected_conflict_class_counts[classification] += 1
        node.status = "BRANCHED"

        branch_specs: list[tuple[frozenset[ForbiddenAction],
                                 frozenset[RequiredAction], ActionSignature, str]]
        splitting = str(getattr(args, "bb_splitting", DEFAULT_BB_SPLITTING))
        if splitting == "disjoint":
            # Evaluate both possible pivots using exact lambda=0 child deltas.
            pivot_choices: list[tuple[tuple[object, ...], ActionSignature,
                                      ActionSignature, float, float]] = []
            for pivot, other, negative_delta in (
                    (conflict.action_a, conflict.action_b, delta_a),
                    (conflict.action_b, conflict.action_a, delta_b)):
                before_dp = cache_stats.get("dp_cache_misses", 0)
                before_hits = cache_stats.get("strong_branching_cache_hits", 0)
                positive_delta, positive_hit = _bb_eval_required_variant(
                    node, pivot, other, executable=executable, dataset=dataset,
                    trains=trains, candidates=candidates, mow=mow, arcs=arcs,
                    args=args, output=output, candidate_fingerprint=fingerprint,
                    physical_fingerprint=physical_fingerprint, dp_cache=dp_cache,
                    cache_stats=cache_stats, variant_cache=variant_cache)
                node.strong_branching_dp_calls += (
                    cache_stats.get("dp_cache_misses", 0) - before_dp)
                node.strong_branching_cache_hits += (
                    cache_stats.get("strong_branching_cache_hits", 0) - before_hits)
                pivot_score = min(negative_delta, positive_delta)
                pivot_choices.append((
                    (pivot_score, max(negative_delta, positive_delta),
                     -conflict.time_min, pivot.train_id, pivot.route_index,
                     pivot.arc_position, pivot.start_cell, pivot.wait_tick),
                    pivot, other, negative_delta, positive_delta))
            _, pivot, other, negative_delta, positive_delta = max(
                pivot_choices, key=lambda item: item[0])
            node.selected_conflict_score = min(delta_a, delta_b)
            propagated_forbidden: frozenset[ForbiddenAction] = frozenset()
            propagation_infeasible = False
            if getattr(args, "bb_enable_required_action_propagation", False):
                (propagated_forbidden, propagation_infeasible,
                 propagation_stats) = propagate_required_action_conflicts(
                    pivot, node.base_paths, trains, candidates,
                    safety_headway_minutes=args.safety_headway_minutes,
                    time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
                    wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
                    existing_required=node.required_actions)
                node.required_residual_movements += propagation_stats[
                    "required_residual_movements"]
                node.labels_pruned_required_headway += propagation_stats[
                    "labels_pruned_required_headway"]
                node.labels_pruned_required_opposing += propagation_stats[
                    "labels_pruned_required_opposing"]
                node.labels_pruned_required_special += propagation_stats[
                    "labels_pruned_required_special"]
                node.required_action_infeasibility += propagation_stats[
                    "required_action_infeasibility"]
            branch_specs = [
                (frozenset(set(node.forbidden_actions) | {pivot.forbidden}),
                 node.required_actions, pivot, "disjoint-negative"),
                (frozenset(set(node.forbidden_actions) | {other.forbidden} |
                            set(propagated_forbidden) |
                            ({other.forbidden} if propagation_infeasible else set())),
                 frozenset(set(node.required_actions) | {pivot.required}),
                 pivot, "disjoint-positive"),
            ]
        else:
            branch_specs = [
                (frozenset(set(node.forbidden_actions) | {conflict.action_a.forbidden}),
                 node.required_actions, conflict.action_a, "standard-negative"),
                (frozenset(set(node.forbidden_actions) | {conflict.action_b.forbidden}),
                 node.required_actions, conflict.action_b, "standard-negative"),
            ]

        parent_key = bb_constraint_key(
            node.forbidden_actions, node.required_actions,
            candidate_fingerprint=fingerprint)
        for raw_forbidden, raw_required, action, branch_kind in branch_specs:
            if branch_kind == "disjoint-positive":
                required_branches += 1
            else:
                forbidden_branches += 1
            child_forbidden, child_required, infeasible, normalization = (
                normalize_bb_constraints(raw_forbidden, raw_required))
            child_key = bb_constraint_key(
                child_forbidden, child_required, candidate_fingerprint=fingerprint)
            source_record = {
                "node_id": next_node_id, "parent_id": node.node_id,
                "branch_kind": branch_kind,
            }
            if child_key in seen_constraints:
                duplicate_nodes_skipped += 1
                prior = seen_sources.get(child_key, [])
                if child_key == parent_key:
                    cause = "required_forbidden_redundancy"
                elif branch_kind == "standard-negative" and any(
                        item.get("parent_id") == node.node_id for item in prior):
                    cause = "standard_splitting_child_overlap"
                elif prior:
                    cause = "same_constraint_set_reached_in_different_branch_order"
                else:
                    cause = "constraint_canonicalization_failure"
                duplicate_causes[cause] += 1
                duplicate_attempts.append({
                    "constraint_key": hashlib.sha256(repr(child_key).encode()).hexdigest(),
                    "attempt": source_record, "existing_paths": prior,
                    "cause": cause,
                })
                continue
            seen_constraints.add(child_key)
            seen_sources[child_key].append(source_record)
            child = BBNode(
                node_id=next_node_id, parent_id=node.node_id,
                depth=node.depth + 1, forbidden_actions=child_forbidden,
                required_actions=child_required,
                inherited_lower_bound=node.lower_bound,
                lower_bound=node.lower_bound, branch_action=action,
                branch_conflict_type=conflict.conflict_type,
                branch_conflict_arc=conflict.arc_id,
                branch_conflict_time=conflict.time_min,
                branch_kind=branch_kind,
            )
            next_node_id += 1
            nodes_created += 1
            maximum_depth = max(maximum_depth, child.depth)
            child, recovered, recovered_validation = solve_node(
                child, node.best_lambda, args.bb_node_dual_iterations)
            accept_recovered(recovered, recovered_validation)
            if child.status == "INFEASIBLE":
                nodes_pruned_infeasible += 1
                node_log.append(_bb_node_log_row(
                    child, incumbent_ub=incumbent_ub, global_lb=current_global_lb(),
                    prune_reason="infeasible-train",
                    candidate_fingerprint=fingerprint))
            elif math.isfinite(incumbent_ub) and child.lower_bound >= incumbent_ub - BB_TOLERANCE:
                nodes_pruned_bound += 1
                child.status = "PRUNED_BOUND"
                node_log.append(_bb_node_log_row(
                    child, incumbent_ub=incumbent_ub, global_lb=current_global_lb(),
                    prune_reason="node-lb>=incumbent-ub",
                    candidate_fingerprint=fingerprint))
            else:
                heapq.heappush(heap, (child.lower_bound, child.depth, child.node_id, child))

        node_log.append(_bb_node_log_row(
            node, incumbent_ub=incumbent_ub, global_lb=append_global_lb(),
            prune_reason="conflict-branched",
            candidate_fingerprint=fingerprint))
        if (getattr(args, "bb_checkpoint_interval", 0) > 0 and
                nodes_expanded % args.bb_checkpoint_interval == 0):
            _write_bb_checkpoint(
                checkpoint_path, fingerprint=fingerprint,
                open_nodes=[item[3] for item in heap],
                incumbent_ub=incumbent_ub, incumbent_routes=incumbent_routes,
                next_node_id=next_node_id, global_lb_history=global_lb_history,
                termination_reason="CHECKPOINT", candidate_count=candidate_count,
                physical_fingerprint=physical_fingerprint,
                root_bound_certificate_path=(
                    Path(args.bb_root_bound_certificate).resolve()
                    if getattr(args, "bb_root_bound_certificate", None) else None))

    if not heap and termination_reason == "OPEN_NODES":
        termination_reason = "PROVEN_OPTIMAL" if math.isfinite(incumbent_ub) else "INFEASIBLE"
    final_global_lb = current_global_lb()
    if termination_reason == "PROVEN_OPTIMAL" and math.isfinite(incumbent_ub):
        final_global_lb = incumbent_ub
    if final_global_lb is not None and math.isfinite(final_global_lb):
        if not global_lb_history or abs(global_lb_history[-1] - final_global_lb) > EPS:
            append_global_lb()
            final_global_lb = current_global_lb()
    relative_ub, relative_lb = _bb_gap(
        final_global_lb if final_global_lb is not None else math.inf, incumbent_ub)

    if incumbent_routes:
        _write_routes_file(output / "bb_incumbent_routes.tsv", incumbent_routes)
    else:
        _write_routes_file(output / "bb_incumbent_routes.tsv", {})
    node_fields = [
        "node_id", "parent_id", "depth", "status", "branch_train_id",
        "branch_route_index", "branch_arc_position", "branch_start_cell",
        "branch_wait_tick", "base_lower_bound", "dual_lower_bound",
        "inherited_lower_bound", "node_lower_bound", "incumbent_ub_at_solve",
        "global_lb_after_processing", "absolute_gap", "relative_gap_ub_normalized",
        "relative_gap_lb_normalized", "dual_iterations", "conflict_count",
        "selected_conflict_type", "selected_conflict_arc", "selected_conflict_time",
        "candidate_conflicts_examined", "selected_conflict_class",
        "selected_delta_a", "selected_delta_b", "selected_conflict_score",
        "strong_branching_dp_calls", "strong_branching_cache_hits",
        "bypass_attempts", "bypass_successes", "conflicts_before_bypass",
        "conflicts_after_bypass", "bypass_train_id", "old_path_signature",
        "new_path_signature", "pair_lower_bound", "pair_bound_exact",
        "required_residual_movements", "labels_pruned_required_headway",
        "labels_pruned_required_opposing", "labels_pruned_required_special",
        "required_action_infeasibility",
        "pair_matching_edges", "required_action_count", "forbidden_action_count",
        "branch_kind", "prune_reason", "constraint_schema_version",
        "candidate_fingerprint", "constraint_set_hash", "constraint_set_json",
        "runtime_sec",
    ]
    with (output / "bb_nodes.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=node_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(node_log)
    conflict_fields = [
        "node_id", "depth", "conflict_type", "arc_id", "time_min",
        "train_a", "train_b", "action_a", "action_b",
        "selected_conflict_class", "selected_delta_a", "selected_delta_b",
        "selected_conflict_score", "candidate_conflicts_examined",
        "strong_branching_dp_calls", "strong_branching_cache_hits",
    ]
    with (output / "bb_conflicts.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=conflict_fields, extrasaction="ignore")
        writer.writeheader()
        for row in conflict_log:
            writer.writerow({
                **row, "action_a": json.dumps(row["action_a"], sort_keys=True),
                "action_b": json.dumps(row["action_b"], sort_keys=True),
            })
    _write_bb_checkpoint(
        checkpoint_path, fingerprint=fingerprint,
        open_nodes=[item[3] for item in heap], incumbent_ub=incumbent_ub,
        incumbent_routes=incumbent_routes, next_node_id=next_node_id,
        global_lb_history=global_lb_history, termination_reason=termination_reason,
        candidate_count=candidate_count, physical_fingerprint=physical_fingerprint,
        root_bound_certificate_path=(
            Path(args.bb_root_bound_certificate).resolve()
            if getattr(args, "bb_root_bound_certificate", None) else None))

    min_open_lb = min((item[0] for item in heap), default=(
        incumbent_ub if math.isfinite(incumbent_ub) else math.nan))
    max_open_lb = max((item[0] for item in heap), default=(
        incumbent_ub if math.isfinite(incumbent_ub) else math.nan))
    global_matches_open = (
        final_global_lb is not None and math.isfinite(float(min_open_lb)) and
        abs(float(final_global_lb) - float(min_open_lb)) <= BB_TOLERANCE)
    status = {
        "PROVEN_OPTIMAL": "PROVEN_OPTIMAL", "GAP_REACHED": "GAP_REACHED",
        "TIME_LIMIT": "TIME_LIMIT", "NODE_LIMIT": "NODE_LIMIT",
        "INFEASIBLE": "INFEASIBLE",
    }.get(termination_reason, "DIAGNOSTIC_ONLY")
    zero_fraction = (
        sum(value <= BB_TOLERANCE for value in delta_values) / len(delta_values)
        if delta_values else 0.0)
    initial_global = global_lb_history[0] if global_lb_history else None
    improvement_per_100 = (
        (float(final_global_lb) - float(initial_global)) * 100.0 / nodes_expanded
        if initial_global is not None and final_global_lb is not None and nodes_expanded
        else 0.0)
    duplicate_path_report = [
        {
            "constraint_key": hashlib.sha256(repr(key).encode()).hexdigest(),
            "attempts": attempts,
        }
        for key, attempts in seen_sources.items()
        if len(attempts) > 1
    ]
    # Add duplicate attempts that never acquired a second source entry.
    duplicate_path_report.extend({
        "constraint_key": item["constraint_key"],
        "attempts": [item["attempt"], *item["existing_paths"]],
        "cause": item["cause"],
    } for item in duplicate_attempts if not any(
        item["constraint_key"] == report["constraint_key"]
        for report in duplicate_path_report))
    root_certificate_values = [
        float(value) for value in (root_base_lb, root_dual_lb, root_pair_lb)
        if value is not None and math.isfinite(float(value))
    ]
    if root_certificate_lb > 0.0:
        root_certificate_values.append(root_certificate_lb)
    certified_root_lb = max(root_certificate_values, default=0.0)
    # Keep root preprocessing and subsequent tree progress separate.  The
    # former is obtained before any CBS expansion; only the latter is a
    # per-expanded-node search metric.  In particular, do not divide a root
    # certificate uplift by the number of tree nodes.
    root_preprocessing_uplift = (
        float(certified_root_lb) - float(root_base_lb)
        if root_base_lb is not None and math.isfinite(float(root_base_lb))
        and math.isfinite(float(certified_root_lb)) else None)
    post_root_tree_uplift = (
        float(final_global_lb) - float(initial_global)
        if initial_global is not None and final_global_lb is not None
        and math.isfinite(float(initial_global))
        and math.isfinite(float(final_global_lb)) else None)
    # A resumed/inherited root certificate may be stronger than the current
    # run's time-limited root pair recomputation.  Keep both values explicit:
    # the current-run diagnostic must not be mistaken for the formal pair
    # certificate that supplies the inherited floor.
    inherited_pair_lb = (
        float(root_certificate.get("root_pair_lb"))
        if root_certificate is not None and
        root_certificate.get("root_pair_lb") is not None else None)
    inherited_matching = (
        [tuple(item) for item in root_certificate.get(
            "selected_disjoint_matching", [])]
        if root_certificate is not None else [])
    certified_pair_lb = max(
        [value for value in (root_pair_lb, inherited_pair_lb)
         if value is not None and math.isfinite(float(value))],
        default=None)
    certified_pair_matching = (
        inherited_matching if inherited_pair_lb is not None and
        (root_pair_lb is None or inherited_pair_lb >= root_pair_lb - BB_TOLERANCE)
        else list(root_pair_matching_edges))
    certificate_payload = {
        "certificate_version": BB_ROOT_CERTIFICATE_VERSION,
        "candidate_fingerprint": fingerprint,
        "candidate_count": candidate_count,
        "physical_model_fingerprint": physical_fingerprint,
        "time_discretization_fingerprint": time_fingerprint,
        "root_domain_constraint_fingerprint": root_domain_constraint_fingerprint(fingerprint),
        "root_base_lb": root_base_lb,
        "root_dual_lb": root_dual_lb,
        "root_pair_lb": certified_pair_lb,
        "current_run_root_pair_lb": root_pair_lb,
        "inherited_root_pair_lb": inherited_pair_lb,
        "root_action_dual_lb": root_action_dual_lb,
        "root_group_lb": root_group_lb,
        "root_pair_bound_exact": root_pair_bound_exact,
        "root_dual_history": root_dual_history,
        "pair_group_subproblem_certificates": (
            root_certificate.get("pair_group_subproblem_certificates", [])
            if root_certificate else [
                {"train_ids": [left, right], "certified_lb": weight,
                 "exact": root_pair_bound_exact}
                for left, right, weight in root_pair_matching_edges
            ]),
        "selected_disjoint_matching": [list(edge) for edge in certified_pair_matching],
        "current_run_selected_disjoint_matching": [
            list(edge) for edge in root_pair_matching_edges],
        "inherited_root_certificate_source": (
            root_certificate.get("creation_run") if root_certificate else None),
        "certified_root_lb": certified_root_lb,
        "creation_run": str(output),
        "validation_status": "PASS" if (
            math.isfinite(certified_root_lb) and
            certified_root_lb >= -BB_TOLERANCE and
            certified_root_lb <= incumbent_ub + BB_TOLERANCE
        ) else "FAIL",
    }
    root_certificate_output = output / "bb_root_bound_certificate.json"
    root_certificate_output.write_text(
        json.dumps(certificate_payload, indent=2, sort_keys=True), encoding="utf-8")
    summary = {
        "status": status,
        "certified": True,
        "certificate_scope": "fixed 908-route PATH-K candidate universe + discretized-time DP model",
        "dataset": str(dataset), "train_count": len(trains), "path_k": args.path_k,
        "candidate_count": candidate_count, "candidate_fingerprint": fingerprint,
        "candidate_universe_expanded_before_root": candidate_expanded,
        "candidate_universe_immutable": True,
        "physical_model_fingerprint": physical_fingerprint,
        "time_discretization_fingerprint": time_fingerprint,
        "constraint_schema_version": BB_CONSTRAINT_SCHEMA_VERSION,
        "time_step": getattr(args, "time_step", DEFAULT_TIME_STEP),
        "departure_step": getattr(args, "departure_step", DEFAULT_DEPARTURE_STEP),
        "wait_step": getattr(args, "wait_step", DEFAULT_WAIT_STEP),
        "horizon": args.horizon, "capacity_model": capacity_model,
        "capacity_map_complete": capacity_complete,
        "capacity_map_entries": len(capacity_map),
        "capacity_map_audit": capacity_audit,
        "capacity_relaxation_scope": dual_note,
        "root_base_LB": root_base_lb, "root_best_dual_LB": root_dual_lb,
        "root_pair_LB": root_pair_lb,
        "certified_root_pair_LB": certified_pair_lb,
        "root_action_dual_LB": root_action_dual_lb,
        "root_group_LB": root_group_lb,
        "root_pair_bound_exact": root_pair_bound_exact,
        "root_pair_matching_edges": [list(edge) for edge in root_pair_matching_edges],
        "certified_root_pair_matching_edges": [
            list(edge) for edge in certified_pair_matching],
        "root_bound_certificate": str(root_certificate_output.resolve()),
        "inherited_root_certificate_LB": root_certificate_lb,
        "certified_root_LB": certified_root_lb,
        "root_dual_history": root_dual_history,
        "root_dual_max_capacity_violation": max(
            (float(row.get("max_gradient", 0.0)) for row in root_dual_history),
            default=0.0),
        "initial_global_LB": initial_global,
        "final_global_LB": final_global_lb,
        "minimum_open_node_LB": min_open_lb,
        "maximum_open_node_LB": max_open_lb,
        "global_LB_equals_minimum_open_leaf_LB": global_matches_open,
        "initial_UB": initial_ub if math.isfinite(initial_ub) else None,
        "best_UB": incumbent_ub if math.isfinite(incumbent_ub) else None,
        "absolute_gap": (max(0.0, incumbent_ub - final_global_lb)
                         if math.isfinite(incumbent_ub) and final_global_lb is not None else None),
        "relative_gap_ub_normalized": relative_ub,
        "relative_gap_lb_normalized": relative_lb,
        "nodes_created": nodes_created, "nodes_expanded": nodes_expanded,
        "nodes_open": len(heap), "nodes_pruned_bound": nodes_pruned_bound,
        "nodes_pruned_infeasible": nodes_pruned_infeasible,
        "nodes_fathomed_feasible": nodes_fathomed_feasible,
        "duplicate_nodes_skipped": duplicate_nodes_skipped,
        "duplicate_constraint_set_count": len(duplicate_attempts),
        "duplicate_generation_paths": duplicate_path_report,
        "duplicate_causes": duplicate_causes,
        "maximum_depth": maximum_depth,
        "zero_delta_child_count": sum(value <= BB_TOLERANCE for value in delta_values),
        "zero_delta_child_fraction": zero_fraction,
        "both_children_zero_delta_count": both_children_zero_delta_count,
        "conflict_class_counts": selected_conflict_class_counts,
        "conflict_class_counts_all_examined": conflict_class_counts,
        "bypass_attempts": bypass_totals["attempts"],
        "bypass_successes": bypass_totals["successes"],
        "required_branches": required_branches,
        "forbidden_branches": forbidden_branches,
        "required_action_propagation": {
            "enabled": bool(getattr(args, "bb_enable_required_action_propagation", False)),
            "residual_movements": sum(int(row.get(
                "required_residual_movements", 0) or 0) for row in node_log),
            "labels_pruned_required_headway": sum(int(row.get(
                "labels_pruned_required_headway", 0) or 0) for row in node_log),
            "labels_pruned_required_opposing": sum(int(row.get(
                "labels_pruned_required_opposing", 0) or 0) for row in node_log),
            "labels_pruned_required_special": sum(int(row.get(
                "labels_pruned_required_special", 0) or 0) for row in node_log),
            "required_action_infeasibility": sum(int(row.get(
                "required_action_infeasibility", 0) or 0) for row in node_log),
        },
        "search_policy": getattr(args, "bb_search_policy", DEFAULT_BB_SEARCH_POLICY),
        "focal_weight": getattr(args, "bb_focal_weight", DEFAULT_BB_FOCAL_WEIGHT),
        "focal_secondary": getattr(args, "bb_focal_secondary", DEFAULT_BB_FOCAL_SECONDARY),
        "pair_subproblems_solved": cache_stats.get("pair_subproblems_solved", 0),
        "cache_statistics": cache_stats,
        "LB_improvement_per_100_expanded_nodes": improvement_per_100,
        "root_preprocessing_uplift": root_preprocessing_uplift,
        "post_root_tree_uplift": post_root_tree_uplift,
        "post_root_tree_uplift_per_100_expanded_nodes": improvement_per_100,
        # Backward-compatible names.  The per-100 root field is intentionally
        # null: root preprocessing is not tree progress.
        "LB_improvement_over_root_base": (
            float(final_global_lb) - float(root_base_lb)
            if root_base_lb is not None and final_global_lb is not None
            and math.isfinite(float(root_base_lb))
            and math.isfinite(float(final_global_lb)) else None),
        "LB_improvement_over_root_base_per_100_expanded_nodes": None,
        "runtime_sec": time.monotonic() - started,
        "termination_reason": termination_reason,
        "incumbent_physically_validated": (
            bool(incumbent_routes) and incumbent_validation.get("status") == "PASS"),
        "incumbent_validation_status": incumbent_validation.get("status", "UNKNOWN"),
        "incumbent_validation": _bb_serializable_validation(incumbent_validation),
        "cli_configuration": {
            key: (str(value) if isinstance(value, Path) else value)
            for key, value in vars(args).items()
        },
        "dual_certified": capacity_certified,
        "pairwise_bound_certified": True,
        "global_lb_monotone": all(
            right + BB_TOLERANCE >= left
            for left, right in zip(global_lb_history, global_lb_history[1:])
        ),
        "global_lb_history_length": len(global_lb_history),
        "output_files": [str((output / name).resolve()) for name in (
            "bb_summary.json", "bb_nodes.csv", "bb_conflicts.csv",
            "bb_incumbent_routes.tsv", "bb_open_nodes_checkpoint.json",
            "bb_root_bound_certificate.json")],
    }
    (output / "bb_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def solve_conflict_bb(args: argparse.Namespace) -> dict[str, object]:
    """Run fixed-domain CBS/B&B with exact inner DP and optional LR duals."""
    started = time.monotonic()
    dataset = Path(args.dataset).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).with_name("path_k_dp.cpp")
    executable = (Path(args.cpp_exe).resolve() if args.cpp_exe
                  else Path(__file__).with_name("build") / "path_k_dp")
    executable = ensure_cpp(source, executable)
    arcs, trains, mow = load_dataset(dataset)
    graph = build_graph(arcs)
    candidates = all_candidates(trains, graph, arcs, args.path_k)
    capacity_model = str(getattr(args, "lr_capacity_model", DEFAULT_LR_CAPACITY_MODEL))
    capacity_map_path = getattr(args, "lr_capacity_map", None)
    capacity_map = load_lr_capacity_map(
        Path(capacity_map_path).resolve() if capacity_map_path else None)
    # A supplied capacity map is tied to the candidate universe for which it
    # was audited.  Do not silently add siding routes after reading it.
    candidate_expanded = False if capacity_map else _prepare_bb_candidates(
        trains, graph, arcs, candidates)
    fingerprint = candidate_universe_fingerprint(candidates)
    candidate_count = sum(len(values) for values in candidates.values())
    capacity_complete = capacity_model == "headway-relaxation" and bool(capacity_map)
    capacity_certified = capacity_complete
    dual_note = (
        "certified headway-relaxation capacity map"
        if capacity_certified else
        "dual disabled for certificate; using λ=0 CBS node bound because "
        "block30 C_ref=1 is a diagnostic aggregate reference term, not a "
        "proven physical relaxation RHS for continuous headway"
    )

    incumbent_ub = math.inf
    incumbent_routes: dict[str, dict[str, str]] = {}
    incumbent_validation: dict[str, object] = {
        "status": "NOT_ATTEMPTED", "conflicts": []}
    initial_ub = math.inf
    if getattr(args, "bb_incumbent_tsv", None):
        initial_ub, incumbent_routes, incumbent_validation = _bb_load_incumbent(
            Path(args.bb_incumbent_tsv).resolve(), trains, candidates, arcs, mow, args)
        incumbent_ub = initial_ub

    checkpoint_path = (
        Path(args.bb_resume_checkpoint).resolve()
        if getattr(args, "bb_resume_checkpoint", None)
        else output / "bb_open_nodes_checkpoint.json"
    )
    restored = _bb_read_checkpoint(
        checkpoint_path, fingerprint) if getattr(args, "bb_resume_checkpoint", None) else None
    global_lb_history: list[float] = list(restored.get("global_lb_history", [])) if restored else []
    node_log: list[dict[str, object]] = []
    conflict_log: list[dict[str, object]] = []
    heap: list[tuple[float, int, int, BBNode]] = []
    next_node_id = int(restored.get("next_node_id", 0)) if restored else 0
    root_base_lb: float | None = None
    root_dual_lb: float | None = None
    root_dual_history: list[dict[str, object]] = []
    if restored:
        restored_routes = {
            str(tid): dict(row)
            for tid, row in dict(restored.get("incumbent_routes", {})).items()
        }
        if restored_routes:
            restored_validation = validate_physical_schedule(
                restored_routes, trains, candidates, arcs, mow,
                horizon_minutes=args.horizon,
                safety_headway_minutes=args.safety_headway_minutes,
                time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
                wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
            )
            if restored_validation["status"] != "PASS":
                raise ValueError(
                    "checkpoint incumbent failed physical validation: "
                    f"{restored_validation}")
            restored_cost = _bb_physical_cost(restored_routes)
            if not incumbent_routes or restored_cost < incumbent_ub - EPS:
                incumbent_ub = restored_cost
                incumbent_routes = restored_routes
                incumbent_validation = restored_validation
        elif math.isfinite(float(restored.get("incumbent_ub", math.inf))):
            raise ValueError(
                "checkpoint contains a finite incumbent UB but no incumbent routes")
        for spec in restored.get("open_nodes", []):
            node = _bb_restore_node(spec)
            heapq.heappush(heap, (node.lower_bound, node.depth, node.node_id, node))
        root_candidates = [node for _, _, _, node in heap if node.depth == 0]
        if root_candidates:
            root_base_lb = root_candidates[0].base_lower_bound
            root_dual_lb = root_candidates[0].dual_lower_bound
            root_dual_history = list(root_candidates[0].dual_history)
    else:
        root = BBNode(
            node_id=next_node_id, parent_id=None, depth=0,
            forbidden_actions=frozenset(), inherited_lower_bound=0.0,
            lower_bound=0.0,
        )
        next_node_id += 1
        root, recovered, recovered_validation = _bb_solve_node(
            root, executable=executable, dataset=dataset, trains=trains,
            candidates=candidates, lambdas={}, mow=mow, arcs=arcs, args=args,
            output=output, capacity_certified=capacity_certified,
            capacity_model=capacity_model, capacity_map=capacity_map,
            incumbent_ub=incumbent_ub,
            dual_iterations=args.bb_root_dual_iterations,
        )
        root_base_lb, root_dual_lb = root.base_lower_bound, root.dual_lower_bound
        root_dual_history = list(root.dual_history)
        if recovered is not None:
            incumbent_validation = recovered_validation
            candidate_cost = _bb_physical_cost(recovered)
            if candidate_cost < incumbent_ub - EPS:
                incumbent_ub, incumbent_routes = candidate_cost, recovered
            elif not incumbent_routes:
                incumbent_ub, incumbent_routes = candidate_cost, recovered
        if root.status == "INFEASIBLE":
            node_log.append(_bb_node_log_row(
                root, incumbent_ub=incumbent_ub, global_lb=None,
                prune_reason="infeasible-train"))
        else:
            heapq.heappush(heap, (root.lower_bound, root.depth, root.node_id, root))

    nodes_created = 1 if not restored else len(heap)
    nodes_expanded = 0
    nodes_pruned_bound = 0
    nodes_pruned_infeasible = 0
    nodes_fathomed_feasible = 0
    duplicate_nodes_skipped = 0
    maximum_depth = max((item[1] for item in heap), default=0)
    seen_constraints = {
        tuple(sorted(node.forbidden_actions)) for _, _, _, node in heap
    }
    strong_cache: dict[tuple[ForbiddenAction, ...], float] = {}
    last_global_lb = global_lb_history[-1] if global_lb_history else -math.inf
    termination_reason = "OPEN_NODES"

    def current_global_lb() -> float | None:
        return _bb_open_global_lb(heap, incumbent_ub)

    def append_global_lb() -> float | None:
        nonlocal last_global_lb
        value = current_global_lb()
        if (value is not None and math.isfinite(incumbent_ub) and
                value > incumbent_ub + BB_TOLERANCE):
            raise AssertionError("CBS global lower bound exceeded incumbent UB")
        if value is not None and math.isfinite(last_global_lb) and value < last_global_lb - BB_TOLERANCE:
            raise AssertionError("CBS global lower bound decreased")
        if value is not None:
            last_global_lb = value
            global_lb_history.append(value)
        return value

    def strong_delta(
        action: ForbiddenAction, train_id: str,
        parent_actions: frozenset[ForbiddenAction],
    ) -> float:
        key = tuple(sorted(set(parent_actions) | {action}))
        if key in strong_cache:
            return strong_cache[key]
        rows = run_cpp_batch(
            executable, dataset, [next(t for t in trains if t.train_id == train_id)],
            {train_id: candidates[train_id]}, {}, mow, arcs,
            bin_minutes=args.bin_minutes, horizon_minutes=args.horizon,
            output_root=output, cfg=args,
            forbidden_actions=frozenset(set(parent_actions) | {action}),
        )
        feasible = [float(row["physical_cost"]) for row in rows if row.get("feasible") == "1"]
        value = min(feasible) - math.inf if False else (
            min(feasible) if feasible else math.inf)
        strong_cache[key] = value
        return value

    def choose_conflict(node: BBNode, conflicts: list[PhysicalConflict]) -> PhysicalConflict:
        if args.bb_conflict_selection == "earliest" or len(conflicts) == 1:
            return conflicts[0]
        ranked: list[tuple[tuple[float, float, float, str, str, int], PhysicalConflict]] = []
        for conflict in conflicts[:args.bb_strong_branching_candidates]:
            delta_a = strong_delta(
                conflict.action_a.forbidden, conflict.action_a.train_id,
                node.forbidden_actions)
            delta_b = strong_delta(
                conflict.action_b.forbidden, conflict.action_b.train_id,
                node.forbidden_actions)
            score = (
                min(delta_a, delta_b), max(delta_a, delta_b),
                -conflict.time_min, conflict.train_a, conflict.train_b, conflict.arc_id,
            )
            ranked.append((score, conflict))
        return max(ranked, key=lambda item: item[0])[1]

    while heap:
        elapsed = time.monotonic() - started
        if elapsed >= args.bb_time_limit_sec:
            termination_reason = "TIME_LIMIT"
            break
        global_lb = append_global_lb()
        if (global_lb is not None and math.isfinite(incumbent_ub) and
                ((args.bb_relative_gap > 0 and
                  (_bb_gap(global_lb, incumbent_ub)[0] or math.inf) <= args.bb_relative_gap) or
                 (args.bb_absolute_gap > 0 and
                  incumbent_ub - global_lb <= args.bb_absolute_gap + BB_TOLERANCE))):
            termination_reason = "GAP_REACHED"
            break
        if nodes_expanded >= args.bb_max_nodes:
            termination_reason = "NODE_LIMIT"
            break

        _, _, _, node = heapq.heappop(heap)
        if node.status == "INFEASIBLE":
            nodes_pruned_infeasible += 1
            node_log.append(_bb_node_log_row(
                node, incumbent_ub=incumbent_ub, global_lb=global_lb,
                prune_reason="infeasible-train"))
            continue
        if math.isfinite(incumbent_ub) and node.lower_bound >= incumbent_ub - BB_TOLERANCE:
            nodes_pruned_bound += 1
            node.status = "PRUNED_BOUND"
            node_log.append(_bb_node_log_row(
                node, incumbent_ub=incumbent_ub, global_lb=current_global_lb(),
                prune_reason="node-lb>=incumbent-ub"))
            continue

        nodes_expanded += 1
        if not node.base_paths:
            node, recovered, recovered_validation = _bb_solve_node(
                node, executable=executable, dataset=dataset, trains=trains,
                candidates=candidates, lambdas=node.best_lambda, mow=mow, arcs=arcs,
                args=args, output=output, capacity_certified=capacity_certified,
                capacity_model=capacity_model, capacity_map=capacity_map,
                incumbent_ub=incumbent_ub,
                dual_iterations=args.bb_node_dual_iterations,
            )
            if recovered is not None and _bb_physical_cost(recovered) < incumbent_ub - EPS:
                incumbent_validation = recovered_validation
                incumbent_ub, incumbent_routes = _bb_physical_cost(recovered), recovered
            if node.status == "INFEASIBLE":
                nodes_pruned_infeasible += 1
                node_log.append(_bb_node_log_row(
                    node, incumbent_ub=incumbent_ub, global_lb=current_global_lb(),
                    prune_reason="infeasible-train"))
                continue

        conflicts = all_physical_conflicts(
            node.base_paths, trains, candidates,
            safety_headway_minutes=args.safety_headway_minutes,
            time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
            wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
        )
        if not conflicts:
            validation = validate_physical_schedule(
                node.base_paths, trains, candidates, arcs, mow,
                horizon_minutes=args.horizon,
                safety_headway_minutes=args.safety_headway_minutes,
                time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
                wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
            )
            if validation["status"] != "PASS":
                node.status = "FAILED_VALIDATION"
                node_log.append(_bb_node_log_row(
                    node, incumbent_ub=incumbent_ub, global_lb=current_global_lb(),
                    prune_reason="base-path-validation-failed"))
                continue
            candidate_cost = _bb_physical_cost(node.base_paths)
            if candidate_cost < incumbent_ub - EPS:
                incumbent_validation = validation
                incumbent_ub, incumbent_routes = candidate_cost, node.base_paths
            nodes_fathomed_feasible += 1
            node.status = "FATHOMED_FEASIBLE"
            node_log.append(_bb_node_log_row(
                node, incumbent_ub=incumbent_ub, global_lb=current_global_lb(),
                prune_reason="conflict-free-base-paths"))
            continue

        conflict = choose_conflict(node, conflicts)
        node.conflict = conflict
        node.branch_conflict_type = conflict.conflict_type
        node.branch_conflict_arc = conflict.arc_id
        node.branch_conflict_time = conflict.time_min
        conflict_log.append(_bb_conflict_dict(conflict) | {
            "node_id": node.node_id, "depth": node.depth,
        })
        node.status = "BRANCHED"
        for action in (conflict.action_a, conflict.action_b):
            action_key = action.forbidden
            child_actions = frozenset(set(node.forbidden_actions) | {action_key})
            canonical = tuple(sorted(child_actions))
            if canonical in seen_constraints:
                duplicate_nodes_skipped += 1
                continue
            seen_constraints.add(canonical)
            child = BBNode(
                node_id=next_node_id, parent_id=node.node_id,
                depth=node.depth + 1, forbidden_actions=child_actions,
                inherited_lower_bound=node.lower_bound,
                lower_bound=node.lower_bound, branch_action=action,
                branch_conflict_type=conflict.conflict_type,
                branch_conflict_arc=conflict.arc_id,
                branch_conflict_time=conflict.time_min,
            )
            next_node_id += 1
            nodes_created += 1
            maximum_depth = max(maximum_depth, child.depth)
            child, recovered, recovered_validation = _bb_solve_node(
                child, executable=executable, dataset=dataset, trains=trains,
                candidates=candidates, lambdas=node.best_lambda, mow=mow, arcs=arcs,
                args=args, output=output, capacity_certified=capacity_certified,
                capacity_model=capacity_model, capacity_map=capacity_map,
                incumbent_ub=incumbent_ub,
                dual_iterations=args.bb_node_dual_iterations,
            )
            if recovered is not None and _bb_physical_cost(recovered) < incumbent_ub - EPS:
                incumbent_validation = recovered_validation
                incumbent_ub, incumbent_routes = _bb_physical_cost(recovered), recovered
            if child.status == "INFEASIBLE":
                nodes_pruned_infeasible += 1
                node_log.append(_bb_node_log_row(
                    child, incumbent_ub=incumbent_ub, global_lb=current_global_lb(),
                    prune_reason="infeasible-train"))
            elif math.isfinite(incumbent_ub) and child.lower_bound >= incumbent_ub - BB_TOLERANCE:
                nodes_pruned_bound += 1
                child.status = "PRUNED_BOUND"
                node_log.append(_bb_node_log_row(
                    child, incumbent_ub=incumbent_ub, global_lb=current_global_lb(),
                    prune_reason="node-lb>=incumbent-ub"))
            else:
                heapq.heappush(heap, (child.lower_bound, child.depth, child.node_id, child))
        node_log.append(_bb_node_log_row(
            node, incumbent_ub=incumbent_ub, global_lb=append_global_lb(),
            prune_reason="conflict-branched"))
        if args.bb_checkpoint_interval > 0 and nodes_expanded % args.bb_checkpoint_interval == 0:
            _write_bb_checkpoint(
                checkpoint_path, fingerprint=fingerprint, open_nodes=[item[3] for item in heap],
                incumbent_ub=incumbent_ub, incumbent_routes=incumbent_routes,
                next_node_id=next_node_id, global_lb_history=global_lb_history,
                termination_reason="CHECKPOINT",
            )

    if not heap and termination_reason == "OPEN_NODES":
        termination_reason = "PROVEN_OPTIMAL" if math.isfinite(incumbent_ub) else "INFEASIBLE"
    final_global_lb = current_global_lb()
    if termination_reason == "PROVEN_OPTIMAL" and math.isfinite(incumbent_ub):
        final_global_lb = incumbent_ub
    relative_ub, relative_lb = _bb_gap(
        final_global_lb if final_global_lb is not None else math.inf, incumbent_ub)
    status = {
        "PROVEN_OPTIMAL": "PROVEN_OPTIMAL", "GAP_REACHED": "GAP_REACHED",
        "TIME_LIMIT": "TIME_LIMIT", "NODE_LIMIT": "NODE_LIMIT",
        "INFEASIBLE": "INFEASIBLE",
    }.get(termination_reason, "DIAGNOSTIC_ONLY")
    if incumbent_routes:
        _write_routes_file(output / "bb_incumbent_routes.tsv", incumbent_routes)
    else:
        _write_routes_file(output / "bb_incumbent_routes.tsv", {})
    node_fields = [
        "node_id", "parent_id", "depth", "status", "branch_train_id",
        "branch_route_index", "branch_arc_position", "branch_start_cell",
        "branch_wait_tick", "base_lower_bound", "dual_lower_bound",
        "inherited_lower_bound", "node_lower_bound", "incumbent_ub_at_solve",
        "global_lb_after_processing", "absolute_gap", "relative_gap_ub_normalized",
        "relative_gap_lb_normalized", "dual_iterations", "conflict_count",
        "selected_conflict_type", "selected_conflict_arc", "selected_conflict_time",
        "prune_reason", "runtime_sec",
    ]
    with (output / "bb_nodes.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=node_fields)
        writer.writeheader()
        writer.writerows(node_log)
    conflict_fields = [
        "node_id", "depth", "conflict_type", "arc_id", "time_min",
        "train_a", "train_b", "action_a", "action_b",
    ]
    with (output / "bb_conflicts.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=conflict_fields)
        writer.writeheader()
        for row in conflict_log:
            writer.writerow({
                **row, "action_a": json.dumps(row["action_a"], sort_keys=True),
                "action_b": json.dumps(row["action_b"], sort_keys=True),
            })
    _write_bb_checkpoint(
        checkpoint_path, fingerprint=fingerprint, open_nodes=[item[3] for item in heap],
        incumbent_ub=incumbent_ub, incumbent_routes=incumbent_routes,
        next_node_id=next_node_id, global_lb_history=global_lb_history,
        termination_reason=termination_reason,
    )
    summary = {
        "status": status, "certified": True,
        "certificate_scope": "fixed PATH-K discretized-time model",
        "dataset": str(dataset), "train_count": len(trains),
        "path_k": args.path_k, "candidate_count": candidate_count,
        "candidate_fingerprint": fingerprint,
        "candidate_universe_expanded_before_root": candidate_expanded,
        "time_step": getattr(args, "time_step", DEFAULT_TIME_STEP),
        "departure_step": getattr(args, "departure_step", DEFAULT_DEPARTURE_STEP),
        "wait_step": getattr(args, "wait_step", DEFAULT_WAIT_STEP),
        "horizon": args.horizon, "capacity_model": capacity_model,
        "capacity_map_complete": capacity_complete,
        "capacity_map_entries": len(capacity_map),
        "capacity_relaxation_scope": dual_note,
        "root_base_LB": root_base_lb,
        "root_best_dual_LB": root_dual_lb,
        "root_dual_history": root_dual_history,
        "root_dual_max_capacity_violation": max(
            (float(row.get("max_gradient", 0.0)) for row in root_dual_history),
            default=0.0),
        "initial_global_LB": global_lb_history[0] if global_lb_history else None,
        "final_global_LB": final_global_lb,
        "initial_UB": initial_ub if math.isfinite(initial_ub) else None,
        "best_UB": incumbent_ub if math.isfinite(incumbent_ub) else None,
        "absolute_gap": (max(0.0, incumbent_ub - final_global_lb)
                         if math.isfinite(incumbent_ub) and final_global_lb is not None else None),
        "relative_gap_ub_normalized": relative_ub,
        "relative_gap_lb_normalized": relative_lb,
        "nodes_created": nodes_created, "nodes_expanded": nodes_expanded,
        "nodes_open": len(heap), "nodes_pruned_bound": nodes_pruned_bound,
        "nodes_pruned_infeasible": nodes_pruned_infeasible,
        "nodes_fathomed_feasible": nodes_fathomed_feasible,
        "duplicate_nodes_skipped": duplicate_nodes_skipped,
        "maximum_depth": maximum_depth,
        "runtime_sec": time.monotonic() - started,
        "termination_reason": termination_reason,
        "incumbent_physically_validated": (
            bool(incumbent_routes) and incumbent_validation.get("status") == "PASS"),
        "incumbent_validation_status": incumbent_validation.get("status", "UNKNOWN"),
        "dual_certified": capacity_certified,
        "global_lb_monotone": all(
            right + BB_TOLERANCE >= left
            for left, right in zip(global_lb_history, global_lb_history[1:])
        ),
        "output_files": [
            "bb_summary.json", "bb_nodes.csv", "bb_conflicts.csv",
            "bb_incumbent_routes.tsv", "bb_open_nodes_checkpoint.json",
        ],
    }
    (output / "bb_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def solve(args: argparse.Namespace) -> dict[str, object]:
    started = time.monotonic()
    if getattr(args, "enable_conflict_bb", False):
        return solve_conflict_bb_v2(args)
    dataset = Path(args.dataset).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    lr_capacity_model = str(getattr(args, "lr_capacity_model", DEFAULT_LR_CAPACITY_MODEL))
    lr_capacity_map_path_value = getattr(args, "lr_capacity_map", None)
    lr_capacity_map_path = (
        Path(lr_capacity_map_path_value).resolve()
        if lr_capacity_map_path_value else None
    )
    if lr_capacity_model == "headway-relaxation" and lr_capacity_map_path is None:
        raise ValueError(
            "--lr-capacity-map is required when --lr-capacity-model "
            "headway-relaxation is selected")
    lr_capacity_map = load_lr_capacity_map(lr_capacity_map_path)
    source = Path(__file__).with_name("path_k_dp.cpp")
    executable = (Path(args.cpp_exe).resolve() if args.cpp_exe
                  else Path(__file__).with_name("build") / "path_k_dp")
    executable = ensure_cpp(source, executable)
    arcs, trains, mow = load_dataset(dataset)
    graph = build_graph(arcs)
    candidates = all_candidates(trains, graph, arcs, args.path_k)
    candidate_count = sum(len(values) for values in candidates.values())
    lambdas: dict[tuple[int, int], float] = {}
    previous_selected: dict[str, dict[str, str]] | None = None
    route_hashes: list[str] = []
    trajectory_hashes: list[str] = []
    route_cycle_counters: dict[int, int] = {}
    trajectory_cycle_counters: dict[int, int] = {}
    cycle_observations: list[dict[str, object]] = []
    cell_history: dict[tuple[int, int], list[dict[str, object]]] = defaultdict(list)
    resource_trace_rows: list[dict[str, object]] = []
    arrival_trace_rows: list[dict[str, object]] = []
    train_trace_rows: list[dict[str, object]] = []
    averaged_arrivals: dict[tuple[int, int], float] | None = None
    price_queue_history: list[dict[tuple[int, int], float]] = []
    cap_cells_ever: set[tuple[int, int]] = set()
    iterations_with_cap_hit = 0
    route_change_events = 0
    total_route_changes = 0
    total_timing_changes = 0
    history: list[dict[str, object]] = []
    best_lower_bound = -math.inf
    iteration_of_best_lb = -1
    best_dual_bound = -math.inf
    best_lambda: dict[tuple[int, int], float] = {}
    dual_iteration_of_best = -1
    dual_theta = float(getattr(args, "dual_theta_initial", DEFAULT_DUAL_THETA_INITIAL))
    dual_stall_rounds = 0
    dual_history: list[dict[str, object]] = []
    valid_ub_history: list[float] = []
    best_valid_ub = math.inf
    stable_rounds = 0
    ub_recovery_errors: list[str] = []
    for iteration in range(args.max_iterations):
        rows = run_cpp_batch(
            executable, dataset, trains, candidates, lambdas, mow, arcs,
            bin_minutes=args.bin_minutes, horizon_minutes=args.horizon,
            output_root=output, cfg=args,
        )
        relaxed_selected, by_train = select_relaxed_argmins(
            rows, trains, candidates)
        # Feasible recovery is a separate object.  Its occupancy must never
        # be fed back as a dual subgradient for the relaxed argmins above.
        try:
            ub_selected = repair_main_track_order(
                {train_id: dict(row) for train_id, row in relaxed_selected.items()},
                trains, candidates, executable, dataset, lambdas, mow, arcs,
                bin_minutes=args.bin_minutes, horizon_minutes=args.horizon,
                output_root=output, cfg=args,
            )
        except RuntimeError as exc:
            # The relaxed OFQ/LR iteration remains diagnostic when the
            # existing primal order-repair cannot find a trajectory.  Do not
            # abort the LB-side history and do not mark the relaxed schedule
            # as a feasible UB.
            ub_recovery_errors.append(f"iteration {iteration}: {exc}")
            ub_selected = relaxed_selected
        selected = relaxed_selected
        occupancy = resource_occupancy(
            relaxed_selected, args.bin_minutes, args.safety_headway_minutes)
        actual_arrivals = resource_arrivals(selected, args.bin_minutes)
        previous_averaged_arrivals = averaged_arrivals
        rho = arrival_averaging_rho(
            args.arrival_averaging_policy, iteration, args.arrival_averaging_rho)
        averaged_arrivals = update_arrival_average(
            previous_averaged_arrivals, actual_arrivals,
            policy=args.arrival_averaging_policy, rho=rho)
        arrival_metrics = arrival_map_metrics(
            previous_averaged_arrivals, averaged_arrivals, actual_arrivals)
        selected_ids_by_cell = resource_occupancy_train_ids(
            selected, args.bin_minutes, args.safety_headway_minutes)
        arriving_ids_by_cell = resource_arrival_train_ids(selected, args.bin_minutes)
        cells = fluid_queue_cells(
            actual_arrivals, occupancy, lambdas, arcs, mow,
            bin_minutes=args.bin_minutes, horizon=args.horizon,
            price_arrivals=averaged_arrivals,
            lr_capacity_model=lr_capacity_model,
            lr_capacity_map=lr_capacity_map,
        )
        minimum_sum = sum(
            min(float(row["total_priced_cost"]) for row in by_train[train.train_id]
                if row.get("feasible") == "1") for train in trains)
        current_lambda_dot_capacity = lambda_dot_capacity(
            lambdas, model=lr_capacity_model, capacity_map=lr_capacity_map)
        current_dual_value = minimum_sum - current_lambda_dot_capacity
        ub_validation = validate_physical_schedule(
            ub_selected, trains, candidates, arcs, mow,
            horizon_minutes=args.horizon,
            safety_headway_minutes=args.safety_headway_minutes,
            time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
            wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
        )
        ub_cost = (
            sum(float(row.get("physical_cost", 0.0) or 0.0)
                for row in ub_selected.values())
            if ub_validation["status"] == "PASS" else math.inf
        )
        if math.isfinite(ub_cost):
            best_valid_ub = min(best_valid_ub, ub_cost)
            valid_ub_history.append(ub_cost)

        if args.price_policy == FLUID_QUEUE:
            next_lambda, target = fluid_lambda_update(
                cells, lambdas, alpha=args.alpha, beta=args.beta, gamma=args.gamma,
                wait_reference_minutes=args.wait_reference_minutes,
                queue_wait_cost_per_min=args.queue_wait_cost_per_min,
                max_price_wait_minutes=args.max_price_wait_minutes,
                use_price_queue=True,
            )
        elif args.price_policy == LEGACY_SUBGRADIENT:
            next_lambda, target = legacy_lambda_update(
                lambdas, occupancy, iteration=iteration,
                step_size_initial=args.step_size_initial,
                minimum_step_size=args.minimum_step_size, gamma=args.gamma,
                lr_capacity_model=lr_capacity_model,
                lr_capacity_map=lr_capacity_map,
            )
        else:
            next_lambda, gradient, dual_step = dual_subgradient_update(
                lambdas, occupancy, iteration=iteration,
                dual_value=current_dual_value,
                incumbent_ub=best_valid_ub,
                model=lr_capacity_model, capacity_map=lr_capacity_map,
                theta=dual_theta,
                fallback_step=max(args.step_size_initial, 1e-3),
                norm_tolerance=args.dual_norm_tolerance,
            )
            target = next_lambda
            if current_dual_value > best_dual_bound + EPS:
                best_dual_bound = current_dual_value
                best_lambda = dict(lambdas)
                dual_iteration_of_best = iteration
                dual_stall_rounds = 0
            else:
                dual_stall_rounds += 1
                if dual_stall_rounds >= args.dual_stall_rounds:
                    dual_theta = max(args.dual_theta_min, dual_theta * 0.5)
                    dual_stall_rounds = 0
            dual_history.append({
                "iteration": iteration, "dual_value": current_dual_value,
                "best_dual_bound": max(best_dual_bound, current_dual_value),
                "lambda_dot_capacity": current_lambda_dot_capacity,
                "minimum_sum": minimum_sum,
                "gradient_norm_sq": dual_step["norm_sq"],
                "step": dual_step["step"],
                "used_polyak": dual_step["used_polyak"],
                "theta": dual_theta,
                "valid_ub": best_valid_ub,
                "ub_validation": ub_validation["status"],
            })
        for key, cell in cells.items():
            cell.lambda_old = float(lambdas.get(key, 0.0))
            cell.lambda_target = float(target.get(key, 0.0))
            cell.lambda_new = float(next_lambda.get(key, 0.0))

        current_price_queue = {
            key: float(cell.price_queue_after_train) for key, cell in cells.items()
        }
        previous_price_queue = price_queue_history[-1] if price_queue_history else {}
        price_queue_changes = [
            abs(current_price_queue.get(key, 0.0) -
                previous_price_queue.get(key, 0.0))
            for key in set(current_price_queue) | set(previous_price_queue)
        ]
        price_queue_l1_change = sum(price_queue_changes)
        price_queue_linf_change = max(price_queue_changes, default=0.0)
        price_queue_history.append(current_price_queue)

        snapshots = snapshot_cells(cells, selected_ids_by_cell, arriving_ids_by_cell)
        for key, snapshot in snapshots.items():
            cell_history[key].append(snapshot)
            raw_wait = float(snapshot["backlog_clearance_min"])
            resource_trace_rows.append({
                "iteration": iteration, "arc_id": snapshot["arc_id"],
                "time_bin": snapshot["time_bin"],
                "time_start_min": int(snapshot["time_bin"]) * args.bin_minutes,
                "time_end_min": (int(snapshot["time_bin"]) + 1) * args.bin_minutes,
                "arrival_count": snapshot["arrival_count"],
                "actual_arrival_count": snapshot["actual_arrival_count"],
                "averaged_arrival_count": snapshot["averaged_arrival_count"],
                "lr_occupancy_usage": snapshot["lr_occupancy_usage"],
                "lr_excess": snapshot["lr_excess"],
                "queue_before_train": snapshot["queue_before_train"],
                "queue_after_train": snapshot["queue_after_train"],
                "backlog_clearance_min": snapshot["backlog_clearance_min"],
                "actual_queue_before": snapshot["queue_before_train"],
                "actual_queue_after": snapshot["queue_after_train"],
                "actual_backlog_clearance_min": snapshot["backlog_clearance_min"],
                "price_queue_before": snapshot["price_queue_before_train"],
                "price_queue_after": snapshot["price_queue_after_train"],
                "price_backlog_clearance_min": snapshot["price_backlog_clearance_min"],
                "rho": rho,
                "lambda_old": snapshot["lambda_old"],
                "lambda_target": snapshot["lambda_target"],
                "lambda_new": snapshot["lambda_new"],
                "lambda_change": float(snapshot["lambda_new"]) - float(snapshot["lambda_old"]),
                "wait_to_reference_ratio": (
                    raw_wait / args.wait_reference_minutes
                    if math.isfinite(raw_wait) else math.inf),
                "price_wait_cap_hit": int(raw_wait >= args.max_price_wait_minutes),
                "selected_train_ids": ";".join(sorted(snapshot["selected_train_ids"])),
                "arriving_train_ids": ";".join(sorted(snapshot["arriving_train_ids"])),
            })
            actual_arrival = float(snapshot["actual_arrival_count"])
            averaged_arrival = float(snapshot["averaged_arrival_count"])
            previous_average_arrival = float(
                (previous_averaged_arrivals or {}).get(key, 0.0))
            if (abs(actual_arrival) > EPS or abs(averaged_arrival) > EPS or
                    abs(previous_average_arrival) > EPS or
                    float(snapshot["queue_after_train"]) > EPS or
                    float(snapshot["price_queue_after_train"]) > EPS or
                    float(snapshot["lambda_new"]) > EPS):
                arrival_trace_rows.append({
                    "iteration": iteration, "arc_id": snapshot["arc_id"],
                    "time_bin": snapshot["time_bin"], "rho": rho,
                    "actual_arrival": actual_arrival,
                    "previous_average_arrival": previous_average_arrival,
                    "new_average_arrival": averaged_arrival,
                    "arrival_difference": actual_arrival - averaged_arrival,
                    "actual_queue": snapshot["queue_after_train"],
                    "price_queue": snapshot["price_queue_after_train"],
                    "lambda_target": snapshot["lambda_target"],
                    "lambda_new": snapshot["lambda_new"],
                    "selected_train_ids": ";".join(
                        sorted(snapshot["selected_train_ids"])),
                })

        current_route_map = route_signature_map(selected)
        current_trajectory_map = trajectory_signature_map(selected)
        route_hash = route_signature_digest(selected)
        trajectory_hash = trajectory_signature_digest(selected)
        route_hashes.append(route_hash)
        trajectory_hashes.append(trajectory_hash)
        cycle_info = cycle_observation(
            route_hashes, trajectory_hashes,
            route_cycle_counters, trajectory_cycle_counters,
        )
        cycle_observations.append(cycle_info)
        changed_train_count, changed_route_count, changed_timing_count, change_flags = (
            changed_train_counts(previous_selected, selected))
        if previous_selected is not None:
            route_change_events += int(changed_route_count > 0)
        total_route_changes += changed_route_count
        total_timing_changes += changed_timing_count
        for train_id, row in sorted(selected.items()):
            route_changed, timing_changed = change_flags.get(train_id, (False, False))
            train_trace_rows.append({
                "iteration": iteration, "train_id": train_id,
                "route_index": row.get("route_index", ""), "arc_ids": row.get("arc_ids", ""),
                "departure_min": row.get("departure_min", ""), "arrival_min": row.get("arrival_min", ""),
                "departure_delay_min": row.get("departure_delay_min", ""),
                "origin_wait_cost": row.get("origin_wait_cost", ""),
                "siding_wait_min": row.get("siding_wait_min", ""),
                "enroute_wait_min": row.get("enroute_wait_min", ""),
                "running_cost": row.get("running_cost", ""),
                "waiting_cost": row.get("waiting_cost", ""),
                "arrival_schedule_cost": row.get("arrival_schedule_cost", ""),
                "lambda_cost": row.get("lambda_cost", ""),
                "physical_cost": row.get("physical_cost", ""),
                "total_priced_cost": row.get("total_priced_cost", ""),
                "route_changed_from_previous": int(route_changed),
                "timing_changed_from_previous": int(timing_changed),
            })

        minimum_sum = sum(
            min(float(row["total_priced_cost"]) for row in by_train[train.train_id]
                if row.get("feasible") == "1") for train in trains)
        reported_lambda_dot_capacity = lambda_dot_capacity(
            lambdas, model=lr_capacity_model, capacity_map=lr_capacity_map)
        lower_bound = minimum_sum - reported_lambda_dot_capacity
        max_occupancy = max(occupancy.values(), default=0.0)
        excess_values = capacity_excess(
            occupancy, model=lr_capacity_model, capacity_map=lr_capacity_map)
        max_excess = max(0.0, max(excess_values.values(), default=0.0))
        raw_waits = [cell.clearance_wait_min for cell in cells.values()
                     if cell.queue_after_train > EPS]
        finite_waits = [wait for wait in raw_waits if math.isfinite(wait)]
        max_raw_wait = max(raw_waits, default=0.0)
        cap_hits = sum(
            1 for wait in raw_waits if wait >= args.max_price_wait_minutes)
        positive_queue_cells = len(raw_waits)
        max_queue = max((cell.queue_after_train for cell in cells.values()), default=0.0)
        total_queue = sum(cell.queue_after_train for cell in cells.values())
        price_raw_waits = [cell.price_clearance_wait_min for cell in cells.values()
                           if cell.price_queue_after_train > EPS]
        price_finite_waits = [wait for wait in price_raw_waits
                              if math.isfinite(wait)]
        max_price_queue = max(
            (cell.price_queue_after_train for cell in cells.values()), default=0.0)
        total_price_queue = sum(
            cell.price_queue_after_train for cell in cells.values())
        max_price_raw_wait = max(price_raw_waits, default=0.0)
        episodes = congestion_episodes(
            cells, bin_minutes=args.bin_minutes,
            queue_wait_cost_per_min=args.queue_wait_cost_per_min,
            early_arrival_cost_per_min=args.early_arrival_cost_per_min,
            late_arrival_cost_per_min=args.late_arrival_cost_per_min,
        )
        route_stable = (
            previous_selected is not None and
            current_route_map == route_signature_map(previous_selected)
        )
        trajectory_stable = (
            previous_selected is not None and
            current_trajectory_map == trajectory_signature_map(previous_selected)
        )
        queue_changes = []
        for key, series in cell_history.items():
            if len(series) >= 2:
                queue_changes.append(abs(
                    float(series[-1]["queue_after_train"]) -
                    float(series[-2]["queue_after_train"])))
        max_queue_change = max(queue_changes, default=0.0)
        total_queue_change = sum(queue_changes)
        changes = [abs(next_lambda.get(key, 0.0) - lambdas.get(key, 0.0))
                   for key in set(next_lambda) | set(lambdas)]
        l1 = sum(changes)
        linf = max(changes, default=0.0)
        active_changes = [
            abs(next_lambda.get(key, 0.0) - lambdas.get(key, 0.0))
            for key in set(next_lambda) | set(lambdas)
            if abs(next_lambda.get(key, 0.0)) > EPS or
            abs(lambdas.get(key, 0.0)) > EPS or
            abs(target.get(key, 0.0)) > EPS
        ]
        mean_active_lambda_change = (
            sum(active_changes) / len(active_changes) if active_changes else 0.0)
        scale = max(
            1.0,
            max((abs(value) for value in next_lambda.values()), default=0.0),
            max((abs(value) for value in lambdas.values()), default=0.0),
        )
        relative = linf / scale
        price_stable = relative <= args.lambda_tolerance
        aggregate_price_stable = (
            price_stable and price_queue_linf_change <= args.price_queue_tolerance
        )
        cap_keys = {
            key for key, cell in cells.items()
            if cell.queue_after_train > EPS and
            cell.clearance_wait_min >= args.max_price_wait_minutes
        }
        cap_cells_ever.update(cap_keys)
        if cap_keys:
            iterations_with_cap_hit += 1
        if lower_bound > best_lower_bound:
            best_lower_bound = lower_bound
            iteration_of_best_lb = iteration
        if trajectory_stable and price_stable:
            stable_rounds += 1
        else:
            stable_rounds = 0
        converged = stable_rounds >= args.stable_rounds
        history.append({
            "iteration": iteration, "sum_min_subproblem": minimum_sum,
            "lambda_dot_capacity": reported_lambda_dot_capacity,
            # Instrumentation only: these fields expose the already computed
            # dual-like score and resource diagnostics for external audit.
            # They do not participate in the OFQ/LR update or any selection.
            "wall_time_sec": time.monotonic() - started,
            "current_dual_value": current_dual_value,
            "multiplier_norm": math.sqrt(sum(
                value * value for value in next_lambda.values())),
            "queue_metric": total_queue,
            "num_resource_violations": sum(
                value > EPS for value in excess_values.values()),
            "lower_bound": lower_bound,
            "best_lower_bound": best_lower_bound, "max_lr_occupancy": max_occupancy,
            "max_lr_excess": max_excess, "max_queue_train": max_queue,
            "max_wait_min": max(finite_waits, default=0.0),
            "mean_positive_wait_min": (sum(finite_waits) / len(finite_waits)
                                       if finite_waits else 0.0),
            "total_queue_train": total_queue,
            "max_actual_queue": max_queue,
            "total_actual_queue": total_queue,
            "max_price_queue": max_price_queue,
            "total_price_queue": total_price_queue,
            "max_price_backlog_clearance_min": max(price_finite_waits, default=0.0),
            "max_price_raw_clearance_wait_min": max_price_raw_wait,
            "price_queue_l1_change": price_queue_l1_change,
            "price_queue_linf_change": price_queue_linf_change,
            **arrival_metrics,
            "rho": rho,
            "congestion_episode_count": len(episodes), "mean_lambda": (
                sum(next_lambda.values()) / len(next_lambda) if next_lambda else 0.0),
            "max_lambda": max(next_lambda.values(), default=0.0),
            "max_lambda_target": max(target.values(), default=0.0),
            "max_raw_clearance_wait_min": max_raw_wait,
            "price_wait_cap_hit_count": cap_hits,
            "positive_queue_cell_count": positive_queue_cells,
            "max_wait_to_reference_ratio": (
                max_raw_wait / args.wait_reference_minutes
                if args.wait_reference_minutes > 0 else math.inf),
            "lambda_l1_change": l1, "lambda_linf_change": linf,
            "max_lambda_change": linf,
            "mean_active_lambda_change": mean_active_lambda_change,
            "max_queue_change": max_queue_change,
            "total_queue_change": total_queue_change,
            "changed_train_count": changed_train_count,
            "changed_route_count": changed_route_count,
            "changed_timing_count": changed_timing_count,
            "lambda_scale": scale,
            "relative_lambda_linf_change": relative,
            "route_signature": route_hash,
            "trajectory_signature": trajectory_hash,
            **cycle_info,
            "demand_keys": len(actual_arrivals),
            "lambda_keys": len(next_lambda), "feasible_trains": len(selected),
            "total_trains": len(trains), "route_stable": route_stable,
            "trajectory_stable": trajectory_stable,
            "price_stable": price_stable, "converged": converged,
            "aggregate_price_stable": aggregate_price_stable,
        })
        previous_selected = selected
        lambdas = next_lambda
        if converged:
            break

    # The loop above evaluates lambda^k and then creates lambda^(k+1).  Once
    # the stopping decision is made, perform one read-only DP/resource pass at
    # the actual final lambda so all final files refer to the same state.
    final_rows = run_cpp_batch(
        executable, dataset, trains, candidates, lambdas, mow, arcs,
        bin_minutes=args.bin_minutes, horizon_minutes=args.horizon,
        output_root=output, cfg=args,
    )
    final_relaxed_selected, final_by_train = select_relaxed_argmins(
        final_rows, trains, candidates)
    try:
        final_selected = repair_main_track_order(
            {train_id: dict(row) for train_id, row in final_relaxed_selected.items()},
            trains, candidates, executable, dataset, lambdas, mow, arcs,
            bin_minutes=args.bin_minutes, horizon_minutes=args.horizon,
            output_root=output, cfg=args,
        )
    except RuntimeError as exc:
        ub_recovery_errors.append(f"final evaluation: {exc}")
        final_selected = final_relaxed_selected
    final_occupancy = resource_occupancy(
        final_relaxed_selected, args.bin_minutes, args.safety_headway_minutes)
    final_arrivals = resource_arrivals(final_relaxed_selected, args.bin_minutes)
    final_price_arrivals = (
        final_arrivals if args.arrival_averaging_policy == "none"
        else (averaged_arrivals or final_arrivals)
    )
    final_cells = fluid_queue_cells(
        final_arrivals, final_occupancy, lambdas, arcs, mow,
        bin_minutes=args.bin_minutes, horizon=args.horizon,
        price_arrivals=final_price_arrivals,
        lr_capacity_model=lr_capacity_model,
        lr_capacity_map=lr_capacity_map,
    )
    final_episodes = congestion_episodes(
        final_cells, bin_minutes=args.bin_minutes,
        queue_wait_cost_per_min=args.queue_wait_cost_per_min,
        early_arrival_cost_per_min=args.early_arrival_cost_per_min,
        late_arrival_cost_per_min=args.late_arrival_cost_per_min,
    )
    if args.price_policy == FLUID_QUEUE:
        _, final_target = fluid_lambda_update(
            final_cells, lambdas, alpha=args.alpha, beta=args.beta,
            gamma=args.gamma, wait_reference_minutes=args.wait_reference_minutes,
            queue_wait_cost_per_min=args.queue_wait_cost_per_min,
            max_price_wait_minutes=args.max_price_wait_minutes,
            use_price_queue=True,
        )
    elif args.price_policy == LEGACY_SUBGRADIENT:
        _, final_target = legacy_lambda_update(
            lambdas, final_occupancy, iteration=max(0, len(history) - 1),
            step_size_initial=args.step_size_initial,
            minimum_step_size=args.minimum_step_size, gamma=args.gamma,
            lr_capacity_model=lr_capacity_model,
            lr_capacity_map=lr_capacity_map,
        )
    else:
        final_target = dict(lambdas)
    for key, cell in final_cells.items():
        cell.lambda_old = float(lambdas.get(key, 0.0))
        cell.lambda_target = float(final_target.get(key, 0.0))
        cell.lambda_new = float(lambdas.get(key, 0.0))

    final_route_signature_map = route_signature_map(final_selected)
    final_trajectory_signature_map = trajectory_signature_map(final_selected)
    final_route_hash = route_signature_digest(final_selected)
    final_trajectory_hash = trajectory_signature_digest(final_selected)
    final_changed_train_count, final_changed_route_count, final_changed_timing_count, _ = (
        changed_train_counts(previous_selected, final_relaxed_selected))
    final_route_stable = (
        previous_selected is not None and
        final_route_signature_map == route_signature_map(previous_selected))
    final_trajectory_stable = (
        previous_selected is not None and
        trajectory_signature_map(final_relaxed_selected) == trajectory_signature_map(previous_selected))
    if previous_selected is not None and final_changed_route_count > 0:
        route_change_events += 1
    total_route_changes += final_changed_route_count
    total_timing_changes += final_changed_timing_count
    final_minimum_sum = sum(
        min(float(row["total_priced_cost"]) for row in final_by_train[train.train_id]
            if row.get("feasible") == "1") for train in trains)
    final_lambda_dot_capacity = lambda_dot_capacity(
        lambdas, model=lr_capacity_model, capacity_map=lr_capacity_map)
    final_lower_bound = final_minimum_sum - final_lambda_dot_capacity
    if final_lower_bound > best_lower_bound:
        best_lower_bound = final_lower_bound
        iteration_of_best_lb = len(history)

    final_raw_waits = [cell.clearance_wait_min for cell in final_cells.values()
                       if cell.queue_after_train > EPS]
    final_finite_waits = [wait for wait in final_raw_waits if math.isfinite(wait)]
    final_max_raw_wait = max(final_raw_waits, default=0.0)
    final_cap_hits = sum(
        1 for wait in final_raw_waits if wait >= args.max_price_wait_minutes)
    final_positive_queue_cells = len(final_raw_waits)
    final_price_raw_waits = [cell.price_clearance_wait_min
                             for cell in final_cells.values()
                             if cell.price_queue_after_train > EPS]
    final_price_finite_waits = [wait for wait in final_price_raw_waits
                                if math.isfinite(wait)]
    final_max_price_queue = max(
        (cell.price_queue_after_train for cell in final_cells.values()), default=0.0)
    final_total_price_queue = sum(
        cell.price_queue_after_train for cell in final_cells.values())
    final_max_price_raw_wait = max(final_price_raw_waits, default=0.0)
    final_stale_stats = stale_price_stats_from_cells(final_cells)
    largest_episode = max(
        final_episodes,
        key=lambda episode: (
            float(episode["duration_P_min"])
            if episode["duration_P_min"] not in ("", None) else -1.0,
            float(episode["Qmax_train"]),
        ),
        default=None,
    )
    final_lambda_values = list(lambdas.values())
    cycle_rows = detect_cycle_cells(cell_history)
    cycle_ever = [
        observation for observation in cycle_observations
        if bool(observation.get("detected_persistent_cycle"))
    ]
    detected_signature_cycle = bool(cycle_ever)
    detected_approximate_cell_cycle = bool(cycle_rows)
    if cycle_ever:
        cycle_types = [
            observation for observation in cycle_ever
            if observation.get("detected_cycle_signature_type") == "trajectory"
        ] or cycle_ever
        detected_cycle_period = min(
            int(observation["detected_cycle_period"]) for observation in cycle_types)
        detected_cycle_signature_type = cycle_types[-1].get(
            "detected_cycle_signature_type", "trajectory")
    else:
        detected_cycle_period = 0
        detected_cycle_signature_type = "none"
    # Keep the signature-cycle field faithful to section 6 of the diagnostic
    # specification.  Local cell oscillations are reported separately below;
    # they may exist while many trains change and the global hash never repeats.
    detected_cell_cycle_period = 2 if detected_approximate_cell_cycle else 0
    detected_persistent_cycle = detected_signature_cycle
    dominant_cycle = cycle_rows[0] if cycle_rows else None
    tail_diagnostics, tail_cycle_rows = compute_tail_diagnostics(
        history, cell_history, route_hashes, trajectory_hashes, final_cells,
        tail_window=args.tail_window, train_count=len(trains),
        max_price_wait_minutes=args.max_price_wait_minutes,
    )
    final_costs = {
        field: sum(float(row.get(field, 0.0) or 0.0) for row in final_selected.values())
        for field in (
            "origin_wait_cost", "running_cost", "waiting_cost", "arrival_schedule_cost",
            "physical_cost", "lambda_cost", "total_priced_cost",
        )
    }
    final_ub_validation = validate_physical_schedule(
        final_selected, trains, candidates, arcs, mow,
        horizon_minutes=args.horizon,
        safety_headway_minutes=args.safety_headway_minutes,
        time_step=getattr(args, "time_step", DEFAULT_TIME_STEP),
        wait_step=getattr(args, "wait_step", DEFAULT_WAIT_STEP),
    )

    with (output / "lambda_history.csv").open("w", encoding="utf-8", newline="") as stream:
        if history:
            writer = csv.DictWriter(stream, fieldnames=list(history[0]))
            writer.writeheader()
            writer.writerows(history)
    write_final_routes(output, final_selected)
    write_resource_state(
        output, final_cells, lambdas, args.bin_minutes,
        lr_capacity_model=lr_capacity_model, lr_capacity_map=lr_capacity_map)
    write_congestion_episodes(output, final_episodes)
    write_price_map(
        output, args.beta, alpha=args.alpha,
        queue_wait_cost_per_min=args.queue_wait_cost_per_min,
        tau0=args.wait_reference_minutes,
        max_price_wait_minutes=args.max_price_wait_minutes,
    )
    resource_trace_rows = [
        row for row in resource_trace_rows
        if float(row["arrival_count"]) > EPS or
        float(row["lr_occupancy_usage"]) > EPS or
        float(row["queue_before_train"]) > EPS or
        float(row["queue_after_train"]) > EPS or
        float(row["lambda_old"]) > EPS or
        float(row["lambda_target"]) > EPS or
        float(row["lambda_new"]) > EPS
    ]
    write_resource_iteration_trace(output, resource_trace_rows)
    write_arrival_averaging_trace(output, arrival_trace_rows)
    write_train_iteration_trace(output, train_trace_rows)
    write_cycle_cells(output, cycle_rows)
    write_tail_cycle_cells(output, tail_cycle_rows)
    write_bottleneck_trace(output, cell_history, cycle_rows, len(history))
    summary = {
        "status": (
            "PASS" if final_selected and final_ub_validation["status"] == "PASS"
            else "FAILED_VALIDATION" if final_selected else "FAIL"
        ), "dataset": str(dataset),
        "path_k": args.path_k, "candidate_routes": candidate_count,
        "trains": len(trains), "iterations": len(history),
        "converged": bool(history and history[-1]["converged"]),
        "oracle_backend": "path-k-cpp", "price_policy": args.price_policy,
        "alpha": args.alpha, "beta": args.beta, "gamma": args.gamma,
        "arrival_averaging_policy": args.arrival_averaging_policy,
        "arrival_averaging_rho": args.arrival_averaging_rho,
        "arrival_averaging_rho_rule": (
            "exact_current_actual" if args.arrival_averaging_policy == "none"
            else "1/(k+1)" if args.arrival_averaging_policy == "msa"
            else "1/sqrt(k+1)" if args.arrival_averaging_policy == "sqrt-msa"
            else "constant"),
        "price_queue_tolerance": args.price_queue_tolerance,
        "wait_reference_minutes": args.wait_reference_minutes,
        "max_price_wait_minutes": args.max_price_wait_minutes,
        "queue_wait_cost_per_min": args.queue_wait_cost_per_min,
        "bin_minutes": args.bin_minutes,
        "departure_slack_minutes": args.departure_slack_minutes,
        "physical_feasibility_model": "HEADWAY_PHYSICAL" if lr_capacity_model == "headway-relaxation" else "CURRENT_PHYSICAL_VALIDATOR",
        "block30_semantics": BLOCK30_SEMANTICS,
        "physical_capacity_semantics": PHYSICAL_CAPACITY_SEMANTICS,
        "pricing_reference_capacity": BLOCK30_PRICING_REFERENCE_CAPACITY,
        "pricing_reference_capacity_semantics": (
            "Fluid-Queue/LR reference quantity per aggregate cell; it does not "
            "limit the number of trains using the resource during the whole "
            "30-minute window"),
        "lr_capacity_model": lr_capacity_model,
        "lr_capacity_map": str(lr_capacity_map_path) if lr_capacity_map_path else None,
        "lr_capacity_semantics": (
            "headway-consistent relaxed 30-minute occupancy upper bound used "
            "only in -lambda^T C"
            if lr_capacity_model == "headway-relaxation"
            else "diagnostic block30 reference RHS used only in -lambda^T C "
                 "and reference excess; never a hard timetable constraint"),
        "lr_capacity": BLOCK30_PRICING_REFERENCE_CAPACITY,
        "fluid_queue_arrival_semantics": "arc entry events",
        "fluid_queue_service_capacity_semantics": (
            "Fluid-Queue service quantity per fully open aggregate window; "
            "not physical train-count capacity"),
        "queue_units": "train", "waiting_units": "minute",
        "lambda_units": "generalized cost per occupied resource block",
        "active_time_block_baseline": "30-minute resource blocks only",
        "congestion_landmark_semantics": (
            "T0=onset, Tp=peak queue, Tc=clearance; P=Tc-T0; block-level approximation"),
        "preferred_schedule_time_semantics": (
            "terminal_want is train-specific and is independent of Tp"),
        "capacity_resolution_warning": (
            "30-minute cells are aggregation/pricing windows only; fully-open "
            "Cq=1, mu=1/30, and block30 C_ref=1 are reference quantities, not "
            "whole-window one-train physical limits"),
        "safety_headway_minutes": args.safety_headway_minutes,
        "origin_wait_cost_per_min": args.origin_wait_cost_per_min,
        "running_cost_per_min": args.running_cost_per_min,
        "siding_wait_cost_per_min": args.siding_wait_cost_per_min,
        "early_arrival_cost_per_min": args.early_arrival_cost_per_min,
        "late_arrival_cost_per_min": args.late_arrival_cost_per_min,
        "legacy_step_size_note": (
            "step-size arguments are used only by legacy-subgradient; ignored by fluid-queue"),
        "relaxed_argmins_used_for_dual": True,
        "ub_recovery_separated_from_dual_occupancy": True,
        "ub_recovery_errors": ub_recovery_errors,
        "ub_recovery_error_count": len(ub_recovery_errors),
        "final_ub_validation_status": final_ub_validation["status"],
        "final_ub_validation_conflict_count": final_ub_validation["conflict_count"],
        "dual_method": "Polyak projected subgradient" if args.price_policy == DUAL_SUBGRADIENT else None,
        "dual_capacity_certified": (
            lr_capacity_model == "headway-relaxation" and bool(lr_capacity_map)),
        "dual_iterations": len(dual_history),
        "dual_best_bound": best_dual_bound if math.isfinite(best_dual_bound) else None,
        "dual_best_iteration": dual_iteration_of_best,
        "dual_best_lambda": {
            f"{key[0]}:{key[1]}": value for key, value in best_lambda.items()
        },
        "dual_history": dual_history,
        "initial_lower_bound": history[0]["lower_bound"] if history else None,
        "initial_LB": history[0]["lower_bound"] if history else None,
        "best_lower_bound_diagnostic": best_lower_bound,
        "best_LB": best_lower_bound,
        "final_lower_bound_diagnostic": final_lower_bound,
        "final_LB": final_lower_bound,
        "iteration_of_best_lower_bound": iteration_of_best_lb,
        "final_max_lambda": max(lambdas.values(), default=0.0),
        "final_mean_lambda": (
            sum(final_lambda_values) / len(final_lambda_values)
            if final_lambda_values else 0.0),
        "final_nonzero_lambda_entries": sum(1 for value in lambdas.values() if value > EPS),
        "route_change_events": route_change_events,
        "total_route_changes": total_route_changes,
        "total_timing_changes": total_timing_changes,
        "final_changed_train_count": final_changed_train_count,
        "final_changed_route_count": final_changed_route_count,
        "final_changed_timing_count": final_changed_timing_count,
        "final_route_stable": final_route_stable,
        "final_trajectory_stable": final_trajectory_stable,
        "final_route_signature": final_route_hash,
        "final_trajectory_signature": final_trajectory_hash,
        "final_evaluation_lambda_iteration": len(history),
        "final_routes_evaluated_at_final_lambda": True,
        "final_lambda_dot_capacity": final_lambda_dot_capacity,
        "final_max_lr_occupancy": max(final_occupancy.values(), default=0.0),
        "final_max_lr_excess": max(0.0, max(
            capacity_excess(final_occupancy, model=lr_capacity_model,
                            capacity_map=lr_capacity_map).values(),
            default=0.0)),
        "final_max_queue_train": max((cell.queue_after_train for cell in final_cells.values()), default=0.0),
        "final_max_actual_queue": max((cell.queue_after_train for cell in final_cells.values()), default=0.0),
        "final_total_actual_queue": sum(cell.queue_after_train for cell in final_cells.values()),
        "final_max_backlog_clearance_min": max(final_finite_waits, default=0.0),
        "final_max_raw_clearance_wait_min": final_max_raw_wait,
        "final_max_price_queue": final_max_price_queue,
        "final_total_price_queue": final_total_price_queue,
        "final_max_price_backlog_clearance_min": max(final_price_finite_waits, default=0.0),
        "final_max_price_raw_clearance_wait_min": final_max_price_raw_wait,
        "final_max_lambda_target": max(final_target.values(), default=0.0),
        "final_price_wait_cap_hit_count": final_cap_hits,
        "final_positive_queue_cell_count": final_positive_queue_cells,
        "final_stale_price_cell_count": int(final_stale_stats["count"]),
        "stale_price_cell_count": int(final_stale_stats["count"]),
        "stale_price_mass": final_stale_stats["mass"],
        "max_stale_price": final_stale_stats["max"],
        "stale_price_count_gt_1": int(final_stale_stats["count_gt_1"]),
        "stale_price_count_gt_10": int(final_stale_stats["count_gt_10"]),
        "final_max_wait_to_reference_ratio": (
            final_max_raw_wait / args.wait_reference_minutes
            if args.wait_reference_minutes > 0 else math.inf),
        **final_costs,
        "final_relative_lambda_linf_change": (
            history[-1]["relative_lambda_linf_change"] if history else 0.0),
        "final_relative_lambda_linf": (
            history[-1]["relative_lambda_linf_change"] if history else 0.0),
        "arrival_average_l1_change": (
            history[-1].get("arrival_average_l1_change", 0.0) if history else 0.0),
        "arrival_average_linf_change": (
            history[-1].get("arrival_average_linf_change", 0.0) if history else 0.0),
        "actual_vs_average_arrival_l1": (
            history[-1].get("actual_vs_average_arrival_l1", 0.0) if history else 0.0),
        "actual_vs_average_arrival_linf": (
            history[-1].get("actual_vs_average_arrival_linf", 0.0) if history else 0.0),
        "price_queue_l1_change": (
            history[-1].get("price_queue_l1_change", 0.0) if history else 0.0),
        "price_queue_linf_change": (
            history[-1].get("price_queue_linf_change", 0.0) if history else 0.0),
        "aggregate_price_stable": bool(
            history[-1].get("aggregate_price_stable", False) if history else False),
        "detected_persistent_cycle": detected_persistent_cycle,
        "detected_signature_cycle": detected_signature_cycle,
        "detected_approximate_cell_cycle": detected_approximate_cell_cycle,
        "detected_cell_cycle_period": detected_cell_cycle_period,
        "detected_cycle_period": detected_cycle_period,
        "detected_cycle_signature_type": detected_cycle_signature_type,
        "dominant_cycle_cell": dominant_cycle,
        "cycle_lambda_amplitude": (
            dominant_cycle["cycle_lambda_amplitude"] if dominant_cycle else 0.0),
        "cycle_queue_amplitude": (
            dominant_cycle["cycle_queue_amplitude"] if dominant_cycle else 0.0),
        "cycle_price_queue_amplitude": (
            dominant_cycle.get("cycle_price_queue_amplitude", 0.0)
            if dominant_cycle else 0.0),
        "cycle_lambda_mass": sum(
            float(row["cycle_lambda_amplitude"]) for row in cycle_rows),
        "cycle_queue_mass": sum(
            float(row["cycle_queue_amplitude"]) for row in cycle_rows),
        "cycle_cells_lambda_amp_gt_1": sum(
            float(row["cycle_lambda_amplitude"]) > 1.0 for row in cycle_rows),
        "cycle_cells_lambda_amp_gt_10": sum(
            float(row["cycle_lambda_amplitude"]) > 10.0 for row in cycle_rows),
        "unique_cells_ever_hitting_cap": len(cap_cells_ever),
        "iterations_with_cap_hit": iterations_with_cap_hit,
        "iterations_with_any_cap_hit": iterations_with_cap_hit,
        "fraction_iterations_with_cap_hit": (
            iterations_with_cap_hit / len(history) if history else 0.0),
        "cycle_cell_count": len(cycle_rows),
        "whole_run_cycle_cell_count": len(cycle_rows),
        "largest_congestion_episode": largest_episode,
        **tail_diagnostics,
        "output_files": ["lambda_history.csv", "final_routes.tsv", "resource_state.csv",
                         "congestion_episodes.csv", "price_map.csv",
                         "resource_iteration_trace.csv", "arrival_averaging_trace.csv",
                         "train_iteration_trace.csv",
                         "cycle_cells.csv", "tail_cycle_cells.csv",
                         "bottleneck_trace.csv", "summary.json"],
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def beta_labels(row: dict[str, object]) -> list[str]:
    labels: list[str] = []
    has_oscillation = (
        bool(row["detected_persistent_cycle"]) or
        bool(row.get("detected_approximate_cell_cycle", False))
    )
    if bool(row["converged"]) and not has_oscillation:
        labels.append("STABLE")
    if has_oscillation:
        labels.append("OSCILLATORY")
    if float(row["final_max_queue_train"]) >= 4.0 or float(row["final_max_lr_excess"]) >= 4.0:
        labels.append("HIGH_CONGESTION")
    if float(row["fraction_iterations_with_cap_hit"]) > 0.25:
        labels.append("PRICE_SATURATED")
    if not bool(row["converged"]) and "OSCILLATORY" not in labels:
        labels.append("UNRESOLVED")
    return labels or ["UNRESOLVED"]


def beta_summary_row(summary: dict[str, object]) -> dict[str, object]:
    fields = [
        "beta", "iterations", "converged", "final_relative_lambda_linf_change",
        "final_route_stable", "final_trajectory_stable", "detected_persistent_cycle",
        "detected_signature_cycle", "detected_approximate_cell_cycle",
        "detected_cycle_period", "detected_cell_cycle_period",
        "detected_cycle_signature_type",
        "cycle_cell_count", "cycle_lambda_amplitude", "cycle_queue_amplitude",
        "total_route_changes", "total_timing_changes",
        "final_changed_train_count", "final_max_queue_train",
        "final_max_backlog_clearance_min", "final_max_lr_occupancy",
        "final_max_lr_excess", "final_max_lambda", "final_mean_lambda",
        "final_max_lambda_target", "unique_cells_ever_hitting_cap",
        "iterations_with_cap_hit", "best_lower_bound_diagnostic",
        "final_lower_bound_diagnostic", "iteration_of_best_lower_bound",
        "origin_wait_cost", "running_cost", "waiting_cost", "arrival_schedule_cost", "physical_cost",
        "lambda_cost", "total_priced_cost", "final_route_signature",
        "final_trajectory_signature",
    ]
    row = {field: summary.get(field, "") for field in fields}
    # Keep the concise names used by the calibration specification alongside
    # the more explicit diagnostic names retained in summary.json.
    row["final_relative_lambda_linf"] = summary.get(
        "final_relative_lambda_linf", summary.get("final_relative_lambda_linf_change", 0.0))
    row["initial_LB"] = summary.get("initial_LB", summary.get("initial_lower_bound", ""))
    row["best_lower_bound"] = summary.get(
        "best_LB", summary.get("best_lower_bound_diagnostic", ""))
    row["final_lower_bound"] = summary.get(
        "final_LB", summary.get("final_lower_bound_diagnostic", ""))
    row["iteration_of_best_lower_bound"] = summary.get(
        "iteration_of_best_lower_bound", "")
    row["iterations_with_any_cap_hit"] = summary.get(
        "iterations_with_any_cap_hit", summary.get("iterations_with_cap_hit", 0))
    row["fraction_iterations_with_cap_hit"] = summary.get(
        "fraction_iterations_with_cap_hit", 0.0)
    row["labels"] = ",".join(beta_labels(row))
    return row


def write_beta_calibration_report(root: Path, rows: list[dict[str, object]]) -> None:
    recommendations = sorted(
        rows,
        key=lambda row: (
            bool(row["detected_persistent_cycle"]),
            float(row["fraction_iterations_with_cap_hit"]),
            float(row["final_relative_lambda_linf_change"]),
            float(row["final_max_lr_excess"]),
            float(row["final_max_queue_train"]),
            float(row["physical_cost"]),
        ),
    )[:2]
    lines = [
        "# Beta calibration report",
        "",
        "Active formulation: 30-minute aggregation/pricing cells; diagnostic "
        "LR reference C_ref=1; fully-open Fluid Queue service quantity Cq=1, "
        "mu=1/30 train/min. These are not whole-window physical limits.",
        "Alpha, gamma, queue cost, tau0, wait cap, safety headway, and all "
        "generalized-cost parameters were held fixed.",
        "",
        "## Price-map interpretation",
        "",
        "For a fully open block and tau0=30, `w=30Q`, so the unclipped target "
        "is `lambda_hat = 30 * Q^beta` when alpha=theta_Q=1.",
        "This is a feedback-gain diagnostic, not a criterion by itself.",
        "",
        "## Results",
        "",
        "| beta | iterations | converged | persistent cycle | evidence | period | cell-cycle cells | max Q | max w | max LR excess | max lambda | mean lambda | cap-hit iterations | physical cost | final LB | labels |",
        "|---:|---:|:---:|:---:|:---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---|",
    ]
    for row in rows:
        display_period = (
            row["detected_cycle_period"]
            if bool(row["detected_persistent_cycle"])
            else row.get("detected_cell_cycle_period", 0)
        )
        display_evidence = row["detected_cycle_signature_type"]
        if (not bool(row["detected_persistent_cycle"]) and
                bool(row.get("detected_approximate_cell_cycle", False))):
            display_evidence = "cell-level"
        lines.append(
            f"| {row['beta']} | {row['iterations']} | {row['converged']} | "
            f"{row['detected_persistent_cycle']} | {display_evidence} | "
            f"{display_period} | {row['cycle_cell_count']} | "
            f"{float(row['final_max_queue_train']):.3g} | "
            f"{float(row['final_max_backlog_clearance_min']):.3g} | "
            f"{float(row['final_max_lr_excess']):.3g} | "
            f"{float(row['final_max_lambda']):.3g} | "
            f"{float(row['final_mean_lambda']):.3g} | "
            f"{row['iterations_with_cap_hit']} | "
            f"{float(row['physical_cost']):.3f} | "
            f"{float(row['final_lower_bound_diagnostic']):.3f} | {row['labels']} |"
        )
    lines.extend(["", "## Per-beta interpretation", ""])
    for row in rows:
        lines.extend([
            f"### beta={row['beta']}",
            "",
            f"- Classification: {row['labels']}",
            f"- Final route signature: `{row['final_route_signature']}`",
            f"- Final trajectory signature: `{row['final_trajectory_signature']}`",
            f"- Cycle evidence: persistent full-signature cycle = {row['detected_signature_cycle']}; "
            f"approximate cell-level cycle = {row['detected_approximate_cell_cycle']} "
            f"({row['cycle_cell_count']} cells); dominant lambda amplitude = "
            f"{float(row['cycle_lambda_amplitude']):.3g}",
            f"- Route changes: {row['total_route_changes']}; timing changes: {row['total_timing_changes']}",
            f"- Best LB: {float(row['best_lower_bound_diagnostic']):.3f}; "
            f"final LB: {float(row['final_lower_bound_diagnostic']):.3f}",
            "",
        ])
    lines.extend([
        "## Recommendation for the next gamma sweep",
        "",
        "No beta is selected from lambda magnitude alone. The candidates below "
        "are the two best diagnostic candidates under the ordering: no detected "
        "full-signature cycle, lower cap saturation, lower relative Linf, lower LR "
        "excess/queue, then physical cost. Cell-level cycle evidence is reported "
        "separately because a changing full trajectory can hide local two-cycles.",
        "",
    ])
    for row in recommendations:
        lines.append(f"- beta={row['beta']} ({row['labels']})")
    lines.extend([
        "",
        "These are candidates for a later gamma sweep, not a final beta winner. "
        "DEFAULT_BETA remains 4.",
    ])
    (root / "beta_calibration_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_beta_sweep(args: argparse.Namespace) -> dict[str, object]:
    if args.price_policy != FLUID_QUEUE:
        raise ValueError("--beta-sweep requires --price-policy fluid-queue")
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, object]] = []
    price_rows: list[dict[str, object]] = []
    for beta in (1, 2, 3, 4):
        child = argparse.Namespace(**vars(args))
        child.beta = float(beta)
        child.output = root / f"beta_{beta}"
        child.beta_sweep = False
        summary = solve(child)
        row = beta_summary_row(summary)
        summaries.append(row)
        with (child.output / "price_map.csv").open(newline="", encoding="utf-8") as stream:
            price_rows.extend(csv.DictReader(stream))

    fields = list(summaries[0])
    with (root / "beta_calibration_summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summaries)
    with (root / "price_map.csv").open("w", encoding="utf-8", newline="") as stream:
        fields = ["beta", "queue_train", "clearance_wait_min",
                  "unclipped_lambda_target", "clipped_wait_min",
                  "lambda_target_after_wait_cap"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(price_rows)
    write_beta_calibration_report(root, summaries)
    return {
        "status": "PASS", "beta_values": [1, 2, 3, 4],
        "output": str(root), "summary_file": str(root / "beta_calibration_summary.csv"),
        "report_file": str(root / "beta_calibration_report.md"),
    }


ARRIVAL_AVERAGING_SWEEP_POLICIES = (
    ("none", "exact_current_actual"),
    ("msa", "1/(k+1)"),
    ("sqrt-msa", "1/sqrt(k+1)"),
    ("constant", "rho=0.20"),
)


def arrival_averaging_summary_row(summary: dict[str, object]) -> dict[str, object]:
    """Select the compact comparison row for one arrival-state experiment."""
    fields = [
        "arrival_averaging_policy", "arrival_averaging_rho_rule", "beta", "gamma",
        "iterations", "converged", "aggregate_price_stable",
        "final_relative_lambda_linf", "tail_changed_train_rate",
        "tail_changed_route_rate", "tail_changed_timing_rate",
        "tail_trajectory_stable_fraction", "tail_joint_stable_fraction",
        "tail_cycle_cell_count", "tail_cycle_lambda_mass", "tail_cycle_queue_mass",
        "tail_cycle_cells_lambda_amp_gt_1", "tail_cycle_cells_lambda_amp_gt_10",
        "tail_mean_relative_lambda_linf", "tail_mean_max_queue",
        "tail_mean_max_lr_excess", "final_max_queue_train", "final_max_lr_excess",
        "final_max_price_queue", "arrival_average_l1_change",
        "arrival_average_linf_change", "actual_vs_average_arrival_l1",
        "actual_vs_average_arrival_linf", "price_queue_l1_change",
        "price_queue_linf_change", "stale_price_cell_count", "stale_price_mass",
        "max_stale_price", "stale_price_count_gt_1", "stale_price_count_gt_10",
        "tail_mean_stale_price_cell_count", "tail_mean_stale_price_mass",
        "fraction_iterations_with_cap_hit", "physical_cost", "lambda_cost",
        "total_priced_cost", "best_LB", "final_LB", "final_route_signature",
        "final_trajectory_signature",
    ]
    row = {field: summary.get(field, "") for field in fields}
    policy = summary.get("arrival_averaging_policy", "none")
    row["averaging_policy"] = (
        "constant-0.20" if policy == "constant" else policy)
    row["averaging_rho_rule"] = (
        "rho=0.20" if policy == "constant"
        else summary.get("arrival_averaging_rho_rule", ""))
    row["cap_fraction"] = summary.get("fraction_iterations_with_cap_hit", 0.0)
    row["best_LB"] = summary.get("best_LB", summary.get("best_lower_bound_diagnostic", ""))
    row["final_LB"] = summary.get("final_LB", summary.get("final_lower_bound_diagnostic", ""))
    return row


def arrival_averaging_screening_rank(row: dict[str, object]) -> tuple[object, ...]:
    """Rank policies for confirmation without treating averaging as convergence."""
    return (
        0 if bool(row["converged"]) else 1,
        float(row["tail_changed_train_rate"]),
        int(row["tail_cycle_cells_lambda_amp_gt_10"]),
        float(row["tail_cycle_lambda_mass"]),
        int(row["tail_cycle_cell_count"]),
        -float(row["tail_joint_stable_fraction"]),
        float(row["price_queue_linf_change"]),
        float(row["tail_mean_max_lr_excess"]),
        float(row["tail_mean_max_queue"]),
        float(row["stale_price_mass"]),
        float(row["physical_cost"]),
    )


def write_arrival_averaging_report(
    root: Path, rows: list[dict[str, object]], long_rows: list[dict[str, object]],
    robustness: dict[str, object] | None,
) -> None:
    lines = [
        "# Arrival averaging calibration report", "",
        "The physical model is unchanged: 30-minute aggregation/pricing cells, "
        "diagnostic LR reference C_ref=1, fully-open Cq=1, mu=1/30 train/min, "
        "PATH-K, generalized cost, "
        "MOW, and actual congestion episodes.", "",
        "Actual arrivals are produced by the current discrete timetable. "
        "Averaged arrivals are only the iterative aggregate state used to "
        "stabilize the congestion-price feedback:", "",
        "`A_actual --rho--> A_bar -> Q_price -> lambda_hat`; actual `Q_actual` "
        "remains authoritative for physical congestion and T0/Tp/Tc.", "",
        "The four primary runs use beta=3, gamma=0.10, alpha=1, tau0=30, "
        "wait cap=180, and 100 iterations unless strict convergence occurs.", "",
        "## Primary results", "",
        "| policy | rho rule | iterations | converged | aggregate stable | "
        "tail changed train | tail trajectory stable | tail cycle cells | "
        "cycle lambda mass | cycle lambda >10 | tail max Q | tail max LR excess | "
        "price queue Linf | stale mass | physical cost | labels |",
        "|:--|:--|---:|:---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--|",
    ]
    for row in rows:
        labels = []
        if bool(row["converged"]): labels.append("STRICTLY_CONVERGED")
        if bool(row["aggregate_price_stable"]): labels.append("AGGREGATE_STABLE")
        if float(row["tail_changed_train_rate"]) <= 0.01:
            labels.append("TRAJECTORY_STABLE_TAIL")
        if int(row["tail_cycle_cells_lambda_amp_gt_10"]) > 0:
            labels.append("SIGNIFICANT_CYCLE")
        lines.append(
            f"| {row['averaging_policy']} | {row['averaging_rho_rule']} | "
            f"{row['iterations']} | {row['converged']} | {row['aggregate_price_stable']} | "
            f"{float(row['tail_changed_train_rate']):.4f} | "
            f"{float(row['tail_trajectory_stable_fraction']):.3f} | "
            f"{row['tail_cycle_cell_count']} | "
            f"{float(row['tail_cycle_lambda_mass']):.3g} | "
            f"{row['tail_cycle_cells_lambda_amp_gt_10']} | "
            f"{float(row['tail_mean_max_queue']):.3g} | "
            f"{float(row['tail_mean_max_lr_excess']):.3g} | "
            f"{float(row['price_queue_linf_change']):.3g} | "
            f"{float(row['stale_price_mass']):.3g} | "
            f"{float(row['physical_cost']):.3f} | {','.join(labels) or 'UNRESOLVED'} |"
        )
    lines.extend(["", "## Selected confirmation runs", ""])
    if long_rows:
        lines.extend([
            "| policy | beta | gamma | iterations | converged | tail changed train | "
            "tail cycle cells | cycle lambda mass | tail max Q | tail max LR excess | "
            "physical cost |",
            "|:--|---:|---:|---:|:---:|---:|---:|---:|---:|---:|---:|",
        ])
        for row in long_rows:
            lines.append(
                f"| {row['averaging_policy']} | {row['beta']} | {row['gamma']} | "
                f"{row['confirmation_iterations']} | {row['converged']} | "
                f"{float(row['tail_changed_train_rate']):.4f} | "
                f"{row['tail_cycle_cell_count']} | "
                f"{float(row['tail_cycle_lambda_mass']):.3g} | "
                f"{float(row['tail_mean_max_queue']):.3g} | "
                f"{float(row['tail_mean_max_lr_excess']):.3g} | "
                f"{float(row['physical_cost']):.3f} |"
            )
    else:
        lines.append("No confirmation runs were selected.")
    lines.extend(["", "## Interpretation", ""])
    lines.append(
        "A method is not called successful from aggregate price stability alone. "
        "The primary questions are whether discrete train changes and significant "
        "local cycles decrease, whether Q_price stabilizes, and whether actual "
        "queue/LR excess and physical cost remain acceptable."
    )
    if robustness is None:
        lines.append("The conditional beta=2, gamma=0.15 robustness check was not justified by the screening results.")
    else:
        lines.append(
            f"Conditional robustness check: policy={robustness['arrival_averaging_policy']}, "
            f"beta=2, gamma=0.15, iterations={robustness['confirmation_iterations']}."
        )
    lines.extend([
        "", "The formal solver convergence criterion remains trajectory_stable "
        "AND price_stable for stable_rounds consecutive iterations. "
        "aggregate_price_stable is diagnostic-only.",
    ])
    (root / "arrival_averaging_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def run_arrival_averaging_sweep(args: argparse.Namespace) -> dict[str, object]:
    if args.price_policy != FLUID_QUEUE:
        raise ValueError("--arrival-averaging-sweep requires --price-policy fluid-queue")
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    primary_rows: list[dict[str, object]] = []
    for policy, _ in ARRIVAL_AVERAGING_SWEEP_POLICIES:
        child = argparse.Namespace(**vars(args))
        child.beta = 3.0
        child.gamma = 0.10
        child.max_iterations = 100
        child.output = root / (
            "no_average" if policy == "none" else
            "sqrt_msa" if policy == "sqrt-msa" else
            "constant_0p20" if policy == "constant" else "msa")
        child.arrival_averaging_policy = policy
        child.arrival_averaging_sweep = False
        child.beta_sweep = False
        child.gamma_sweep = False
        primary_rows.append(arrival_averaging_summary_row(solve(child)))

    fields = list(primary_rows[0])
    with (root / "arrival_averaging_summary.csv").open(
            "w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(primary_rows)

    ranked = sorted(primary_rows, key=arrival_averaging_screening_rank)
    selected_rows = ranked[:2]
    long_rows: list[dict[str, object]] = []
    for row in selected_rows:
        child = argparse.Namespace(**vars(args))
        child.beta = float(row["beta"])
        child.gamma = float(row["gamma"])
        child.max_iterations = 300
        child.arrival_averaging_policy = str(row["arrival_averaging_policy"])
        child.output = root / f"{child.arrival_averaging_policy.replace('-', '_')}_300"
        child.arrival_averaging_sweep = False
        child.beta_sweep = False
        child.gamma_sweep = False
        long_row = arrival_averaging_summary_row(solve(child))
        long_row["confirmation_iterations"] = long_row["iterations"]
        long_rows.append(long_row)
    if long_rows:
        long_fields = list(long_rows[0])
        with (root / "arrival_averaging_long_run_summary.csv").open(
                "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=long_fields)
            writer.writeheader()
            writer.writerows(long_rows)

    baseline = next(row for row in primary_rows
                    if row["arrival_averaging_policy"] == "none")
    robustness: dict[str, object] | None = None
    candidate = next((row for row in ranked
                      if row["arrival_averaging_policy"] != "none"), None)
    materially_better = bool(candidate) and (
        float(candidate["tail_changed_train_rate"]) <
        0.90 * float(baseline["tail_changed_train_rate"]) or
        float(candidate["tail_cycle_lambda_mass"]) <
        0.75 * float(baseline["tail_cycle_lambda_mass"]) or
        bool(candidate["aggregate_price_stable"]) and
        not bool(baseline["aggregate_price_stable"])
    )
    if materially_better and candidate is not None:
        child = argparse.Namespace(**vars(args))
        child.beta = 2.0
        child.gamma = 0.15
        child.max_iterations = 100
        child.arrival_averaging_policy = str(candidate["arrival_averaging_policy"])
        child.output = root / f"{child.arrival_averaging_policy.replace('-', '_')}_beta2_gamma0p15"
        child.arrival_averaging_sweep = False
        child.beta_sweep = False
        child.gamma_sweep = False
        robustness = arrival_averaging_summary_row(solve(child))
        robustness["confirmation_iterations"] = robustness["iterations"]

    write_arrival_averaging_report(root, primary_rows, long_rows, robustness)
    return {
        "status": "PASS", "beta": 3.0, "gamma": 0.10,
        "policies": [policy for policy, _ in ARRIVAL_AVERAGING_SWEEP_POLICIES],
        "primary_iterations": 100,
        "selected_for_confirmation": [
            {"policy": row["arrival_averaging_policy"],
             "beta": row["beta"], "gamma": row["gamma"]}
            for row in selected_rows
        ],
        "conditional_beta2_robustness": (
            {"policy": robustness["arrival_averaging_policy"],
             "iterations": robustness["confirmation_iterations"]}
            if robustness else None),
        "output": str(root),
        "summary_file": str(root / "arrival_averaging_summary.csv"),
        "long_run_summary_file": str(root / "arrival_averaging_long_run_summary.csv"),
        "report_file": str(root / "arrival_averaging_report.md"),
    }


GAMMA_SWEEP_BETAS = (2.0, 3.0)
GAMMA_SWEEP_VALUES = (0.03, 0.05, 0.10, 0.15)


def gamma_labels(row: dict[str, object]) -> list[str]:
    """Classify a gamma run using explicit, diagnostic-only thresholds."""
    labels: list[str] = []
    converged = bool(row["converged"])
    tail_cycle_count = int(row["tail_cycle_cell_count"])
    changed_rate = float(row["tail_changed_train_rate"])
    joint_fraction = float(row["tail_joint_stable_fraction"])
    stale_mean = float(row["tail_mean_stale_price_cell_count"])
    active_mean = float(row["tail_mean_active_positive_lambda_cells"])
    if converged:
        labels.append("STRICTLY_CONVERGED")
    if (not bool(row["tail_detected_signature_cycle"]) and
            tail_cycle_count == 0 and changed_rate <= 0.01 and
            joint_fraction >= 0.90):
        labels.append("TAIL_STABLE")
    if tail_cycle_count >= 3 or changed_rate > 0.05:
        labels.append("LOCAL_CHATTERING")
    if stale_mean > max(1.0, 0.10 * max(active_mean, 1.0)):
        labels.append("PRICE_SLOW_DECAY")
    if (float(row["tail_mean_max_queue"]) >= 4.0 or
            float(row["tail_mean_max_lr_excess"]) >= 4.0):
        labels.append("HIGH_CONGESTION")
    if float(row["tail_fraction_iterations_with_cap_hit"]) > 0.25:
        labels.append("PRICE_SATURATED")
    if not converged and not labels:
        labels.append("IMPROVING_BUT_UNRESOLVED"
                      if float(row["tail_relative_linf_trend"]) < 0.0
                      else "UNRESOLVED")
    return labels or ["UNRESOLVED"]


def gamma_summary_row(summary: dict[str, object]) -> dict[str, object]:
    fields = [
        "beta", "gamma", "iterations", "converged",
        "final_relative_lambda_linf", "final_route_stable",
        "final_trajectory_stable", "detected_signature_cycle",
        "detected_approximate_cell_cycle", "whole_run_cycle_cell_count",
        "tail_window", "tail_changed_train_rate", "tail_changed_route_rate",
        "tail_changed_timing_rate", "tail_route_stable_fraction",
        "tail_trajectory_stable_fraction", "tail_price_stable_fraction",
        "tail_joint_stable_fraction", "tail_mean_relative_lambda_linf",
        "tail_max_relative_lambda_linf", "tail_relative_linf_trend",
        "tail_mean_lambda_linf_change", "tail_max_lambda_linf_change",
        "tail_mean_max_lambda", "tail_max_max_lambda",
        "tail_mean_max_queue", "tail_max_max_queue",
        "tail_mean_max_lr_excess", "tail_max_max_lr_excess",
        "tail_mean_total_queue", "tail_detected_signature_cycle",
        "tail_detected_cycle_period", "tail_detected_cycle_signature_type",
        "tail_cycle_cell_count", "tail_mean_cycle_lambda_amplitude",
        "tail_max_cycle_lambda_amplitude", "tail_mean_cycle_queue_amplitude",
        "tail_max_cycle_queue_amplitude", "tail_iterations_with_cap_hit",
        "tail_fraction_iterations_with_cap_hit", "tail_unique_cells_hitting_cap",
        "tail_mean_active_positive_lambda_cells",
        "tail_final_active_positive_lambda_cells", "tail_mean_positive_queue_cells",
        "tail_mean_stale_price_cell_count", "tail_final_stale_price_cell_count",
        "final_stale_price_cell_count", "final_max_queue_train",
        "final_max_backlog_clearance_min", "final_max_lr_occupancy",
        "final_max_lr_excess", "final_max_lambda", "final_mean_lambda",
        "final_max_lambda_target", "best_lower_bound_diagnostic",
        "final_lower_bound_diagnostic", "iteration_of_best_lower_bound",
        "origin_wait_cost", "running_cost", "waiting_cost", "arrival_schedule_cost", "physical_cost",
        "lambda_cost", "total_priced_cost", "final_route_signature",
        "final_trajectory_signature",
    ]
    row = {field: summary.get(field, "") for field in fields}
    row["best_lower_bound"] = summary.get(
        "best_LB", summary.get("best_lower_bound_diagnostic", ""))
    row["final_lower_bound"] = summary.get(
        "final_LB", summary.get("final_lower_bound_diagnostic", ""))
    row["labels"] = ",".join(gamma_labels(row))
    return row


def gamma_screening_rank(row: dict[str, object]) -> tuple[object, ...]:
    """Ranking used only to select at most two long-run confirmations."""
    return (
        0 if bool(row["converged"]) else 1,
        float(row["tail_changed_train_rate"]),
        int(row["tail_cycle_cell_count"]),
        -float(row["tail_joint_stable_fraction"]),
        float(row["tail_mean_relative_lambda_linf"]),
        float(row["tail_mean_max_lr_excess"]),
        float(row["tail_mean_max_queue"]),
        float(row["tail_fraction_iterations_with_cap_hit"]),
        float(row["tail_mean_stale_price_cell_count"]),
        float(row["physical_cost"]),
    )


def gamma_pareto_frontier(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return non-dominated rows for stability/chatter/congestion/cost metrics."""
    lower_fields = (
        "tail_changed_train_rate", "tail_cycle_cell_count",
        "tail_mean_max_queue", "tail_mean_max_lr_excess",
        "tail_fraction_iterations_with_cap_hit",
        "tail_mean_stale_price_cell_count", "physical_cost",
    )
    frontier: list[dict[str, object]] = []
    for candidate in rows:
        dominated = False
        for other in rows:
            if other is candidate:
                continue
            no_worse = all(
                float(other[field]) <= float(candidate[field])
                for field in lower_fields
            ) and float(other["tail_joint_stable_fraction"]) >= float(
                candidate["tail_joint_stable_fraction"])
            strictly_better = any(
                float(other[field]) < float(candidate[field])
                for field in lower_fields
            ) or float(other["tail_joint_stable_fraction"]) > float(
                candidate["tail_joint_stable_fraction"])
            if no_worse and strictly_better:
                dominated = True
                break
        if not dominated:
            frontier.append(candidate)
    return sorted(frontier, key=lambda row: (
        float(row["physical_cost"]),
        float(row["tail_mean_max_queue"]),
        float(row["tail_changed_train_rate"]),
    ))


def gamma_fs(value: float) -> str:
    return f"{value:.2f}".replace(".", "p")


def write_gamma_rows(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_gamma_calibration_report(
    root: Path,
    rows: list[dict[str, object]],
    long_rows: list[dict[str, object]],
    stability_choice: dict[str, object],
    tradeoff_choice: dict[str, object],
    pareto_rows: list[dict[str, object]],
) -> None:
    lines = [
        "# Gamma calibration report",
        "",
        "Fixed formulation: 30-minute aggregation/pricing cells; diagnostic "
        "LR reference C_ref=1; fully-open Fluid Queue service quantity "
        "Cq=1 train-equivalent per cell; mu=1/30 train/min; alpha=1; theta_Q=1; "
        "tau0=30 min; wait cap=180 min; safety headway=3 min.",
        "The primary runs use beta in {2,3} and gamma in {0.03,0.05,0.10,0.15}. "
        "Gamma changes temporal smoothing only; it does not change lambda_hat.",
        "",
        "Tail diagnostics use the final 20 post-initial iteration transitions. "
        "Formal convergence remains trajectory_stable AND price_stable for "
        "stable_rounds consecutive iterations; tail fractions are diagnostic only.",
        "",
        "Transparent labels: TAIL_STABLE requires zero tail cell cycles, "
        "tail changed-train rate <=0.01, joint stable fraction >=0.90, and no "
        "tail signature cycle. LOCAL_CHATTERING means at least 3 tail cycle "
        "cells or changed-train rate >0.05. PRICE_SLOW_DECAY means mean stale "
        "positive-price cells exceed max(1,10% of mean active positive-price cells).",
        "",
        "## Screening results",
        "",
        "| beta | gamma | converged | tail changed-train | tail trajectory stable | tail relative Linf | tail cycle cells | tail max Q | tail max LR excess | stale cells | cap fraction | physical cost | labels |",
        "|---:|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['beta']} | {row['gamma']} | {row['converged']} | "
            f"{float(row['tail_changed_train_rate']):.4f} | "
            f"{float(row['tail_trajectory_stable_fraction']):.3f} | "
            f"{float(row['tail_mean_relative_lambda_linf']):.4f} | "
            f"{row['tail_cycle_cell_count']} | "
            f"{float(row['tail_max_max_queue']):.3g} | "
            f"{float(row['tail_max_max_lr_excess']):.3g} | "
            f"{float(row['tail_mean_stale_price_cell_count']):.3g} | "
            f"{float(row['tail_fraction_iterations_with_cap_hit']):.3f} | "
            f"{float(row['physical_cost']):.3f} | {row['labels']} |"
        )
    for beta in GAMMA_SWEEP_BETAS:
        lines.extend(["", f"## beta={beta:g}", ""])
        for row in rows:
            if float(row["beta"]) != beta:
                continue
            lines.append(
                f"- gamma={row['gamma']}: tail changed-train rate "
                f"{float(row['tail_changed_train_rate']):.4f}, tail cycle cells "
                f"{row['tail_cycle_cell_count']}, mean stale cells "
                f"{float(row['tail_mean_stale_price_cell_count']):.3g}, "
                f"mean max Q {float(row['tail_mean_max_queue']):.3g}, mean max LR excess "
                f"{float(row['tail_mean_max_lr_excess']):.3g}, physical cost "
                f"{float(row['physical_cost']):.3f}; {row['labels']}."
            )
    lines.extend(["", "## 300-iteration confirmations", ""])
    if long_rows:
        lines.append("| beta | gamma | iterations | converged | tail changed-train | tail cycle cells | tail mean max Q | tail mean max LR excess | physical cost | reason |")
        lines.append("|---:|---:|---:|:---:|---:|---:|---:|---:|---:|:---|")
        for row in long_rows:
            lines.append(
                f"| {row['beta']} | {row['gamma']} | {row['confirmation_iterations']} | "
                f"{row['converged']} | {float(row['tail_changed_train_rate']):.4f} | "
                f"{row['tail_cycle_cell_count']} | {float(row['tail_mean_max_queue']):.3g} | "
                f"{float(row['tail_mean_max_lr_excess']):.3g} | "
                f"{float(row['physical_cost']):.3f} | {row['selected_for_confirmation_reason']} |"
            )
    else:
        lines.append("No confirmation runs were selected.")
    lines.extend([
        "", "## Two conclusions", "",
        f"- Strongest numerical stability by the stated lexicographic diagnostic "
        f"ranking: beta={stability_choice['beta']}, gamma={stability_choice['gamma']}.",
        f"- Best stability-vs-congestion-cost Pareto choice: beta={tradeoff_choice['beta']}, "
        f"gamma={tradeoff_choice['gamma']}. The screening Pareto frontier contains: "
        + ", ".join(f"({row['beta']},{row['gamma']})" for row in pareto_rows) + ".",
        "- A smaller gamma is not treated as automatic success: lower movement is "
        "compared jointly with tail chattering, stale prices, queue, LR excess, "
        "cap saturation, and physical generalized cost.",
        "- Lower-bound values remain diagnostic only. Fluid-Queue updates are not "
        "dual subgradient ascent, so monotone LB improvement is not expected.",
        "- DEFAULT_BETA remains 4 and DEFAULT_GAMMA remains 0.15.",
    ])
    (root / "gamma_calibration_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def run_gamma_sweep(args: argparse.Namespace) -> dict[str, object]:
    if args.price_policy != FLUID_QUEUE:
        raise ValueError("--gamma-sweep requires --price-policy fluid-queue")
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    primary_limit = args.max_iterations if args.max_iterations_explicit else 100
    screening_rows: list[dict[str, object]] = []
    for beta in GAMMA_SWEEP_BETAS:
        for gamma in GAMMA_SWEEP_VALUES:
            child = argparse.Namespace(**vars(args))
            child.beta = beta
            child.gamma = gamma
            child.max_iterations = primary_limit
            child.output = root / f"beta_{int(beta)}_gamma_{gamma_fs(gamma)}"
            child.beta_sweep = False
            child.gamma_sweep = False
            summary = solve(child)
            screening_rows.append(gamma_summary_row(summary))

    write_gamma_rows(root / "gamma_calibration_summary.csv", screening_rows)
    ranked = sorted(screening_rows, key=gamma_screening_rank)
    pareto_rows = gamma_pareto_frontier(screening_rows)
    stability_choice = ranked[0]
    tradeoff_choice = pareto_rows[0] if pareto_rows else ranked[0]
    selected_candidates = [stability_choice]
    if tradeoff_choice is not stability_choice:
        selected_candidates.append(tradeoff_choice)
    elif len(ranked) > 1:
        selected_candidates.append(ranked[1])
    selected_keys = {
        (float(row["beta"]), float(row["gamma"]))
        for row in selected_candidates
    }
    selected_rows = [
        row for row in screening_rows
        if (float(row["beta"]), float(row["gamma"])) in selected_keys
    ]
    long_rows: list[dict[str, object]] = []
    for row in selected_rows:
        reason_parts: list[str] = []
        if row is stability_choice:
            reason_parts.append("strongest_numerical_stability")
        if row is tradeoff_choice:
            reason_parts.append("pareto_stability_congestion_cost")
        child = argparse.Namespace(**vars(args))
        child.beta = float(row["beta"])
        child.gamma = float(row["gamma"])
        child.max_iterations = 300
        child.output = root / (
            f"beta_{int(child.beta)}_gamma_{gamma_fs(child.gamma)}_300")
        child.beta_sweep = False
        child.gamma_sweep = False
        summary = solve(child)
        long_row = gamma_summary_row(summary)
        long_row["screening_iterations"] = primary_limit
        long_row["confirmation_iterations"] = summary["iterations"]
        long_row["selected_for_confirmation_reason"] = "+".join(reason_parts) or "top_two_screening_rank"
        long_rows.append(long_row)
    write_gamma_rows(root / "gamma_long_run_summary.csv", long_rows)
    write_gamma_calibration_report(
        root, screening_rows, long_rows,
        stability_choice, tradeoff_choice, pareto_rows,
    )
    return {
        "status": "PASS", "beta_values": list(GAMMA_SWEEP_BETAS),
        "gamma_values": list(GAMMA_SWEEP_VALUES),
        "screening_iterations": primary_limit,
        "selected_for_confirmation": [
            {"beta": row["beta"], "gamma": row["gamma"]}
            for row in selected_rows
        ],
        "output": str(root),
        "summary_file": str(root / "gamma_calibration_summary.csv"),
        "long_run_summary_file": str(root / "gamma_long_run_summary.csv"),
        "report_file": str(root / "gamma_calibration_report.md"),
    }


def _assert_close(actual: float, expected: float, tol: float = 1e-9) -> None:
    if abs(actual - expected) > tol:
        raise AssertionError(f"expected {expected}, got {actual}")


def self_test() -> None:
    """Deterministic 30-minute queue, episode, pricing, MOW and legacy tests."""
    arrivals = {(0, 0): 1.0, (0, 1): 3.0}
    cells = fluid_queue_cells(arrivals, {}, {},
                              {0: Arc(0, 0, 1, 1.0, True, "0", 60.0, 60.0)},
                              [], bin_minutes=30.0, horizon=120.0)
    _assert_close(cells[(0, 0)].queue_after_train, 0.0)
    _assert_close(cells[(0, 1)].queue_after_train, 2.0)
    _assert_close(cells[(0, 2)].queue_after_train, 1.0)
    _assert_close(cells[(0, 3)].queue_after_train, 0.0)
    _assert_close(cells[(0, 1)].clearance_wait_min, 60.0)
    _assert_close(cells[(0, 2)].clearance_wait_min, 30.0)
    episodes = congestion_episodes(
        cells, bin_minutes=30.0, queue_wait_cost_per_min=1.0,
        early_arrival_cost_per_min=1.0, late_arrival_cost_per_min=1.0)
    assert len(episodes) == 1, episodes
    episode = episodes[0]
    _assert_close(episode["T0_onset_min"], 30.0)
    _assert_close(episode["Tp_peak_min"], 60.0)
    _assert_close(episode["Tc_clear_min"], 120.0)
    _assert_close(episode["duration_P_min"], 90.0)
    _assert_close(episode["omega_train"], episode["Qmax_train"])
    _assert_close(episode["omega_minus_qmax"], 0.0)
    assert not any(key.startswith("T1") for key in episode)
    d_plus = cells[(0, 1)].arrival_count
    c_plus = cells[(0, 1)].queue_service_capacity_train
    _assert_close(d_plus - c_plus, cells[(0, 1)].queue_after_train)
    _assert_close(fluid_lambda_target(cells[(0, 1)], alpha=1.0, beta=2.0,
                                      wait_reference_minutes=30.0,
                                      queue_wait_cost_per_min=1.0,
                                      max_price_wait_minutes=180.0), 120.0)
    _assert_close((1.0 - 0.15) * 20.0 + 0.15 * 120.0, 35.0)
    mow = [{"a": 0, "b": 1, "start": 30.0, "end": 60.0}]
    closed = fluid_queue_cells({(0, 0): 2.0}, {}, {},
                               {0: Arc(0, 0, 1, 1.0, True, "0", 60.0, 60.0)},
                               mow, bin_minutes=30.0, horizon=90.0)
    _assert_close(closed[(0, 0)].queue_after_train, 1.0)
    _assert_close(closed[(0, 0)].clearance_wait_min, 60.0)
    overlapping = {0: [(5.0, 20.0), (10.0, 25.0)]}
    _assert_close(open_minutes_for_block(overlapping, 0, 0.0, 30.0), 10.0)
    selected = {"T": {"legs": "7,25,28"}}
    occupancy = resource_occupancy(selected, 30.0, 3.0)
    assert occupancy == {(7, 0): 1.0, (7, 1): 1.0}, occupancy
    physical = 10.0 + 20.0 + 5.0 + 8.0
    _assert_close(physical + 7.0, 50.0)
    damped, raw = legacy_lambda_update(
        {(0, 0): 20.0}, {(0, 0): 3.0}, iteration=0,
        step_size_initial=1.0, minimum_step_size=0.0, gamma=0.15)
    _assert_close(raw[(0, 0)], 22.0)
    _assert_close(damped[(0, 0)], 20.3)

    trajectory_a = {"T": {"route_index": "0", "arc_ids": "1,2",
                           "departure_min": "0", "arrival_min": "20",
                           "waits": "0,0", "legs": "1,0,10;2,10,20"}}
    trajectory_b = {"T": {"route_index": "0", "arc_ids": "1,2",
                           "departure_min": "0", "arrival_min": "20",
                           "waits": "0,0", "legs": "1,0,10;2,10,20"}}
    trajectory_c = {"T": {"route_index": "0", "arc_ids": "1,2",
                           "departure_min": "1", "arrival_min": "21",
                           "waits": "0,0", "legs": "1,1,11;2,11,21"}}
    assert trajectory_signature_digest(trajectory_a) == trajectory_signature_digest(trajectory_b)
    assert trajectory_signature_digest(trajectory_a) != trajectory_signature_digest(trajectory_c)

    route_counter: dict[int, int] = {}
    trajectory_counter: dict[int, int] = {}
    route_history: list[str] = []
    trajectory_history: list[str] = []
    for value in ("A", "B", "A", "B", "A", "B"):
        route_history.append(value)
        trajectory_history.append(value)
        cycle = cycle_observation(
            route_history, trajectory_history, route_counter, trajectory_counter)
    assert cycle["detected_persistent_cycle"] and cycle["detected_cycle_period"] == 2
    route_counter = {}
    trajectory_counter = {}
    route_history = []
    trajectory_history = []
    for value in ("A", "B", "C", "D", "E", "F"):
        route_history.append(value)
        trajectory_history.append(value)
        cycle = cycle_observation(
            route_history, trajectory_history, route_counter, trajectory_counter)
    assert not cycle["detected_persistent_cycle"]

    gamma = 0.15
    high_target = 120.0
    high = high_target / (2.0 - gamma)
    low = (1.0 - gamma) * high_target / (2.0 - gamma)
    _assert_close((1.0 - gamma) * low + gamma * high_target, high)
    _assert_close((1.0 - gamma) * high, low)
    _assert_close((high - low) / high, gamma)
    # A relative Linf near gamma is consistent with this exact period-2 map;
    # actual cycle declarations still require repeated signatures/cell states.

    for beta in (1.0, 2.0, 3.0, 4.0):
        q2 = ResourceCell(0, 0, 0.0, 0.0, 1.0, 30.0, 1.0,
                          1.0 / 30.0, 0.0, 2.0, 60.0, 0)
        _assert_close(fluid_lambda_target(
            q2, alpha=1.0, beta=beta, wait_reference_minutes=30.0,
            queue_wait_cost_per_min=1.0, max_price_wait_minutes=180.0),
            30.0 * 2.0 ** beta)
        q0 = ResourceCell(0, 0, 0.0, 0.0, 1.0, 30.0, 1.0,
                          1.0 / 30.0, 0.0, 0.0, 0.0, 0)
        _assert_close(fluid_lambda_target(
            q0, alpha=1.0, beta=beta, wait_reference_minutes=30.0,
            queue_wait_cost_per_min=1.0, max_price_wait_minutes=180.0), 0.0)

    def fake_history(changed: list[int]) -> list[dict[str, object]]:
        return [{
            "iteration": i, "changed_train_count": value,
            "changed_route_count": value, "changed_timing_count": value,
            "route_stable": value == 0, "trajectory_stable": value == 0,
            "price_stable": value == 0, "relative_lambda_linf_change": 0.0,
            "lambda_linf_change": 0.0, "max_lambda": 0.0,
            "max_queue_train": 0.0, "max_lr_excess": 0.0,
            "total_queue_train": 0.0, "price_wait_cap_hit_count": 0,
        } for i, value in enumerate(changed)]

    tail_test = compute_tail_diagnostics(
        fake_history([0, 1, 2, 4]), {}, ["A"] * 4, ["A"] * 4, {},
        tail_window=2, train_count=4, max_price_wait_minutes=180.0)
    _assert_close(tail_test[0]["tail_changed_train_rate"], 0.75)
    assert tail_test[0]["tail_transition_count"] == 2

    transient_hashes = ["A", "B", "A", "B", "A", "B", "C", "C", "C", "C"]
    full_counter_route: dict[int, int] = {}
    full_counter_trajectory: dict[int, int] = {}
    full_detected = False
    for i, value in enumerate(transient_hashes):
        full_observation = cycle_observation(
            transient_hashes[:i + 1], transient_hashes[:i + 1],
            full_counter_route, full_counter_trajectory)
        full_detected = full_detected or bool(
            full_observation["detected_persistent_cycle"])
    assert full_detected
    tail_transient = tail_signature_diagnostics(
        transient_hashes, transient_hashes, tail_iteration_indices(10, 4))
    assert not tail_transient["tail_detected_signature_cycle"]

    persistent_tail = ["A", "B", "A", "B", "A", "B"]
    tail_persistent = tail_signature_diagnostics(
        persistent_tail, persistent_tail, tail_iteration_indices(6, 5))
    assert tail_persistent["tail_detected_signature_cycle"]
    assert tail_persistent["tail_detected_cycle_period"] == 2

    stale = ResourceCell(
        arc_id=0, time_bin=0, arrival_count=0.0,
        lr_occupancy_usage=0.0, lr_capacity=1.0, mow_open_minutes=30.0,
        queue_service_capacity_train=1.0, mu_train_per_min=1.0 / 30.0,
        queue_before_train=0.0, queue_after_train=0.0,
        clearance_wait_min=0.0, unresolved_queue=0,
        lambda_old=5.0, lambda_target=0.0, lambda_new=5.0,
    )
    assert stale_price_count_from_cells({(0, 0): stale}) == 1

    for gamma, expected in ((0.15, 85.0), (0.10, 90.0),
                            (0.05, 95.0), (0.03, 97.0)):
        _assert_close((1.0 - gamma) * 100.0 + gamma * 0.0, expected)
    target_by_gamma = [fluid_lambda_target(
        q2, alpha=1.0, beta=2.0, wait_reference_minutes=30.0,
        queue_wait_cost_per_min=1.0, max_price_wait_minutes=180.0)
        for _ in (0.03, 0.05, 0.10, 0.15)]
    assert target_by_gamma == [target_by_gamma[0]] * 4

    # Arrival-state MSA tests.  These deliberately keep actual and price
    # arrivals/queues separate so a future refactor cannot silently replace
    # physical congestion diagnostics with the smoothed price state.
    averaged = update_arrival_average(
        {(0, 0): 2.0}, {(0, 0): 4.0}, policy="msa", rho=0.5)
    _assert_close(averaged[(0, 0)], 3.0)
    _assert_close(arrival_averaging_rho("msa", 0, 0.2), 1.0)
    _assert_close(arrival_averaging_rho("msa", 3, 0.2), 0.25)
    _assert_close(arrival_averaging_rho("sqrt-msa", 3, 0.2), 0.5)
    _assert_close(arrival_averaging_rho("constant", 3, 0.2), 0.2)
    assert update_arrival_average(
        {(0, 0): 2.0}, {(0, 0): 4.0}, policy="none", rho=1.0) == {(0, 0): 4.0}
    same_actual = fluid_queue_cells(
        {(0, 0): 2.0}, {}, {},
        {0: Arc(0, 0, 1, 1.0, True, "0", 60.0, 60.0)}, [],
        bin_minutes=30.0, horizon=60.0,
        price_arrivals={(0, 0): 2.0},
    )
    _assert_close(same_actual[(0, 0)].queue_after_train,
                  same_actual[(0, 0)].price_queue_after_train)
    _assert_close(update_arrival_average(
        {(0, 0): 2.0}, {(0, 0): 4.0}, policy="constant", rho=0.2
    )[(0, 0)], 2.4)

    separated = fluid_queue_cells(
        {(0, 1): 2.0}, {}, {},
        {0: Arc(0, 0, 1, 1.0, True, "0", 60.0, 60.0)}, [],
        bin_minutes=30.0, horizon=90.0,
        price_arrivals={(0, 1): 1.0, (0, 2): 1.0},
    )
    assert separated[(0, 1)].queue_after_train != separated[(0, 1)].price_queue_after_train
    assert len(congestion_episodes(
        separated, bin_minutes=30.0, queue_wait_cost_per_min=1.0,
        early_arrival_cost_per_min=1.0, late_arrival_cost_per_min=1.0)) == 1
    price_q2 = ResourceCell(
        0, 0, 0.0, 0.0, 1.0, 30.0, 1.0, 1.0 / 30.0,
        0.0, 0.0, 0.0, 0, price_queue_after_train=2.0,
        price_clearance_wait_min=60.0,
    )
    _assert_close(fluid_lambda_target(
        price_q2, alpha=1.0, beta=2.0, wait_reference_minutes=30.0,
        queue_wait_cost_per_min=1.0, max_price_wait_minutes=180.0,
        use_price_queue=True), 120.0)
    stale_stats = stale_price_stats_from_snapshots([
        {"lambda_new": 0.5, "queue_after_train": 0.0, "lambda_target": 0.0},
        {"lambda_new": 12.0, "queue_after_train": 0.0, "lambda_target": 0.0},
    ])
    _assert_close(stale_stats["count"], 2.0)
    _assert_close(stale_stats["mass"], 12.5)
    _assert_close(stale_stats["count_gt_1"], 1.0)
    _assert_close(stale_stats["count_gt_10"], 1.0)

    # Certified dual/CBS helper tests.  These are deliberately tiny and do
    # not invoke the external executable, so they also exercise the exact
    # mathematical data flow in isolation.
    cap_map = {(7, 0): 2.0}
    _assert_close(capacity_value(
        (7, 0), model="headway-relaxation", capacity_map=cap_map), 2.0)
    _assert_close(lambda_dot_capacity(
        {(7, 0): 3.0}, model="headway-relaxation", capacity_map=cap_map), 6.0)
    dual_new, dual_gradient, dual_info = dual_subgradient_update(
        {}, {(7, 0): 3.0}, iteration=0, dual_value=10.0,
        incumbent_ub=20.0, model="headway-relaxation", capacity_map=cap_map,
        theta=1.0, fallback_step=1.0)
    assert dual_gradient[(7, 0)] == 1.0
    assert dual_new[(7, 0)] > 0.0 and dual_info["used_polyak"]
    assert all(value >= 0.0 and math.isfinite(value) for value in dual_new.values())
    no_ub_new, _, no_ub_info = dual_subgradient_update(
        {(7, 0): 5.0}, {(7, 0): 3.0}, iteration=1, dual_value=10.0,
        incumbent_ub=math.inf, model="headway-relaxation", capacity_map=cap_map,
        theta=1.0, fallback_step=1.0)
    assert not no_ub_info["used_polyak"] and no_ub_new[(7, 0)] > 5.0

    tiny_train_a = Train("TA", 0.0, 0, 1, 1.0, None)
    tiny_train_b = Train("TB", 0.0, 0, 1, 1.0, None)
    tiny_candidate_a = PathCandidate("TA", 0, (7,), (0, 1), (True,), ("0",))
    tiny_candidate_b = PathCandidate("TB", 0, (7,), (0, 1), (True,), ("0",))
    tiny_candidates = {"TA": [tiny_candidate_a], "TB": [tiny_candidate_b]}
    tiny_rows = {
        "TA": {"train_id": "TA", "route_index": "0", "arc_ids": "7",
               "waits": "0", "legs": "7,0,10", "departure_min": "0",
               "arrival_min": "10", "total_priced_cost": "10", "physical_cost": "10",
               "feasible": "1"},
        "TB": {"train_id": "TB", "route_index": "0", "arc_ids": "7",
               "waits": "0", "legs": "7,1,11", "departure_min": "1",
               "arrival_min": "11", "total_priced_cost": "10", "physical_cost": "10",
               "feasible": "1"},
    }
    same_cell_rows = {
        "TA": dict(tiny_rows["TA"]),
        "TB": dict(tiny_rows["TB"]),
    }
    same_cell_rows["TB"]["legs"] = "7,10,20"
    same_cell_rows["TB"]["departure_min"] = "10"
    same_cell_rows["TB"]["arrival_min"] = "20"
    same_cell_occupancy = resource_occupancy(same_cell_rows, 30.0, 3.0)
    assert same_cell_occupancy == {(7, 0): 2.0}, same_cell_occupancy
    same_cell_reference_excess = capacity_excess(
        same_cell_occupancy, model="block30", capacity_map={})
    _assert_close(same_cell_reference_excess[(7, 0)], 1.0)
    assert not all_physical_conflicts(
        same_cell_rows, [tiny_train_a, tiny_train_b], tiny_candidates,
        safety_headway_minutes=3.0)
    same_cell_validation = validate_physical_schedule(
        same_cell_rows, [tiny_train_a, tiny_train_b], tiny_candidates,
        {7: Arc(7, 0, 1, 1.0, True, "0", 60.0, 60.0)}, [],
        horizon_minutes=120.0, safety_headway_minutes=3.0)
    assert same_cell_validation["status"] == "PASS", same_cell_validation
    # The positive reference excess above is diagnostic only: it must not
    # override the exact physical validator for two headway-separated trains.
    relaxed_selected, _ = select_relaxed_argmins(
        list(tiny_rows.values()), [tiny_train_a, tiny_train_b], tiny_candidates)
    ub_selected = {train_id: dict(row) for train_id, row in relaxed_selected.items()}
    ub_selected["TA"]["legs"] = "7,30,40"
    assert relaxed_selected["TA"]["legs"] == "7,0,10"
    assert resource_occupancy(relaxed_selected, 30.0, 3.0) != resource_occupancy(
        ub_selected, 30.0, 3.0)
    tiny_conflicts = all_physical_conflicts(
        tiny_rows, [tiny_train_a, tiny_train_b], tiny_candidates,
        safety_headway_minutes=3.0)
    assert len(tiny_conflicts) == 1
    tiny_conflict = tiny_conflicts[0]
    child_a = frozenset({tiny_conflict.action_a.forbidden})
    child_b = frozenset({tiny_conflict.action_b.forbidden})
    assert child_a and child_b and child_a != child_b
    assert tiny_conflict.action_a.forbidden in child_a
    assert tiny_conflict.action_b.forbidden in child_b
    assert tiny_conflict.action_b.forbidden not in child_a
    assert tiny_conflict.action_a.forbidden not in child_b
    # Any parent assignment retaining both conflicting actions is excluded by
    # at least one child restriction; the children cover the complement.
    assert not (tiny_conflict.action_a.forbidden not in child_a and
                tiny_conflict.action_b.forbidden not in child_a)
    assert not (tiny_conflict.action_a.forbidden not in child_b and
                tiny_conflict.action_b.forbidden not in child_b)
    open_nodes = [
        (20.0, 0, 2, BBNode(2, None, 0, frozenset(), 0.0, 20.0)),
        (10.0, 0, 1, BBNode(1, None, 0, frozenset(), 0.0, 10.0)),
    ]
    _assert_close(_bb_open_global_lb(open_nodes, math.inf), 10.0)
    _assert_close(_bb_open_global_lb([], 20.0), 20.0)
    assert 10.0 <= 20.0 + BB_TOLERANCE
    assert _bb_gap(10.0, 20.0) == (0.5, 1.0)
    assert candidate_universe_fingerprint(tiny_candidates) != candidate_universe_fingerprint(
        {"TA": [tiny_candidate_a], "TB": [PathCandidate(
            "TB", 1, (7,), (0, 1), (True,), ("0",))]})
    print("Fluid Queue self-tests: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    sweep_group = parser.add_mutually_exclusive_group()
    sweep_group.add_argument("--beta-sweep", action="store_true",
                             help="run the fixed beta={1,2,3,4} Fluid Queue calibration")
    sweep_group.add_argument("--gamma-sweep", action="store_true",
                             help="run the beta={2,3}, gamma damping calibration")
    sweep_group.add_argument("--arrival-averaging-sweep", action="store_true",
                             help="run the four input-arrival averaging policies")
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cpp-exe", type=Path, default=None)
    parser.add_argument("--enable-time-window-pruning", action="store_true",
                        help="enable the exact/admissible C++ time-window bounds")
    parser.add_argument("--enable-gh-pruning", action="store_true",
                        help="enable exact/admissible single-train g+h pruning")
    parser.add_argument("--path-k", type=int, default=DEFAULT_PATH_K)
    parser.add_argument("--bin-minutes", type=float, default=DEFAULT_BIN_MINUTES)
    parser.add_argument("--horizon", type=float, default=DEFAULT_HORIZON_MINUTES)
    parser.add_argument("--departure-slack-minutes", type=float,
                        default=DEFAULT_DEPARTURE_SLACK_MINUTES,
                        help="maximum departure delay after train entry")
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--time-step", type=float, default=DEFAULT_TIME_STEP)
    parser.add_argument("--wait-step", type=float, default=DEFAULT_WAIT_STEP)
    parser.add_argument("--departure-step", type=float, default=DEFAULT_DEPARTURE_STEP)
    parser.add_argument("--max-wait-minutes", type=float, default=180.0)
    parser.add_argument("--tail-window", type=int, default=20,
                        help="final post-initial transitions used by tail diagnostics")
    parser.add_argument("--price-policy", choices=(
        FLUID_QUEUE, LEGACY_SUBGRADIENT, DUAL_SUBGRADIENT),
                        default=FLUID_QUEUE)
    parser.add_argument("--lr-capacity-model", choices=LR_CAPACITY_MODELS,
                        default=DEFAULT_LR_CAPACITY_MODEL,
                        help=("Lagrangian relaxation capacity only; Fluid Queue "
                              "service remains separate"))
    parser.add_argument("--lr-capacity-map", type=Path, default=None,
                        help="headway_relaxation_capacity.csv for Scheme A")
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    parser.add_argument("--beta", type=float, default=DEFAULT_BETA)
    parser.add_argument("--gamma", type=float, default=DEFAULT_GAMMA)
    parser.add_argument("--arrival-averaging-policy", choices=ARRIVAL_AVERAGING_POLICIES,
                        default=DEFAULT_ARRIVAL_AVERAGING_POLICY,
                        help="price-driving arrival state policy")
    parser.add_argument("--arrival-averaging-rho", type=float,
                        default=DEFAULT_ARRIVAL_AVERAGING_RHO,
                        help="constant arrival averaging weight")
    parser.add_argument("--price-queue-tolerance", type=float,
                        default=DEFAULT_PRICE_QUEUE_TOLERANCE,
                        help="diagnostic price-queue Linf stability tolerance")
    parser.add_argument("--wait-reference-minutes", type=float, default=DEFAULT_WAIT_REFERENCE_MINUTES)
    parser.add_argument("--max-price-wait-minutes", type=float, default=DEFAULT_MAX_PRICE_WAIT_MINUTES)
    parser.add_argument("--queue-wait-cost-per-min", type=float, default=DEFAULT_QUEUE_WAIT_COST_PER_MIN)
    parser.add_argument("--safety-headway-minutes", type=float, default=DEFAULT_SAFETY_HEADWAY_MINUTES)
    parser.add_argument("--origin-wait-cost-per-min", type=float, default=DEFAULT_ORIGIN_WAIT_COST_PER_MIN)
    parser.add_argument("--running-cost-per-min", type=float, default=DEFAULT_RUNNING_COST_PER_MIN)
    parser.add_argument("--siding-wait-cost-per-min", type=float, default=DEFAULT_SIDING_WAIT_COST_PER_MIN)
    parser.add_argument("--early-arrival-cost-per-min", type=float, default=DEFAULT_EARLY_ARRIVAL_COST_PER_MIN)
    parser.add_argument("--late-arrival-cost-per-min", type=float, default=DEFAULT_LATE_ARRIVAL_COST_PER_MIN)
    parser.add_argument("--step-size-initial", type=float, default=1.0,
                        help="legacy-subgradient only; ignored by fluid-queue")
    parser.add_argument("--minimum-step-size", type=float, default=0.0,
                        help="legacy-subgradient only; ignored by fluid-queue")
    parser.add_argument("--lambda-tolerance", type=float, default=0.05)
    parser.add_argument("--stable-rounds", type=int, default=2)
    parser.add_argument("--dual-theta-initial", type=float,
                        default=DEFAULT_DUAL_THETA_INITIAL)
    parser.add_argument("--dual-theta-min", type=float, default=DEFAULT_DUAL_THETA_MIN)
    parser.add_argument("--dual-stall-rounds", type=int,
                        default=DEFAULT_DUAL_STALL_ROUNDS)
    parser.add_argument("--dual-max-iterations-root", type=int,
                        default=DEFAULT_DUAL_ROOT_ITERATIONS)
    parser.add_argument("--dual-max-iterations-node", type=int,
                        default=DEFAULT_DUAL_NODE_ITERATIONS)
    parser.add_argument("--dual-norm-tolerance", type=float, default=1e-12)
    parser.add_argument("--enable-conflict-bb", action="store_true")
    parser.add_argument("--bb-max-nodes", type=int, default=DEFAULT_BB_MAX_NODES)
    parser.add_argument("--bb-time-limit-sec", type=float,
                        default=DEFAULT_BB_TIME_LIMIT_SEC)
    parser.add_argument("--bb-relative-gap", type=float,
                        default=DEFAULT_BB_RELATIVE_GAP)
    parser.add_argument("--bb-absolute-gap", type=float,
                        default=DEFAULT_BB_ABSOLUTE_GAP)
    parser.add_argument("--bb-root-dual-iterations", type=int,
                        default=DEFAULT_DUAL_ROOT_ITERATIONS)
    parser.add_argument("--bb-node-dual-iterations", type=int,
                        default=DEFAULT_DUAL_NODE_ITERATIONS)
    parser.add_argument("--bb-conflict-selection", choices=("earliest", "cardinal"),
                        default=DEFAULT_BB_CONFLICT_SELECTION)
    parser.add_argument("--bb-search-policy", choices=("best_bound", "focal"),
                        default=DEFAULT_BB_SEARCH_POLICY,
                        help="CBS expansion order; global LB remains best-bound over all open leaves")
    parser.add_argument("--bb-focal-weight", type=float, default=DEFAULT_BB_FOCAL_WEIGHT,
                        help="focal eligibility multiplier relative to minimum open LB")
    parser.add_argument("--bb-focal-secondary", choices=("conflicts", "excess_cost", "hybrid"),
                        default=DEFAULT_BB_FOCAL_SECONDARY,
                        help="deterministic secondary key within the focal band")
    parser.add_argument("--bb-strong-branching-candidates", type=int,
                        default=DEFAULT_BB_STRONG_BRANCHING_CANDIDATES)
    parser.add_argument("--bb-enable-bypass", action="store_true",
                        help="enable safe ICBS representative-path bypass")
    parser.add_argument("--bb-max-bypass-per-node", type=int,
                        default=DEFAULT_BB_MAX_BYPASS_PER_NODE)
    parser.add_argument("--bb-splitting", choices=("standard", "disjoint"),
                        default=DEFAULT_BB_SPLITTING)
    parser.add_argument("--bb-enable-required-action-propagation", action="store_true",
                        help="propagate only exact representative actions incompatible with a required pivot")
    parser.add_argument("--bb-enable-pairwise-bound", action="store_true",
                        help="enable certified conflict-pair matching bound")
    parser.add_argument("--bb-pairwise-max-pairs", type=int,
                        default=DEFAULT_BB_PAIRWISE_MAX_PAIRS)
    parser.add_argument("--bb-pairwise-node-depth", type=int,
                        default=DEFAULT_BB_PAIRWISE_NODE_DEPTH)
    parser.add_argument("--bb-pairwise-time-limit-sec", type=float,
                        default=DEFAULT_BB_PAIRWISE_TIME_LIMIT_SEC)
    parser.add_argument("--bb-checkpoint-interval", type=int,
                        default=DEFAULT_BB_CHECKPOINT_INTERVAL)
    parser.add_argument("--bb-resume-checkpoint", type=Path, default=None)
    parser.add_argument("--bb-root-bound-certificate", type=Path, default=None,
                        help="validated root LB certificate to inherit on every descendant")
    parser.add_argument("--bb-incumbent-tsv", "--bb-initial-incumbent-routes",
                        dest="bb_incumbent_tsv", type=Path, default=None)
    args = parser.parse_args()
    args.max_iterations_explicit = args.max_iterations is not None
    if args.max_iterations is None:
        args.max_iterations = DEFAULT_MAX_ITERATIONS
    if args.self_test:
        self_test()
        return
    if args.dataset is None or args.output is None:
        parser.error("--dataset and --output are required unless --self-test is used")
    for name in ("bin_minutes", "horizon", "departure_slack_minutes", "time_step",
                 "wait_step", "departure_step", "max_wait_minutes", "alpha", "beta", "gamma",
                 "wait_reference_minutes", "max_price_wait_minutes",
                 "queue_wait_cost_per_min", "safety_headway_minutes",
                 "origin_wait_cost_per_min",
                 "arrival_averaging_rho", "price_queue_tolerance",
                 "dual_theta_initial", "dual_theta_min", "dual_norm_tolerance",
                 "bb_time_limit_sec", "bb_relative_gap", "bb_absolute_gap",
                 "bb_focal_weight"):
        if not math.isfinite(float(getattr(args, name))):
            parser.error(f"{name} must be finite")
    if (args.bin_minutes <= 0 or args.horizon <= 0 or args.departure_slack_minutes < 0 or
            args.time_step <= 0 or args.wait_step <= 0 or args.departure_step <= 0 or
            args.max_wait_minutes < 0 or
            args.path_k <= 0 or
            args.max_iterations <= 0 or args.tail_window <= 0 or
            args.dual_theta_initial < 0 or args.dual_theta_min < 0 or
            args.dual_norm_tolerance < 0 or args.bb_time_limit_sec <= 0 or
            args.bb_max_nodes <= 0 or args.bb_relative_gap < 0 or
            args.bb_absolute_gap < 0 or args.dual_stall_rounds <= 0 or
            args.bb_focal_weight < 1.0 or
            args.dual_max_iterations_root <= 0 or args.dual_max_iterations_node < 0 or
            args.bb_root_dual_iterations < 0 or args.bb_node_dual_iterations < 0 or
            args.bb_strong_branching_candidates <= 0 or args.bb_checkpoint_interval < 0 or
            args.bb_max_bypass_per_node < 0 or args.bb_pairwise_max_pairs < 0 or
            args.bb_pairwise_node_depth < 0 or args.bb_pairwise_time_limit_sec <= 0):
        parser.error("bin-minutes, horizon, path-k, max-iterations, and tail-window must be positive")
    if abs(args.bin_minutes - DEFAULT_BIN_MINUTES) > EPS:
        parser.error(
            "the active benchmark currently supports only bin_minutes=30; "
            "other resolutions are intentionally disabled")
    if args.alpha < 0 or args.beta < 0 or not 0 <= args.gamma <= 1:
        parser.error("alpha/beta must be nonnegative and gamma must be in [0,1]")
    if args.wait_reference_minutes <= 0 or args.max_price_wait_minutes < 0:
        parser.error("wait-reference-minutes must be positive and max-price-wait-minutes nonnegative")
    if not 0.0 < args.arrival_averaging_rho <= 1.0:
        parser.error("arrival-averaging-rho must be in (0,1]")
    if args.price_queue_tolerance < 0:
        parser.error("price-queue-tolerance must be nonnegative")
    if args.dual_theta_min > args.dual_theta_initial:
        parser.error("dual-theta-min cannot exceed dual-theta-initial")
    if args.enable_conflict_bb and args.beta_sweep:
        parser.error("--enable-conflict-bb cannot be combined with calibration sweeps")
    if args.beta_sweep:
        print(json.dumps(run_beta_sweep(args), indent=2, sort_keys=True))
        return
    if args.gamma_sweep:
        print(json.dumps(run_gamma_sweep(args), indent=2, sort_keys=True))
        return
    if args.arrival_averaging_sweep:
        print(json.dumps(run_arrival_averaging_sweep(args), indent=2, sort_keys=True))
        return
    print(json.dumps(solve(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
