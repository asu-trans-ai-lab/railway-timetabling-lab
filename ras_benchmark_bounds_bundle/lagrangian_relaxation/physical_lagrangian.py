"""Production physical cross-train Lagrangian relaxation.

This is the certified lower-bound engine.  It replaces the historical
``lagrangian.py`` safe-v1 capacity relaxation on the production LB path;
``lagrangian.py`` itself is untouched and remains available as
``historical_safe_v1_diagnostic``.

Architecture (see ARCHITECTURE.md):

    Resource B&B
        -> valid physical cross-train LR
             +-- Fluid Queue pi : support / prioritization only
             +-- projected subgradient : mu magnitude
        -> event-priced unrestricted network DP

The coupling mathematics is frozen in
``cross_train_relaxation/PHYSICAL_COUPLING_MODEL.md`` and implemented by
``cross_train_relaxation/physical_constraint_model.py``.  Nothing here redefines
a family, a validator predicate, an event, or a headway boundary convention.

Terminology, enforced throughout:

    pi(r, b)   Fluid Queue congestion signal on an arc x 30-minute block.
               Used ONLY to choose which valid constraints to activate and in
               what order.  It never enters the certified bound.

    mu(k)      Multiplier on a verified physical coupling inequality.  This is
               the only quantity that appears in L(mu).

The Fluid Queue is a support detector, not a dual optimizer and not dual
ascent.  Magnitude comes from the projected subgradient layer.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from . import physical_constraint_model as pcm
from .physical_constraint_model import Coupling, Event
from .fluid_queue import fluid_lambda_update, fluid_queue_cells
from dp.network_dp_interface import (
    Arc,
    BranchRestrictions,
    DPConfig,
    DPResult,
    NetworkDP,
    Train,
)

EPS = 1e-9

#: Only families whose validity is proved in PHYSICAL_COUPLING_MODEL.md may
#: contribute to a certified bound.  Family B is proved but is certified on no
#: RAS arc (every arc is bidirectional), so it is not in the production set.
VERIFIED_FAMILY_REGISTRY = frozenset({"A", "C"})

#: Active-set construction modes.
FULL_OBSERVED_EVENT_POOL = "FULL_OBSERVED_EVENT_POOL"
LAZY_VALIDATOR_ACTIVATION = "LAZY_VALIDATOR_ACTIVATION"
FLUID_QUEUE_PRIORITIZED = "FLUID_QUEUE_PRIORITIZED"

#: Multiplier initialization modes (task section 17).
MU_ZERO = "MU_ZERO"
MU_FROM_RAW_PI = "MU_FROM_RAW_PI"
MU_SUPPORT_ONLY = "MU_SUPPORT_ONLY"


def entry_tick(entry: float, time_step: float) -> int:
    """The DP's own entry tick.  Must match ``network_dp.cpp`` exactly."""
    return int(round(entry / time_step))


def aggregate_event_prices(
    couplings: Sequence[Coupling], multipliers: Mapping[str, float],
    *, time_step: float,
) -> dict[tuple[str, int, bool, int], float]:
    """``price_i(e) = sum_{k incident to (i,e)} mu_k``, in DP channel keys.

    This is the only object the DP receives.  It is per train, so the DP never
    learns of another train, a pair, or a clique membership.
    """
    prices: dict[tuple[str, int, bool, int], float] = {}
    for coupling in couplings:
        value = float(multipliers.get(coupling.key, 0.0))
        if value <= EPS:
            continue
        for train_id, event in coupling.terms:
            key = (str(train_id), int(event.arc_id), bool(event.ab),
                   entry_tick(event.entry, time_step))
            prices[key] = prices.get(key, 0.0) + value
    return prices


def events_of(result: DPResult) -> tuple[Event, ...]:
    return pcm.legs_to_events(result.legs, result.ab_flags)


@dataclass(frozen=True)
class PhysicalLREvaluation:
    """One certified evaluation of ``L(mu)``."""

    dual_value: float
    minimum_sum: float
    mu_dot_capacity: float
    paths: dict[str, DPResult]
    events: dict[str, tuple[Event, ...]]
    usage: dict[str, float]                 # G_k
    violation: dict[str, float]             # G_k - C_k  (the subgradient)
    violated: tuple[Coupling, ...]
    reconstruction: float
    reconstruction_error: float
    certified: bool
    infeasible_train: str | None = None

    @property
    def subgradient_norm(self) -> float:
        return math.sqrt(sum(value * value for value in self.violation.values()))


def _certified(couplings: Iterable[Coupling]) -> bool:
    return all(item.family in VERIFIED_FAMILY_REGISTRY for item in couplings)


def evaluate(
    dp: NetworkDP, *, arcs: Mapping[int, Arc], trains: Sequence[Train],
    mow: Sequence[Mapping[str, float | int]], config: DPConfig,
    restrictions: BranchRestrictions, couplings: Sequence[Coupling],
    multipliers: Mapping[str, float],
) -> PhysicalLREvaluation:
    """Evaluate the certified physical Lagrangian at ``mu``.

    ``L(mu) = sum_i min_p [ c_i(p) + sum_e price_i(e) ] - sum_k mu_k C_k``

    and, independently,

    ``L(mu) = sum_i physical_i + sum_k mu_k (G_k - C_k)``.

    Both are computed and asserted equal.
    """
    negative = [key for key, value in multipliers.items() if float(value) < -EPS]
    if negative:
        raise ValueError(f"physical multipliers must be non-negative: {negative[:3]}")
    # Couplings name exact (entry, EXIT) events, but this historical price
    # channel keys only entry. A siding wait can change exit while retaining
    # that key. Charging its multiplier to every wait alternative is not the
    # claimed Lagrangian and can exceed OPT even when both identities agree.
    # Fail closed until this optional channel supports exit-specific pricing.
    variable_dwell = (config.wait_step > 0 and config.max_wait > 0 and
                      math.floor(config.max_wait/config.wait_step + EPS) >= 1)
    if variable_dwell:
        for coupling in couplings:
            if float(multipliers.get(coupling.key, 0.0)) <= EPS:
                continue
            if any(arcs[event.arc_id].track_type == "S" for _, event in coupling.terms):
                raise ValueError(
                    "UNSUPPORTED_EVENT_PRICE_DOMAIN: exact siding events with variable dwell "
                    "cannot be certified by entry-only prices; use the unpriced ConflictBB "
                    "bound or implement exit-specific LR pricing")
    prices = aggregate_event_prices(couplings, multipliers, time_step=config.time_step)
    paths = dp.solve(arcs, trains, mow, {}, restrictions, config, event_prices=prices)
    infeasible = next((train.train_id for train in trains
                       if not paths[train.train_id].feasible), None)
    if infeasible is not None:
        return PhysicalLREvaluation(
            math.inf, math.inf, 0.0, paths, {}, {}, {}, (), math.inf, 0.0,
            _certified(couplings), infeasible)
    for row in paths.values():
        # The legacy safe-v1 channel must contribute nothing to this bound.
        if abs(row.lambda_cost) > 1e-12:
            raise AssertionError(
                f"legacy safe-v1 lambda cost leaked into the physical LR for {row.train_id}")
        if abs(row.objective_identity_error) > 1e-7:
            raise AssertionError(
                f"generalized cost identity failed for {row.train_id}: "
                f"{row.objective_identity_error}")

    events = {train_id: events_of(row) for train_id, row in paths.items()}
    # Build each train's canonical event set ONCE, then evaluate every G_k
    # against it.  Mathematically identical to ``item.usage(events)``; it only
    # stops the per-train set being rebuilt once per coupling.
    present = {train_id: frozenset(event.canonical for event in items)
               for train_id, items in events.items()}
    usage = {item.key: item.usage_with(present) for item in couplings}
    violation = {item.key: usage[item.key] - item.rhs for item in couplings}
    minimum_sum = sum(row.generalized_cost for row in paths.values())
    mu_dot = sum(max(0.0, float(multipliers.get(item.key, 0.0))) * item.rhs
                 for item in couplings)
    dual = minimum_sum - mu_dot
    reconstruction = (sum(row.physical_cost for row in paths.values())
                      + sum(max(0.0, float(multipliers.get(item.key, 0.0)))
                            * violation[item.key] for item in couplings))
    error = abs(dual - reconstruction)
    if error > 1e-6:
        raise AssertionError(
            f"physical LR dual decomposition identity failed: "
            f"L(mu)={dual} vs reconstruction={reconstruction}")
    violated = tuple(item for item in couplings if violation[item.key] > 1e-9)
    return PhysicalLREvaluation(
        dual, minimum_sum, mu_dot, paths, events, usage, violation, violated,
        reconstruction, error, _certified(couplings))


# ---------------------------------------------------------------------------
# Fluid Queue support detection.  fluid_queue.py is used unchanged.
# ---------------------------------------------------------------------------

def fluid_queue_support(
    events: Mapping[str, Sequence[Event]], arcs: Mapping[int, Arc],
    mow: Sequence[Mapping[str, float | int]], *, config: DPConfig,
    previous_pi: Mapping[tuple[int, int], float] | None = None,
    alpha: float = 1.0, beta: float = 4.0, gamma: float = 0.15,
    wait_reference_minutes: float = 30.0, queue_wait_cost_per_min: float = 1.0,
    max_price_wait_minutes: float = 180.0,
) -> tuple[dict[tuple[int, int], float], set[tuple[int, int]], dict[str, float]]:
    """Run the original Fluid Queue and return ``(pi, congested regions, stats)``.

    A region is ``(arc_id, 30-minute block)``.  It is reported as congested when
    the queue is positive, the price is positive, or a finite clearance wait is
    positive -- i.e. exactly the diagnostics the frozen Fluid Queue already
    produces.  Nothing here changes ``C_q``, ``mu_service``, ``Q``, the
    clearance wait, ``pi_hat``, or ``gamma``.
    """
    arrivals: dict[tuple[int, int], float] = {}
    for items in events.values():
        for event in items:
            block = max(0, int(math.floor(event.entry / config.bin_minutes + EPS)))
            key = (int(event.arc_id), block)
            arrivals[key] = arrivals.get(key, 0.0) + 1.0
    old = dict(previous_pi or {})
    cells = fluid_queue_cells(arrivals, old, arcs, mow,
                              bin_minutes=config.bin_minutes, horizon=config.horizon)
    new_pi, _target = fluid_lambda_update(
        cells, old, alpha=alpha, beta=beta, gamma=gamma,
        wait_reference_minutes=wait_reference_minutes,
        queue_wait_cost_per_min=queue_wait_cost_per_min,
        max_price_wait_minutes=max_price_wait_minutes, use_price_queue=True)
    congested = set()
    for key, cell in cells.items():
        wait = cell.clearance_wait_min
        if (cell.queue_after_train > EPS or new_pi.get(key, 0.0) > EPS
                or (math.isfinite(wait) and wait > EPS)):
            congested.add(key)
    stats = {
        "pi_max": max(new_pi.values(), default=0.0),
        "pi_nonzero": float(sum(1 for value in new_pi.values() if value > EPS)),
        "congested_regions": float(len(congested)),
        "max_queue": max((cell.queue_after_train for cell in cells.values()), default=0.0),
    }
    return {key: value for key, value in new_pi.items() if value > EPS}, congested, stats


def region_of(coupling: Coupling, bin_minutes: float) -> tuple[int, int]:
    entries = [event.entry for _train, event in coupling.terms]
    anchor = min(entries) if entries else 0.0
    return (int(coupling.arc_id), max(0, int(math.floor(anchor / bin_minutes + EPS))))


def in_support(coupling: Coupling, congested: set[tuple[int, int]], *,
               bin_minutes: float, include_neighbour: bool = True) -> bool:
    """Is this valid coupling inside a Fluid-Queue-congested region?

    The neighbouring block is admitted because a coupling window may straddle a
    block boundary: its earliest entry can sit in block ``b`` while the queue
    that detects it accumulates in ``b+1``.  This only affects WHICH valid
    constraints are activated, never their validity.
    """
    arc, block = region_of(coupling, bin_minutes)
    if (arc, block) in congested:
        return True
    if include_neighbour:
        return (arc, block + 1) in congested or (arc, block - 1) in congested
    return False


# ---------------------------------------------------------------------------
# Node engine.
# ---------------------------------------------------------------------------

@dataclass
class PhysicalLRRun:
    node_id: int | str
    restrictions: BranchRestrictions
    best_dual: float = -math.inf
    best_multipliers: dict[str, float] = field(default_factory=dict)
    best_couplings: list[Coupling] = field(default_factory=list)
    best_paths: dict[str, DPResult] = field(default_factory=dict)
    L_at_mu_zero: float = -math.inf
    couplings: list[Coupling] = field(default_factory=list)
    multipliers: dict[str, float] = field(default_factory=dict)
    final_paths: dict[str, DPResult] = field(default_factory=dict)
    final_events: dict[str, tuple[Event, ...]] = field(default_factory=dict)
    iteration_log: list[dict[str, object]] = field(default_factory=list)
    activation_log: list[dict[str, object]] = field(default_factory=list)
    multiplier_log: list[dict[str, object]] = field(default_factory=list)
    fluid_queue_log: list[dict[str, object]] = field(default_factory=list)
    status: str = "OPEN"
    certified: bool = True
    infeasible_reason: str = ""
    singleton_fathom: dict[str, object] | None = None
    iterations: int = 0
    dp_calls: int = 0
    runtime_sec: float = 0.0


def run_physical_lr(
    dp: NetworkDP, *, node_id: int | str, arcs: Mapping[int, Arc],
    trains: Sequence[Train], mow: Sequence[Mapping[str, float | int]],
    config: DPConfig, restrictions: BranchRestrictions | None = None,
    initial_couplings: Sequence[Coupling] = (),
    initial_multipliers: Mapping[str, float] | None = None,
    activation: str = LAZY_VALIDATOR_ACTIVATION,
    initialization: str = MU_SUPPORT_ONLY,
    event_universe: Mapping[str, Sequence[Event]] | None = None,
    iterations: int = 40, step0: float = 8.0,
    step_rule: str = "diminishing", polyak_theta: float = 1.0,
    validated_ub: float | None = None,
    distances: Mapping[int, Mapping[int, float]] | None = None,
    fluid_queue_every: int = 1,
    candidate_sink: object | None = None,
    stop_after_no_improvement: int | None = None,
) -> PhysicalLRRun:
    """Certified physical LR at one node.

    Roles, kept strictly separate:

    * Fluid Queue -- proposes an active set of ALREADY VALID constraints and
      prioritizes them.  It never manufactures a constraint and never sets a
      magnitude.
    * Projected subgradient -- optimizes ``mu`` magnitude on those valid
      constraints.  Legitimate here precisely because the constraints are
      genuine Lagrangian constraints.

    No monotonicity is assumed; ``best_dual`` is the running maximum.
    """
    started = time.monotonic()
    restrictions = restrictions or BranchRestrictions()
    run = PhysicalLRRun(node_id=node_id, restrictions=restrictions)
    if distances is None:
        distances = pcm.shortest_snapped_times(arcs, 1.0, config.time_step)
    if validated_ub is None and step_rule == "polyak":
        raise ValueError("the Polyak step needs an independently validated UB")

    pool: list[Coupling] = [item for item in initial_couplings]
    keys = {item.key for item in pool}
    mu: dict[str, float] = {key: max(0.0, float(value))
                            for key, value in (initial_multipliers or {}).items()
                            if key in keys}
    pi: dict[tuple[int, int], float] = {}
    congested: set[tuple[int, int]] = set()
    stale = 0

    def activate(events: Mapping[str, Sequence[Event]], iteration: int) -> int:
        nonlocal pool, keys
        source = (event_universe if (activation == FULL_OBSERVED_EVENT_POOL
                                     and event_universe is not None) else events)
        found_a, rejected = pcm.family_a_windows(
            {tid: sorted(set(items)) for tid, items in source.items()}, arcs,
            headway=config.safety_headway, time_step=config.time_step,
            distances=distances)
        found_c, _ = pcm.family_c_pairs(
            {tid: sorted(set(items)) for tid, items in source.items()}, arcs,
            headway=config.safety_headway)
        candidates = pcm._drop_dominated(list(found_a) + list(found_c))
        if activation == FLUID_QUEUE_PRIORITIZED:
            candidates = [item for item in candidates
                          if in_support(item, congested, bin_minutes=config.bin_minutes)]
        added = 0
        for item in candidates:
            if item.key in keys:
                continue
            if item.family not in VERIFIED_FAMILY_REGISTRY:
                continue
            pool.append(item)
            keys.add(item.key)
            mu.setdefault(item.key, 0.0)   # new constraints always start at zero
            added += 1
            run.activation_log.append({
                "node_id": node_id, "iteration": iteration, "key": item.key,
                "family": item.family, "rule": item.rule, "arc_id": item.arc_id,
                "rhs": item.rhs, "terms": len(item.terms),
                "activation_mode": activation, "detail": item.detail,
                "fluid_queue_region": str(region_of(item, config.bin_minutes)),
                "globally_valid": True})
        if rejected:
            run.activation_log.append({
                "node_id": node_id, "iteration": iteration, "key": "",
                "family": "A", "rule": "self-conflict side condition",
                "arc_id": -1, "rhs": "", "terms": len(rejected),
                "activation_mode": activation,
                "detail": "windows not emitted (single train could contribute twice)",
                "fluid_queue_region": "", "globally_valid": True})
        pool = pcm._drop_dominated(pool)
        keys = {item.key for item in pool}
        for key in list(mu):
            if key not in keys:
                del mu[key]
        return added

    for iteration in range(iterations):
        evaluation = evaluate(dp, arcs=arcs, trains=trains, mow=mow, config=config,
                              restrictions=restrictions, couplings=pool, multipliers=mu)
        run.dp_calls += len(trains)
        run.iterations = iteration + 1
        if evaluation.infeasible_train is not None:
            run.status = "INFEASIBLE"
            run.infeasible_reason = f"train {evaluation.infeasible_train} has no legal trajectory"
            break
        run.certified = run.certified and evaluation.certified
        if iteration == 0:
            if any(value > EPS for value in mu.values()):
                run.L_at_mu_zero = evaluate(
                    dp, arcs=arcs, trains=trains, mow=mow, config=config,
                    restrictions=restrictions, couplings=pool, multipliers={}).dual_value
                run.dp_calls += len(trains)
            else:
                # mu is already zero, so this evaluation IS L(0); no extra DP call.
                run.L_at_mu_zero = evaluation.dual_value
        improved = evaluation.dual_value > run.best_dual
        if improved:
            run.best_dual = evaluation.dual_value
            run.best_multipliers = dict(mu)
            # Preserve the exact active set at the best dual point.  The lazy
            # pool can subsequently add or drop dominated constraints, so the
            # final pool is not necessarily sufficient to replay best_mu.
            run.best_couplings = list(pool)
            run.best_paths = dict(evaluation.paths)
            stale = 0
        else:
            stale += 1
        if candidate_sink is not None:
            # Primal-side observer only: it cannot touch mu, the pool or L(mu).
            candidate_sink(iteration, dict(evaluation.events), evaluation.dual_value,
                           sum(row.physical_cost for row in evaluation.paths.values()),
                           improved)
        run.final_paths = dict(evaluation.paths)
        run.final_events = dict(evaluation.events)

        # Fluid Queue: support detection only.
        if activation == FLUID_QUEUE_PRIORITIZED and iteration % max(1, fluid_queue_every) == 0:
            pi, congested, stats = fluid_queue_support(
                evaluation.events, arcs, mow, config=config, previous_pi=pi)
            run.fluid_queue_log.append({
                "node_id": node_id, "iteration": iteration, **stats,
                "role": "support/prioritization only; pi never enters L(mu)"})

        added = activate(evaluation.events, iteration)
        run.iteration_log.append({
            "node_id": node_id, "iteration": iteration,
            "L_mu": evaluation.dual_value, "best_dual": run.best_dual,
            "minimum_sum": evaluation.minimum_sum,
            "mu_dot_capacity": evaluation.mu_dot_capacity,
            "reconstruction_error": evaluation.reconstruction_error,
            "active_couplings": len(pool), "activated_this_iteration": added,
            "violated_couplings": len(evaluation.violated),
            "subgradient_norm": evaluation.subgradient_norm,
            "nonzero_multipliers": sum(1 for value in mu.values() if value > EPS),
            "max_multiplier": max(mu.values(), default=0.0),
            "step_rule": step_rule, "activation_mode": activation,
            "initialization": initialization,
            "physical_lr_certified": run.certified,
            "runtime_sec": time.monotonic() - started})

        # Projected subgradient on mu magnitude.  Recompute the violation after
        # activation so newly added constraints get a gradient immediately.
        events = evaluation.events
        present_now = {train_id: frozenset(event.canonical for event in items)
                       for train_id, items in events.items()}
        gradient = {item.key: item.usage_with(present_now) - item.rhs
                    for item in pool}
        norm_squared = sum(value * value for value in gradient.values())
        if norm_squared <= EPS:
            run.status = "NO_VIOLATION"
            break
        if (stop_after_no_improvement is not None
                and stale >= stop_after_no_improvement):
            run.status = "EARLY_STOP_NO_IMPROVEMENT"
            break
        if step_rule == "polyak":
            assert validated_ub is not None
            step = max(0.0, polyak_theta * (validated_ub - evaluation.dual_value) / norm_squared)
        else:
            step = step0 / (math.sqrt(iteration + 1.0) * math.sqrt(norm_squared))
        for key, slope in gradient.items():
            mu[key] = max(0.0, mu.get(key, 0.0) + step * slope)
        run.multiplier_log.append({
            "node_id": node_id, "iteration": iteration, "step": step,
            "step_rule": step_rule, "gradient_norm": math.sqrt(norm_squared),
            "multipliers": ";".join(f"{key}={value:.6g}"
                                    for key, value in sorted(mu.items()) if value > EPS)})
    run.couplings = pool
    run.multipliers = mu
    if run.status == "OPEN":
        run.status = "COMPLETED"
    run.runtime_sec = time.monotonic() - started
    return run


def singleton_infeasibility_certificate(
    dp: NetworkDP, *, arcs: Mapping[int, Arc], trains: Sequence[Train],
    mow: Sequence[Mapping[str, float | int]], config: DPConfig,
    restrictions: BranchRestrictions, couplings: Sequence[Coupling],
    domain_sizes: Mapping[str, int] | None = None,
) -> dict[str, object] | None:
    """Optional exact fathoming shortcut, diagnostic only.

    If every train's domain at this node is a single trajectory and that unique
    joint selection violates a verified coupling, the node's feasible set is
    empty.  This is a *shortcut*: the relaxation already certifies the same
    thing through an unbounded dual, and that remains the primary proof.
    """
    if not domain_sizes or any(size != 1 for size in domain_sizes.values()):
        return None
    evaluation = evaluate(dp, arcs=arcs, trains=trains, mow=mow, config=config,
                          restrictions=restrictions, couplings=couplings,
                          multipliers={})
    if evaluation.infeasible_train is not None:
        return {"node_infeasible": True, "reason": "a train subproblem is infeasible"}
    if not evaluation.violated:
        return None
    witness = evaluation.violated[0]
    return {
        "node_infeasible": True,
        "reason": "every train domain is a singleton and the unique joint "
                  "selection violates a verified physical coupling",
        "violated_key": witness.key, "family": witness.family, "rule": witness.rule,
        "usage_G_k": evaluation.usage[witness.key], "rhs_C_k": witness.rhs,
        "dual_slope": evaluation.violation[witness.key],
        "L_at_mu_zero": evaluation.dual_value,
        "dual_is_unbounded": True,
        "note": "diagnostic shortcut; the unbounded dual is the primary proof",
    }
