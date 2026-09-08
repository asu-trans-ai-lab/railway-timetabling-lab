from __future__ import annotations

from ..resource_reservation_lite.branch_and_bound import BBSummary


def extract_conflict_history(summary: BBSummary) -> list[dict[str, object]]:
    """Convert an exact B&B tree into conflict-learning-ready records.

    No ML is used here. The purpose is to preserve branch outcomes so later work
    can compare conflict-ordering rules without changing exactness.
    """

    children: dict[int, list] = {}
    for node in summary.nodes:
        if node.parent_id is not None:
            children.setdefault(node.parent_id, []).append(node)

    rows: list[dict[str, object]] = []
    for node in summary.nodes:
        if node.status != "EXPANDED" or not node.conflicts:
            continue
        c = node.conflicts[0]
        child_nodes = children.get(node.node_id, [])
        best_child_lb = min((ch.node_lb for ch in child_nodes), default=None)
        best_safe_tasks = max((ch.safe_tasks for ch in child_nodes), default=node.safe_tasks)
        best_safe_frontier = max((ch.safe_frontier_min for ch in child_nodes), default=node.safe_frontier_min)
        rows.append({
            "node_id": node.node_id,
            "depth": node.depth,
            "resource_id": c.resource_id,
            "conflict_time_min": c.time_min,
            "left_task": c.left.key.label(),
            "right_task": c.right.key.label(),
            "parent_lb": node.node_lb,
            "num_conflicts_before": len(node.conflicts),
            "safe_tasks_before": node.safe_tasks,
            "safe_frontier_before": node.safe_frontier_min,
            "child_count": len(child_nodes),
            "best_child_lb": best_child_lb,
            "delta_best_child_lb": None if best_child_lb is None else best_child_lb - node.node_lb,
            "best_child_safe_tasks": best_safe_tasks,
            "delta_safe_tasks": best_safe_tasks - node.safe_tasks,
            "best_child_safe_frontier": best_safe_frontier,
            "delta_safe_frontier": best_safe_frontier - node.safe_frontier_min,
        })
    return rows
