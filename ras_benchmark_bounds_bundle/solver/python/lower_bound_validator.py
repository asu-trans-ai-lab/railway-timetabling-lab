"""Independent validator for a physical-LR LOWER-BOUND certificate.

This is the LB side of the certification boundary.  It answers exactly one
question:

    "does this node certificate mathematically reconstruct a valid Lagrangian
     lower bound for this exact node domain?"

It is NOT a timetable validator and it never decides feasibility.

Non-circularity
---------------
This module must never call ``physical_lagrangian.run_physical_lr`` to check a
value that ``run_physical_lr`` produced.  It consumes a stored certificate and
rebuilds the bound from primitives:

  * the frozen coupling model (``physical_constraint_model``) to re-derive that
    every coupling in the certificate is a valid inequality,
  * the production ``NetworkDP`` for a FIXED-mu replay (a single DP solve at
    given prices -- not a dual ascent),
  * plain arithmetic for ``L(mu)``.

Trust boundary
--------------
What this validator establishes is listed in
``verification/validator_audit/lb_validator_scope.md``.  It does NOT prove the
frozen coupling definitions are themselves valid physics; those remain part of
the trusted mathematical specification.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from . import physical_constraint_model as pcm
from .physical_constraint_model import Coupling, Event
from .dp_interface import (
    Arc, BranchRestrictions, DPConfig, NetworkDP, Train,
)


def _entry_tick(entry: float, time_step: float) -> int:
    """The DP's entry tick, re-derived here rather than imported.

    ``physical_lagrangian.entry_tick`` is the function under test on this side
    of the boundary, so the validator restates the rule instead of reusing it;
    a regression test pins the two against each other.
    """
    return int(round(entry / time_step))

LB_TOL = 1e-6
VERIFIED_FAMILIES = frozenset({"A", "C"})


@dataclass
class LBCertificate:
    """Everything needed to replay a node's certified lower bound."""

    node_id: object
    parent_id: object
    bound_source: str
    restrictions: BranchRestrictions
    couplings: tuple[Coupling, ...]
    multipliers: dict[str, float]
    event_prices: dict[tuple[str, int, bool, int], float]
    per_train_physical: dict[str, float]
    per_train_dual: dict[str, float]
    per_train_generalized: dict[str, float]
    per_train_legacy_lambda: dict[str, float]
    usage: dict[str, float]
    minimum_sum: float
    mu_dot_capacity: float
    dual_value: float
    reconstruction: float
    inherited_lb: float
    node_lb: float
    activation_mode: str
    fluid_queue_enabled: bool


@dataclass
class LBValidationReport:
    status: str = "PASS"
    checks: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    reconstructed_L1: float | None = None
    reconstructed_L2: float | None = None
    replayed_minimum_sum: float | None = None

    def fail(self, check: str, message: str) -> None:
        self.status = "FAIL"
        self.checks[check] = "FAIL"
        self.errors.append(message)

    def ok(self, check: str) -> None:
        self.checks.setdefault(check, "PASS")


def _finite(*values) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(float(v))
               for v in values)


def validate_lower_bound_certificate(
    certificate: LBCertificate,
    *,
    dp: NetworkDP,
    arcs: Mapping[int, Arc],
    trains: Sequence[Train],
    mow: Sequence[Mapping[str, float | int]],
    config: DPConfig,
    exact_node_opt: float | None = None,
    same_scope_validated_ub: float | None = None,
    parent_restrictions: BranchRestrictions | None = None,
    parent_certified_lb: float | None = None,
) -> LBValidationReport:
    """Rebuild the certificate from primitives and report every disagreement."""
    report = LBValidationReport()
    cert = certificate

    # Independent domain check: replaying the same entry-only pricing map
    # cannot certify exact events when multiple siding exits share that key.
    variable_dwell = (config.wait_step > 0 and config.max_wait > 0 and
                      math.floor(config.max_wait/config.wait_step + 1e-9) >= 1)
    if variable_dwell:
        for coupling in cert.couplings:
            if float(cert.multipliers.get(coupling.key, 0.0)) <= 1e-9:
                continue
            for _, event in coupling.terms:
                arc = arcs.get(event.arc_id)
                if arc is not None and arc.track_type == "S":
                    report.fail("event_price_domain",
                                "UNSUPPORTED_EVENT_PRICE_DOMAIN: entry-only pricing aliases "
                                "distinct siding exit/dwell events")
    report.ok("event_price_domain")

    # ---- check 1: only verified coupling families, structurally re-derived
    seen_keys: set[str] = set()
    for coupling in cert.couplings:
        if coupling.family not in VERIFIED_FAMILIES:
            report.fail("verified_families",
                        f"coupling {coupling.key}: family {coupling.family!r} is "
                        f"not in the certified registry {sorted(VERIFIED_FAMILIES)}")
            continue
        if coupling.key in seen_keys:
            report.fail("verified_families",
                        f"duplicate coupling key {coupling.key}")
        seen_keys.add(coupling.key)
        if abs(float(coupling.rhs) - 1.0) > LB_TOL:
            report.fail("verified_families",
                        f"coupling {coupling.key}: rhs {coupling.rhs} != 1; every "
                        f"verified Family A/C inequality has C_k = 1")
        if not coupling.terms:
            report.fail("verified_families",
                        f"coupling {coupling.key}: no terms")
        for owner, event in coupling.terms:
            if not isinstance(event, Event):
                report.fail("verified_families",
                            f"coupling {coupling.key}: malformed event {event!r}")
                continue
            if event.arc_id != coupling.arc_id:
                report.fail("verified_families",
                            f"coupling {coupling.key}: term arc {event.arc_id} != "
                            f"coupling arc {coupling.arc_id}")
            if event.arc_id not in arcs:
                report.fail("verified_families",
                            f"coupling {coupling.key}: unknown arc {event.arc_id}")
            if not _finite(event.entry, event.exit) or event.exit <= event.entry:
                report.fail("verified_families",
                            f"coupling {coupling.key}: malformed interval "
                            f"[{event.entry}, {event.exit})")
            if str(owner) not in {t.train_id for t in trains}:
                report.fail("verified_families",
                            f"coupling {coupling.key}: term owner {owner!r} is not "
                            f"a train in this instance")
        # Family A must be an entry-headway window: every term is an entry on
        # the SAME (arc, direction) inside a half-open window of width h, and
        # at least two distinct trains contribute.  Re-derived from the frozen
        # definition rather than trusted from the stored rule string.
        if coupling.family == "A":
            directions = {bool(e.ab) for _o, e in coupling.terms}
            arc_ids = {int(e.arc_id) for _o, e in coupling.terms}
            owners = {str(o) for o, _e in coupling.terms}
            if len(directions) != 1 or len(arc_ids) != 1:
                report.fail("verified_families",
                            f"coupling {coupling.key}: Family A mixes "
                            f"arcs {sorted(arc_ids)} / directions {sorted(directions)}")
            if len(owners) < 2:
                report.fail("verified_families",
                            f"coupling {coupling.key}: Family A couples fewer than "
                            f"two distinct trains ({sorted(owners)})")
            entries = [float(e.entry) for _o, e in coupling.terms]
            if entries:
                span = max(entries) - min(entries)
                if span >= config.safety_headway - 1e-9:
                    report.fail("verified_families",
                                f"coupling {coupling.key}: Family A entry span "
                                f"{span} is not inside a half-open window of "
                                f"width h={config.safety_headway}")
            arc = arcs.get(coupling.arc_id)
            if arc is not None and not pcm.is_main_track(arc.track_type):
                report.fail("verified_families",
                            f"coupling {coupling.key}: Family A is defined on main "
                            f"track only, but arc {coupling.arc_id} has track_type "
                            f"{arc.track_type!r}")

        # Family C must be an actual validator-incompatible pair, re-derived
        # from the frozen model rather than trusted from the stored rule string.
        if coupling.family == "C":
            if len(coupling.terms) != 2:
                report.fail("verified_families",
                            f"coupling {coupling.key}: Family C must have exactly "
                            f"two terms, has {len(coupling.terms)}")
            else:
                (owner_l, left), (owner_r, right) = coupling.terms
                if owner_l == owner_r:
                    report.fail("verified_families",
                                f"coupling {coupling.key}: Family C couples a "
                                f"train with itself ({owner_l})")
                arc = arcs.get(coupling.arc_id)
                if arc is not None:
                    conflict, rule = pcm.pair_conflict(
                        left, right, arc.track_type, config.safety_headway)
                    if not conflict:
                        report.fail("verified_families",
                                    f"coupling {coupling.key}: the frozen model "
                                    f"says this event pair does NOT conflict, so "
                                    f"the inequality is not valid")
                    elif rule != coupling.rule:
                        report.fail("verified_families",
                                    f"coupling {coupling.key}: stored rule "
                                    f"{coupling.rule!r} != re-derived {rule!r}")
    report.ok("verified_families")

    # ---- check 2: multipliers ------------------------------------------
    keys = {c.key for c in cert.couplings}
    for key, value in cert.multipliers.items():
        if not _finite(value):
            report.fail("multipliers", f"mu[{key}] = {value} is not finite")
            continue
        if value < -LB_TOL:
            report.fail("multipliers", f"mu[{key}] = {value} is negative")
        if value > LB_TOL and key not in keys:
            report.fail("multipliers",
                        f"mu[{key}] = {value} has no active verified coupling")
    report.ok("multipliers")

    # ---- check 3: no Fluid Queue / legacy lambda in L(mu) ----------------
    if cert.fluid_queue_enabled:
        report.fail("no_price_leakage",
                    "certificate declares Fluid Queue ENABLED; the verification "
                    "configuration requires it off")
    for train_id, legacy in cert.per_train_legacy_lambda.items():
        if not _finite(legacy) or abs(legacy) > LB_TOL:
            report.fail("no_price_leakage",
                        f"{train_id}: legacy lambda cost {legacy} leaked into the "
                        f"generalized cost; only mu on verified couplings may price "
                        f"the certified LR")
    if cert.bound_source != "physical_cross_train_lr_v1":
        report.fail("no_price_leakage",
                    f"bound_source {cert.bound_source!r} is not the certified "
                    f"physical cross-train LR")
    report.ok("no_price_leakage")

    # ---- check 4: event prices reconstructed from couplings and mu -------
    rebuilt: dict[tuple[str, int, bool, int], float] = {}
    for coupling in cert.couplings:
        value = float(cert.multipliers.get(coupling.key, 0.0))
        if value <= 1e-9:
            continue
        for owner, event in coupling.terms:
            key = (str(owner), int(event.arc_id), bool(event.ab),
                   _entry_tick(event.entry, config.time_step))
            rebuilt[key] = rebuilt.get(key, 0.0) + value
    stored_prices = {k: v for k, v in cert.event_prices.items() if abs(v) > 1e-12}
    rebuilt_nonzero = {k: v for k, v in rebuilt.items() if abs(v) > 1e-12}
    if set(stored_prices) != set(rebuilt_nonzero):
        only_stored = sorted(set(stored_prices) - set(rebuilt_nonzero))[:3]
        only_rebuilt = sorted(set(rebuilt_nonzero) - set(stored_prices))[:3]
        report.fail("event_prices",
                    f"event-price support differs: only-in-certificate="
                    f"{only_stored}, only-in-reconstruction={only_rebuilt}")
    else:
        for key, value in rebuilt_nonzero.items():
            if abs(value - stored_prices[key]) > LB_TOL:
                report.fail("event_prices",
                            f"price{key} reconstructed {value} != certificate "
                            f"{stored_prices[key]}")
    report.ok("event_prices")

    # ---- check 5: fixed-mu DP replay (NOT a dual ascent) -----------------
    replay_sum = 0.0
    try:
        replayed = dp.solve(dict(arcs), list(trains), list(mow), {},
                            cert.restrictions, config, event_prices=rebuilt)
    except Exception as exc:                                   # noqa: BLE001
        report.fail("fixed_mu_replay", f"replay failed: {exc}")
        replayed = {}
    for train in trains:
        row = replayed.get(train.train_id)
        certified = cert.per_train_generalized.get(train.train_id)
        if row is None or not row.feasible:
            report.fail("fixed_mu_replay",
                        f"{train.train_id}: replay found no legal trajectory")
            continue
        replay_sum += row.generalized_cost
        if certified is None:
            report.fail("fixed_mu_replay",
                        f"{train.train_id}: certificate has no generalized cost")
            continue
        if row.generalized_cost < certified - LB_TOL:
            report.fail("fixed_mu_replay",
                        f"{train.train_id}: replay minimum {row.generalized_cost} is "
                        f"STRICTLY BELOW the certified {certified}; the stored "
                        f"L(mu) is too high")
        elif abs(row.generalized_cost - certified) > LB_TOL:
            report.fail("fixed_mu_replay",
                        f"{train.train_id}: replay minimum {row.generalized_cost} != "
                        f"certified {certified}")
        if abs(row.lambda_cost) > LB_TOL:
            report.fail("no_price_leakage",
                        f"{train.train_id}: replay charged legacy lambda "
                        f"{row.lambda_cost}")
    report.replayed_minimum_sum = replay_sum
    report.ok("fixed_mu_replay")

    # ---- check 6: L(mu) two independent ways -----------------------------
    mu_dot = sum(max(0.0, float(cert.multipliers.get(c.key, 0.0))) * c.rhs
                 for c in cert.couplings)
    l1 = sum(cert.per_train_generalized.values()) - mu_dot
    l2 = (sum(cert.per_train_physical.values())
          + sum(max(0.0, float(cert.multipliers.get(c.key, 0.0)))
                * (cert.usage.get(c.key, 0.0) - c.rhs) for c in cert.couplings))
    report.reconstructed_L1 = l1
    report.reconstructed_L2 = l2
    if abs(l1 - l2) > LB_TOL:
        report.fail("dual_identity",
                    f"L1 (min-sum form) {l1} != L2 (usage form) {l2}")
    if abs(l1 - cert.dual_value) > LB_TOL:
        report.fail("dual_identity",
                    f"reconstructed L(mu) {l1} != certificate dual_value "
                    f"{cert.dual_value}")
    if abs(mu_dot - cert.mu_dot_capacity) > LB_TOL:
        report.fail("dual_identity",
                    f"mu.C reconstructed {mu_dot} != certificate "
                    f"{cert.mu_dot_capacity}")
    if abs(sum(cert.per_train_generalized.values()) - cert.minimum_sum) > LB_TOL:
        report.fail("dual_identity",
                    f"minimum_sum reconstructed "
                    f"{sum(cert.per_train_generalized.values())} != certificate "
                    f"{cert.minimum_sum}")
    for train_id, generalized in cert.per_train_generalized.items():
        physical = cert.per_train_physical.get(train_id, float("nan"))
        dual = cert.per_train_dual.get(train_id, float("nan"))
        if not _finite(generalized, physical, dual) or abs(
                generalized - physical - dual) > LB_TOL:
            report.fail("dual_identity",
                        f"{train_id}: generalized {generalized} != physical "
                        f"{physical} + event-price {dual}")
    report.ok("dual_identity")

    # ---- check 7: node domain / branch restrictions ----------------------
    if parent_restrictions is not None:
        pr, pp = parent_restrictions.required(), parent_restrictions.prohibited()
        cr, cp = cert.restrictions.required(), cert.restrictions.prohibited()
        added = sum(len(set(cr.get(t, set())) - set(pr.get(t, set())))
                    for t in set(cr) | set(pr))
        added += sum(len(set(cp.get(t, set())) - set(pp.get(t, set())))
                     for t in set(cp) | set(pp))
        removed = sum(len(set(pr.get(t, set())) - set(cr.get(t, set())))
                      for t in set(cr) | set(pr))
        removed += sum(len(set(pp.get(t, set())) - set(cp.get(t, set())))
                       for t in set(cp) | set(pp))
        if removed:
            report.fail("node_domain",
                        f"child drops {removed} parent restriction(s); a child "
                        f"domain must be a subset of its parent")
        if added != 1:
            report.fail("node_domain",
                        f"child adds {added} restrictions; exactly one branch "
                        f"side is expected")
    for train_id, resources in cert.restrictions.required().items():
        overlap = resources & cert.restrictions.prohibited().get(train_id, set())
        if overlap:
            report.fail("node_domain",
                        f"{train_id}: {sorted(overlap)} both REQUIRED and PROHIBITED")
    report.ok("node_domain")

    # ---- check 8: node_lb = max(inherited, reconstructed) ----------------
    expected_lb = max(cert.inherited_lb, l1) if _finite(cert.inherited_lb) else l1
    if not _finite(cert.node_lb) or abs(cert.node_lb - expected_lb) > LB_TOL:
        report.fail("node_lb_formula",
                    f"node_lb {cert.node_lb} != max(inherited {cert.inherited_lb}, "
                    f"reconstructed {l1}) = {expected_lb}")
    if parent_certified_lb is not None and _finite(cert.inherited_lb):
        if cert.inherited_lb > parent_certified_lb + LB_TOL:
            report.fail("node_lb_formula",
                        f"inherited_lb {cert.inherited_lb} exceeds the parent's "
                        f"certified bound {parent_certified_lb}")
    report.ok("node_lb_formula")

    # ---- check 9: consistency with an exact oracle and a same-scope UB ----
    if exact_node_opt is not None:
        if cert.node_lb > exact_node_opt + LB_TOL:
            report.fail("oracle_consistency",
                        f"certified LB {cert.node_lb} EXCEEDS the exhaustive "
                        f"optimum {exact_node_opt}")
    report.ok("oracle_consistency")
    if same_scope_validated_ub is not None:
        if cert.node_lb > same_scope_validated_ub + LB_TOL:
            report.fail("bound_consistency",
                        f"BOUND CONSISTENCY FAILURE: certified LB {cert.node_lb} > "
                        f"validated UB {same_scope_validated_ub}")
    report.ok("bound_consistency")
    return report


# ---------------------------------------------------------------------------
# Certificate extraction (section 15).
#
# Building a certificate is not the same as validating one: this reads what a
# run already produced.  The validation entry point above never calls it.
# ---------------------------------------------------------------------------

def build_certificate_from_node(
    node, *, config: DPConfig, bound_source: str | None = None,
    fluid_queue_enabled: bool = False,
) -> LBCertificate:
    """Extract a replayable certificate from a solved ``BBNode``."""
    couplings = tuple(node.physical_couplings)
    multipliers = {k: float(v) for k, v in node.physical_multipliers.items()}
    paths = dict(node.best_paths)
    prices: dict[tuple[str, int, bool, int], float] = {}
    for coupling in couplings:
        value = float(multipliers.get(coupling.key, 0.0))
        if value <= 1e-9:
            continue
        for owner, event in coupling.terms:
            key = (str(owner), int(event.arc_id), bool(event.ab),
                   _entry_tick(event.entry, config.time_step))
            prices[key] = prices.get(key, 0.0) + value

    events = {tid: tuple(pcm.legs_to_events(r.legs, r.ab_flags))
              for tid, r in paths.items()}
    present = {tid: frozenset(e.canonical for e in items)
               for tid, items in events.items()}
    usage = {c.key: c.usage_with(present) for c in couplings}
    mu_dot = sum(max(0.0, multipliers.get(c.key, 0.0)) * c.rhs for c in couplings)
    minimum_sum = sum(r.generalized_cost for r in paths.values())
    dual = minimum_sum - mu_dot
    reconstruction = (sum(r.physical_cost for r in paths.values())
                      + sum(max(0.0, multipliers.get(c.key, 0.0))
                            * (usage[c.key] - c.rhs) for c in couplings))
    return LBCertificate(
        node_id=node.node_id, parent_id=node.parent_id,
        bound_source=bound_source or getattr(node, "bound_source", ""),
        restrictions=node.restrictions,
        couplings=couplings, multipliers=multipliers, event_prices=prices,
        per_train_physical={t: r.physical_cost for t, r in paths.items()},
        per_train_dual={t: r.physical_dual_cost for t, r in paths.items()},
        per_train_generalized={t: r.generalized_cost for t, r in paths.items()},
        per_train_legacy_lambda={t: r.lambda_cost for t, r in paths.items()},
        usage=usage, minimum_sum=minimum_sum, mu_dot_capacity=mu_dot,
        dual_value=dual, reconstruction=reconstruction,
        inherited_lb=node.inherited_lb, node_lb=node.lower_bound,
        activation_mode=str(getattr(node, "physical_activation", "")),
        fluid_queue_enabled=fluid_queue_enabled)


def certificate_to_dict(cert: LBCertificate) -> dict:
    """JSON-serialisable form, for saving alongside a verification case."""
    return {
        "node_id": cert.node_id, "parent_id": cert.parent_id,
        "bound_source": cert.bound_source,
        "restrictions": {
            "required": {k: sorted(v) for k, v in cert.restrictions.required().items()},
            "prohibited": {k: sorted(v) for k, v in cert.restrictions.prohibited().items()}},
        "couplings": [{
            "key": c.key, "family": c.family, "rule": c.rule, "arc_id": c.arc_id,
            "rhs": c.rhs,
            "terms": [{"train": o, "arc_id": e.arc_id, "ab": e.ab,
                       "entry": e.entry, "exit": e.exit} for o, e in c.terms],
        } for c in cert.couplings],
        "multipliers": cert.multipliers,
        "event_prices": {f"{k[0]}|{k[1]}|{int(k[2])}|{k[3]}": v
                         for k, v in cert.event_prices.items()},
        "per_train_physical": cert.per_train_physical,
        "per_train_event_price": cert.per_train_dual,
        "per_train_generalized": cert.per_train_generalized,
        "per_train_legacy_lambda": cert.per_train_legacy_lambda,
        "usage_G_k": cert.usage, "minimum_sum": cert.minimum_sum,
        "mu_dot_capacity": cert.mu_dot_capacity, "dual_value": cert.dual_value,
        "reconstruction": cert.reconstruction, "inherited_lb": cert.inherited_lb,
        "node_lb": cert.node_lb, "activation_mode": cert.activation_mode,
        "fluid_queue_enabled": cert.fluid_queue_enabled,
    }
