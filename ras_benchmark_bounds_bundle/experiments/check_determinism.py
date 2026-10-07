"""Serial versus parallel, and run-to-run: E3 must return the same certified bounds whichever way it is run.

For every instance the engine can close, E3 runs `repeats` times in one process and `repeats` times split over
`workers` processes. Reported per run: status, LB, UB, nodes, seconds, and the validator's verdict on the schedule.
Expected: every run proves the same optimum (LB = UB, identical across all runs). Node counts and times may differ
between serial and parallel (the shares are searched in a different order and share the incumbent through a file);
the certificate may not.

A time-limited run that does NOT close is an anytime result: its intermediate LB / UB depend on how much of the tree
was searched by the deadline, so serial and parallel runs stopped by the same clock need not agree. Use `--seconds`
large enough for the instance to close when comparing certificates.

    python -m experiments.check_determinism [--repeats 2] [--workers 8] [--seconds 1800] [--s01 /path/to/S01/n8]
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from adapters.control_cells import identify_bottleneck
from adapters.fixed_track_adapter import to_chain_instance, to_package_schedule, validate
from adapters.toy_corridor import toy_model
from solver.python.e3_bb import STRONGEST, run_parallel, run_single
from solver.python.siding_model import write_instance
from solver.python.siding_validate import read_schedule
from solver.python.siding_validate import validate as validate_chain

PACKAGE = Path(__file__).resolve().parents[1]
OUT = PACKAGE / "results" / "determinism"


def best_file(res: dict, work: Path) -> Path | None:
    files = [Path(res["schedule"])] if "schedule" in res else \
        [Path(r["out"]) for r in res["results"]] + [work / "greedy.csv"]
    return files


def instances(s01: str | None):
    work = Path(tempfile.mkdtemp())
    out = [("fixed-track D1", to_chain_instance("D1", work / "D1.txt"), "D1", STRONGEST)]
    for n in (5, 8):
        m = identify_bottleneck(toy_model(n, cells=10))
        out.append((f"toy N={n}, 10-min cells, phase-time", write_instance(m, work / f"toy{n}.txt"), m,
                    ["--phase", "--rule", "interval", "--plunge", "20"]))
    if s01:
        from adapters.s01_adapter import model_of
        out.append((f"S01 {Path(s01).name}", write_instance(model_of(Path(s01)), work / "s01.txt"), ("s01", s01),
                    STRONGEST))
    return out


def judge(kind, files, ub) -> str:
    """The validator on the run's best schedule (the least valid one must equal the reported UB)."""
    best = None
    for f in files:
        if not f.exists():
            continue
        if kind == "D1":
            v, _ = validate("D1", to_package_schedule(f, "D1", f.with_name(f.stem + "_pkg.csv")))
        elif isinstance(kind, tuple):
            from adapters.s01_adapter import model_of, tt0, validate_s01, write_s01_schedule
            m = model_of(Path(kind[1]))
            idx = {r.name: i for i, r in enumerate(m.resources)}
            s = read_schedule(f)
            legs = {k: tuple((idx[n], e, x) for _, n, e, x in s[t.train_id]) for k, t in enumerate(m.trains)}
            d, _ = validate_s01(Path(kind[1]), write_s01_schedule(m, legs, f.with_suffix(".tsv")))
            v = None if d is None else d + tt0(Path(kind[1]))
        else:
            errors, total, _ = validate_chain(kind, read_schedule(f))
            v = None if errors else total
        if v is not None and (best is None or v < best):
            best = v
    return "PASS" if best == ub else f"FAIL (best valid schedule {best}, reported UB {ub})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seconds", type=float, default=1800)
    ap.add_argument("--s01", default=None, help="an S01 instance directory (not in this repository)")
    ap.add_argument("--only", default="", help="run only the instances whose name contains this text")
    ap.add_argument("--modes", default="serial,parallel", help="serial, parallel or both")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, inst, kind, tree in instances(args.s01):
        if args.only and args.only not in name:
            continue
        for mode in args.modes.split(","):
            for rep in range(args.repeats):
                work = Path(tempfile.mkdtemp(prefix=f"det_{mode}_{rep}_"))
                if mode == "serial":
                    res = run_single(inst, work, args.seconds, tree)
                    nodes = res["nodes"]
                else:
                    res = run_parallel(inst, work, args.seconds, workers=args.workers, tree=tree, log=lambda s: None)
                    nodes = sum(r.get("nodes", 0) for r in res["results"][1:])
                row = {"instance": name, "mode": mode, "run": rep + 1, "proven": res["proven"], "lb": res["lb"],
                       "ub": res["ub"], "nodes": nodes, "seconds": res["seconds"],
                       "validator": judge(kind, best_file(res, work), res["ub"])}
                print(json.dumps(row), flush=True)
                rows.append(row)
    verdicts = []
    for name in dict.fromkeys(r["instance"] for r in rows):
        rs = [r for r in rows if r["instance"] == name]
        same = len({(r["lb"], r["ub"]) for r in rs}) == 1 and all(r["proven"] for r in rs)
        ok = all(r["validator"] == "PASS" for r in rs)
        verdicts.append({"instance": name, "identical_certificates": same, "all_validated": ok,
                         "value": rs[0]["ub"], "runs": len(rs)})
        print(f"{name}: {'IDENTICAL' if same else 'DIFFERENT'} certificates over {len(rs)} runs "
              f"(value {rs[0]['ub']}), validator {'PASS' if ok else 'FAIL'}")
    (OUT / f"determinism{('_' + args.only.replace(' ', '_')) if args.only else ''}.json").write_text(json.dumps({"rows": rows, "verdicts": verdicts}, indent=1))
    return 0 if all(v["identical_certificates"] and v["all_validated"] for v in verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
