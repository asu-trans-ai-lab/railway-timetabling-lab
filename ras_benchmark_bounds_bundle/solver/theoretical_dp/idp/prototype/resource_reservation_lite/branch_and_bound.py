from __future__ import annotations

from dataclasses import asdict, dataclass, field
import heapq
import math
import time

from .conflicts import all_conflicts, safe_frontier
from .constructive import greedy_conflict_upper_bound, priority_upper_bound
from .model import Conflict, Instance, ReservationDecision, Schedule, canonical_reservations
from .scheduler import schedule_from_reservations

TOL = 1e-9


@dataclass
class BBNode:
    node_id: int
    parent_id: int | None
    depth: int
    reservations: tuple[ReservationDecision, ...]
    branch_decision: ReservationDecision | None = None
    schedule: Schedule | None = None
    conflicts: list[Conflict] = field(default_factory=list)
    node_lb: float = math.inf
    safe_frontier_min: float = 0.0
    safe_tasks: int = 0
    status: str = "OPEN"
    fathom_reason: str = ""


@dataclass
class BBSummary:
    instance: str
    status: str
    nodes: list[BBNode]
    trace: list[dict[str, object]]
    root_lb: float
    global_lb: float
    incumbent_ub: float | None
    best_schedule: Schedule | None
    best_reservations: tuple[ReservationDecision, ...]
    initial_ub: float | None
    first_ub_seconds: float | None
    nodes_generated: int
    nodes_expanded: int
    nodes_pruned_bound: int
    nodes_pruned_cycle: int
    feasible_leaves: int
    elapsed_seconds: float

    @property
    def absolute_gap(self) -> float | None:
        if self.incumbent_ub is None or not math.isfinite(self.global_lb):
            return None
        return max(0.0, self.incumbent_ub - self.global_lb)

    @property
    def relative_gap(self) -> float | None:
        if self.absolute_gap is None:
            return None
        return self.absolute_gap / max(abs(self.incumbent_ub or 0.0), 1e-12)


class ReservationBranchAndBound:
    """Exact B&B for the idealized fixed-chain, unary-resource benchmark.

    * Node subproblem: forward DP / longest path under inherited reservations.
    * Conflict rule: earliest unresolved protected resource overlap.
    * Branch: resource reserved by task A before B OR B before A.
    * Initial UB: guaranteed global train-priority schedule, optionally improved
      by the greedy earliest-conflict repair.
    * Node selection: best lower bound, then larger safe-task frontier.
    """

    def __init__(self, instance: Instance, *, max_nodes: int = 100_000, use_greedy_ub: bool = True):
        if max_nodes < 1:
            raise ValueError("max_nodes must be >= 1")
        self.instance = instance
        self.max_nodes = max_nodes
        self.use_greedy_ub = use_greedy_ub

    def _solve_node(self, node: BBNode) -> None:
        schedule = schedule_from_reservations(self.instance, node.reservations)
        node.schedule = schedule
        if not schedule.feasible_precedence:
            node.status = "PRUNED_CYCLE"
            node.fathom_reason = schedule.reason
            return
        node.node_lb = schedule.objective_total_travel
        node.conflicts = all_conflicts(self.instance, schedule)
        node.safe_frontier_min, node.safe_tasks = safe_frontier(self.instance, schedule, node.conflicts)
        if not node.conflicts:
            node.status = "FEASIBLE"

    def solve(self) -> BBSummary:
        started = time.perf_counter()
        trace: list[dict[str, object]] = []
        nodes: list[BBNode] = []
        expanded = pruned_bound = pruned_cycle = feasible_leaves = 0
        budget_stopped = False

        # A finite incumbent exists by construction in this idealized model.
        priority = priority_upper_bound(self.instance)
        incumbent = priority.schedule.objective_total_travel
        best_schedule = priority.schedule
        best_reservations = priority.reservations
        initial_ub = incumbent
        first_ub_seconds = time.perf_counter() - started
        incumbent_source = priority.method

        if self.use_greedy_ub:
            greedy = greedy_conflict_upper_bound(self.instance)
            if greedy is not None and greedy.schedule.objective_total_travel < incumbent - TOL:
                incumbent = greedy.schedule.objective_total_travel
                best_schedule = greedy.schedule
                best_reservations = greedy.reservations
                incumbent_source = greedy.method

        def record(node: BBNode | None, action: str, **extra: object) -> None:
            live_lbs = [n.node_lb for n in nodes if n.status == "OPEN" and math.isfinite(n.node_lb)]
            global_lb = min(live_lbs + ([incumbent] if incumbent is not None else []), default=math.inf)
            row: dict[str, object] = {
                "iteration": len(trace),
                "action": action,
                "elapsed_seconds": time.perf_counter() - started,
                "incumbent_ub": incumbent,
                "incumbent_source": incumbent_source,
                "global_lb": global_lb,
                "absolute_gap": None if incumbent is None or not math.isfinite(global_lb) else max(0.0, incumbent - global_lb),
                "relative_gap": None if incumbent is None or not math.isfinite(global_lb) else max(0.0, incumbent - global_lb) / max(abs(incumbent), 1e-12),
            }
            if node is not None:
                row.update({
                    "node_id": node.node_id,
                    "parent_id": node.parent_id,
                    "depth": node.depth,
                    "status": node.status,
                    "node_lb": node.node_lb,
                    "num_reservations": len(node.reservations),
                    "num_conflicts": len(node.conflicts),
                    "safe_frontier_min": node.safe_frontier_min,
                    "safe_tasks": node.safe_tasks,
                    "total_tasks": self.instance.total_task_count,
                    "safe_task_ratio": node.safe_tasks / self.instance.total_task_count,
                    "branch_decision": None if node.branch_decision is None else node.branch_decision.label(),
                    "selected_conflict": None if not node.conflicts else node.conflicts[0].label(),
                    "fathom_reason": node.fathom_reason,
                    "node_delay_lb": None if node.schedule is None or not node.schedule.feasible_precedence else node.schedule.total_delay,
                })
            row.update(extra)
            trace.append(row)

        root = BBNode(0, None, 0, ())
        self._solve_node(root)
        nodes.append(root)
        root_lb = root.node_lb
        record(root, "root_solved", initial_ub=initial_ub)

        # Feasible root means no resource coupling at all.
        if root.status == "FEASIBLE":
            feasible_leaves += 1
            if root.node_lb < incumbent - TOL:
                incumbent, best_schedule, best_reservations = root.node_lb, root.schedule, root.reservations
                incumbent_source = "ROOT_FEASIBLE"
            root.status = "FATHOMED_FEASIBLE"
            record(root, "root_feasible")
        elif root.status == "PRUNED_CYCLE":
            pruned_cycle += 1

        heap: list[tuple[float, int, float, int]] = []
        if root.status == "OPEN" and root.node_lb < incumbent - TOL:
            heapq.heappush(heap, (root.node_lb, -root.safe_tasks, -root.safe_frontier_min, root.node_id))
        elif root.status == "OPEN":
            root.status = "PRUNED_BOUND"
            root.fathom_reason = "root LB >= initial feasible UB"
            pruned_bound += 1
            record(root, "prune_bound")

        while heap and len(nodes) < self.max_nodes:
            _, _, _, node_id = heapq.heappop(heap)
            node = nodes[node_id]
            if node.status != "OPEN":
                continue
            if node.node_lb >= incumbent - TOL:
                node.status = "PRUNED_BOUND"
                node.fathom_reason = "node LB >= incumbent UB"
                pruned_bound += 1
                record(node, "prune_bound")
                continue

            if not node.conflicts:
                node.status = "FATHOMED_FEASIBLE"
                feasible_leaves += 1
                if node.node_lb < incumbent - TOL:
                    incumbent, best_schedule, best_reservations = node.node_lb, node.schedule, node.reservations
                    incumbent_source = "BNB_FEASIBLE_LEAF"
                    record(node, "incumbent")
                else:
                    record(node, "fathom_feasible")
                continue

            selected = node.conflicts[0]
            if len(nodes) + 2 > self.max_nodes:
                budget_stopped = True
                # Keep this node live so its valid lower bound remains in the
                # final global bound; do not create only one side of a covering
                # resource-reservation disjunction.
                node.status = "OPEN"
                node.fathom_reason = "node budget cannot install both reservation children"
                heapq.heappush(heap, (node.node_lb, -node.safe_tasks, -node.safe_frontier_min, node.node_id))
                record(node, "budget_stop")
                break
            node.status = "EXPANDED"
            expanded += 1
            record(node, "branch", selected_conflict=selected.label())

            for first, second in ((selected.left.key, selected.right.key), (selected.right.key, selected.left.key)):
                decision = ReservationDecision(selected.resource_id, first, second)
                child_res = canonical_reservations(node.reservations + (decision,))
                if child_res == node.reservations:
                    continue
                child = BBNode(len(nodes), node.node_id, node.depth + 1, child_res, decision)
                self._solve_node(child)
                nodes.append(child)

                if child.status == "PRUNED_CYCLE":
                    pruned_cycle += 1
                    record(child, "prune_cycle")
                    continue
                if child.node_lb + TOL < node.node_lb:
                    raise AssertionError("adding a resource reservation reduced the node lower bound")
                if child.status == "FEASIBLE":
                    feasible_leaves += 1
                    if child.node_lb < incumbent - TOL:
                        incumbent, best_schedule, best_reservations = child.node_lb, child.schedule, child.reservations
                        incumbent_source = "BNB_FEASIBLE_LEAF"
                        child.status = "FATHOMED_FEASIBLE"
                        record(child, "incumbent")
                    else:
                        child.status = "FATHOMED_FEASIBLE"
                        record(child, "fathom_feasible")
                    continue
                if child.node_lb >= incumbent - TOL:
                    child.status = "PRUNED_BOUND"
                    child.fathom_reason = "node LB >= incumbent UB"
                    pruned_bound += 1
                    record(child, "prune_bound")
                    continue
                heapq.heappush(heap, (child.node_lb, -child.safe_tasks, -child.safe_frontier_min, child.node_id))
                record(child, "child_open")

        open_nodes = [n for n in nodes if n.status == "OPEN"]
        if heap or open_nodes:
            status = "NODE_BUDGET_EXHAUSTED" if budget_stopped or len(nodes) >= self.max_nodes else "UNRESOLVED"
            live_lbs = [n.node_lb for n in open_nodes if math.isfinite(n.node_lb)]
            global_lb = min(live_lbs + [incumbent], default=incumbent)
        else:
            status = "PROVEN_OPTIMAL"
            global_lb = incumbent
        elapsed = time.perf_counter() - started
        record(None, "finished", solver_status=status, nodes_generated=len(nodes), nodes_expanded=expanded)
        return BBSummary(
            instance=self.instance.name,
            status=status,
            nodes=nodes,
            trace=trace,
            root_lb=root_lb,
            global_lb=global_lb,
            incumbent_ub=incumbent,
            best_schedule=best_schedule,
            best_reservations=best_reservations,
            initial_ub=initial_ub,
            first_ub_seconds=first_ub_seconds,
            nodes_generated=len(nodes),
            nodes_expanded=expanded,
            nodes_pruned_bound=pruned_bound,
            nodes_pruned_cycle=pruned_cycle,
            feasible_leaves=feasible_leaves,
            elapsed_seconds=elapsed,
        )
