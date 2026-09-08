"""Adapter around the existing trusted physical schedule validator.

The clean lower bound never calls this module.  It is used only to certify
incumbent schedules returned by the independent UB stream.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Iterable, Mapping

from solver.python.dp_interface import Arc, BranchRestrictions, DPResult, Train
from solver.python.headway_model import HEADWAY_MODEL_LEGACY_ENTRY, physics_metadata


def _legacy_types(
    arcs: Mapping[int, Arc], trains: Iterable[Train], mow: Iterable[Mapping[str, float | int]],
):
    # Import the narrow trusted validator only at the UB boundary.  No legacy
    # B&B, PATH-K pricing, queue, or branch module is imported here.
    from . import trusted_physics as legacy

    legacy_arcs = {
        arc_id: legacy.Arc(
            arc_id=arc.arc_id, a=arc.a, b=arc.b, length=arc.length,
            bidirectional=arc.bidirectional, track_type=arc.track_type,
            speed_ab=arc.speed_ab, speed_ba=arc.speed_ba,
        ) for arc_id, arc in arcs.items()
    }
    legacy_trains = [legacy.Train(
        train_id=train.train_id, entry_min=train.entry_min,
        origin=train.origin, destination=train.destination,
        smult=train.smult, terminal_want=train.terminal_want,
    ) for train in trains]
    legacy_mow = [dict(window) for window in mow]
    return legacy, legacy_arcs, legacy_trains, legacy_mow


def _row(result: DPResult) -> dict[str, str]:
    legs = ";".join(f"{arc},{start:.17g},{exit_:.17g}" for arc, start, exit_ in result.legs)
    return {
        "train_id": result.train_id,
        "route_index": "0",
        "feasible": "1" if result.feasible else "0",
        "legs": legs,
        "waits": ",".join(f"{wait:.17g}" for wait in result.waits),
        "departure_min": "" if result.departure_min is None else f"{result.departure_min:.17g}",
        "arrival_min": "" if result.arrival_min is None else f"{result.arrival_min:.17g}",
        "arc_ids": ",".join(str(arc) for arc in result.arc_ids),
        "physical_cost": f"{result.physical_cost:.17g}",
        "total_priced_cost": f"{result.generalized_cost:.17g}",
    }


def validate_schedule(
    selected: Mapping[str, DPResult],
    *,
    arcs: Mapping[int, Arc],
    trains: list[Train],
    mow: list[Mapping[str, float | int]],
    restrictions: BranchRestrictions | None = None,
    horizon: float = 1440.0,
    safety_headway: float = 3.0,
    time_step: float = 0.5,
    wait_step: float = 5.0,
    headway_model: str = HEADWAY_MODEL_LEGACY_ENTRY,
) -> dict[str, object]:
    metadata = physics_metadata(headway_model, safety_headway)
    restrictions = restrictions or BranchRestrictions()
    errors: list[str] = []
    req = restrictions.required()
    pro = restrictions.prohibited()
    for train in trains:
        result = selected.get(train.train_id)
        if result is None or not result.feasible:
            errors.append(f"{train.train_id}: missing/infeasible DP trajectory")
            continue
        if not req.get(train.train_id, set()) <= result.resources:
            errors.append(f"{train.train_id}: REQUIRE resource missing from returned path")
        if pro.get(train.train_id, set()) & result.resources:
            errors.append(f"{train.train_id}: PROHIBIT resource appears in returned path")
        errors.extend(restrictions.violations(result))
        identity = result.objective_identity_error
        if abs(identity) > 1e-7:
            errors.append(f"{train.train_id}: generalized-cost identity error={identity}")
        component_sum = result.origin_wait_cost + result.running_cost + result.siding_wait_cost + result.early_arrival_cost + result.late_arrival_cost
        if abs(component_sum - result.physical_cost) > 1e-7:
            errors.append(f"{train.train_id}: physical objective decomposition error")
    if errors:
        return {
            **metadata,
            "status": "FAIL", "validator_status": "NOT_RUN", "errors": errors,
            "conflicts": [], "train_count": len(selected),
        }

    legacy, legacy_arcs, legacy_trains, legacy_mow = _legacy_types(arcs, trains, mow)
    candidates = {
        train.train_id: [legacy.PathCandidate(
            train_id=train.train_id, route_index=0,
            arc_ids=tuple(selected[train.train_id].arc_ids),
            node_ids=tuple(selected[train.train_id].node_ids),
            ab_flags=tuple(selected[train.train_id].ab_flags),
            track_ids=tuple(selected[train.train_id].track_types),
        )]
        for train in trains
    }
    rows = {train_id: _row(result) for train_id, result in selected.items()}
    report = legacy.validate_physical_schedule(
        rows, legacy_trains, candidates, legacy_arcs, legacy_mow,
        horizon_minutes=horizon, safety_headway_minutes=safety_headway,
        time_step=time_step, wait_step=wait_step, require_complete=True,
        headway_model=headway_model,
    )
    report = dict(report)
    report["validator_status"] = report.get("status", "FAIL")
    report["objective_reconstruction"] = sum(result.physical_cost for result in selected.values())
    report["clean_selected_train_count"] = len(selected)
    return report


def independently_validated_ub(
    selected: Mapping[str, DPResult], **kwargs: object,
) -> tuple[float | None, dict[str, object]]:
    report = validate_schedule(selected, **kwargs)
    if report.get("status") != "PASS":
        return None, report
    return sum(result.physical_cost for result in selected.values()), report


# ===========================================================================
# Hardened UPPER-BOUND validator.
#
# This is the UB side of the certification boundary and answers exactly one
# question: "is this complete timetable a physically feasible incumbent, and
# what is its independently reconstructed physical objective?"
#
# It does NOT compute, check or import any lower bound.  The layering is
#
#     completeness -> topology -> track -> timing -> objective
#                  -> branch restrictions -> trusted joint validator
#
# and an earlier failure means the trusted joint validator can no longer turn
# the candidate into a valid upper bound.  Every clean-side layer recomputes
# from RAW data (arc geometry, train attributes, legs, waits) and never trusts
# a stored cost field.  The exact discretisation rule reproduced here is
# recorded, with numerical evidence, in
# verification/validator_audit/ub_leg_semantics_audit.md.
# ===========================================================================

import math as _math

UB_TOL = 1e-6


def _snap_tick(minute: float, time_step: float) -> int:
    """`network_dp.cpp:307` -- ceil(minute/time_step - EPS)."""
    return int(_math.ceil(minute / time_step - 1e-9))


def _grid_time(tick: int, time_step: float) -> float:
    """`network_dp.cpp:311`."""
    return tick * time_step


def _traversal_minutes(arc: Arc, smult: float, ab: bool) -> float:
    """`network_dp.cpp:301`."""
    speed = (arc.speed_ab if ab else arc.speed_ba) * max(smult, 1e-9)
    if speed <= 0.0:
        return float("inf")
    return 60.0 * arc.length / speed


def _finite(*values: float) -> bool:
    return all(isinstance(v, (int, float)) and _math.isfinite(float(v))
               for v in values)


def validate_upper_bound_schedule(
    selected: Mapping[str, DPResult],
    *,
    arcs: Mapping[int, Arc],
    trains: list[Train],
    mow: list[Mapping[str, float | int]],
    restrictions: "BranchRestrictions | None" = None,
    horizon: float = 1440.0,
    safety_headway: float = 3.0,
    time_step: float = 0.5,
    wait_step: float = 5.0,
    origin_wait_cost: float = 1.0,
    running_cost: float = 1.0,
    siding_wait_cost: float = 1.0,
    early_cost: float = 1.0,
    late_cost: float = 1.0,
    headway_model: str = HEADWAY_MODEL_LEGACY_ENTRY,
) -> dict[str, object]:
    """Layered UB validation.  PASS means the timetable may become an incumbent.

    The certified upper bound is ``reconstructed_objective``, never the stored
    ``physical_cost``: a tampered stored cost must not become a bound just
    because the trusted joint validator finds the *timing* feasible.
    """
    restrictions = restrictions or BranchRestrictions()
    by_id = {t.train_id: t for t in trains}
    report: dict[str, object] = {
        **physics_metadata(headway_model, safety_headway),
        "single_train_status": "PASS", "topology_status": "PASS",
        "track_status": "PASS", "timing_status": "PASS",
        "branch_status": "PASS", "objective_status": "PASS",
        "legacy_validator_status": "NOT_RUN", "joint_physical_status": "NOT_RUN",
        "errors": [], "conflicts": [],
        "reconstructed_objective": None, "stored_objective": None,
        "per_train_reconstruction": {},
    }
    errors: list[str] = report["errors"]           # type: ignore[assignment]

    def fail(layer: str, message: str) -> None:
        report[layer] = "FAIL"
        errors.append(message)

    # ---- check 1: exactly one feasible trajectory per train ---------------
    expected = {t.train_id for t in trains}
    got = set(selected)
    for missing in sorted(expected - got):
        fail("single_train_status", f"{missing}: missing trajectory")
    for extra in sorted(got - expected):
        fail("single_train_status", f"{extra}: trajectory for unknown train")
    for train_id in sorted(expected & got):
        result = selected[train_id]
        if result is None:
            fail("single_train_status", f"{train_id}: trajectory is None")
        elif not result.feasible:
            fail("single_train_status", f"{train_id}: DPResult.feasible is False")
    if report["single_train_status"] == "FAIL":
        report["status"] = "FAIL"
        return report

    reconstructed_total = 0.0
    stored_total = 0.0
    required = restrictions.required()
    prohibited = restrictions.prohibited()

    for train_id in sorted(expected):
        result = selected[train_id]
        train = by_id[train_id]
        arc_ids = list(result.arc_ids)
        node_ids = list(result.node_ids)
        ab_flags = list(result.ab_flags)
        track_types = list(result.track_types)
        waits = list(result.waits)
        legs = list(result.legs)

        # ---- check 2: route topology, reconstructed from the arc table ----
        if len(node_ids) != len(arc_ids) + 1:
            fail("topology_status",
                 f"{train_id}: len(node_ids)={len(node_ids)} != len(arc_ids)+1="
                 f"{len(arc_ids) + 1}")
        if len(ab_flags) != len(arc_ids):
            fail("topology_status",
                 f"{train_id}: len(ab_flags)={len(ab_flags)} != len(arc_ids)="
                 f"{len(arc_ids)}")
        if node_ids and node_ids[0] != train.origin:
            fail("topology_status",
                 f"{train_id}: first node {node_ids[0]} != origin {train.origin}")
        if node_ids and node_ids[-1] != train.destination:
            fail("topology_status",
                 f"{train_id}: last node {node_ids[-1]} != destination "
                 f"{train.destination}")
        for k, arc_id in enumerate(arc_ids):
            arc = arcs.get(arc_id)
            if arc is None:
                fail("topology_status", f"{train_id}: leg {k} uses unknown arc {arc_id}")
                continue
            if k + 1 >= len(node_ids) or k >= len(ab_flags):
                continue
            ab = bool(ab_flags[k])
            want_from, want_to = (arc.a, arc.b) if ab else (arc.b, arc.a)
            if node_ids[k] != want_from or node_ids[k + 1] != want_to:
                fail("topology_status",
                     f"{train_id}: leg {k} arc {arc_id} ab={ab} connects "
                     f"{want_from}->{want_to} but path has "
                     f"{node_ids[k]}->{node_ids[k + 1]}")
            # BA traversal is legal only on a bidirectional arc
            # (`network_dp.cpp` builds the reverse edge only when
            # bidirectional_flag == 1).
            if not ab and not arc.bidirectional:
                fail("topology_status",
                     f"{train_id}: leg {k} traverses arc {arc_id} B->A but the "
                     f"arc is not bidirectional")

        # ---- check 3: track metadata must match the authoritative table ---
        if len(track_types) != len(arc_ids):
            fail("track_status",
                 f"{train_id}: len(track_types)={len(track_types)} != "
                 f"len(arc_ids)={len(arc_ids)}")
        for k, arc_id in enumerate(arc_ids):
            arc = arcs.get(arc_id)
            if arc is None or k >= len(track_types):
                continue
            if str(track_types[k]) != str(arc.track_type):
                fail("track_status",
                     f"{train_id}: leg {k} arc {arc_id} track_type "
                     f"{track_types[k]!r} != authoritative {arc.track_type!r}")

        # ---- checks 4 and 5: timing and running time, from raw geometry ---
        if len(legs) != len(arc_ids) or len(waits) != len(arc_ids):
            fail("timing_status",
                 f"{train_id}: legs/waits length mismatch "
                 f"({len(legs)}/{len(waits)} vs {len(arc_ids)} arcs)")
        departure = result.departure_min
        arrival = result.arrival_min
        if departure is None or arrival is None or not _finite(departure, arrival):
            fail("timing_status", f"{train_id}: departure/arrival not finite")
        else:
            if departure < train.entry_min - UB_TOL:
                fail("timing_status",
                     f"{train_id}: departure {departure} precedes entry "
                     f"{train.entry_min}")
            if _snap_tick(departure, time_step) * time_step != departure:
                fail("timing_status",
                     f"{train_id}: departure {departure} is not on the "
                     f"{time_step}-minute grid")

        running_recon = 0.0
        siding_recon = 0.0
        previous_exit = departure
        for k, arc_id in enumerate(arc_ids):
            arc = arcs.get(arc_id)
            if arc is None or k >= len(legs) or k >= len(waits):
                continue
            leg_arc, start, exit_ = legs[k]
            wait = float(waits[k])
            if int(leg_arc) != int(arc_id):
                fail("timing_status",
                     f"{train_id}: leg {k} arc {leg_arc} != arc_ids[{k}]={arc_id}")
            if not _finite(start, exit_, wait):
                fail("timing_status", f"{train_id}: leg {k} has non-finite times")
                continue
            if wait < -UB_TOL:
                fail("timing_status", f"{train_id}: leg {k} negative wait {wait}")
            if str(arc.track_type) != "S" and wait > UB_TOL:
                fail("timing_status",
                     f"{train_id}: leg {k} waits {wait} on non-siding track "
                     f"{arc.track_type!r}")
            if wait > UB_TOL and wait_step > 0 and abs(
                    wait / wait_step - round(wait / wait_step)) > 1e-9:
                fail("timing_status",
                     f"{train_id}: leg {k} wait {wait} is not a multiple of "
                     f"wait_step {wait_step}")
            if exit_ <= start + UB_TOL:
                fail("timing_status",
                     f"{train_id}: leg {k} exit {exit_} does not exceed start {start}")
            if previous_exit is not None and abs(start - previous_exit) > UB_TOL:
                fail("timing_status",
                     f"{train_id}: leg {k} starts at {start} but the previous "
                     f"movement ended at {previous_exit}")
            if exit_ > horizon + UB_TOL:
                fail("timing_status",
                     f"{train_id}: leg {k} exit {exit_} exceeds horizon {horizon}")
            ab = bool(ab_flags[k]) if k < len(ab_flags) else True
            tau = _traversal_minutes(arc, train.smult, ab)
            if not _math.isfinite(tau) or tau <= 0.0:
                fail("timing_status",
                     f"{train_id}: leg {k} arc {arc_id} has non-positive "
                     f"traversal time")
            else:
                # exact executable rule, NOT a loosened tolerance
                expected_exit = _grid_time(
                    _snap_tick(start + wait + tau, time_step), time_step)
                if abs(exit_ - expected_exit) > UB_TOL:
                    fail("timing_status",
                         f"{train_id}: leg {k} exit {exit_} != snapped "
                         f"start+wait+tau = {expected_exit} "
                         f"(start={start}, wait={wait}, tau={tau})")
                running_recon += running_cost * tau
            if str(arc.track_type) == "S":
                siding_recon += wait * siding_wait_cost
            previous_exit = exit_
        if previous_exit is not None and arrival is not None and legs:
            if abs(arrival - previous_exit) > UB_TOL:
                fail("timing_status",
                     f"{train_id}: arrival {arrival} != final leg exit "
                     f"{previous_exit}")

        # ---- check 6: objective reconstructed from RAW data ---------------
        origin_recon = (origin_wait_cost * (departure - train.entry_min)
                        if departure is not None else float("nan"))
        want = train.terminal_want
        if want is None or not _finite(want):
            early_recon = late_recon = 0.0
        else:
            early_recon = early_cost * max(0.0, want - (arrival or 0.0))
            late_recon = late_cost * max(0.0, (arrival or 0.0) - want)
        total_recon = (origin_recon + running_recon + siding_recon
                       + early_recon + late_recon)
        pairs = (("origin_wait_cost", origin_recon, result.origin_wait_cost),
                 ("running_cost", running_recon, result.running_cost),
                 ("siding_wait_cost", siding_recon, result.siding_wait_cost),
                 ("early_arrival_cost", early_recon, result.early_arrival_cost),
                 ("late_arrival_cost", late_recon, result.late_arrival_cost))
        for name, recon, stored in pairs:
            if not _finite(recon, stored) or abs(recon - stored) > UB_TOL:
                fail("objective_status",
                     f"{train_id}: {name} reconstructed {recon} != stored {stored}")
        if not _finite(total_recon, result.physical_cost) or abs(
                total_recon - result.physical_cost) > UB_TOL:
            fail("objective_status",
                 f"{train_id}: reconstructed physical cost {total_recon} != "
                 f"stored {result.physical_cost}")
        report["per_train_reconstruction"][train_id] = {   # type: ignore[index]
            "origin_wait_cost": origin_recon, "running_cost": running_recon,
            "siding_wait_cost": siding_recon, "early_arrival_cost": early_recon,
            "late_arrival_cost": late_recon, "total": total_recon,
            "stored_total": result.physical_cost}
        reconstructed_total += total_recon
        stored_total += result.physical_cost

        # ---- check 7: branch restrictions, against a rebuilt resource set --
        used = {int(a) for a in arc_ids}
        if used != set(result.resources):
            fail("branch_status",
                 f"{train_id}: DPResult.resources {sorted(result.resources)} != "
                 f"arcs actually traversed {sorted(used)}")
        need = required.get(train_id, set())
        ban = prohibited.get(train_id, set())
        both = need & ban
        if both:
            fail("branch_status",
                 f"{train_id}: resources {sorted(both)} are both REQUIRED and "
                 f"PROHIBITED")
        for resource in sorted(need - used):
            fail("branch_status",
                 f"{train_id}: REQUIRE u({train_id}, arc {resource}) violated")
        for resource in sorted(ban & used):
            fail("branch_status",
                 f"{train_id}: PROHIBIT u({train_id}, arc {resource}) violated")
        for violation in restrictions.violations(result):
            fail("branch_status", violation)

    report["reconstructed_objective"] = reconstructed_total
    report["stored_objective"] = stored_total

    clean_layers = ("single_train_status", "topology_status", "track_status",
                    "timing_status", "branch_status", "objective_status")
    if any(report[layer] == "FAIL" for layer in clean_layers):
        report["status"] = "FAIL"
        return report

    # ---- check 8: the trusted joint physical validator stays the final gate
    legacy_report = validate_schedule(
        selected, arcs=arcs, trains=trains, mow=mow, restrictions=restrictions,
        horizon=horizon, safety_headway=safety_headway, time_step=time_step,
        wait_step=wait_step, headway_model=headway_model)
    if legacy_report['physics_fingerprint'] != report['physics_fingerprint']:
        raise RuntimeError('physical model mismatch inside trusted validation')
    report["legacy_validator_status"] = str(legacy_report.get("status", "FAIL"))
    report["joint_physical_status"] = report["legacy_validator_status"]
    report["conflicts"] = list(legacy_report.get("conflicts") or [])
    for message in (legacy_report.get("errors") or []):
        errors.append(f"legacy: {message}")
    report["legacy_report"] = legacy_report
    report["status"] = "PASS" if report["legacy_validator_status"] == "PASS" else "FAIL"
    return report


def certified_upper_bound(
    selected: Mapping[str, DPResult], **kwargs: object,
) -> tuple[float | None, dict[str, object]]:
    """`(reconstructed objective, report)`, or `(None, report)` when not a UB."""
    report = validate_upper_bound_schedule(selected, **kwargs)   # type: ignore[arg-type]
    if report.get("status") != "PASS":
        return None, report
    return float(report["reconstructed_objective"]), report      # type: ignore[arg-type]


def _jsonable(value):
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def validate_solution_file(dataset: str | Path, schedule: Path) -> dict[str, object]:
    from adapters.ras_adapter import load_ras_dataset
    from validator.schedule_io import read_schedule

    payload, selected = read_schedule(schedule)
    arcs, trains, mow = load_ras_dataset(dataset)
    config = payload["config"]
    _, report = certified_upper_bound(
        selected,
        arcs=arcs,
        trains=trains,
        mow=mow,
        restrictions=BranchRestrictions(),
        horizon=float(config["horizon"]),
        safety_headway=float(config["safety_headway"]),
        time_step=float(config["time_step"]),
        wait_step=float(config["wait_step"]),
        origin_wait_cost=float(config["origin_wait_cost"]),
        running_cost=float(config["running_cost"]),
        siding_wait_cost=float(config["siding_wait_cost"]),
        early_cost=float(config["early_cost"]),
        late_cost=float(config["late_cost"]),
        headway_model=str(config["headway_model"]),
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Independently validate a serialized timetable.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate_solution_file(args.dataset, args.schedule)
    output = args.output or args.schedule.with_name("validator_report.json")
    output.write_text(json.dumps(_jsonable(report), indent=2, sort_keys=True) + "\n")
    print(f"{report['status']}: {output}")


if __name__ == "__main__":
    main()
