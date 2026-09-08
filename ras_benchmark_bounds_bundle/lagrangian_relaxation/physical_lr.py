"""Experimental physical cross-train Lagrangian relaxation (prototype).

This module does NOT replace ``lagrangian.py`` and does NOT touch
``network_dp.cpp``, ``fluid_queue.py``, or any frozen file.  It exists to
answer three questions, in order:

    Q1  Is the new physical relaxation valid?
    Q2  Does it have a stronger dual optimum than L(0)?
    Q3  Can the Fluid Queue supply useful multipliers for it?

The certified bound is always

    L(mu) = sum_i min_{p in P_i(N)} [ c_i(p) + sum_e price_i(e) [e in p] ]
            - sum_k mu_k * rhs_k ,      mu >= 0

with ``price_i(e) = sum_{k incident to (i,e)} mu_k``.  Every coupling in the
pool is a valid inequality for the physically feasible set, so weak duality
gives ``L(mu) <= OPT`` for every ``mu >= 0`` (PHYSICAL_COUPLING_MODEL.md §4).

``EventPricedDP`` is an independent restatement of ``network_dp.cpp`` with one
addition: an additive per-event price.  It is verified against the compiled C++
DP at zero price, which is what licenses using it as the subproblem solver
while the C++ event-price channel does not yet exist.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from . import physical_constraint_model as pcm
from .physical_constraint_model import Coupling, Event

EPS = 1e-9


# ---------------------------------------------------------------------------
# Subproblem solver.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TrainSolution:
    train_id: str
    feasible: bool
    generalized_cost: float
    physical_cost: float
    event_price: float
    events: tuple[Event, ...]
    reason: str = ""


class EventPricedDP:
    """Unrestricted single-train network DP with an additive event price.

    Every rule is copied from ``network_dp.cpp``: forward tick snapping,
    siding-only waiting charged at ``siding_wait_cost``, MOW on the snapped
    interval, admissible reverse-Dijkstra horizon pruning, destination as a
    terminal, REQUIRE bitmask acceptance, PROHIBIT transition deletion, and
    dominance only within an identical ``(tick, node, mask)`` key.

    The single addition is ``prices``: a mapping from an ``Event`` to a
    non-negative price added to the generalized cost when the corresponding
    transition is taken.  ``physical_cost`` never includes it, so the identity
    ``generalized = physical + event_price`` holds by construction.
    """

    def __init__(self, arcs: Mapping[int, object], mow: Iterable[Mapping[str, float | int]],
                 config) -> None:
        self.arcs = dict(arcs)
        self.config = config
        self.graph: dict[int, list[tuple[int, int, bool]]] = defaultdict(list)
        for arc in self.arcs.values():
            self.graph[arc.a].append((arc.b, arc.arc_id, True))
            if arc.bidirectional:
                self.graph[arc.b].append((arc.a, arc.arc_id, False))
        for node in self.graph:
            self.graph[node].sort()
        self.mow: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for window in mow:
            a, b = int(window["a"]), int(window["b"])
            for arc in self.arcs.values():
                if {arc.a, arc.b} == {a, b}:
                    self.mow[arc.arc_id].append((float(window["start"]), float(window["end"])))
        self._remaining: dict[tuple[int, float], dict[int, float]] = {}
        self._moves: dict[float, dict[int, tuple]] = {}

    def _move_table(self, smult: float) -> dict[int, tuple]:
        """Precomputed outgoing (target, arc_id, ab, tau, waits, wait_costs).

        Pure memoization of values the transition loop would otherwise recompute
        for every one of the ~2881 time layers.  No rule changes.
        """
        key = round(smult, 12)
        cached = self._moves.get(key)
        if cached is not None:
            return cached
        cfg = self.config
        table: dict[int, tuple] = {}
        for node, edges in self.graph.items():
            rows = []
            for target, arc_id, ab in edges:
                arc = self.arcs[arc_id]
                tau = self._tau(arc_id, smult, ab)
                if not tau > 0.0 or not math.isfinite(tau):
                    continue
                if arc.track_type == "S" and cfg.wait_step > 0 and cfg.max_wait > 0:
                    count = int(math.floor(cfg.max_wait / cfg.wait_step + EPS))
                    waits = tuple(index * cfg.wait_step for index in range(count + 1))
                    costs = tuple(w * cfg.siding_wait_cost for w in waits)
                else:
                    waits, costs = (0.0,), (0.0,)
                rows.append((target, arc_id, ab, tau, waits, costs,
                             cfg.running_cost * tau))
            table[node] = tuple(rows)
        self._moves[key] = table
        return table

    def _tau(self, arc_id: int, smult: float, ab: bool) -> float:
        arc = self.arcs[arc_id]
        speed = (arc.speed_ab if ab else arc.speed_ba) * max(smult, 1e-9)
        if speed <= 0.0:
            return math.inf
        return 60.0 * arc.length / speed

    def _snap(self, minutes: float) -> int:
        return int(math.ceil(minutes / self.config.time_step - EPS))

    def _mow_free(self, arc_id: int, start: float, exit_: float) -> bool:
        return all(not (start < end - EPS and begin < exit_ - EPS)
                   for begin, end in self.mow.get(arc_id, ()))

    def _remaining_time(self, destination: int, smult: float) -> dict[int, float]:
        key = (destination, round(smult, 12))
        cached = self._remaining.get(key)
        if cached is not None:
            return cached
        reverse: dict[int, list[tuple[int, float]]] = defaultdict(list)
        for node, edges in self.graph.items():
            for target, arc_id, ab in edges:
                tau = self._tau(arc_id, smult, ab)
                if math.isfinite(tau) and tau > 0.0:
                    reverse[target].append((node, tau))
        distance = {destination: 0.0}
        queue = [(0.0, destination)]
        while queue:
            value, node = heapq.heappop(queue)
            if value > distance.get(node, math.inf) + EPS:
                continue
            for previous, tau in reverse.get(node, ()):
                candidate = value + tau
                if candidate < distance.get(previous, math.inf) - EPS:
                    distance[previous] = candidate
                    heapq.heappush(queue, (candidate, previous))
        self._remaining[key] = distance
        return distance

    def solve(self, train, *, prices: Mapping[Event, float] | None = None,
              required: Iterable[int] = (), prohibited: Iterable[int] = ()) -> TrainSolution:
        cfg = self.config
        prices = prices or {}
        required = tuple(sorted({int(item) for item in required}))
        prohibited = frozenset(int(item) for item in prohibited)
        if set(required) & prohibited:
            return TrainSolution(train.train_id, False, math.inf, math.inf, 0.0, (),
                                 "contradictory required/prohibited resource")
        bit_of = {resource: index for index, resource in enumerate(required)}
        full_mask = (1 << len(required)) - 1
        max_tick = int(math.floor(cfg.horizon / cfg.time_step + EPS))
        remaining = self._remaining_time(train.destination, train.smult)
        if train.origin not in remaining or train.entry_min > cfg.horizon + EPS:
            return TrainSolution(train.train_id, False, math.inf, math.inf, 0.0, (),
                                 "destination is unreachable in the physical network")

        # state: (node, mask) -> (generalized, physical, event_price, parent)
        layers: list[dict[tuple[int, int], tuple[float, float, float, object]]] = [
            {} for _ in range(max_tick + 1)]

        def push(tick: int, node: int, mask: int, gen: float, phys: float,
                 price: float, parent) -> None:
            layer = layers[tick]
            key = (node, mask)
            old = layer.get(key)
            if old is not None and old[0] <= gen + 1e-12:
                return
            layer[key] = (gen, phys, price, parent)

        departures = max(0, int(math.floor(cfg.departure_slack / cfg.departure_step + EPS)))
        for step in range(departures + 1):
            tick = self._snap(train.entry_min + step * cfg.departure_step)
            if tick < 0 or tick > max_tick:
                continue
            moment = tick * cfg.time_step
            cost = cfg.origin_wait_cost * (moment - train.entry_min)
            push(tick, train.origin, 0, cost, cost, 0.0, None)

        best: tuple[float, float, float, object] | None = None
        moves = self._move_table(train.smult)
        snap = self._snap
        step_minutes = cfg.time_step
        mow_arcs = frozenset(self.mow)
        for tick in range(max_tick + 1):
            layer = layers[tick]
            if not layer:
                continue
            moment = tick * cfg.time_step
            for (node, mask), state in list(layer.items()):
                gen, phys, price, parent = state
                far = remaining.get(node)
                if far is None or moment + far > cfg.horizon + EPS:
                    continue
                if node == train.destination:
                    if mask != full_mask:
                        continue
                    early = late = 0.0
                    if train.terminal_want is not None:
                        early = max(0.0, train.terminal_want - moment)
                        late = max(0.0, moment - train.terminal_want)
                    terminal = cfg.early_cost * early + cfg.late_cost * late
                    total = gen + terminal
                    if best is None or total < best[0] - 1e-12:
                        best = (total, phys + terminal, price, parent)
                    continue  # destination is terminal
                for target, arc_id, ab, tau, waits, wait_costs, run_cost in moves.get(node, ()):
                    if arc_id in prohibited:
                        continue
                    for wait, wait_cost in zip(waits, wait_costs):
                        next_tick = snap(moment + wait + tau)
                        if next_tick <= tick or next_tick > max_tick:
                            continue
                        exit_ = next_tick * step_minutes
                        if arc_id in mow_arcs and not self._mow_free(arc_id, moment, exit_):
                            continue
                        event = Event(arc_id, ab, moment, exit_)
                        extra = prices.get(event, 0.0)
                        if extra < 0.0:
                            extra = 0.0
                        step_cost = run_cost + wait_cost
                        next_mask = mask | (1 << bit_of[arc_id]) if arc_id in bit_of else mask
                        push(next_tick, target, next_mask, gen + step_cost + extra,
                             phys + step_cost, price + extra, (event, (node, mask), tick))
        if best is None:
            return TrainSolution(train.train_id, False, math.inf, math.inf, 0.0, (),
                                 "no legal physical-network trajectory satisfies restrictions")
        events: list[Event] = []
        parent = best[3]
        while parent is not None:
            event, key, tick = parent
            events.append(event)
            parent = layers[tick][key][3]
        events.reverse()
        return TrainSolution(train.train_id, True, best[0], best[1], best[2], tuple(events))


# ---------------------------------------------------------------------------
# Relaxation evaluation.
# ---------------------------------------------------------------------------

@dataclass
class PhysicalLREvaluation:
    dual_value: float
    minimum_sum: float
    multiplier_dot_rhs: float
    solutions: dict[str, TrainSolution]
    violated: list[Coupling] = field(default_factory=list)
    infeasible_train: str | None = None


def evaluate(dp: EventPricedDP, trains: Sequence[object], couplings: Sequence[Coupling],
             multipliers: Mapping[str, float], *,
             required: Mapping[str, Iterable[int]] | None = None,
             prohibited: Mapping[str, Iterable[int]] | None = None,
             ) -> PhysicalLREvaluation:
    """Evaluate ``L(mu)``, always with the certified formula."""
    prices = pcm.event_prices(couplings, multipliers)
    solutions: dict[str, TrainSolution] = {}
    total = 0.0
    infeasible = None
    for train in trains:
        solution = dp.solve(
            train, prices=prices.get(train.train_id, {}),
            required=(required or {}).get(train.train_id, ()),
            prohibited=(prohibited or {}).get(train.train_id, ()))
        solutions[train.train_id] = solution
        if not solution.feasible:
            infeasible = train.train_id
        else:
            total += solution.generalized_cost
    if infeasible is not None:
        return PhysicalLREvaluation(math.inf, math.inf, 0.0, solutions,
                                    infeasible_train=infeasible)
    dot = pcm.multiplier_dot_rhs(couplings, multipliers)
    selected = {train_id: solution.events for train_id, solution in solutions.items()}
    violated = [item for item in couplings if item.is_violated(selected)]
    return PhysicalLREvaluation(total - dot, total, dot, solutions, violated)


def generate_couplings(selected: Mapping[str, Sequence[Event]], arcs, *,
                       headway: float, time_step: float, families: str = "A+C",
                       distances=None, smult: float = 1.0,
                       ) -> tuple[list[Coupling], list[dict[str, object]]]:
    """Build valid couplings from a current selection (lazy activation).

    Discovering a constraint from an observed selection does not affect its
    validity: each emitted inequality is valid for the entire physically
    feasible set, because it is a sub-sum of an exact clique or an exact
    pairwise incompatibility.  Restricting the terms to observed events only
    weakens the inequality, never invalidates it.
    """
    events = {train_id: sorted(set(items)) for train_id, items in selected.items()}
    couplings: list[Coupling] = []
    rejected: list[dict[str, object]] = []
    if "A" in families:
        found, dropped = pcm.family_a_windows(
            events, arcs, headway=headway, time_step=time_step,
            smult=smult, distances=distances)
        couplings += found
        rejected += dropped
    if "B" in families:
        found, dropped = pcm.family_b_ticks(
            events, arcs, headway=headway, time_step=time_step,
            smult=smult, distances=distances)
        couplings += found
        rejected += dropped
    if "C" in families:
        found, dropped = pcm.family_c_pairs(events, arcs, headway=headway)
        couplings += found
        rejected += dropped
    # A pairwise inequality whose two terms sit inside an emitted clique window
    # is implied by that window, so keeping it would only add a redundant
    # multiplier.  Dropping a valid inequality is always safe (section 19).
    return pcm._drop_dominated(couplings), rejected


def ascend(dp: EventPricedDP, trains, couplings: Sequence[Coupling], *,
           multipliers: Mapping[str, float] | None = None,
           iterations: int = 60, step: float = 4.0,
           required=None, prohibited=None, log: list | None = None,
           ) -> tuple[float, dict[str, float]]:
    """Projected subgradient search over ``mu >= 0``.  AUDIT TOOL ONLY.

    This exists solely to answer Q2 -- "does the valid relaxation have a
    stronger dual at all?".  It is not, and must not become, the production
    price policy; the production policy remains the Fluid Queue.  Whatever
    ``mu`` it returns, the reported bound is still ``L(mu)``.
    """
    mu = {item.key: max(0.0, float((multipliers or {}).get(item.key, 0.0)))
          for item in couplings}
    best = -math.inf
    best_mu = dict(mu)
    for index in range(iterations):
        evaluation = evaluate(dp, trains, couplings, mu,
                              required=required, prohibited=prohibited)
        value = evaluation.dual_value
        if value > best:
            best, best_mu = value, dict(mu)
        if log is not None:
            log.append({"iteration": index, "dual_value": value, "best": best,
                        "nonzero_multipliers": sum(1 for v in mu.values() if v > EPS)})
        if not math.isfinite(value):
            break
        selected = {train_id: solution.events
                    for train_id, solution in evaluation.solutions.items()}
        gradient = {item.key: item.usage(selected) - item.rhs for item in couplings}
        norm = math.sqrt(sum(value * value for value in gradient.values()))
        if norm <= EPS:
            break
        size = step / math.sqrt(index + 1.0)
        for key, slope in gradient.items():
            mu[key] = max(0.0, mu[key] + size * slope / norm)
    return best, best_mu
