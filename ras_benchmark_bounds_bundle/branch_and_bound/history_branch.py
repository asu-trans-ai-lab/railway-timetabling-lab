"""Pairwise complete-trajectory certificates for the audited overtaking rule.

Physics are rechecked on the two COMPLETE paths. The authoritative predicate
only reads those paths and fixed arc metadata; no third train can legalize them.
Local transition certificates remain in conflict_branch.py unchanged.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
from . import conflict_branch as local
from dp.trajectory_nogood import Trajectory,TrajectoryNoGood,trajectory_context
from .validator_adapter import certified_upper_bound
from lagrangian_relaxation.physical_constraint_model import Event

OVERTAKE='illegal-main-track-overtake'

@dataclass(frozen=True)
class HistoryBranch(local.ConflictBranch):
    nogood_i: TrajectoryNoGood
    nogood_j: TrajectoryNoGood

    def restriction_for(self,train_id):
        if train_id==self.train_i: return self.nogood_i.row()
        if train_id==self.train_j: return self.nogood_j.row()
        raise ValueError('train not part of complete-trajectory certificate')

    def children(self,parent):
        children=tuple(parent.with_forbidden_trajectory(ng) for ng in (self.nogood_i,self.nogood_j))
        if any(c==parent for c in children):
            raise RuntimeError('selected history pair regenerated an inherited no-good')
        return children

    def pair_present(self,paths):
        return self.nogood_i.trajectory.matches(paths[self.train_i]) and self.nogood_j.trajectory.matches(paths[self.train_j])


def build(edge,*,paths,trains,arcs,mow,config,domains,parent_id,proof_scope,validation_args):
    from . import soft_lagrangian
    if edge['conflict_type']!=OVERTAKE: raise ValueError('not an audited overtaking rule')
    ti,tj=str(edge['train_i']),str(edge['train_j'])
    by_id={t.train_id:t for t in trains}
    if ti==tj or ti not in by_id or tj not in by_id: raise ValueError('invalid overtaking pair')
    pair={t:paths[t] for t in (ti,tj)}
    value,report=certified_upper_bound(pair,trains=[by_id[ti],by_id[tj]],**validation_args)
    if report.get('errors') or value is not None:
        raise ValueError('complete pair lacks a valid physical infeasibility certificate')
    records=local.trusted_records(report.get('conflicts') or [])
    if dict(edge) not in records:
        raise ValueError('reported overtaking not reproduced by the complete pair alone')
    trajectories=[Trajectory.from_result(pair[t],config,
                   trajectory_context(arcs,by_id[t],mow,config,domains.get(t))) for t in (ti,tj)]
    ident=f'{ti}:{trajectories[0].signature}|{tj}:{trajectories[1].signature}|{OVERTAKE}'
    sha=hashlib.sha256(Path(soft_lagrangian.__file__).read_bytes()).hexdigest()
    nogoods=[TrajectoryNoGood(p,parent_id,ident,proof_scope,validator_sha256=sha) for p in trajectories]
    events=[Event(int(edge['resource']),edge[f'direction_{s}']=='AB',float(edge[f'entry_{s}']),float(edge[f'exit_{s}'])) for s in ('i','j')]
    return HistoryBranch(ident,ti,tj,int(edge['resource']),OVERTAKE,*events,config.safety_headway,
        'The unchanged pairwise validator rejects these two complete trajectories independently of all other trains. '
        'Every feasible parent schedule changes at least one complete trajectory; both negative children cover it.',*nogoods,
        headway_model=config.headway_model)


def select(edges,*,enabled,**kwargs):
    ordered=sorted(edges,key=lambda e:(min(float(e['entry_i']),float(e['entry_j'])),str(e['conflict_type']),
        str(e['train_i']),str(e['train_j']),int(e['resource']),float(e['exit_i']),float(e['exit_j'])))
    rejected,chosen=[],None
    for edge in ordered:
        try:
            if enabled and edge['conflict_type']==OVERTAKE:
                conflict=build(edge,**kwargs)
            else:
                conflict=local.build_conflict(edge,time_step=kwargs['config'].time_step,
                    arcs=kwargs['arcs'],headway=kwargs['config'].safety_headway,
                    headway_model=kwargs['config'].headway_model)
        except ValueError as exc:
            rejected.append({**dict(edge),'reason':str(exc),'status':'UNSUPPORTED_CONFLICT'})
        else:
            if chosen is None: chosen=conflict
    return chosen,rejected
