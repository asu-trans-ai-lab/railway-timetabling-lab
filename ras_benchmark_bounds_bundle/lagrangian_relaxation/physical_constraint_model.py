"""Valid cross-train physical coupling constraints, derived from the validator.

Authority for every predicate in this file is
``soft_lagrangian._physical_leg_pair_conflict`` /
``soft_lagrangian._overtake_witness``.  Nothing here is derived from the old
``sum_i a(i,r,b) <= C_LR(r,b)`` block model, from PATH-K, or from an observed
relaxed solution.

Notation, kept deliberately distinct from the Fluid Queue:

    pi(r, b)   Fluid Queue congestion price on a 30-minute aggregation block.
               Produced by fluid_queue.py.  NOT a certified dual multiplier.

    mu(k)      Multiplier on coupling constraint k.  Every constraint built
               here is a valid inequality for the physically feasible set, so
               L(mu) <= OPT for every mu >= 0.

A ``Coupling`` is always of the form

    sum over its terms of x(i, e)  <=  rhs

where ``x(i, e) = 1`` iff train i's trajectory contains movement event ``e``.
Because each term names exactly one (train, event) pair with coefficient one,
dualizing gives an additive per-train event price and the subproblems stay
independent.  See PHYSICAL_COUPLING_MODEL.md for the proofs.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

# The validator's own tolerance.  Reproduced, not re-chosen.
EPS = 1e-9

MAIN_TRACK_TYPES = frozenset({"0", "1", "2"})


def is_main_track(track_type: str) -> bool:
    """Exact restatement of ``soft_lagrangian._main_track_type``."""
    return str(track_type).strip() in MAIN_TRACK_TYPES


@dataclass(frozen=True, order=True)
class Event:
    """One discrete movement of one train over one physical arc.

    ``entry``/``exit`` are the DP's snapped grid times, i.e. exactly the
    ``legs`` triple the validator consumes.  ``ab`` is the direction flag.
    These four fields are precisely the arguments
    ``_physical_leg_pair_conflict`` reads, so two events determine their
    mutual legality with no further path context.
    """

    arc_id: int
    ab: bool
    entry: float
    exit: float

    @property
    def canonical(self) -> tuple[int, bool, float, float]:
        """Import-path independent identity of this event.

        ``Event`` is a frozen dataclass, so ``__eq__`` requires both operands
        to be instances of the *same class object*.  This module can legally be
        reached under more than one import path (``cross_train_relaxation...``
        and ``resource_lr_bb_clean.cross_train_relaxation...``), which produces
        two distinct class objects and would make otherwise-identical events
        compare unequal.  Feature matching therefore compares canonical tuples,
        never object identity.  The event definition itself is unchanged.
        """
        return (int(self.arc_id), bool(self.ab), float(self.entry), float(self.exit))

    def protected_end(self, headway: float) -> float:
        return self.exit + max(0.0, headway)

    def covers_tick(self, tick: float, headway: float) -> bool:
        """True iff ``tick`` lies in the protected support ``[entry, exit+h)``.

        Uses the validator's own strict-with-EPS convention so that
        "two protected intervals overlap" and "two protected supports share a
        grid tick" are the same statement (proved in the design document).
        """
        return self.entry <= tick + EPS and tick < self.protected_end(headway) - EPS


def pair_conflict(left: Event, right: Event, track_type: str,
                  headway: float) -> tuple[bool, str]:
    """Exact restatement of ``soft_lagrangian._physical_leg_pair_conflict``.

    Only called for two events on the *same* arc, which is what
    ``_profile_leg_pairs`` iterates over; ``track_type`` is that arc's type, so
    the validator's ``main = main(left) and main(right)`` reduces to
    ``is_main_track(track_type)``.
    """
    same_direction = bool(left.ab) == bool(right.ab)
    if same_direction and is_main_track(track_type):
        return (abs(left.entry - right.entry) < max(0.0, headway) - EPS,
                "same-direction-main-headway")
    left_end = left.protected_end(headway)
    right_end = right.protected_end(headway)
    if left.entry < right_end - EPS and right.entry < left_end - EPS:
        return True, ("same-direction-special-overlap" if same_direction
                      else "opposing-protected-overlap")
    return False, ""


@dataclass(frozen=True)
class Coupling:
    """``sum_{(i,e) in terms} x(i,e) <= rhs``, a valid physical inequality."""

    key: str
    family: str
    rule: str
    rhs: float
    terms: tuple[tuple[str, Event], ...]
    arc_id: int
    detail: str = ""

    def feature(self, train_id: str, events: Iterable[Event]) -> float:
        """``g_ik(p_i)``: how many of this train's terms the trajectory uses."""
        present = {event.canonical for event in events}
        return float(sum(1 for owner, event in self.terms
                         if owner == str(train_id) and event.canonical in present))

    def usage(self, selected: Mapping[str, Sequence[Event]]) -> float:
        return sum(self.feature(train_id, events)
                   for train_id, events in selected.items())

    def usage_with(self, present: Mapping[str, frozenset]) -> float:
        """``G_k`` from pre-built canonical event sets.

        Identical arithmetic to :meth:`usage`; it only avoids rebuilding the
        per-train canonical set once per coupling.  Terms are counted with
        multiplicity exactly as :meth:`feature` counts them.
        """
        total = 0
        for owner, event in self.terms:
            bucket = present.get(str(owner))
            if bucket is not None and event.canonical in bucket:
                total += 1
        return float(total)

    def is_violated(self, selected: Mapping[str, Sequence[Event]]) -> bool:
        return self.usage(selected) > self.rhs + 1e-9


# ---------------------------------------------------------------------------
# Self-conflict safety.
#
# The validator only compares movements of *different* trains.  An aggregated
# inequality with rhs = 1 over (train, event) terms is therefore valid only if
# no single feasible trajectory can contribute two terms to it.  The two
# aggregate families (A and B) are emitted only for indices where that is
# provable from the network alone -- never from an observed path.
# ---------------------------------------------------------------------------

def _snapped(minutes: float, time_step: float) -> float:
    return math.ceil(minutes / time_step - EPS) * time_step


def _traversal(arc, smult: float, ab: bool) -> float:
    speed = (arc.speed_ab if ab else arc.speed_ba) * max(smult, 1e-9)
    if speed <= 0.0:
        return math.inf
    return 60.0 * arc.length / speed


def shortest_snapped_times(arcs: Mapping[int, object], smult: float,
                           time_step: float) -> dict[int, dict[int, float]]:
    """All-pairs shortest travel time using snapped per-transition durations.

    Snapping each transition forward is exactly what the DP does, so this is a
    valid lower bound on any real elapsed time between two nodes.
    """
    outgoing: dict[int, list[tuple[int, float]]] = defaultdict(list)
    nodes: set[int] = set()
    for arc in arcs.values():
        nodes.add(arc.a)
        nodes.add(arc.b)
        forward = _snapped(_traversal(arc, smult, True), time_step)
        if math.isfinite(forward) and forward > 0:
            outgoing[arc.a].append((arc.b, forward))
        if arc.bidirectional:
            backward = _snapped(_traversal(arc, smult, False), time_step)
            if math.isfinite(backward) and backward > 0:
                outgoing[arc.b].append((arc.a, backward))
    result: dict[int, dict[int, float]] = {}
    for source in sorted(nodes):
        distance = {source: 0.0}
        queue = [(0.0, source)]
        while queue:
            value, node = heapq.heappop(queue)
            if value > distance.get(node, math.inf) + EPS:
                continue
            for nxt, weight in outgoing.get(node, ()):
                candidate = value + weight
                if candidate < distance.get(nxt, math.inf) - EPS:
                    distance[nxt] = candidate
                    heapq.heappush(queue, (candidate, nxt))
        result[source] = distance
    return result


def minimum_reentry_gap(arcs: Mapping[int, object], arc_id: int, ab: bool,
                        *, smult: float, time_step: float,
                        distances: Mapping[int, Mapping[int, float]],
                        same_direction_only: bool) -> float:
    """Least possible time between two movements of ONE train over ``arc_id``.

    ``same_direction_only`` returns the least possible ``entry2 - entry1`` for
    two traversals in direction ``ab``.  The train must traverse the arc and
    then travel from the far endpoint back to the near endpoint, so the bound
    is ``snapped_tau(r, ab) + dist(tail -> head)``.  Endpoints differ (the
    parser rejects ``a == b``), so that distance uses at least one transition
    and is strictly positive.

    Otherwise the quantity returned is the least possible ``entry2 - exit1``
    for two traversals in any direction: movement one ends at an endpoint of
    the arc and movement two starts at an endpoint of the arc, so the bound is
    ``min over endpoints u, v of dist(u -> v)``.  On a bidirectional arc that
    minimum is zero, because a train may leave at ``b`` and immediately
    re-enter the same arc heading back.  Zero is the mathematically correct
    answer and it is what makes Family B inapplicable there.

    Both quantities are computed from the network alone -- never from an
    observed path.  Infinity means the second movement is impossible, which is
    the safest possible answer.
    """
    arc = arcs[arc_id]
    head, tail = (arc.a, arc.b) if ab else (arc.b, arc.a)
    if same_direction_only:
        traverse = _snapped(_traversal(arc, smult, ab), time_step)
        return traverse + distances.get(tail, {}).get(head, math.inf)
    endpoints = (arc.a, arc.b)
    return min(distances.get(source, {}).get(target, math.inf)
               for source in endpoints for target in endpoints)


# ---------------------------------------------------------------------------
# Family A -- exact sliding-window cliques for same-direction main-track entry
#             headway.
# ---------------------------------------------------------------------------

def family_a_windows(
    events_by_train: Mapping[str, Iterable[Event]],
    arcs: Mapping[int, object], *, headway: float, time_step: float,
    smult: float = 1.0, distances: Mapping[int, Mapping[int, float]] | None = None,
) -> tuple[list[Coupling], list[dict[str, object]]]:
    """Return the certified windows plus a rejection log.

    For a main-track arc ``r`` and direction ``d``, the validator forbids two
    different trains from entering with ``|t_i - t_j| < h``.  The conflict
    graph on entry times is an indifference graph, whose cliques are exactly
    the sets of entries contained in a half-open window of width ``h``.  Each
    maximal clique therefore yields the exact inequality

        sum over entries in [t, t+h) of x(i,e)  <=  1

    which is emitted only when a single train provably cannot contribute two
    entries to the same window.
    """
    if distances is None:
        distances = shortest_snapped_times(arcs, smult, time_step)
    by_index: dict[tuple[int, bool], list[tuple[str, Event]]] = defaultdict(list)
    for train_id, events in events_by_train.items():
        for event in events:
            arc = arcs.get(event.arc_id)
            if arc is None or not is_main_track(arc.track_type):
                continue
            by_index[(event.arc_id, bool(event.ab))].append((train_id, event))
    couplings: list[Coupling] = []
    rejected: list[dict[str, object]] = []
    for (arc_id, ab) in sorted(by_index, key=lambda key: (key[0], key[1])):
        members = sorted(by_index[(arc_id, ab)], key=lambda item: (item[1].entry, item[0]))
        gap = minimum_reentry_gap(arcs, arc_id, ab, smult=smult, time_step=time_step,
                                  distances=distances, same_direction_only=True)
        safe = gap >= max(0.0, headway) - EPS
        anchors = sorted({event.entry for _train, event in members})
        for anchor in anchors:
            window = [(train_id, event) for train_id, event in members
                      if anchor - EPS <= event.entry < anchor + headway - EPS]
            if len({train_id for train_id, _event in window}) < 2:
                continue  # a single train in the window couples nothing
            if not safe:
                rejected.append({
                    "family": "A", "arc_id": arc_id, "ab": ab, "anchor": anchor,
                    "reason": "single train could re-enter within the window",
                    "minimum_reentry_gap": gap, "headway": headway})
                continue
            couplings.append(Coupling(
                key=f"A|{arc_id}|{'AB' if ab else 'BA'}|{anchor:.6f}",
                family="A", rule="same-direction-main-headway", rhs=1.0,
                terms=tuple(sorted(window, key=lambda item: (item[0], item[1]))),
                arc_id=arc_id,
                detail=f"entry window [{anchor:.6f}, {anchor + headway:.6f})"))
    # A window contained in another window's term set adds nothing.
    return _drop_dominated(couplings), rejected


def _drop_dominated(couplings: Sequence[Coupling]) -> list[Coupling]:
    kept: list[Coupling] = []
    term_sets = [frozenset(item.terms) for item in couplings]
    for index, coupling in enumerate(couplings):
        if any(index != other and term_sets[index] < term_sets[other]
               for other in range(len(couplings))):
            continue
        if any(index > other and term_sets[index] == term_sets[other]
               for other in range(len(couplings))):
            continue
        kept.append(coupling)
    return kept


# ---------------------------------------------------------------------------
# Family B -- exact protected-occupancy tick capacity, valid ONLY where every
#             direction pair forbids overlap, i.e. on non-main arcs.
# ---------------------------------------------------------------------------

def family_b_ticks(
    events_by_train: Mapping[str, Iterable[Event]],
    arcs: Mapping[int, object], *, headway: float, time_step: float,
    smult: float = 1.0, distances: Mapping[int, Mapping[int, float]] | None = None,
) -> tuple[list[Coupling], list[dict[str, object]]]:
    """Protected-occupancy capacity on arcs where all overlap is forbidden.

    On a non-main arc the validator applies the protected-overlap predicate to
    both the same-direction and the opposing case, so no two movements of
    different trains may have overlapping protected supports.  Overlap is
    equivalent to sharing a grid tick, so the tick capacity is exact.

    This is deliberately NOT emitted on main-track arcs: there the validator
    permits two same-direction movements to overlap provided their entries are
    at least ``h`` apart, and a tick capacity of one would exclude feasible
    timetables.  That is precisely the error the previous C_LR made.
    """
    if distances is None:
        distances = shortest_snapped_times(arcs, smult, time_step)
    by_arc: dict[int, list[tuple[str, Event]]] = defaultdict(list)
    for train_id, events in events_by_train.items():
        for event in events:
            arc = arcs.get(event.arc_id)
            if arc is None or is_main_track(arc.track_type):
                continue
            by_arc[event.arc_id].append((train_id, event))
    couplings: list[Coupling] = []
    rejected: list[dict[str, object]] = []
    for arc_id in sorted(by_arc):
        members = by_arc[arc_id]
        gap = minimum_reentry_gap(arcs, arc_id, True, smult=smult, time_step=time_step,
                                  distances=distances, same_direction_only=False)
        safe = gap >= max(0.0, headway) - EPS
        ticks = sorted({event.entry for _train, event in members})
        for tick in ticks:
            window = [(train_id, event) for train_id, event in members
                      if event.covers_tick(tick, headway)]
            if len({train_id for train_id, _event in window}) < 2:
                continue
            if not safe:
                rejected.append({
                    "family": "B", "arc_id": arc_id, "tick": tick,
                    "reason": "single train could re-occupy within the headway",
                    "minimum_reentry_gap": gap, "headway": headway})
                continue
            couplings.append(Coupling(
                key=f"B|{arc_id}|{tick:.6f}", family="B",
                rule="protected-overlap-non-main", rhs=1.0,
                terms=tuple(sorted(window, key=lambda item: (item[0], item[1]))),
                arc_id=arc_id, detail=f"protected occupancy of tick {tick:.6f}"))
    return _drop_dominated(couplings), rejected


# ---------------------------------------------------------------------------
# Family C -- exact pairwise event incompatibility.  Always valid; needs no
#             self-conflict side condition because the two terms belong to two
#             different trains by construction.
# ---------------------------------------------------------------------------

def family_c_pairs(
    events_by_train: Mapping[str, Iterable[Event]],
    arcs: Mapping[int, object], *, headway: float,
    rules: Iterable[str] | None = None,
) -> tuple[list[Coupling], list[dict[str, object]]]:
    """``x(i,e) + x(j,f) <= 1`` for every validator-incompatible pair."""
    allowed = None if rules is None else set(rules)
    train_ids = sorted(events_by_train)
    couplings: list[Coupling] = []
    # Resource-local generation.  ``Event`` sorts arc-major, so iterating a
    # train's sorted unique events and, for each, only the other train's events
    # on THAT arc reproduces the original nested-loop order exactly while
    # skipping every pair that could not share a resource.  The per-train sort
    # is also hoisted out of the inner loops, where it was being repeated once
    # per left event.
    unique: dict[str, list[Event]] = {
        train_id: sorted(set(events_by_train[train_id])) for train_id in train_ids}
    by_arc: dict[str, dict[int, list[Event]]] = {}
    for train_id in train_ids:
        grouped: dict[int, list[Event]] = defaultdict(list)
        for event in unique[train_id]:
            grouped[event.arc_id].append(event)
        by_arc[train_id] = dict(grouped)
    for left_index, left_id in enumerate(train_ids):
        for right_id in train_ids[left_index + 1:]:
            right_by_arc = by_arc[right_id]
            for left in unique[left_id]:
                for right in right_by_arc.get(left.arc_id, ()):
                    arc = arcs.get(left.arc_id)
                    if arc is None:
                        continue
                    conflict, rule = pair_conflict(left, right, arc.track_type, headway)
                    if not conflict:
                        continue
                    if allowed is not None and rule not in allowed:
                        continue
                    couplings.append(Coupling(
                        key=f"C|{left_id}|{left.arc_id}|{left.entry:.6f}|{left_id != right_id}"
                            f"|{right_id}|{right.entry:.6f}|{rule}",
                        family="C", rule=rule, rhs=1.0,
                        terms=((left_id, left), (right_id, right)),
                        arc_id=left.arc_id,
                        detail=f"{left_id}@{left.entry:.6f} vs {right_id}@{right.entry:.6f}"))
    return couplings, []


# ---------------------------------------------------------------------------
# Dualization support.
# ---------------------------------------------------------------------------

def event_prices(
    couplings: Sequence[Coupling], multipliers: Mapping[str, float],
) -> dict[str, dict[Event, float]]:
    """Aggregate ``price_i(e) = sum_{k incident to (i,e)} mu_k``.

    This is the ONLY object a single-train subproblem needs.  It never names
    another train, which is why the subproblems stay independent.
    """
    prices: dict[str, dict[Event, float]] = defaultdict(dict)
    for coupling in couplings:
        value = max(0.0, float(multipliers.get(coupling.key, 0.0)))
        if value <= 0.0:
            continue
        for train_id, event in coupling.terms:
            prices[train_id][event] = prices[train_id].get(event, 0.0) + value
    return dict(prices)


def canonical_event_prices(
    couplings: Sequence[Coupling], multipliers: Mapping[str, float],
) -> dict[str, dict[tuple[int, bool, float, float], float]]:
    """``event_prices`` keyed by the import-path independent event tuple."""
    prices: dict[str, dict[tuple[int, bool, float, float], float]] = defaultdict(dict)
    for train_id, table in event_prices(couplings, multipliers).items():
        for event, value in table.items():
            prices[train_id][event.canonical] = value
    return dict(prices)


def multiplier_dot_rhs(couplings: Sequence[Coupling],
                       multipliers: Mapping[str, float]) -> float:
    return sum(max(0.0, float(multipliers.get(item.key, 0.0))) * item.rhs
               for item in couplings)


def legs_to_events(legs: Iterable[tuple[int, float, float]],
                   ab_flags: Sequence[bool]) -> tuple[Event, ...]:
    return tuple(Event(int(arc_id), bool(ab_flags[index]), float(start), float(exit_))
                 for index, (arc_id, start, exit_) in enumerate(legs))


def joint_conflicts(selected: Mapping[str, Sequence[Event]],
                    arcs: Mapping[int, object], *, headway: float) -> list[dict[str, object]]:
    """Every validator leg-pair conflict in a joint selection (no overtaking).

    Overtaking is deliberately excluded: it is not a function of the two events
    alone (see PHYSICAL_COUPLING_MODEL.md section 6).
    """
    found: list[dict[str, object]] = []
    train_ids = sorted(selected)
    for index, left_id in enumerate(train_ids):
        for right_id in train_ids[index + 1:]:
            for left in selected[left_id]:
                for right in selected[right_id]:
                    if left.arc_id != right.arc_id:
                        continue
                    arc = arcs.get(left.arc_id)
                    if arc is None:
                        continue
                    conflict, rule = pair_conflict(left, right, arc.track_type, headway)
                    if conflict:
                        found.append({
                            "train_a": left_id, "train_b": right_id,
                            "arc_id": left.arc_id, "rule": rule,
                            "entry_a": left.entry, "entry_b": right.entry,
                            "exit_a": left.exit, "exit_b": right.exit,
                            "ab_a": left.ab, "ab_b": right.ab})
    return found
