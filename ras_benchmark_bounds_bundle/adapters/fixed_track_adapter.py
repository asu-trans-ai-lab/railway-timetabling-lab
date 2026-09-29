"""The fixed-track RAS model (`mixed_seeded`: routes fixed down to the track, capacity 1 per track segment, H = 3,
waiting only at the origin and at the route's `wait_after` points, where the train stands clear and holds nothing).

Data: data/fixed_track/D{1,2,3}/{params,segments,trains}.tsv (the frozen fixed-route adaptation of RAS 2012 D1-D3)
and data/fixed_track/donors/ (validated schedules; E1 uses one only for its time windows).

    to_chain_instance(dataset, out)   the model as an instance of solver/cpp/siding_lr.cpp (E2 / E3): every track
                                      segment -> a resource of 1 track; wait_after[k] = 1 -> leg k is a pocket (may_stand
                                      2); terminal origins; no phase blocks
    to_package_schedule(csv, ds, out) an engine schedule (train_id,index,resource,entry,exit) -> the validator's
                                      (train_id,index,segment,start,end)
    validate(dataset, schedule)       the independent C++ validator (validator/fixed_track/) -> (OBJ-E, "PASS") or
                                      (None, message)
"""
from __future__ import annotations

import csv
import subprocess
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
DATA = PACKAGE / "data" / "fixed_track"
RAS = PACKAGE / "data"
VALIDATOR_SRC = PACKAGE / "validator" / "fixed_track" / "fixed_track_validate.cpp"
VALIDATOR = PACKAGE / "validator" / "fixed_track" / "build" / "fixed_track_validate"
TT0 = {"D1": 1970, "D2": 2960, "D3": 3054}          # free running time (OBJ-D = OBJ-E - TT0)


def rows(path, delimiter="\t"):
    return list(csv.DictReader(open(path), delimiter=delimiter))


def src(dataset) -> Path:
    """A dataset name (D1..D3) or a directory in the same format."""
    return Path(dataset) if Path(dataset).is_dir() else DATA / dataset


def trains_of(dataset):
    return [dict(name=r["train"], release=int(r["release"]), chain=[int(x) for x in r["chain"].split(",")],
                 run=[int(x) for x in r["run_times"].split(",")], wait=[int(x) for x in r["wait_after"].split(",")])
            for r in rows(src(dataset) / "trains.tsv")]


def directions(native: int) -> dict[str, int]:
    """Train direction from the RAS input (data/RAS_data-set_<d>/input_train_info.csv)."""
    return {r["train_header"]: 1 if r["direction"] == "EASTBOUND" else -1
            for r in rows(RAS / f"RAS_data-set_{native}" / "input_train_info.csv", ",")}


def to_chain_instance(dataset, out: Path, alpha: int = 1, beta: int = 1, native: int | None = None) -> Path:
    d = native or int(str(dataset)[1:])
    segs = [int(r["segment"]) for r in rows(src(dataset) / "segments.tsv")]
    assert all(int(r["capacity"]) == 1 for r in rows(src(dataset) / "segments.tsv"))
    index = {s: i for i, s in enumerate(segs)}
    headway = int({r["key"]: r["value"] for r in rows(src(dataset) / "params.tsv")}["headway"])
    direction = directions(d)
    lines = [f"HEADWAY {headway}", f"ALPHA {alpha}", f"BETA {beta}", "UB 0", f"RESOURCES {len(segs)}"]
    lines += [f"{s} 1" for s in segs]
    lines.append("BLOCKS 0")
    trains = trains_of(dataset)
    lines.append(f"TRAINS {len(trains)}")
    for t in trains:
        n = len(t["chain"])
        legs = " ".join(f"{index[s]} {p} {2 if k < n - 1 and t['wait'][k] == 1 else 0}"
                        for k, (s, p) in enumerate(zip(t["chain"], t["run"])))
        lines.append(f"{t['name']} {t['release']} {1 if direction[t['name']] > 0 else -1} 1 {n} {legs} 0")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    return out


def to_package_schedule(engine_csv: Path, dataset: str, out: Path) -> Path:
    run = {t["name"]: t["run"] for t in trains_of(dataset)}
    lines = ["train_id,index,segment,start,end"]
    for r in rows(engine_csv, ","):
        k = int(r["index"])
        start = int(r["entry"])
        lines.append(f"{r['train_id']},{k},{r['resource']},{start},{start + run[r['train_id']][k]}")
    Path(out).write_text("\n".join(lines) + "\n")
    return Path(out)


def build_validator() -> Path:
    if not VALIDATOR.exists() or VALIDATOR.stat().st_mtime < VALIDATOR_SRC.stat().st_mtime:
        VALIDATOR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["g++", "-O2", "-std=c++17", "-o", str(VALIDATOR), str(VALIDATOR_SRC)], check=True)
    return VALIDATOR


def validate(dataset: str, schedule: Path):
    """The package schedule (train_id,index,segment,start,end) through the C++ validator."""
    tsv = Path(schedule).with_suffix(".validation.tsv")
    p = subprocess.run([str(build_validator()), str(src(dataset)), str(schedule), str(tsv)], capture_output=True,
                       text=True)
    if p.returncode:
        return None, p.stderr.strip()[:300]
    kv = dict(line.split("\t", 1) for line in tsv.read_text().splitlines()[1:])
    return int(kv["total_flow"]), "PASS"
