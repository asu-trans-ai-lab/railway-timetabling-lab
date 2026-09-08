"""Resource B&B over exact conflicting movement exclusions.

The C++ DP optimizes each train in its fixed configured domain under inherited cuts.
No finite penalty, positive use branch, route-list restriction, repair, or LR
iteration is introduced. Node bound = sum of exact unpriced DP minima (mu=0).
See RESOURCE_BB_CORRECTED_DESIGN.md at the repository root for the proof scope.
"""
from __future__ import annotations

import heapq
import math
import time
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from . import conflict_branch as cbranch
from . import history_branch as hbranch
from .dp_interface import Arc, BranchRestrictions, DPConfig, DPResult, NetworkDP, Train
from validator.validate_schedule import certified_upper_bound
from .train_domain import FULL_NETWORK, FULL_SCOPE
from .headway_model import HEADWAY_MODEL_LEGACY_ENTRY, physics_metadata

TOL = 1e-7
OPEN = "OPEN"
EXPANDED = "EXPANDED"
FEASIBLE = "FEASIBLE"
PRUNED_BOUND = "PRUNED_BOUND"
PRUNED_INFEASIBLE = "PRUNED_INFEASIBLE"
UNSUPPORTED_CONFLICT = "UNSUPPORTED_CONFLICT"
LIVE = frozenset({OPEN, UNSUPPORTED_CONFLICT})


@dataclass
class Node:
    node_id: int
    parent_id: int | None
    depth: int
    restrictions: BranchRestrictions
    branch_decision: str = "(root)"
    node_lb: float = -math.inf
    status: str = OPEN
    paths: dict[str, DPResult] = field(default_factory=dict)
    infeasible_trains: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    selected_conflict: cbranch.ConflictBranch | None = None
    validator_pass: bool | None = None
    validator_conflicts: int | None = None
    validator_report: dict = field(default_factory=dict)
    children: list[int] = field(default_factory=list)
    fathom_reason: str | None = None
    new_branch_restriction: dict | None = None
    branch_event: cbranch.ConflictBranch | None = None
    global_lb: float = -math.inf
    incumbent_ub: float | None = None
    absolute_gap: float | None = None
    relative_gap: float | None = None
    generated_iteration: int | None = None
    generated_seconds: float | None = None
    headway_model: str = HEADWAY_MODEL_LEGACY_ENTRY
    safety_headway: float = 3.0
    physics_model_version: str = 'legacy_entry_v1'
    physics_fingerprint: str | None = None


@dataclass
class Summary:
    nodes: list[Node]
    status: str
    global_lb: float
    incumbent_ub: float | None
    best_schedule: dict[str, DPResult]
    trace: list[dict]
    unsupported: list[dict]
    root_lb: float
    best_validator_report: dict = field(default_factory=dict)
    proof_scope: str = FULL_SCOPE
    domain_mode: str = FULL_NETWORK
    domain_fingerprint: str | None = None
    incumbent_provenance: dict = field(default_factory=dict)
    headway_model: str = HEADWAY_MODEL_LEGACY_ENTRY
    safety_headway: float = 3.0
    physics_model_version: str = 'legacy_entry_v1'
    physics_fingerprint: str | None = None


class DPIntegrityError(RuntimeError):
    """Malformed/uncertified subproblem output is NOT an infeasibility proof."""


class ConflictBB:
    def __init__(self, dp: NetworkDP, *, arcs: Mapping[int, Arc], trains: Sequence[Train],
                 mow: Sequence[Mapping[str, float | int]], config: DPConfig,
                 max_nodes: int = 200, max_depth: int = 12, history_policy: str = "trajectory",
                 initial_incumbent=None):
        if history_policy not in {"trajectory", "unresolved"}:
            raise ValueError("history_policy must be trajectory or unresolved (historical replay)")
        self.history_policy = history_policy
        self.initial_incumbent = initial_incumbent
        self.dp, self.arcs, self.trains = dp, dict(arcs), list(trains)
        self.mow, self.config = list(mow), config
        # Domain selection belongs to the DP backend, not to B&B nodes/branching.
        self.scope = dict(proof_scope=getattr(dp,'proof_scope',FULL_SCOPE),
                          domain_mode=getattr(dp,'domain_mode',FULL_NETWORK),
                          domain_fingerprint=getattr(dp,'domain_fingerprint',None),
                          **physics_metadata(config.headway_model, config.safety_headway))
        self.max_nodes, self.max_depth = max_nodes, max_depth
        if max_nodes < 1 or max_depth < 0:
            raise ValueError("node budget must be >=1 and depth budget >=0")
        if not self.trains or len({t.train_id for t in self.trains}) != len(self.trains):
            raise ValueError("nonempty, uniquely identified trains required")
        for name in ("time_step", "wait_step", "departure_step", "bin_minutes"):
            if not math.isfinite(getattr(config, name)) or getattr(config, name) <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if config.time_step < 1e-6:
            raise ValueError("time grid must exceed the validator/event matching tolerance")
        for name in ("horizon", "max_wait", "departure_slack", "safety_headway",
                     "origin_wait_cost", "running_cost", "siding_wait_cost", "early_cost", "late_cost"):
            if not math.isfinite(getattr(config, name)) or getattr(config, name) < 0:
                raise ValueError(f"{name} must be finite and nonnegative for the bound proof")

    def _validation_args(self, restrictions):
        c = self.config
        return dict(arcs=self.arcs, mow=self.mow, restrictions=restrictions,
                    horizon=c.horizon, safety_headway=c.safety_headway,
                    headway_model=c.headway_model,
                    time_step=c.time_step, wait_step=c.wait_step,
                    origin_wait_cost=c.origin_wait_cost, running_cost=c.running_cost,
                    siding_wait_cost=c.siding_wait_cost, early_cost=c.early_cost, late_cost=c.late_cost)

    def _solve(self, node: Node) -> None:
        for key,value in physics_metadata(self.config.headway_model, self.config.safety_headway).items():
            setattr(node,key,value)
        rows = self.dp.solve(self.arcs, self.trains, self.mow, {}, node.restrictions,
                             self.config, event_prices={})
        if set(rows) != {t.train_id for t in self.trains}:
            raise DPIntegrityError("DP result train set differs from requests")
        node.paths = dict(rows)
        for train in self.trains:
            row = rows[train.train_id]
            if not isinstance(row, DPResult):
                raise DPIntegrityError(f"missing/malformed DPResult for {train.train_id}")
            if row.train_id != train.train_id:
                raise DPIntegrityError("DP row identity differs from request")
            if not row.feasible:
                proven_reasons = {"contradictory required/prohibited resource",
                                  "entry is beyond horizon",
                                  "destination is unreachable in the physical network",
                                  "no legal physical-network trajectory satisfies restrictions",
                                  "no legal trajectory in configured train domain"}
                if row.reason not in proven_reasons:
                    raise DPIntegrityError(f"DP did not prove infeasibility: {row.reason}")
                if row.legs or math.isfinite(row.physical_cost) or math.isfinite(row.generalized_cost):
                    raise DPIntegrityError("infeasible DP result contains a finite/partial path")
                node.infeasible_trains.append(train.train_id)
                continue
            # A feasible flag is not enough: independently check complete topology,
            # raw timing/objective, MOW and every inherited restriction BEFORE LB use.
            value, report = certified_upper_bound({train.train_id: row}, trains=[train],
                                                 **self._validation_args(node.restrictions))
            if value is None or not math.isfinite(value):
                raise DPIntegrityError(f"invalid single-train witness {train.train_id}: {report['errors']}")
            if (not math.isfinite(row.generalized_cost) or abs(row.generalized_cost-value) > TOL
                    or abs(row.lambda_cost) > TOL or abs(row.physical_dual_cost) > TOL):
                raise DPIntegrityError("node minimum must be an unpriced physical objective")
        if node.infeasible_trains:
            node.node_lb, node.status = math.inf, PRUNED_INFEASIBLE
            node.fathom_reason = "no complete DP trajectory: " + ", ".join(node.infeasible_trains)
            return
        node.node_lb = sum(rows[t.train_id].physical_cost for t in self.trains)
        value, report = certified_upper_bound(rows, trains=self.trains,
                                             **self._validation_args(node.restrictions))
        if report['physics_fingerprint'] != self.scope['physics_fingerprint']:
            raise DPIntegrityError('validator/config physical model mismatch')
        node.validator_report = report
        node.validator_pass = value is not None
        node.conflicts = cbranch.trusted_records(report.get("conflicts") or [])
        node.validator_conflicts = len(node.conflicts)
        if report.get("errors"):
            raise DPIntegrityError(f"joint validation has structural errors: {report['errors']}")
        if node.validator_pass and node.conflicts:
            raise DPIntegrityError("trusted validator PASS disagrees with its conflict list")

    @staticmethod
    def _global_lb(nodes, incumbent):
        # Unsupported and budget-limited leaves retain their feasible domains.
        frontier = min((n.node_lb for n in nodes if n.status in LIVE), default=math.inf)
        return min(frontier, incumbent if incumbent is not None else math.inf)

    def solve(self) -> Summary:
        started = time.perf_counter()
        nodes, trace, unsupported = [], [], []
        incumbent, best, best_report = None, {}, {}
        best_provenance = {}
        if self.initial_incumbent is not None:
            from .stable_solver.incumbent import validate
            incumbent, best_report = validate(self.initial_incumbent,arcs=self.arcs,trains=self.trains,
                mow=self.mow,config=self.config,domains=getattr(self.dp,'domains',{}))
            best = dict(self.initial_incumbent.schedule)
        pending_parent_id = None
        root = Node(0, None, 0, BranchRestrictions())
        self._solve(root)
        if incumbent is not None and root.node_lb > incumbent + TOL:
            raise DPIntegrityError('root lower bound exceeds validated external incumbent')
        nodes.append(root)
        heap = []
        budget_stops = set()

        def record(node, action, **extra):
            bound = self._global_lb(nodes, incumbent)
            node.global_lb, node.incumbent_ub = bound, incumbent
            node.absolute_gap = (None if incumbent is None or not math.isfinite(bound)
                                 else max(0.0, incumbent-bound))
            node.relative_gap = (None if node.absolute_gap is None else
                                 node.absolute_gap/max(abs(incumbent), 1e-12))
            row = self._row(len(trace), node, action, **{**self.scope,
                'elapsed_seconds': time.perf_counter()-started,
                'coverage_complete': pending_parent_id is None,
                'pending_parent_id': pending_parent_id, **extra})
            trace.append(row)
            return row

        def accept_candidate(node, source):
            nonlocal incumbent, best, best_report, best_provenance
            if not node.validator_pass:
                return
            if node.validator_report['physics_fingerprint'] != self.scope['physics_fingerprint']:
                raise DPIntegrityError('candidate/config physical model mismatch')
            value = float(node.validator_report['reconstructed_objective'])
            if not math.isfinite(value) or value < node.node_lb - TOL:
                raise DPIntegrityError('feasible objective is below its certified node bound')
            # Registration is valid for ANY certified candidate. Fathoming needs
            # the stronger equality: exact independent minima close their node.
            if value <= node.node_lb + TOL:
                node.status, node.fathom_reason = FEASIBLE, 'jointly feasible independent minima close this node'
            if incumbent is None or value < incumbent:
                before = incumbent
                incumbent, best, best_report = value, dict(node.paths), node.validator_report
                ks = {tid:d.k for tid,d in getattr(self.dp, 'domains', {}).items()}
                best_provenance = dict(record(node, 'incumbent',
                    ub_source='BNB_GENERATED',
                    incumbent_source=source, candidate_ub=value,
                    generated_iteration=node.generated_iteration,
                    generated_seconds=node.generated_seconds,
                    K=next(iter(set(ks.values()))) if len(set(ks.values())) == 1 else None,
                    K_by_train=ks, paths=best, validator_report=best_report,
                    incumbent_improvement=None if before is None else before-value))
            elif node.status == FEASIBLE:
                record(node, 'fathom_feasible', candidate_ub=value)

        def prune_bound(node):
            if node.status == OPEN and incumbent is not None and node.node_lb >= incumbent - TOL:
                node.status, node.fathom_reason = PRUNED_BOUND, 'node LB >= validated incumbent UB (tolerance)'
                record(node, 'prune_bound')

        def generated(node, action):
            # No deadline may separate known feasibility from its registration.
            event = record(node, action, coverage_complete=False)
            node.generated_iteration = event['iteration']
            node.generated_seconds = event['elapsed_seconds']
            prune_bound(node)
            if node.status == OPEN:
                accept_candidate(node, 'GENERATED_FEASIBLE_CHILD' if node.parent_id is not None else 'SOLVED_FEASIBLE_ROOT')

        if self.initial_incumbent is not None:
            best_provenance = dict(record(root,'incumbent_initialized',
                ub_source=self.initial_incumbent.source,incumbent_source=self.initial_incumbent.source,
                candidate_ub=incumbent,paths=best,validator_report=best_report,
                origin_scope=self.initial_incumbent.origin_scope,coverage_complete=False))
        generated(root, 'root_generated')
        if root.status == OPEN:
            heap.append((root.node_lb, 0))

        record(root, "solved")
        while heap:
            _, node_id = heapq.heappop(heap)
            node = nodes[node_id]
            if node.status != OPEN:
                continue
            record(node, 'popped')
            prune_bound(node)
            if node.status != OPEN:
                continue

            conflict, rejected = hbranch.select(node.conflicts, enabled=self.history_policy == "trajectory",
                paths=node.paths, trains=self.trains, arcs=self.arcs, mow=self.mow, config=self.config,
                domains=getattr(self.dp, 'domains', {}), parent_id=node.node_id,
                proof_scope=self.scope['proof_scope'], validation_args=self._validation_args(node.restrictions))
            unsupported.extend({**r, "node_id": node.node_id} for r in rejected)
            if conflict is None:
                node.status, node.fathom_reason = UNSUPPORTED_CONFLICT, "no proved local-event or complete-trajectory disjunction"
                record(node, "unsupported")
                continue
            if (conflict.headway_model != self.config.headway_model or
                    conflict.headway != self.config.safety_headway or
                    node.validator_report['physics_fingerprint'] != self.scope['physics_fingerprint']):
                raise DPIntegrityError('validator/branch certificate physical model mismatch')
            node.selected_conflict = conflict
            if node.depth >= self.max_depth or len(nodes)+2 > self.max_nodes:
                reason = "DEPTH_BUDGET_EXHAUSTED" if node.depth >= self.max_depth else "NODE_BUDGET_EXHAUSTED"
                budget_stops.add(reason)
                node.fathom_reason = reason  # stays OPEN and remains in global LB
                record(node, "budget_stop")
                continue

            new_children = []
            is_history = isinstance(conflict, hbranch.HistoryBranch)
            children = conflict.children(node.restrictions) if is_history else cbranch.children(conflict, node.restrictions)
            pending_parent_id = node.node_id
            for tid, restrictions in zip((conflict.train_i, conflict.train_j), children):
                literal = conflict.restriction_for(tid)
                label = (f"forbid {tid}@{literal['resource_id']}/{literal['direction']} "
                         f"movement [{literal['t_lo']:g},{literal['t_hi']:g})") if not is_history else ""
                if is_history:
                    label = f"forbid complete trajectory {tid}:{literal['trajectory_id'][:12]}"
                child = Node(len(nodes), node.node_id, node.depth+1, restrictions,
                             branch_decision=label, new_branch_restriction=literal, branch_event=conflict)
                self._solve(child)
                if child.node_lb < node.node_lb - TOL:
                    raise DPIntegrityError("a restricted unpriced minimum decreased")
                nodes.append(child)
                new_children.append(child)
                node.children.append(child.node_id)
                generated(child, 'child_generated')
                if child.status == OPEN:
                    heapq.heappush(heap, (child.node_lb, child.node_id))
            # Close the parent only after BOTH child domains have been installed.
            node.status = EXPANDED
            pending_parent_id = None
            record(node, "branch", minimum_child_lb=min(c.node_lb for c in new_children),
                   guaranteed_lb_improvement=min(c.node_lb for c in new_children)-node.node_lb)
            for child in new_children:
                pair_present = conflict.pair_present(child.paths) if is_history else all(any(
                    arc == event.arc_id and ab == event.ab and
                    abs(start-event.entry) <= 1e-9 and abs(end-event.exit) <= 1e-9
                    for (arc, start, end), ab in zip(child.paths[tid].legs, child.paths[tid].ab_flags))
                    for tid, event in ((conflict.train_i, conflict.event_i),
                                       (conflict.train_j, conflict.event_j)))
                if pair_present:
                    raise DPIntegrityError("the selected conflicting certificate pair regenerated")
                record(child, "child_solved", parent_lb=node.node_lb,
                       node_lb_improvement=child.node_lb-node.node_lb,
                       restriction_enforced=all(not child.restrictions.violations(r)
                                                for r in child.paths.values() if r.feasible),
                       selected_conflicting_pair_removed=not pair_present,
                       joint_timetable_feasible=child.validator_pass)

        bound = self._global_lb(nodes, incumbent)
        if any(n.status == UNSUPPORTED_CONFLICT for n in nodes):
            status = "UNRESOLVED_UNSUPPORTED_CONFLICT"
        elif any(n.status == OPEN for n in nodes):
            status = "+".join(sorted(budget_stops)) or "UNRESOLVED"
        else:
            status = "PROVEN_OPTIMAL" if incumbent is not None else "PROVEN_INFEASIBLE"
        # A final row records closure at the actual covering frontier, not by
        # copying a feasible objective into an unrelated diagnostic field.
        trace.append({"iteration": len(trace), "action": "finished", "node_id": None,
                      "global_lb": bound, "incumbent_ub": incumbent,
                      "absolute_gap": None if incumbent is None else max(0.0, incumbent-bound),
                      "relative_gap": None if incumbent is None else max(0.0, incumbent-bound)/max(abs(incumbent),1e-12),
                      "status": status, **self.scope})
        return Summary(nodes, status, bound, incumbent, best, trace, unsupported, root.node_lb, best_report,
                       incumbent_provenance=best_provenance, **self.scope)

    @staticmethod
    def _row(iteration, node, action, **extra):
        event = node.selected_conflict or node.branch_event
        return {"iteration": iteration, "node_id": node.node_id, "parent_id": node.parent_id,
                **physics_metadata(node.headway_model, node.safety_headway),
                "depth": node.depth, "action": action, "branch_decision": node.branch_decision,
                "new_branch_restriction": node.new_branch_restriction,
                "all_active_restrictions": node.restrictions.row_list(),
                "node_lb": node.node_lb, "global_lb": node.global_lb,
                "incumbent_ub": node.incumbent_ub, "absolute_gap": node.absolute_gap,
                "relative_gap": node.relative_gap, "num_conflicts": node.validator_conflicts,
                "validator_pass": node.validator_pass, "status": node.status,
                "selected_conflict_id": event.conflict_id if event else None,
                "conflict_type": event.conflict_type if event else None,
                "train_i": event.train_i if event else None, "train_j": event.train_j if event else None,
                "resource": event.arc_id if event else None,
                "interval": ([event.event_i.entry, event.event_i.exit,
                              event.event_j.entry, event.event_j.exit] if event else None),
                "protected_intervals": ([[e.entry, (e.entry+event.headway if
                                           event.conflict_type == "same-direction-main-headway"
                                           else e.protected_end(event.headway))]
                                         for e in (event.event_i, event.event_j)] if event else None),
                "certificate_type": ("PAIRWISE_COMPLETE_TRAJECTORY" if isinstance(event, hbranch.HistoryBranch)
                                     else "EXACT_EVENT_PAIR" if event else None),
                "infeasible_trains": node.infeasible_trains, "fathom_reason": node.fathom_reason, **extra}
