"""Experimental mappings from the Fluid Queue price ``pi`` to physical ``mu``.

``fluid_queue.py`` is NOT modified and NOT reimplemented here: this module
calls it and reads its output.  Every mapping returns ``mu >= 0``, and the
certified bound is always recomputed by ``physical_lr.evaluate`` using the
valid physical Lagrangian.  No correction term is ever applied afterwards.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Mapping, Sequence

from .fluid_queue import fluid_lambda_update, fluid_queue_cells

from .physical_constraint_model import Coupling, Event

EPS = 1e-9


def coupling_block(coupling: Coupling, bin_minutes: float) -> tuple[int, int]:
    """The Fluid Queue cell a coupling is anchored to.

    A coupling lives on one arc and on a set of entry times; its Fluid Queue
    anchor is that arc and the aggregation block of its earliest entry.  This
    is an indexing convention for the mapping only -- it never redefines
    ``pi`` and never feeds back into ``C_q``.
    """
    entries = [event.entry for _train, event in coupling.terms]
    anchor = min(entries) if entries else 0.0
    return (int(coupling.arc_id),
            max(0, int(math.floor(anchor / bin_minutes + EPS))))


def fluid_prices(selected: Mapping[str, Sequence[Event]], arcs, mow, *,
                 bin_minutes: float, horizon: float,
                 previous: Mapping[tuple[int, int], float] | None = None,
                 alpha: float = 1.0, beta: float = 4.0, gamma: float = 0.15,
                 wait_reference_minutes: float = 30.0,
                 queue_wait_cost_per_min: float = 1.0,
                 max_price_wait_minutes: float = 180.0,
                 ) -> dict[tuple[int, int], float]:
    """Run the unmodified Fluid Queue for one iteration and return ``pi``."""
    arrivals: dict[tuple[int, int], float] = defaultdict(float)
    for events in selected.values():
        for event in events:
            block = max(0, int(math.floor(event.entry / bin_minutes + EPS)))
            arrivals[(int(event.arc_id), block)] += 1.0
    old = dict(previous or {})
    cells = fluid_queue_cells(dict(arrivals), old, arcs, mow,
                              bin_minutes=bin_minutes, horizon=horizon)
    new, _target = fluid_lambda_update(
        cells, old, alpha=alpha, beta=beta, gamma=gamma,
        wait_reference_minutes=wait_reference_minutes,
        queue_wait_cost_per_min=queue_wait_cost_per_min,
        max_price_wait_minutes=max_price_wait_minutes, use_price_queue=True)
    return {key: value for key, value in new.items() if value > EPS}


def map_broadcast(couplings: Sequence[Coupling], pi: Mapping[tuple[int, int], float],
                  *, bin_minutes: float, scale: float = 1.0) -> dict[str, float]:
    """M1: give every coupling the ``pi`` of its own arc/block."""
    return {item.key: max(0.0, scale * float(pi.get(coupling_block(item, bin_minutes), 0.0)))
            for item in couplings}


def map_normalized(couplings: Sequence[Coupling], pi: Mapping[tuple[int, int], float],
                   *, bin_minutes: float, scale: float = 1.0) -> dict[str, float]:
    """M2: split each block's ``pi`` evenly across its couplings."""
    counts: dict[tuple[int, int], int] = defaultdict(int)
    for item in couplings:
        counts[coupling_block(item, bin_minutes)] += 1
    result: dict[str, float] = {}
    for item in couplings:
        block = coupling_block(item, bin_minutes)
        share = counts[block] or 1
        result[item.key] = max(0.0, scale * float(pi.get(block, 0.0)) / share)
    return result


def map_activation(couplings: Sequence[Coupling], pi: Mapping[tuple[int, int], float],
                   *, bin_minutes: float, magnitude: float = 1.0) -> dict[str, float]:
    """M3: use ``pi`` only to choose the support; take magnitude from elsewhere."""
    return {item.key: (magnitude if float(pi.get(coupling_block(item, bin_minutes), 0.0)) > EPS
                       else 0.0)
            for item in couplings}


MAPPINGS = {"M1_broadcast": map_broadcast,
            "M2_normalized": map_normalized,
            "M3_activation": map_activation}
