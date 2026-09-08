"""Exact complete-trajectory identity and auditable negative restrictions.

Native ticks/choice indices are identity tokens. Start and completion are explicit;
individual matching events, prefixes, and substrings are not exclusions.
"""
from __future__ import annotations
from dataclasses import asdict,dataclass
import math
from .train_domain import digest,input_fingerprint


def tick(value,step):
    if value is None or not math.isfinite(value) or not math.isfinite(step) or step<=0:
        raise ValueError('trajectory time/grid is not finite and positive')
    ratio=value/step
    if abs(ratio-round(ratio))>1e-7:
        raise ValueError('trajectory identity must use exact native grid values')
    return int(round(ratio))


def trajectory_context(arcs,train,mow,config,domain=None):
    fields=asdict(config)
    # Preserve historical legacy context tokens; new physics cannot reuse them.
    if fields.get('headway_model') == 'LEGACY_ENTRY':
        fields.pop('headway_model')
    return digest(dict(schema='complete_trajectory_v1',instance=input_fingerprint(arcs,train),
        mow=sorted([dict(w) for w in mow],key=lambda w:(w['a'],w['b'],w['start'],w['end'])),
        config=fields,domain=domain.fingerprint if domain else 'full_network'))


@dataclass(frozen=True)
class Trajectory:
    train_id: str
    origin: int
    destination: int
    departure_tick: int
    arrival_tick: int
    # (arc ID, AB flag, entry tick, exit tick, wait-choice integer)
    movements: tuple[tuple[int,bool,int,int,int], ...]
    time_step: float
    wait_step: float
    context: str
    complete: bool = True

    def __post_init__(self):
        if not self.complete or not self.train_id or not self.context:
            raise ValueError('only identified complete trajectories can be excluded')
        if any(not math.isfinite(v) or v<=0 for v in (self.time_step,self.wait_step)):
            raise ValueError('invalid trajectory grids')
        if any(type(v) is not int for v in (self.origin,self.destination,self.departure_tick,self.arrival_tick)):
            raise ValueError('trajectory identity requires integer nodes/ticks')
        if self.departure_tick<0 or self.arrival_tick<self.departure_tick:
            raise ValueError('invalid complete trajectory times')
        previous=self.departure_tick
        for a,ab,start,end,w in self.movements:
            if any(type(v) is not int for v in (a,start,end,w)):
                raise ValueError('movement identity requires native integer ticks and choices')
            if a<=0 or not isinstance(ab,bool) or start!=previous or end<=start or w<0:
                raise ValueError('non-contiguous trajectory identity')
            previous=end
        if previous!=self.arrival_tick:
            raise ValueError('arrival is not terminal completion')

    @property
    def signature(self): return digest(asdict(self))

    @classmethod
    def from_result(cls,row,config,context):
        if not row.feasible or not row.node_ids or len(row.node_ids)!=len(row.legs)+1:
            raise ValueError('cannot exclude an incomplete trajectory')
        if len(row.legs)!=len(row.ab_flags) or len(row.legs)!=len(row.waits):
            raise ValueError('trajectory direction/wait shape mismatch')
        if row.node_ids[-1] in row.node_ids[:-1]:
            raise ValueError('DP completion is absorbing at the destination')
        movements=tuple((a,bool(ab),tick(start,config.time_step),tick(end,config.time_step),tick(w,config.wait_step))
                        for (a,start,end),ab,w in zip(row.legs,row.ab_flags,row.waits))
        return cls(row.train_id,row.node_ids[0],row.node_ids[-1],tick(row.departure_min,config.time_step),
                   tick(row.arrival_min,config.time_step),movements,config.time_step,config.wait_step,context)

    def matches(self,row):
        if not row.feasible or row.train_id!=self.train_id or not row.node_ids:
            return False
        from types import SimpleNamespace
        try:
            other=Trajectory.from_result(row,SimpleNamespace(time_step=self.time_step,wait_step=self.wait_step),self.context)
        except (ValueError,OverflowError): return False
        return other==self


@dataclass(frozen=True)
class TrajectoryNoGood:
    trajectory: Trajectory
    parent_id: int
    conflict_id: str
    proof_scope: str
    certificate_type: str = 'PAIRWISE_COMPLETE_TRAJECTORY'
    validator_sha256: str = ''
    matching_semantics: str = 'anchored START + all ordered movements/waits + END equality; not substring/prefix'

    @property
    def signature(self): return self.trajectory.signature

    def row(self):
        return dict(train_id=self.trajectory.train_id,relation='FORBID_TRAJECTORY',
                    trajectory_id=self.signature,**asdict(self))
