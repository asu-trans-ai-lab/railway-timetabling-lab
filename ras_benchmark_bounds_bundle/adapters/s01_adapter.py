"""The S01 corridor (Irving-Galewood, 381.57 miles, 78 sections, 25 of them double track; waiting only at 21 points
about 20 miles apart and at the origin; H = 3) as a resource-chain model for E2 / E3.

The S01 instances are not part of this repository; pass the directory of one instance (params.tsv, resources.tsv,
trains.tsv in the Sep 18 bottleneck_exact_dp format). Physical rules = that validator (validator/s01/):
  * eastbound runs sections 0..77, westbound 77..0; a task holds its section over [start, end + H) with capacity 1 or 2;
  * a wait before task k > 0 is legal only at a node with a berth (node = section for eastbound, section + 1 for
    westbound); the train stands on the berth and holds no section (berth capacity >= trains: never binding);
  * origin waits are always legal and hold nothing; OBJ-E = sum(end of last task - release), OBJ-D = OBJ-E - TT0.
In the kernel's terms: one resource per section (tracks = capacity), terminal origins, the leg before a berth node a
pocket (may_stand 2). alpha = beta = 1, so the kernel's cost is OBJ-E.
"""
from __future__ import annotations

import csv
import subprocess
from pathlib import Path

from solver.python.siding_model import Model, Resource, Train

PACKAGE = Path(__file__).resolve().parents[1]
VALIDATOR_SRC = PACKAGE / "validator" / "s01"
VALIDATOR = VALIDATOR_SRC / "build" / "s01_validate"


def load(inst_dir: Path) -> dict:
    inst_dir = Path(inst_dir)
    par = {r["key"]: r["value"] for r in csv.DictReader(open(inst_dir / "params.tsv"), delimiter="\t")}
    S, H = int(par["sections"]), int(par["headway"])
    tracks, berths = [0] * S, [0] * (S + 1)
    for r in csv.DictReader(open(inst_dir / "resources.tsv"), delimiter="\t"):
        (tracks if r["kind"] == "track" else berths)[int(r["id"])] = int(r["capacity"])
    trains = [dict(name=r["id"], dir=int(r["direction"]), release=int(r["release"]),
                   run=[int(x) for x in r["run_by_physical_segment"].split(",")])
              for r in csv.DictReader(open(inst_dir / "trains.tsv"), delimiter="\t")]
    assert all(c in (1, 2) for c in tracks)
    assert all(c == 0 or c >= len(trains) for c in berths), "berth capacity could bind"
    return dict(name=par["name"], S=S, H=H, tracks=tracks, berths=berths, trains=trains)


def model_of(inst_dir: Path) -> Model:
    m = load(inst_dir)
    resources = [Resource(f"s{s}", "", "", m["tracks"][s], False, frozenset()) for s in range(m["S"])]
    trains = []
    for t in m["trains"]:
        secs = list(range(m["S"])) if t["dir"] == 1 else list(range(m["S"] - 1, -1, -1))
        path = []
        for k, s in enumerate(secs):
            stand = 0
            if k + 1 < len(secs):                       # the node between this section and the next one
                node = secs[k + 1] if t["dir"] == 1 else secs[k + 1] + 1
                stand = 2 if m["berths"][node] > 0 else 0
            path.append((s, t["run"][s], stand))
        trains.append(Train(t["name"], t["release"], "", "", t["dir"], True, path, []))
    return Model(m["name"], m["H"], 1, 1, resources, trains, [])


def stretches(inst_dir: Path) -> list[dict]:
    """Single-track stretches without a passing place (no berth inside, no double track), ranked by the occupancy
    demand sum over trains of (running time inside + H): the first is the identified bottleneck."""
    m = load(inst_dir)
    blocks, cur = [], []
    for s in range(m["S"]):
        if m["tracks"][s] == 1 and (not cur or m["berths"][s] == 0):
            cur.append(s)
        else:
            if cur:
                blocks.append(cur)
            cur = [s] if m["tracks"][s] == 1 else []
    if cur:
        blocks.append(cur)
    rows = [dict(sections=b, demand=sum(sum(t["run"][s] for s in b) + m["H"] for t in m["trains"])) for b in blocks]
    return sorted(rows, key=lambda r: -r["demand"])


def tt0(inst_dir: Path) -> int:
    return sum(sum(t["run"]) for t in load(inst_dir)["trains"])


def write_s01_schedule(model: Model, legs_by_train: dict[int, tuple], path: Path) -> Path:
    """The validator's schedule: one task per section, [start, start + run]."""
    with open(path, "w") as f:
        f.write("train\tindex\tsegment\tstart\tend\n")
        for k, t in enumerate(model.trains):
            for i, (r, e, _x) in enumerate(legs_by_train[k]):
                f.write(f"{t.train_id}\t{i}\t{r}\t{e}\t{e + t.path[i][1]}\n")
    return Path(path)


def build_validator() -> Path:
    srcs = sorted(VALIDATOR_SRC.glob("*.cpp"))
    if not VALIDATOR.exists() or VALIDATOR.stat().st_mtime < max(s.stat().st_mtime for s in srcs):
        VALIDATOR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["c++", "-std=c++17", "-O2", *map(str, srcs), "-o", str(VALIDATOR)], check=True)
    return VALIDATOR


def validate_s01(inst_dir: Path, schedule: Path) -> tuple[int | None, str]:
    """(OBJ-D = total wait, or None when the validator rejects the schedule; its report)."""
    p = subprocess.run([str(build_validator()), "validate", "--data", str(inst_dir), "--schedule", str(schedule)],
                       capture_output=True, text=True)
    kv = dict(line.split("\t", 1) for line in p.stdout.splitlines() if "\t" in line)
    ok = p.returncode == 0 and kv.get("validation") == "PASS"
    return (int(kv["wait"]) if ok else None), p.stdout + p.stderr
