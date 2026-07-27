from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple, FrozenSet, Optional
from collections import defaultdict, deque
import math

ResourceKey = Tuple[str, int, int]  # (kind, resource_id, time)


@dataclass(frozen=True)
class Node:
    id: int
    x: float
    y: float


@dataclass(frozen=True)
class Arc:
    id: int
    u: int
    v: int
    cost: float = 1.0
    resource_id: int = -1


@dataclass
class Network:
    nodes: Dict[int, Node]
    arcs: List[Arc]
    adjacency: Dict[int, List[Arc]] = field(init=False)
    reverse_adjacency: Dict[int, List[Arc]] = field(init=False)
    _distance_cache: Dict[int, Dict[int, int]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.adjacency = defaultdict(list)
        self.reverse_adjacency = defaultdict(list)
        for arc in self.arcs:
            self.adjacency[arc.u].append(arc)
            self.reverse_adjacency[arc.v].append(arc)

    def node_distance_sq(self, a: int, b: int) -> float:
        if a < 0 or b < 0:
            return 0.0
        na, nb = self.nodes[a], self.nodes[b]
        return (na.x - nb.x) ** 2 + (na.y - nb.y) ** 2

    def hop_distances_to(self, destination: int) -> Dict[int, int]:
        if destination in self._distance_cache:
            return self._distance_cache[destination]
        dist = {destination: 0}
        q = deque([destination])
        while q:
            v = q.popleft()
            for arc in self.reverse_adjacency[v]:
                if arc.u not in dist:
                    dist[arc.u] = dist[v] + 1
                    q.append(arc.u)
        self._distance_cache[destination] = dist
        return dist


@dataclass(frozen=True)
class ServiceTask:
    id: int
    node: int
    duration: int = 1
    earliest: int = 0
    latest: int = 10**9


@dataclass(frozen=True)
class CompletionState:
    completed_prefix: int = 0
    frontier_mask: int = 0
    active_task: int = -1
    remaining_service_time: int = 0


@dataclass(frozen=True)
class AgentState:
    node: int
    time: int
    completion: CompletionState
    previous_node: int = -1
    arrived: bool = False


@dataclass(frozen=True)
class Agent:
    id: int
    origin: int
    destination: int
    release: int
    deadline: int
    tasks: Tuple[ServiceTask, ...] = ()
    move_cost: float = 1.0
    wait_cost: float = 0.25
    service_cost: float = 0.1
    tardy_cost: float = 2.0


@dataclass
class Trajectory:
    agent_ids: Tuple[int, ...]
    states_by_agent: Dict[int, List[AgentState]]
    footprint: FrozenSet[ResourceKey]
    physical_cost: float
    priced_cost: float
    signature: Tuple
    generated_states: int = 0
    scanned_transitions: int = 0
    screened_states: int = 0

    @property
    def arrival_times(self) -> Dict[int, int]:
        out: Dict[int, int] = {}
        for aid, states in self.states_by_agent.items():
            for st in states:
                if st.arrived:
                    out[aid] = st.time
                    break
            else:
                out[aid] = states[-1].time
        return out


@dataclass
class GroupSuperColumn:
    group: Tuple[int, ...]
    trajectory: Trajectory
    reduced_cost: float = math.inf
    quadratic_value: float = math.inf


def undirected_edge_resource(u: int, v: int) -> int:
    a, b = sorted((u, v))
    return a * 100000 + b


def transition_footprint(prev: AgentState, nxt: AgentState) -> FrozenSet[ResourceKey]:
    if nxt.node < 0:
        return frozenset()
    keys = {("node", nxt.node, nxt.time)}
    if prev.node >= 0 and nxt.node >= 0 and prev.node != nxt.node:
        keys.add(("edge", undirected_edge_resource(prev.node, nxt.node), prev.time))
    return frozenset(keys)


def build_line_network(length: int = 5) -> Network:
    nodes = {i: Node(i, float(i), 0.0) for i in range(length)}
    arcs: List[Arc] = []
    aid = 0
    for i in range(length - 1):
        arcs.append(Arc(aid, i, i + 1, 1.0, undirected_edge_resource(i, i + 1))); aid += 1
        arcs.append(Arc(aid, i + 1, i, 1.0, undirected_edge_resource(i, i + 1))); aid += 1
    return Network(nodes, arcs)


def build_cross_network(arm: int = 2) -> Network:
    """A plus-shaped grid with fixed-path-like arms and a shared center."""
    coords: List[Tuple[int, int]] = []
    for x in range(-arm, arm + 1):
        coords.append((x, 0))
    for y in range(-arm, arm + 1):
        if y != 0:
            coords.append((0, y))
    coords = sorted(set(coords))
    idx = {c: i for i, c in enumerate(coords)}
    nodes = {idx[c]: Node(idx[c], float(c[0]), float(c[1])) for c in coords}
    arcs: List[Arc] = []
    aid = 0
    for c, u in idx.items():
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            cc = (c[0] + d[0], c[1] + d[1])
            if cc in idx:
                v = idx[cc]
                arcs.append(Arc(aid, u, v, 1.0, undirected_edge_resource(u, v)))
                aid += 1
    return Network(nodes, arcs)


def find_node_by_coord(network: Network, x: int, y: int) -> int:
    for node in network.nodes.values():
        if int(node.x) == x and int(node.y) == y:
            return node.id
    raise KeyError((x, y))
