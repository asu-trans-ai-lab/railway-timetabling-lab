from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, FrozenSet
import math

from .model import (
    Agent, AgentState, CompletionState, Network, ResourceKey, Trajectory,
    transition_footprint,
)


@dataclass(frozen=True)
class Step:
    next_state: AgentState
    physical_cost: float
    footprint: FrozenSet[ResourceKey]
    action: str


def _reference_node(reference: Optional[Trajectory], agent_id: int, time: int) -> int:
    if reference is None:
        return -1
    states = reference.states_by_agent.get(agent_id, [])
    if not states:
        return -1
    by_time = {s.time: s.node for s in states}
    if time in by_time:
        return by_time[time]
    earlier = [t for t in by_time if t <= time]
    return by_time[max(earlier)] if earlier else by_time[min(by_time)]


def _advance_service(state: AgentState, agent: Agent) -> Optional[Step]:
    c = state.completion
    if c.active_task < 0:
        return None
    remaining = c.remaining_service_time - 1
    if remaining <= 0:
        nc = CompletionState(
            completed_prefix=c.completed_prefix + 1,
            frontier_mask=0,
            active_task=-1,
            remaining_service_time=0,
        )
    else:
        nc = replace(c, remaining_service_time=remaining)
    ns = AgentState(state.node, state.time + 1, nc, state.node, False)
    return Step(ns, agent.service_cost, transition_footprint(state, ns), "service")


def feasible_steps(
    network: Network,
    agent: Agent,
    state: AgentState,
    resource_prices: Mapping[ResourceKey, float],
    reference: Optional[Trajectory] = None,
    rho: float = 0.0,
    resource_extra: Optional[Mapping[ResourceKey, float]] = None,
    distance_monotone: bool = True,
) -> List[Step]:
    if state.arrived:
        ns = AgentState(-1, state.time + 1, state.completion, state.node, True)
        return [Step(ns, 0.0, frozenset(), "remove")]
    if state.time >= agent.deadline:
        return []
    # Flatland-style off-network staging before departure.
    if state.node < 0:
        wait_state = AgentState(-1, state.time + 1, state.completion, -1, False)
        enter_state = AgentState(agent.origin, state.time + 1, state.completion, -1, False)
        raw = [
            Step(wait_state, agent.wait_cost, frozenset(), "stage_wait"),
            Step(enter_state, 0.0, transition_footprint(state, enter_state), "enter"),
        ]
        out = []
        for step in raw:
            priced = step.physical_cost
            for key in step.footprint:
                priced += resource_prices.get(key, 0.0)
                if resource_extra is not None:
                    priced += resource_extra.get(key, 0.0)
            out.append(Step(step.next_state, step.physical_cost, step.footprint, f"{step.action}|{priced:.8f}"))
        return out

    forced = _advance_service(state, agent)
    if forced is not None:
        return [forced]

    c = state.completion
    if c.completed_prefix < len(agent.tasks):
        task = agent.tasks[c.completed_prefix]
        if state.node == task.node and task.earliest <= state.time <= task.latest:
            if task.duration <= 1:
                nc = CompletionState(c.completed_prefix + 1, 0, -1, 0)
            else:
                nc = CompletionState(c.completed_prefix, 0, task.id, task.duration - 1)
            ns = AgentState(state.node, state.time + 1, nc, state.node, False)
            step = Step(ns, agent.service_cost, transition_footprint(state, ns), "start_service")
            return [step]

    steps: List[Step] = []
    # Wait.
    ns = AgentState(state.node, state.time + 1, state.completion, state.node, False)
    steps.append(Step(ns, agent.wait_cost, transition_footprint(state, ns), "wait"))

    dist = network.hop_distances_to(agent.destination)
    current_d = dist.get(state.node, 10**9)
    for arc in network.adjacency.get(state.node, []):
        next_d = dist.get(arc.v, 10**9)
        if distance_monotone and next_d > current_d:
            continue
        # Reachability to the destination plus unfinished service time.
        remaining_service = sum(t.duration for t in agent.tasks[state.completion.completed_prefix:])
        if state.time + 1 + next_d + remaining_service > agent.deadline:
            continue
        arrived = arc.v == agent.destination and state.completion.completed_prefix == len(agent.tasks)
        ns = AgentState(arc.v, state.time + 1, state.completion, state.node, arrived)
        steps.append(Step(ns, agent.move_cost * arc.cost, transition_footprint(state, ns), "move"))

    out: List[Step] = []
    for step in steps:
        physical = step.physical_cost
        priced = physical
        for key in step.footprint:
            priced += resource_prices.get(key, 0.0)
            if resource_extra is not None:
                priced += resource_extra.get(key, 0.0)
        ref_node = _reference_node(reference, agent.id, step.next_state.time)
        if rho > 0.0 and ref_node >= 0 and step.next_state.node >= 0:
            priced += 0.5 * rho * network.node_distance_sq(step.next_state.node, ref_node)
        out.append(Step(step.next_state, physical, step.footprint, f"{step.action}|{priced:.8f}"))
    return out


def _priced_from_action(action: str) -> float:
    return float(action.rsplit("|", 1)[1]) if "|" in action else 0.0


def single_agent_dp(
    network: Network,
    agent: Agent,
    resource_prices: Optional[Mapping[ResourceKey, float]] = None,
    reference: Optional[Trajectory] = None,
    rho: float = 0.0,
    resource_extra: Optional[Mapping[ResourceKey, float]] = None,
    forced_release: Optional[int] = None,
) -> Optional[Trajectory]:
    prices = resource_prices or {}
    release = agent.release if forced_release is None else forced_release
    start = AgentState(-1, release, CompletionState(), -1, False)
    labels: Dict[AgentState, float] = {start: 0.0}
    physical: Dict[AgentState, float] = {start: 0.0}
    pred: Dict[AgentState, Tuple[AgentState, Step]] = {}
    generated = 1
    scanned = 0
    best_terminal: Optional[AgentState] = None
    best_value = math.inf

    for _t in range(release, agent.deadline + 1):
        layer = [s for s in labels if s.time == _t]
        for state in layer:
            if state.arrived:
                if labels[state] < best_value:
                    best_terminal, best_value = state, labels[state]
                continue
            for step in feasible_steps(network, agent, state, prices, reference, rho, resource_extra):
                scanned += 1
                ns = step.next_state
                nv = labels[state] + _priced_from_action(step.action)
                np = physical[state] + step.physical_cost
                if nv + 1e-12 < labels.get(ns, math.inf):
                    if ns not in labels:
                        generated += 1
                    labels[ns] = nv
                    physical[ns] = np
                    pred[ns] = (state, step)
        # Early exit if terminal found and all remaining labels cannot beat it is omitted for clarity.

    terminals = [s for s in labels if s.arrived]
    if not terminals:
        return None
    best_terminal = min(terminals, key=lambda s: labels[s])

    states: List[AgentState] = [best_terminal]
    footprint = set()
    cur = best_terminal
    while cur != start:
        prev, step = pred[cur]
        footprint.update(step.footprint)
        states.append(prev)
        cur = prev
    states.reverse()
    signature = tuple((s.time, s.node, s.completion.completed_prefix, s.completion.active_task, s.completion.remaining_service_time) for s in states)
    return Trajectory(
        (agent.id,), {agent.id: states}, frozenset(footprint),
        physical[best_terminal], labels[best_terminal], signature,
        generated_states=generated, scanned_transitions=scanned,
    )


def _hard_joint_feasible(steps: Sequence[Step]) -> bool:
    occupied = set()
    for step in steps:
        for key in step.footprint:
            if key in occupied:
                return False
            occupied.add(key)
    return True


def joint_group_dp(
    network: Network,
    agents: Sequence[Agent],
    resource_prices: Optional[Mapping[ResourceKey, float]] = None,
    reference: Optional[Trajectory] = None,
    rho: float = 0.0,
    corridor_budget: Optional[float] = None,
    resource_extra: Optional[Mapping[ResourceKey, float]] = None,
) -> Optional[Trajectory]:
    prices = resource_prices or {}
    release = min(a.release for a in agents)
    start_states = tuple(
        AgentState(-1, release, CompletionState(), -1, False) for a in agents
    )
    labels: Dict[Tuple[AgentState, ...], float] = {start_states: 0.0}
    physical: Dict[Tuple[AgentState, ...], float] = {start_states: 0.0}
    pred: Dict[Tuple[AgentState, ...], Tuple[Tuple[AgentState, ...], Tuple[Step, ...]]] = {}
    generated = 1
    scanned = 0
    screened = 0
    horizon = max(a.deadline for a in agents)

    for t in range(release, horizon + 1):
        layer = [s for s in labels if s[0].time == t]
        for joint_state in layer:
            if all(st.arrived for st in joint_state):
                continue
            choices: List[List[Step]] = []
            feasible = True
            for a, st in zip(agents, joint_state):
                steps = feasible_steps(network, a, st, prices, reference, rho, resource_extra)
                if not steps:
                    feasible = False
                    break
                choices.append(steps)
            if not feasible:
                continue
            for combo in product(*choices):
                scanned += 1
                if not _hard_joint_feasible(combo):
                    continue
                if corridor_budget is not None and reference is not None:
                    dev = 0.0
                    for a, step in zip(agents, combo):
                        ref_node = _reference_node(reference, a.id, step.next_state.time)
                        if ref_node >= 0 and step.next_state.node >= 0:
                            dev += network.node_distance_sq(step.next_state.node, ref_node)
                    if rho * dev > corridor_budget + 1e-12:
                        screened += 1
                        continue
                ns = tuple(step.next_state for step in combo)
                nv = labels[joint_state] + sum(_priced_from_action(step.action) for step in combo)
                # Soft spacing/coupling term.
                for p in range(len(combo)):
                    for q in range(p + 1, len(combo)):
                        ni, nj = combo[p].next_state.node, combo[q].next_state.node
                        if ni >= 0 and nj >= 0:
                            d2 = network.node_distance_sq(ni, nj)
                            if d2 < 1.0:
                                nv += 0.5 * (1.0 - d2) ** 2
                np = physical[joint_state] + sum(step.physical_cost for step in combo)
                if nv + 1e-12 < labels.get(ns, math.inf):
                    if ns not in labels:
                        generated += 1
                    labels[ns] = nv
                    physical[ns] = np
                    pred[ns] = (joint_state, tuple(combo))

    terminals = [s for s in labels if all(st.arrived for st in s)]
    if not terminals:
        return None
    end = min(terminals, key=lambda s: labels[s])
    joint_states: List[Tuple[AgentState, ...]] = [end]
    footprint = set()
    cur = end
    while cur != start_states:
        prev, combo = pred[cur]
        for step in combo:
            footprint.update(step.footprint)
        joint_states.append(prev)
        cur = prev
    joint_states.reverse()
    states_by_agent = {
        a.id: [js[k] for js in joint_states] for k, a in enumerate(agents)
    }
    signature = tuple(
        tuple((st.time, st.node, st.completion.completed_prefix, st.arrived) for st in states_by_agent[a.id])
        for a in agents
    )
    return Trajectory(
        tuple(a.id for a in agents), states_by_agent, frozenset(footprint),
        physical[end], labels[end], signature,
        generated_states=generated, scanned_transitions=scanned, screened_states=screened,
    )
