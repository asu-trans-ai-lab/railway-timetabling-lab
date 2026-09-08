"""Idealized Dynamic Programming (IDP) laboratory for train scheduling.

The laboratory is intentionally layered:
IDP-0 physical-chain DP
IDP-1 tiny global resource-state DP
IDP-2 reservation branch-and-bound (wrapper around the existing lite solver)
IDP-3 search-strategy comparison
IDP-4 Lagrangian relaxation + subgradient using a pricing DP
"""

from .physical_chain_dp import ResourceCalendar, solve_physical_chain_dp
from .global_resource_dp import solve_global_resource_dp
from .lagrangian import run_lagrangian_relaxation

__all__ = [
    "ResourceCalendar",
    "solve_physical_chain_dp",
    "solve_global_resource_dp",
    "run_lagrangian_relaxation",
]
