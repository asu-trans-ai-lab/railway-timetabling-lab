"""Python boundary for the clean unrestricted physical-network DP.

This module owns only data translation, executable invocation, and result
decoding. Static train domains optionally restrict the outgoing physical moves;
no fixed-route timing backend, CBS, pairwise, or LP code is used.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping
from types import MappingProxyType
from .train_domain import TrainDomain, FULL_NETWORK, FULL_SCOPE, digest
from .trajectory_nogood import TrajectoryNoGood, trajectory_context
from .headway_model import HEADWAY_MODEL_LEGACY_ENTRY, physics_metadata


EPS = 1e-9


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
class BranchRestrictions:
    """Immutable inherited spatial restrictions and exact movement exclusions."""

    required_resources_by_train: tuple[tuple[str, tuple[int, ...]], ...] = ()
    prohibited_resources_by_train: tuple[tuple[str, tuple[int, ...]], ...] = ()
    # Space-time exclusions: (train_id, arc_id, direction, t_lo, t_hi) meaning
    # "this train may not ENTER this arc in this direction with an entry time in
    # [t_lo, t_hi)".  direction is "AB", "BA" or "ANY".  This is the missing time
    # dimension: the arc-level relations above can only say WHETHER a train uses
    # an arc, never WHEN, so they cannot separate two trains that must both use
    # the same arc.  See RESOURCE_BB_SEMANTICS_AUDIT.md.
    forbidden_windows: tuple[tuple[str, int, str, float, float], ...] = ()
    # Exact movement, including snapped exit/dwell: (train, arc, dir, entry, exit).
    forbidden_events: tuple[tuple[str, int, str, float, float], ...] = ()
    forbidden_trajectories: tuple[TrajectoryNoGood, ...] = ()

    def __post_init__(self) -> None:
        for relation, entries in (("window", self.forbidden_windows),
                                  ("event", self.forbidden_events)):
            for train, arc, direction, start, end in entries:
                allowed = {"AB", "BA", "ANY"} if relation == "window" else {"AB", "BA"}
                if (not train or arc < 0 or direction not in allowed or
                        not math.isfinite(start) or not math.isfinite(end) or
                        start < 0 or end <= start):
                    raise ValueError(f"invalid forbidden {relation}: {(train, arc, direction, start, end)}")

    @staticmethod
    def from_maps(
        required: Mapping[str, Iterable[int]] | None = None,
        prohibited: Mapping[str, Iterable[int]] | None = None,
    ) -> "BranchRestrictions":
        def canonical(source: Mapping[str, Iterable[int]] | None) -> tuple[tuple[str, tuple[int, ...]], ...]:
            return tuple(
                (str(train_id), tuple(sorted({int(resource) for resource in resources})))
                for train_id, resources in sorted((source or {}).items())
                if set(resources)
            )

        # Materialize iterables once; callers commonly pass sets, but this
        # also keeps generator inputs deterministic.
        req = {str(k): tuple(int(v) for v in values) for k, values in (required or {}).items()}
        pro = {str(k): tuple(int(v) for v in values) for k, values in (prohibited or {}).items()}
        return BranchRestrictions(canonical(req), canonical(pro))

    def required(self) -> dict[str, set[int]]:
        return {train_id: set(resources) for train_id, resources in self.required_resources_by_train}

    def prohibited(self) -> dict[str, set[int]]:
        return {train_id: set(resources) for train_id, resources in self.prohibited_resources_by_train}

    def contradictory(self) -> bool:
        req = self.required()
        pro = self.prohibited()
        return any(req.get(train_id, set()) & pro.get(train_id, set()) for train_id in set(req) | set(pro))

    def row_list(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for train_id, resources in self.required_resources_by_train:
            rows.extend({"train_id": train_id, "relation": "REQUIRE", "resource_id": resource} for resource in resources)
        for train_id, resources in self.prohibited_resources_by_train:
            rows.extend({"train_id": train_id, "relation": "PROHIBIT", "resource_id": resource} for resource in resources)
        for train_id, arc_id, direction, t_lo, t_hi in self.forbidden_windows:
            rows.append({"train_id": train_id, "relation": "FORBID_WINDOW",
                         "resource_id": arc_id, "direction": direction,
                         "t_lo": t_lo, "t_hi": t_hi})
        for train_id, arc_id, direction, start, end in self.forbidden_events:
            rows.append({"train_id": train_id, "relation": "FORBID_EVENT",
                         "resource_id": arc_id, "direction": direction,
                         "t_lo": start, "t_hi": end})
        rows.extend(ng.row() for ng in self.forbidden_trajectories)
        return rows

    def forbidden_by_train(self) -> dict[str, list[tuple[int, str, float, float]]]:
        out: dict[str, list[tuple[int, str, float, float]]] = {}
        for train_id, arc_id, direction, t_lo, t_hi in self.forbidden_windows:
            out.setdefault(str(train_id), []).append(
                (int(arc_id), str(direction), float(t_lo), float(t_hi)))
        return out

    def with_forbidden_window(self, train_id: str, arc_id: int, direction: str,
                              t_lo: float, t_hi: float) -> "BranchRestrictions":
        """Return a child carrying every parent restriction plus one exclusion."""
        entry = (str(train_id), int(arc_id), str(direction), float(t_lo), float(t_hi))
        if entry in self.forbidden_windows:
            return self
        return BranchRestrictions(
            self.required_resources_by_train,
            self.prohibited_resources_by_train,
            tuple(sorted(self.forbidden_windows + (entry,))), self.forbidden_events, self.forbidden_trajectories)

    def with_forbidden_event(self, train_id: str, arc_id: int, direction: str,
                             start: float, end: float) -> "BranchRestrictions":
        entry = (str(train_id), int(arc_id), str(direction), float(start), float(end))
        return BranchRestrictions(self.required_resources_by_train,
                                  self.prohibited_resources_by_train,
                                  self.forbidden_windows,
                                  tuple(sorted(set(self.forbidden_events) | {entry})), self.forbidden_trajectories)

    def with_forbidden_trajectory(self, nogood: TrajectoryNoGood) -> "BranchRestrictions":
        if any(ng.signature==nogood.signature for ng in self.forbidden_trajectories): return self
        from dataclasses import replace
        return replace(self, forbidden_trajectories=tuple(sorted(self.forbidden_trajectories+(nogood,),key=lambda n:n.signature)))

    def violations(self, result: "DPResult") -> list[str]:
        """Check actual legs; no reliance on a cached resource set."""
        errors = [f"FORBID_TRAJECTORY {ng.signature} complete equality violated"
                  for ng in self.forbidden_trajectories if ng.trajectory.matches(result)]
        for (arc, start, end), ab in zip(result.legs, result.ab_flags):
            direction = "AB" if ab else "BA"
            for train, resource, d, lo, hi in self.forbidden_windows:
                if (train == result.train_id and resource == arc and d in (direction, "ANY")
                        and start >= lo - EPS and start < hi - EPS):
                    errors.append(f"FORBID_WINDOW {train}@{arc}/{d} [{lo}, {hi}) violated")
            for train, resource, d, entry, exit_ in self.forbidden_events:
                if (train == result.train_id and resource == arc and d == direction
                        and abs(start - entry) <= EPS and abs(end - exit_) <= EPS):
                    errors.append(f"FORBID_EVENT {train}@{arc}/{d} [{entry}, {exit_}) violated")
        return errors


@dataclass(frozen=True)
class DPResult:
    train_id: str
    feasible: bool
    generalized_cost: float
    physical_cost: float
    lambda_cost: float
    origin_wait_cost: float
    running_cost: float
    siding_wait_cost: float
    early_arrival_cost: float
    late_arrival_cost: float
    departure_min: float | None
    arrival_min: float | None
    physical_dual_cost: float = 0.0
    arc_ids: tuple[int, ...] = ()
    node_ids: tuple[int, ...] = ()
    ab_flags: tuple[bool, ...] = ()
    track_types: tuple[str, ...] = ()
    waits: tuple[float, ...] = ()
    legs: tuple[tuple[int, float, float], ...] = ()
    resources: frozenset[int] = frozenset()
    lambda_cells: frozenset[tuple[int, int]] = frozenset()
    diagnostics: dict[str, str] = field(default_factory=dict)
    reason: str = ""

    @property
    def legacy_lambda_cost(self) -> float:
        """Historical safe-v1 block price.  Zero under the physical LR."""
        return self.lambda_cost

    @property
    def objective_identity_error(self) -> float:
        return (self.generalized_cost - self.physical_cost
                - self.lambda_cost - self.physical_dual_cost)

    def as_path_dict(self) -> dict[str, object]:
        return {
            "train_id": self.train_id,
            "feasible": self.feasible,
            "generalized_cost": self.generalized_cost,
            "physical_cost": self.physical_cost,
            "lambda_cost": self.lambda_cost,
            "physical_dual_cost": self.physical_dual_cost,
            "arc_ids": list(self.arc_ids),
            "node_ids": list(self.node_ids),
            "ab_flags": list(self.ab_flags),
            "track_types": list(self.track_types),
            "waits": list(self.waits),
            "legs": [list(leg) for leg in self.legs],
            "resources": sorted(self.resources),
            "lambda_cells": [list(cell) for cell in sorted(self.lambda_cells)],
            "departure_min": self.departure_min,
            "arrival_min": self.arrival_min,
        }


@dataclass(frozen=True)
class DPConfig:
    bin_minutes: float = 30.0
    time_step: float = 0.5
    horizon: float = 1440.0
    max_wait: float = 180.0
    wait_step: float = 5.0
    departure_slack: float = 240.0
    departure_step: float = 5.0
    safety_headway: float = 3.0
    origin_wait_cost: float = 1.0
    running_cost: float = 1.0
    siding_wait_cost: float = 1.0
    early_cost: float = 1.0
    late_cost: float = 1.0
    #: Exact incumbent-based g+h pruning in the DP.  Admissible, so it never
    #: changes the optimum; exposed only so regressions can compare against the
    #: unpruned reference search.
    gh_pruning: bool = True
    headway_model: str = HEADWAY_MODEL_LEGACY_ENTRY

    def __post_init__(self):
        physics_metadata(self.headway_model, self.safety_headway)


def _float(value: str | None, default: float = 0.0) -> float:
    if value in (None, "", "NA", "NaN", "nan"):
        return default
    return float(value)


def _int(value: str | None, default: int = 0) -> int:
    if value in (None, ""):
        return default
    return int(float(value))


def load_dataset(dataset: Path) -> tuple[dict[int, Arc], list[Train], list[dict[str, float | int]]]:
    """Read the active RAS physical schema without importing legacy algorithms."""
    arc_path = dataset / "input_rail_arc.csv"
    train_path = dataset / "input_train_info.csv"
    mow_path = dataset / "input_MOW.csv"
    if not arc_path.exists() or not train_path.exists():
        raise FileNotFoundError(f"{dataset} must contain input_rail_arc.csv and input_train_info.csv")
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
            target = row.get("terminal_want_time", "")
            trains.append(Train(
                train_id=str(row.get("train_header", "")).strip(),
                entry_min=_float(row.get("entry_time")),
                origin=_int(row.get("origin_node_id")),
                destination=_int(row.get("destination_node_id")),
                smult=_float(row.get("speed_multiplier"), 1.0),
                terminal_want=None if target in (None, "") else float(target),
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


def _write_tsv(path: Path, header: list[str], rows: Iterable[Iterable[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _write_inputs(
    root: Path,
    arcs: Mapping[int, Arc],
    trains: Iterable[Train],
    mow: Iterable[Mapping[str, float | int]],
    lambdas: Mapping[tuple[int, int], float],
    restrictions: BranchRestrictions,
    event_prices: Mapping[tuple[str, int, bool, int], float],
) -> None:
    _write_tsv(
        root / "network.tsv",
        ["arc_id", "a", "b", "length", "bidirectional", "track_type", "speed_ab", "speed_ba"],
        [[arc.arc_id, arc.a, arc.b, arc.length, int(arc.bidirectional), arc.track_type, arc.speed_ab, arc.speed_ba]
         for arc in sorted(arcs.values(), key=lambda item: item.arc_id)],
    )
    _write_tsv(
        root / "requests.tsv",
        ["train_id", "entry_min", "origin", "destination", "smult", "terminal_want"],
        [[train.train_id, train.entry_min, train.origin, train.destination, train.smult,
          "" if train.terminal_want is None else train.terminal_want] for train in trains],
    )
    _write_tsv(root / "lambda.tsv", ["arc_id", "time_bin", "price"],
               [[arc, time_bin, value] for (arc, time_bin), value in sorted(lambdas.items())
                if abs(float(value)) > EPS])
    mow_rows: list[list[object]] = []
    for window in mow:
        a, b = int(window["a"]), int(window["b"])
        for arc in arcs.values():
            if {arc.a, arc.b} == {a, b}:
                mow_rows.append([arc.arc_id, window["start"], window["end"]])
    _write_tsv(root / "mow.tsv", ["arc_id", "start_min", "end_min"], mow_rows)
    _write_tsv(root / "branch.tsv",
               ["train_id", "relation", "resource_id", "direction", "t_lo", "t_hi"],
               [[row["train_id"], row["relation"], row["resource_id"],
                 row.get("direction", ""), row.get("t_lo", ""), row.get("t_hi", "")]
                for row in restrictions.row_list() if row["relation"]!="FORBID_TRAJECTORY"])
    _write_tsv(
        root / "event_price.tsv",
        ["train_id", "arc_id", "direction", "entry_tick", "price"],
        [[train_id, arc_id, "AB" if ab else "BA", entry_tick, price]
         for (train_id, arc_id, ab, entry_tick), price
         in sorted(event_prices.items(),
                   key=lambda item: (item[0][0], item[0][1], item[0][2], item[0][3]))
         if abs(float(price)) > EPS])


def _parse_ints(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split(",") if part != "")


def _parse_bools(value: str) -> tuple[bool, ...]:
    return tuple(part == "1" for part in value.split(",") if part != "")


def _parse_floats(value: str) -> tuple[float, ...]:
    return tuple(float(part) for part in value.split(",") if part != "")


def _parse_legs(value: str) -> tuple[tuple[int, float, float], ...]:
    if not value:
        return ()
    out: list[tuple[int, float, float]] = []
    for item in value.split(";"):
        arc, start, exit_ = item.split(",")
        out.append((int(arc), float(start), float(exit_)))
    return tuple(out)


def _parse_cells(value: str) -> frozenset[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for item in value.replace(";", ",").split(",") if value else ():
        if not item:
            continue
        arc, time_bin = item.split(":")
        cells.add((int(arc), int(time_bin)))
    return frozenset(cells)


def _parse_diagnostics(value: str) -> dict[str, str]:
    """Decode the DP's ``key=value`` diagnostics record.

    ``network_dp.cpp`` emits these through ``join_strings``, which separates
    with ``,``.  This split used ``;`` and therefore returned a single key whose
    value was the rest of the record, making every label and transition counter
    unreadable.  Diagnostics only: no bound, route or cost depends on it.
    """
    result: dict[str, str] = {}
    for item in value.split(",") if value else ():
        if "=" in item:
            key, val = item.split("=", 1)
            result[key.strip()] = val.strip()
    return result


def _row_to_result(row: Mapping[str, str]) -> DPResult:
    feasible = row.get("feasible", "0") == "1"
    return DPResult(
        train_id=str(row.get("train_id", "")),
        feasible=feasible,
        generalized_cost=float(row.get("generalized_cost", "inf")),
        physical_cost=float(row.get("physical_cost", "inf")),
        lambda_cost=float(row.get("lambda_cost", "0")),
        physical_dual_cost=float(row.get("physical_dual_cost", "0")),
        origin_wait_cost=float(row.get("origin_wait_cost", "0")),
        running_cost=float(row.get("running_cost", "0")),
        siding_wait_cost=float(row.get("siding_wait_cost", "0")),
        early_arrival_cost=float(row.get("early_arrival_cost", "0")),
        late_arrival_cost=float(row.get("late_arrival_cost", "0")),
        departure_min=None if row.get("departure_min", "nan") in ("", "nan", "NaN") else float(row["departure_min"]),
        arrival_min=None if row.get("arrival_min", "nan") in ("", "nan", "NaN") else float(row["arrival_min"]),
        arc_ids=_parse_ints(row.get("arc_ids", "")),
        node_ids=_parse_ints(row.get("node_ids", "")),
        ab_flags=_parse_bools(row.get("ab_flags", "")),
        track_types=tuple(part for part in row.get("track_types", "").split(",") if part != ""),
        waits=_parse_floats(row.get("waits", "")),
        legs=_parse_legs(row.get("legs", "")),
        resources=frozenset(_parse_ints(row.get("resources", ""))),
        lambda_cells=_parse_cells(row.get("lambda_cells", "")),
        diagnostics=_parse_diagnostics(row.get("diagnostics", "")),
        reason=row.get("infeasibility_reason", ""),
    )


class NetworkDP:
    """Reusable process boundary; every call invokes the clean C++ DP."""

    def __init__(self, source_root: Path, work_root: Path, executable: Path | None = None,
                 *, domains: Mapping[str, TrainDomain] | None = None):
        self.source_root = Path(source_root).resolve()
        self.work_root = Path(work_root).resolve()
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.executable = Path(executable).resolve() if executable else (self.source_root / "build" / "network_dp")
        self.calls = 0
        self.statistics: dict[str, int] = {}
        self._domains = MappingProxyType(dict(domains or {}))
        if domains is not None and not domains:
            raise ValueError("explicit domains must not be empty; use None for default full network")
        if any(tid != d.train_id for tid,d in self.domains.items()):
            raise ValueError("domain mapping key must match train identity")
        if len({d.mode for d in self.domains.values()}) > 1:
            raise ValueError("one domain mode per experiment is required for unambiguous proof scope")
        self._domain_path = None
        if self.domains:
            self._domain_path = self.work_root / "train_domain.tsv"
            with self._domain_path.open('w', newline='') as stream:
                writer=csv.writer(stream,delimiter='\t')
                writer.writerow(['train_id','mode','arc_id','direction','node_id'])
                for tid,d in sorted(self.domains.items()):
                    writer.writerow([tid,d.mode,'','',''])  # declares even an empty corridor
                    if d.mode != FULL_NETWORK:
                        writer.writerows([tid,d.mode,'','',n] for n in d.allowed_nodes)
                        writer.writerows([tid,d.mode,a,'AB' if ab else 'BA',''] for a,ab in d.allowed_movements)
            (self.work_root/'train_domains.json').write_text(json.dumps({
                'domain_mode':self.domain_mode,'proof_scope':self.proof_scope,
                'domain_fingerprint':self.domain_fingerprint,
                'trains':{t:d.metadata() for t,d in sorted(self.domains.items())}},indent=2)+'\n')

    @property
    def domains(self):
        return self._domains

    @property
    def domain_mode(self):
        return next(iter(self.domains.values())).mode if self.domains else FULL_NETWORK

    @property
    def proof_scope(self):
        return next(iter(self.domains.values())).proof_scope if self.domains else FULL_SCOPE

    @property
    def domain_fingerprint(self):
        return digest({t:d.fingerprint for t,d in sorted(self.domains.items())}) if self.domains else None

    def compile(self, *, force: bool = False) -> Path:
        self.executable.parent.mkdir(parents=True, exist_ok=True)
        if force or not self.executable.exists() or self.executable.stat().st_mtime < (self.source_root / "network_dp.cpp").stat().st_mtime:
            command = ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-pedantic",
                       str(self.source_root / "network_dp.cpp"), "-o", str(self.executable)]
            subprocess.run(command, check=True, cwd=self.source_root)
        return self.executable

    def solve(
        self,
        arcs: Mapping[int, Arc],
        trains: Iterable[Train],
        mow: Iterable[Mapping[str, float | int]],
        lambdas: Mapping[tuple[int, int], float],
        restrictions: BranchRestrictions | None = None,
        config: DPConfig | None = None,
        event_prices: Mapping[tuple[str, int, bool, int], float] | None = None,
    ) -> dict[str, DPResult]:
        """Solve every train independently.

        ``event_prices`` is the already-aggregated physical coupling price
        ``price_i(e) = sum_{k incident to (i,e)} mu_k``, keyed by
        ``(train_id, arc_id, ab, entry_tick)``.  The executable receives one
        table per train and never learns that another train exists, so the
        subproblems stay independent.
        """
        restrictions = restrictions or BranchRestrictions()
        event_prices = dict(event_prices or {})
        negative = [key for key, value in event_prices.items() if float(value) < -EPS]
        if negative:
            raise ValueError(f"physical event prices must be non-negative: {negative[:3]}")
        config = config or DPConfig()
        trains = list(trains)
        mow = list(mow)
        by_id = {t.train_id:t for t in trains}
        for ng in restrictions.forbidden_trajectories:
            trajectory=ng.trajectory
            if trajectory.train_id not in by_id: continue
            train=by_id[trajectory.train_id]
            expected=trajectory_context(arcs,train,mow,config,self.domains.get(train.train_id))
            if (trajectory.context!=expected or trajectory.time_step!=config.time_step or trajectory.wait_step!=config.wait_step
                    or trajectory.origin!=train.origin or trajectory.destination!=train.destination):
                raise ValueError('history no-good does not match fixed train/domain/instance context')
        if self.domains:
            for train in trains:
                if train.train_id not in self.domains:
                    raise ValueError(f"missing static domain for train {train.train_id}")
                self.domains[train.train_id].validate(arcs,train)
        if restrictions.contradictory():
            return {train.train_id: DPResult(
                train_id=train.train_id, feasible=False, generalized_cost=math.inf,
                physical_cost=math.inf, lambda_cost=0.0, origin_wait_cost=0.0,
                running_cost=0.0, siding_wait_cost=0.0, early_arrival_cost=0.0,
                late_arrival_cost=0.0, departure_min=None, arrival_min=None,
                reason="contradictory required/prohibited resource",
            ) for train in trains}
        self.compile()
        self.calls += 1
        call_root = self.work_root / f"dp_call_{self.calls:06d}"
        call_root.mkdir(parents=True, exist_ok=True)
        _write_inputs(call_root, arcs, trains, list(mow), lambdas, restrictions, event_prices)
        output = call_root / "result.tsv"
        command = [
            str(self.executable), "--network", str(call_root / "network.tsv"),
            "--requests", str(call_root / "requests.tsv"), "--lambda", str(call_root / "lambda.tsv"),
            "--mow", str(call_root / "mow.tsv"), "--branch", str(call_root / "branch.tsv"),
            "--event-price", str(call_root / "event_price.tsv"),
            "--output", str(output), "--bin-minutes", str(config.bin_minutes),
            "--time-step", str(config.time_step), "--horizon", str(config.horizon),
            "--max-wait", str(config.max_wait), "--wait-step", str(config.wait_step),
            "--departure-slack", str(config.departure_slack), "--departure-step", str(config.departure_step),
            "--headway", str(config.safety_headway), "--origin-wait-cost", str(config.origin_wait_cost),
            "--running-cost", str(config.running_cost), "--siding-wait-cost", str(config.siding_wait_cost),
            "--early-cost", str(config.early_cost), "--late-cost", str(config.late_cost),
            "--gh-pruning", "1" if getattr(config, "gh_pruning", True) else "0",
        ]
        if restrictions.forbidden_trajectories:
            history_path=call_root/'history.tsv'
            header=['train_id','trajectory_id','origin','destination','departure_tick','arrival_tick','time_step','wait_step','movements']
            history_rows=[]
            for ng in restrictions.forbidden_trajectories:
                t=ng.trajectory
                if t.train_id not in by_id: continue
                tokens=';'.join(f"{a},{int(ab)},{start},{end},{w}" for a,ab,start,end,w in t.movements)
                history_rows.append([t.train_id,ng.signature,t.origin,t.destination,t.departure_tick,t.arrival_tick,t.time_step,t.wait_step,tokens])
            _write_tsv(history_path,header,history_rows)
            command.extend(['--history',str(history_path)])
        if self._domain_path is not None:
            command.extend(['--domain',str(self._domain_path)])
        subprocess.run(command, check=True, cwd=self.source_root)
        with output.open(newline="", encoding="utf-8") as stream:
            rows = [_row_to_result(row) for row in csv.DictReader(stream, delimiter="\t")]
        result = {row.train_id: row for row in rows}
        missing = [train.train_id for train in trains if train.train_id not in result]
        if missing:
            raise RuntimeError(f"network DP omitted train rows: {missing}")
        for row in result.values():
            for key,value in row.diagnostics.items():
                if key.startswith(('labels_', 'transitions_', 'history_')):
                    self.statistics[key] = self.statistics.get(key,0) + int(value)
            if row.feasible and restrictions.violations(row):
                raise RuntimeError(f"native DP returned a restriction-violating trajectory: {restrictions.violations(row)}")
            if self.domains and row.feasible and not self.domains[row.train_id].contains(row):
                raise RuntimeError(f"C++ DP returned an outside-domain trajectory for {row.train_id}")
            if row.feasible and abs(row.objective_identity_error) > 1e-7:
                raise AssertionError(
                    f"generalized cost identity failed for {row.train_id}: "
                    f"error={row.objective_identity_error}")
        (call_root / "manifest.json").write_text(json.dumps({
            **physics_metadata(config.headway_model, config.safety_headway),
            "call": self.calls,
            "command": command,
            "restriction_schema": "frozen spatial + FORBID_WINDOW + exact_FORBID_EVENT + complete_FORBID_TRAJECTORY",
            "restriction_fingerprint": digest(restrictions.row_list()),
            "restrictions": restrictions.row_list(),
            "route_domain": "full_physical_network" if self.domain_mode == FULL_NETWORK else self.domain_mode,
            "domain_mode": self.domain_mode,
            "proof_scope": self.proof_scope,
            "domain_fingerprint": self.domain_fingerprint,
            "event_price_rows": len(event_prices),
            "price_channels": "legacy_lambda(arc,bin) + physical_event(arc,dir,entry_tick)",
        }, indent=2) + "\n", encoding="utf-8")
        return result


def fingerprint_network(arcs: Mapping[int, Arc], trains: Iterable[Train]) -> str:
    payload = {
        "arcs": [arc.__dict__ for arc in sorted(arcs.values(), key=lambda item: item.arc_id)],
        "trains": [train.__dict__ for train in trains],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
