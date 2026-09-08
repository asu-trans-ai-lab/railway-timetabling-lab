"""Certified negative/negative disjunctions for trusted movement conflicts.

For the canonical conflict s=(e_i,e_j), each child removes one EXACT movement
(arc, direction, snapped entry, snapped exit). Both events cannot coexist in a
feasible timetable. The negative children cover that feasible set and may
overlap. We never widen a cut to unproved entry times, directions or dwell times.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .physical_constraint_model import Event
from .dp_interface import Arc, BranchRestrictions
from .headway_model import HEADWAY_MODEL_LEGACY_ENTRY

SUPPORTED = frozenset({"same-direction-main-headway", "same-direction-special-overlap",
                       "opposing-protected-overlap", "same-direction-main-segment-clearance"})
EPS = 1e-9


@dataclass(frozen=True)
class ConflictBranch:
    conflict_id: str
    train_i: str
    train_j: str
    arc_id: int
    conflict_type: str
    event_i: Event
    event_j: Event
    headway: float
    proof: str
    headway_model: str = field(default=HEADWAY_MODEL_LEGACY_ENTRY, kw_only=True)

    def describe(self) -> str:
        return (f"{self.train_i} vs {self.train_j} @ arc {self.arc_id}: "
                f"{self.event_i.canonical} / {self.event_j.canonical}; {self.conflict_type}")

    def restriction_for(self, train_id: str) -> dict:
        if train_id not in (self.train_i, self.train_j):
            raise ValueError(f"train {train_id} is not in conflict {self.conflict_id}")
        event = self.event_i if train_id == self.train_i else self.event_j
        return {"train_id": train_id, "relation": "FORBID_EVENT",
                "resource_id": event.arc_id, "direction": "AB" if event.ab else "BA",
                "t_lo": event.entry, "t_hi": event.exit}


def trusted_records(conflicts: Sequence) -> list[dict]:
    """Normalize the actual validator's PhysicalConflict / ActionSignature records."""
    records = []
    for conflict in conflicts:
        a, b = conflict.action_a, conflict.action_b
        records.append({"train_i": conflict.train_a, "train_j": conflict.train_b,
                        "resource": conflict.arc_id, "conflict_type": conflict.conflict_type,
                        "entry_i": a.start_min, "exit_i": a.exit_min,
                        "entry_j": b.start_min, "exit_j": b.exit_min,
                        "direction_i": a.direction, "direction_j": b.direction,
                        "arc_i": a.arc_id, "arc_j": b.arc_id})
    return records


def build_conflict(edge: Mapping[str, object], *, time_step: float,
                   arcs: Mapping[int, Arc], headway: float,
                   headway_model: str = HEADWAY_MODEL_LEGACY_ENTRY) -> ConflictBranch:
    """Recheck a supported exact event pair with the AUTHORITATIVE predicate."""
    from validator.trusted_physics import _physical_leg_pair_conflict

    rule = str(edge["conflict_type"])
    if rule not in SUPPORTED:
        raise ValueError(f"unsupported context-dependent conflict: {rule}")
    arc_id = int(edge["resource"])
    if arc_id not in arcs:
        raise ValueError(f"unknown physical arc {arc_id}")
    if any(int(edge.get(key, arc_id)) != arc_id for key in ("arc_i", "arc_j")):
        raise ValueError("conflict movements are not on the same physical arc")
    train_i, train_j = str(edge["train_i"]), str(edge["train_j"])
    if train_i == train_j or not train_i or not train_j:
        raise ValueError("branch requires two distinct trains")
    events = []
    for suffix in ("i", "j"):
        direction = str(edge[f"direction_{suffix}"])
        start, end = float(edge[f"entry_{suffix}"]), float(edge[f"exit_{suffix}"])
        if (direction not in {"AB", "BA"} or not math.isfinite(start) or
                not math.isfinite(end) or start < 0 or end <= start):
            raise ValueError("malformed movement event")
        if direction == "BA" and not arcs[arc_id].bidirectional:
            raise ValueError("unusable reverse direction")
        if time_step <= 0 or any(abs(t/time_step-round(t/time_step)) > 1e-7
                                for t in (start, end)):
            raise ValueError("event is outside the DP time grid")
        events.append(Event(arc_id, direction == "AB", start, end))
    args = [{"entry": e.entry, "exit": e.exit, "direction": e.ab,
             "track_type": arcs[arc_id].track_type} for e in events]
    yes, actual_rule = _physical_leg_pair_conflict(*args, headway, headway_model=headway_model)
    if not yes or actual_rule != rule:
        raise ValueError(f"validator predicate disagrees with conflict record ({actual_rule}, {yes})")
    ident = f"{train_i}:{events[0].canonical}|{train_j}:{events[1].canonical}|{rule}"
    return ConflictBranch(ident, train_i, train_j, arc_id, rule, *events, headway,
                          "Trusted leg-pair predicate rejects these exact movements; "
                          "every feasible timetable omits at least one.", headway_model=headway_model)


def children(conflict: ConflictBranch, parent: BranchRestrictions
             ) -> tuple[BranchRestrictions, BranchRestrictions]:
    out = []
    for train, event in ((conflict.train_i, conflict.event_i), (conflict.train_j, conflict.event_j)):
        child = parent.with_forbidden_event(train, event.arc_id, "AB" if event.ab else "BA",
                                             event.entry, event.exit)
        if child == parent:
            raise RuntimeError("selected conflict regenerates an inherited forbidden event")
        out.append(child)
    return out[0], out[1]


def select_conflict(edges: Sequence[Mapping[str, object]], *, time_step: float,
                    arcs: Mapping[int, Arc], headway: float,
                    headway_model: str = HEADWAY_MODEL_LEGACY_ENTRY
                    ) -> tuple[ConflictBranch | None, list[dict]]:
    """Earliest supported trusted conflict; all unsupported records are explicit.

    Resolving another proved pair is safe when unsupported conflicts coexist.
    An unsupported-only timetable remains an unresolved live leaf.
    """
    ordered = sorted(edges, key=lambda e: (min(float(e["entry_i"]), float(e["entry_j"])),
                                           str(e["conflict_type"]), str(e["train_i"]),
                                           str(e["train_j"]), int(e["resource"]),
                                           float(e["exit_i"]), float(e["exit_j"])))
    rejected, chosen = [], None
    for edge in ordered:
        try:
            conflict = build_conflict(edge, time_step=time_step, arcs=arcs, headway=headway,
                                      headway_model=headway_model)
        except ValueError as exc:
            rejected.append({**dict(edge), "reason": str(exc), "status": "UNSUPPORTED_CONFLICT"})
        else:
            if chosen is None:
                chosen = conflict
    return chosen, rejected
