"""The resource-chain model shared by E2 and E3 (solver/cpp/siding_lr.cpp) and by E1 on corridor models.

A model is a list of resources (a track segment or a stretch; `tracks` trains may hold it at once) and trains, each with
a fixed chain of legs (resource, running minutes, may_stand):
  may_stand 0  the train may not stand after this leg;
  may_stand 1  it may stand on the resource, still holding it (a station track);
  may_stand 2  a "pocket": after running the leg it stands clear (a siding or a berth) and holds nothing.
A leg is held over [entry, exit + H) (from the release on the first leg when the origin is inside the territory);
a pocket leg is held over [entry, entry + minutes + H). Cost = running + alpha * origin wait + beta * standing.
Blocks (optional): runs of single-track resources that carry phase-time rows (the identified bottleneck).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Resource:
    name: str
    west: str
    east: str
    tracks: int
    siding: bool                # a train may stand inside, holding it (may_stand 1)
    nodes: frozenset


@dataclass
class Train:
    train_id: str
    release: int
    origin: str
    dest: str
    direction: int              # +1 east, -1 west
    terminal_origin: bool       # held outside the territory (holds nothing) or inside it (holds its first resource)
    path: list[tuple[int, int, int]] = field(default_factory=list)   # (resource, minutes, may_stand) in travel order
    through: list[int] = field(default_factory=list)                 # block indices


@dataclass
class Model:
    name: str
    headway: int
    alpha: int
    beta: int
    resources: list[Resource]
    trains: list[Train]
    blocks: list[list[int]]     # runs of >= 2 consecutive single-track resources, west -> east


def write_instance(model: Model, path: Path, ub: int = 0) -> Path:
    """The token file solver/cpp/siding_lr.cpp reads."""
    lines = [f"HEADWAY {model.headway}", f"ALPHA {model.alpha}", f"BETA {model.beta}", f"UB {ub}",
             f"RESOURCES {len(model.resources)}"]
    lines += [f"{r.name} {r.tracks}" for r in model.resources]
    lines.append(f"BLOCKS {len(model.blocks)}")
    lines += [" ".join(map(str, [len(b)] + b)) for b in model.blocks]
    lines.append(f"TRAINS {len(model.trains)}")
    for t in model.trains:
        legs = " ".join(f"{i} {p} {int(s)}" for i, p, s in t.path)
        lines.append(f"{t.train_id} {t.release} {t.direction} {int(t.terminal_origin)} {len(t.path)} {legs} "
                     f"{len(t.through)} " + " ".join(map(str, t.through)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


def read_instance(path: Path) -> Model:
    """The inverse of write_instance (resources by name and track count only)."""
    it = iter(Path(path).read_text().split())
    head, resources, blocks, trains = {}, [], [], []
    for key in it:
        if key in ("HEADWAY", "ALPHA", "BETA", "UB"):
            head[key] = int(next(it))
        elif key == "RESOURCES":
            for _ in range(int(next(it))):
                name, tracks = next(it), int(next(it))
                resources.append(Resource(name, "", "", tracks, False, frozenset()))
        elif key == "BLOCKS":
            for _ in range(int(next(it))):
                blocks.append([int(next(it)) for _ in range(int(next(it)))])
        elif key == "TRAINS":
            for _ in range(int(next(it))):
                tid, rel, d, term, nlegs = next(it), int(next(it)), int(next(it)), int(next(it)), int(next(it))
                legs = [(int(next(it)), int(next(it)), int(next(it))) for _ in range(nlegs)]
                through = [int(next(it)) for _ in range(int(next(it)))]
                trains.append(Train(tid, rel, "", "", d, bool(term), legs, through))
    return Model(Path(path).stem, head["HEADWAY"], head["ALPHA"], head["BETA"], resources, trains, blocks)
