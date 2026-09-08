"""Static PATH-K-induced train subnetworks; independent of temporal B&B cuts.

The existing spatial generator supplies representative paths. Their directed
union is the domain: DP can recombine them and revisit nodes within the horizon.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .network_dp_interface import Arc, Train, DPResult

FULL_NETWORK = 'full_network'
PATH_K_CORRIDOR = 'path_k_corridor'
FULL_SCOPE = 'finite full-network space-time DP domain'
CORRIDOR_SCOPE = 'finite PATH-K-induced train-specific corridor space-time DP domain'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def input_fingerprint(arcs, train):
    return digest({'arcs': [asdict(a) for a in sorted(arcs.values(), key=lambda a:a.arc_id)],
                   'train': asdict(train)})


@dataclass(frozen=True)
class SpatialPath:
    arc_ids: tuple[int, ...]
    node_ids: tuple[int, ...]
    ab_flags: tuple[bool, ...]
    running_minutes: float

    @property
    def signature(self):
        return digest({'arcs': self.arc_ids, 'nodes': self.node_ids, 'directions': self.ab_flags})


@dataclass(frozen=True)
class TrainDomain:
    train_id: str
    mode: str
    allowed_movements: tuple[tuple[int, bool], ...]
    allowed_nodes: tuple[int, ...]
    source_fingerprint: str
    candidate_paths: tuple[SpatialPath, ...] = ()
    k: int | None = None
    generator: str = 'none'
    pool_size: int = 0
    enumeration_cap: int | None = None
    pool_completeness: str = 'not_applicable'

    def __post_init__(self):
        if self.mode not in (FULL_NETWORK, PATH_K_CORRIDOR):
            raise ValueError('unknown train domain mode')
        if tuple(sorted(set(self.allowed_movements))) != self.allowed_movements:
            raise ValueError('allowed movements must be canonical and unique')
        if tuple(sorted(set(self.allowed_nodes))) != self.allowed_nodes:
            raise ValueError('allowed nodes must be canonical and unique')
        if self.mode == PATH_K_CORRIDOR:
            if not isinstance(self.k, int) or isinstance(self.k, bool) or self.k < 1:
                raise ValueError('corridor K must be a positive integer')
            if len(self.candidate_paths) > self.k:
                raise ValueError('more candidates than K')
            union = tuple(sorted({(a,ab) for p in self.candidate_paths for a,ab in zip(p.arc_ids,p.ab_flags)}))
            nodes = tuple(sorted({n for p in self.candidate_paths for n in p.node_ids}))
            if union != self.allowed_movements or nodes != self.allowed_nodes:
                raise ValueError('corridor must equal the directed candidate-path union')

    @property
    def allowed_arcs(self):
        return tuple(sorted({a for a,_ in self.allowed_movements}))

    @property
    def fingerprint(self):
        return digest(asdict(self))

    @property
    def proof_scope(self):
        return FULL_SCOPE if self.mode == FULL_NETWORK else CORRIDOR_SCOPE

    def metadata(self):
        return {**asdict(self), 'fingerprint': self.fingerprint, 'proof_scope': self.proof_scope,
                'direction_policy': 'selected directed movements only',
                'allowed_arcs': self.allowed_arcs,
                'candidate_path_signatures': [p.signature for p in self.candidate_paths],
                'number_of_candidate_paths': len(self.candidate_paths),
                'number_of_corridor_nodes': len(self.allowed_nodes),
                'number_of_corridor_arcs': len(self.allowed_arcs),
                'number_of_corridor_directed_movements': len(self.allowed_movements)}

    def validate(self, arcs, train):
        if train.train_id != self.train_id or input_fingerprint(arcs,train) != self.source_fingerprint:
            raise ValueError('static train domain does not match network/train inputs')
        for p in self.candidate_paths:
            if (len(p.node_ids) != len(p.arc_ids)+1 or len(p.ab_flags) != len(p.arc_ids)
                    or not p.node_ids or p.node_ids[0] != train.origin or p.node_ids[-1] != train.destination):
                raise ValueError('candidate is not a complete directed train O-D path')
            for index,(a,ab) in enumerate(zip(p.arc_ids,p.ab_flags)):
                arc=arcs[a]
                ends=(arc.a,arc.b) if ab else (arc.b,arc.a)
                if (not ab and not arc.bidirectional) or ends != p.node_ids[index:index+2]:
                    raise ValueError('candidate direction/topology mismatch')
                if (arc.speed_ab if ab else arc.speed_ba) <= 0 or arc.length <= 0:
                    raise ValueError('candidate uses an unusable movement')
        for a,ab in self.allowed_movements:
            if a not in arcs or (not ab and not arcs[a].bidirectional):
                raise ValueError('domain contains an invalid physical direction')

    def contains(self, row: DPResult):
        if row.train_id != self.train_id: return False
        if self.mode == FULL_NETWORK: return True
        allowed=set(self.allowed_movements)
        return (set(row.node_ids) <= set(self.allowed_nodes)
                and all((a,ab) in allowed for (a,_,_),ab in zip(row.legs,row.ab_flags)))


def domain_from_paths(arcs, train, paths, *, k=None, generator='supplied spatial paths',
                      pool_size=None, enumeration_cap=None, pool_completeness='supplied'):
    paths=tuple(paths)
    domain=TrainDomain(train.train_id, PATH_K_CORRIDOR,
        tuple(sorted({(a,ab) for p in paths for a,ab in zip(p.arc_ids,p.ab_flags)})),
        tuple(sorted({n for p in paths for n in p.node_ids})), input_fingerprint(arcs,train),
        paths, k if k is not None else max(1,len(paths)), generator,
        len(paths) if pool_size is None else pool_size, enumeration_cap, pool_completeness)
    domain.validate(arcs,train)
    return domain


def spatial_path_pool(arcs, train, *, max_paths=20_000):
    """Reuse the audited deterministic capped DFS generator, without modifying it."""
    from branch_and_bound import soft_lagrangian as legacy
    if not isinstance(max_paths,int) or isinstance(max_paths,bool) or max_paths < 1:
        raise ValueError('max_paths must be positive')
    if not math.isfinite(train.smult) or train.smult <= 0:
        raise ValueError('train speed multiplier must be positive and finite')
    graph=legacy.build_graph(dict(arcs))
    # Legacy generator substitutes 1 minute for nonpositive speeds; clean DP
    # makes these movements infeasible. Remove them before spatial enumeration.
    graph={n:[e for e in es if arcs[e.arc_id].length > 0
              and math.isfinite(arcs[e.arc_id].length)
              and math.isfinite(arcs[e.arc_id].speed_ab if e.ab else arcs[e.arc_id].speed_ba)
              and (arcs[e.arc_id].speed_ab if e.ab else arcs[e.arc_id].speed_ba) > 0]
           for n,es in graph.items()}
    if train.origin not in legacy._reverse_reachable(graph,train.destination):
        return ()
    raw=legacy.enumerate_candidates(train,graph,dict(arcs),max_paths,max_paths=max_paths)
    return tuple(SpatialPath(p.arc_ids,p.node_ids,p.ab_flags,
                 sum(legacy.traversal_minutes(arcs[a],train.smult,ab) for a,ab in zip(p.arc_ids,p.ab_flags)))
                 for p in raw)


def build_train_domains(arcs, trains, *, domain_mode=FULL_NETWORK, k=None, max_paths=20_000, pools=None):
    """Build once before B&B. Fixed-cap pools may be reused for a K sweep."""
    if domain_mode not in (FULL_NETWORK,PATH_K_CORRIDOR): raise ValueError('unknown domain_mode')
    trains=list(trains)
    if len({t.train_id for t in trains}) != len(trains): raise ValueError('duplicate train IDs')
    if domain_mode == FULL_NETWORK and k is not None: raise ValueError('K requires path_k_corridor mode')
    domains={}
    for train in trains:
        if domain_mode == FULL_NETWORK:
            moves=tuple(sorted((a.arc_id,ab) for a in arcs.values() for ab in (True,False)
                        if (ab or a.bidirectional) and a.length>0 and (a.speed_ab if ab else a.speed_ba)>0))
            domains[train.train_id]=TrainDomain(train.train_id,FULL_NETWORK,moves,
                tuple(sorted({n for a in arcs.values() for n in (a.a,a.b)}|{train.origin,train.destination})),
                input_fingerprint(arcs,train))
        else:
            if not isinstance(k,int) or isinstance(k,bool) or k<1 or k>max_paths:
                raise ValueError('K must be a positive integer no greater than the fixed enumeration cap')
            pool=spatial_path_pool(arcs,train,max_paths=max_paths) if pools is None else pools[train.train_id]
            domains[train.train_id]=domain_from_paths(arcs,train,pool[:k],k=k,
                generator='soft_lagrangian.enumerate_candidates; fixed capped DFS sorted by running time',
                pool_size=len(pool),enumeration_cap=max_paths,
                pool_completeness='complete' if len(pool)<max_paths else 'cap_reached_completeness_unknown')
    return domains
