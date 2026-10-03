"""Adversarial inputs: the kernel and the Python layer must refuse what they cannot run -- with the reason and a
nonzero exit code -- and never crash, hang or silently accept it. The validators must catch every kind of broken
timetable.

    python -m tests.test_adversarial            (or: python -m pytest tests/test_adversarial.py)

Sanitizer run (not part of the test, see tests/sanitize_kernel.sh): the kernel built with -fsanitize=address,undefined
runs every mode (ub, lb, bb with phase / meet / block / cell rules, price with meet rows and the heuristic, split and
resume) on the toy corridor and D1 without a report.
"""
from __future__ import annotations

import signal
import subprocess
import tempfile
from pathlib import Path

from solver.python.siding_kernel import build

GOOD = """HEADWAY 3
ALPHA 1
BETA 1
UB 0
RESOURCES 3
A 1
B 1
C 1
BLOCKS 0
TRAINS 2
E1 0 1 1 3 0 10 0 1 20 0 2 10 0 0
W1 5 -1 1 3 2 10 0 1 20 0 0 10 0 0
"""


def mutate(old, new):
    assert GOOD.count(old) == 1, old
    return GOOD.replace(old, new)


MALFORMED = {
    "empty": "",
    "garbage": "hello world\n",
    "truncated": GOOD[:len(GOOD) // 2],
    "trains count larger than the trains given": mutate("TRAINS 2", "TRAINS 5"),
    "negative headway": mutate("HEADWAY 3", "HEADWAY -3"),
    "resource index out of range": mutate("E1 0 1 1 3 0 10", "E1 0 1 1 3 7 10"),
    "negative resource index": mutate("E1 0 1 1 3 0 10", "E1 0 1 1 3 -1 10"),
    "zero running time": mutate("E1 0 1 1 3 0 10", "E1 0 1 1 3 0 0"),
    "negative running time": mutate("E1 0 1 1 3 0 10", "E1 0 1 1 3 0 -10"),
    "negative release": mutate("E1 0 1", "E1 -50 1"),
    "release beyond any horizon": mutate("E1 0 1", "E1 100000000 1"),
    "no trains": GOOD.split("TRAINS")[0] + "TRAINS 0\n",
    "no resources": "HEADWAY 3\nALPHA 1\nBETA 1\nUB 0\nRESOURCES 0\nBLOCKS 0\nTRAINS 0\n",
    "zero tracks": mutate("\nA 1\n", "\nA 0\n"),
    "negative tracks": mutate("\nA 1\n", "\nA -2\n"),
    "duplicate train id": mutate("W1 5", "E1 5"),
    "block resource out of range": mutate("BLOCKS 0", "BLOCKS 1\n2 1 9"),
    "train passes a block that does not exist": mutate("E1 0 1 1 3 0 10 0 1 20 0 2 10 0 0",
                                                       "E1 0 1 1 3 0 10 0 1 20 0 2 10 0 1 4"),
    "stand flag not 0 / 1 / 2": mutate("E1 0 1 1 3 0 10 0", "E1 0 1 1 3 0 10 7"),
    "non-numeric field": mutate("E1 0 1", "E1 zero 1"),
    "a train with no legs": mutate("E1 0 1 1 3 0 10 0 1 20 0 2 10 0 0", "E1 0 1 1 0 0"),
    "direction 0": mutate("E1 0 1 1", "E1 0 0 1"),
    "negative alpha": mutate("ALPHA 1", "ALPHA -1"),
    "running time beyond any horizon": mutate("E1 0 1 1 3 0 10", "E1 0 1 1 3 0 100000000"),
    "unknown section": GOOD + "SPEED 80\n",
}
MODES = {"ub": ["--mode", "ub", "--time-cap", "0"],
         "lb": ["--mode", "lb", "--ub", "1000", "--iters", "20"],
         "bb": ["--mode", "bb", "--ub", "1000", "--time-cap", "3"],
         "bb meet": ["--mode", "bb", "--ub", "1000", "--meet", "--rule", "block", "--time-cap", "3"]}


def run(text: str, args: list[str], work: Path):
    f = work / "inst.txt"
    f.write_text(text)
    return subprocess.run([str(build()), str(f), *args, "--out", str(work / "out.csv")], capture_output=True,
                          text=True, timeout=60)


def test_a_valid_instance_runs_in_every_mode():
    work = Path(tempfile.mkdtemp())
    for name, args in MODES.items():
        p = run(GOOD, args, work)
        assert p.returncode == 0, (name, p.stderr)


def test_every_malformed_instance_is_refused_with_a_reason():
    work = Path(tempfile.mkdtemp())
    for case, text in MALFORMED.items():
        for name, args in MODES.items():
            p = run(text, args, work)
            assert p.returncode > 0, f"{case} / {name}: accepted (rc {p.returncode})"
            assert p.returncode != -signal.SIGSEGV and p.returncode != -signal.SIGABRT, f"{case} / {name}: crashed"
            assert "invalid instance" in p.stderr or "cannot open" in p.stderr, (case, name, p.stderr)


def test_phase_and_meet_rows_need_a_positive_headway():
    work = Path(tempfile.mkdtemp())
    h0 = mutate("HEADWAY 3", "HEADWAY 0")
    assert run(h0, MODES["bb"], work).returncode == 0          # H = 0 is a valid model without those rows
    for extra in (["--meet"], ["--phase"]):
        p = run(h0, ["--mode", "bb", "--ub", "1000", *extra, "--time-cap", "3"], work)
        assert p.returncode == 2 and "H >= 1" in p.stderr, p.stderr


def test_unknown_option_is_refused():
    work = Path(tempfile.mkdtemp())
    p = run(GOOD, ["--mode", "bb", "--ub", "1000", "--branch-everything"], work)
    assert p.returncode == 2 and "unknown option" in p.stderr


def test_the_python_layer_raises_the_kernels_reason():
    from solver.python.e3_bb import run_parallel
    from solver.python.siding_kernel import greedy_ub
    work = Path(tempfile.mkdtemp())
    f = work / "bad.txt"
    f.write_text(MALFORMED["duplicate train id"])
    try:
        greedy_ub(f, work / "g.csv")
        raise AssertionError("accepted")
    except RuntimeError as exc:
        assert "duplicate train id" in str(exc)
    try:
        run_parallel(f, work, 10, workers=0)
        raise AssertionError("accepted")
    except ValueError:
        pass


def test_the_resource_chain_validator_catches_every_kind_of_broken_timetable():
    from adapters.toy_corridor import toy_model
    from solver.python.siding_validate import validate
    m = toy_model(2)                                     # E1 at 0, W1 at 15 on 10-20-10, H = 10
    ok = {"E1": [(0, "R12", 0, 10), (1, "S2", 10, 11), (2, "R23", 11, 31), (3, "S3", 31, 32), (4, "R34", 32, 42)],
          "W1": [(0, "R34", 52, 62), (1, "S3", 62, 63), (2, "R23", 63, 83), (3, "S2", 83, 84),
                 (4, "R12", 84, 94)]}                    # W1 waits at its origin until E1 has cleared R34
    errors, total, _ = validate(m, ok)
    assert not errors, errors
    broken = {
        "check4": {**ok, "W1": [(0, "R34", 15, 25), (1, "S3", 25, 26), (2, "R23", 26, 46), (3, "S2", 46, 47),
                                (4, "R12", 47, 57)]},                                           # meets on R23
        "check1": {"E1": ok["E1"]},                                                              # a train missing
        "check2": {**ok, "E1": [(0, "R12", -5, 5)] + ok["E1"][1:]},                             # before release
        "check3": {**ok, "E1": [(0, "R12", 0, 5)] + [(1, "S2", 5, 11)] + ok["E1"][2:]},         # too fast
    }
    for want, sched in broken.items():
        errors, _, _ = validate(m, sched)
        assert any(e.startswith(want) for e in errors), (want, errors)
    stand = {**ok, "E1": [(0, "R12", 0, 10), (1, "S2", 10, 11), (2, "R23", 11, 40), (3, "S3", 40, 41),
                          (4, "R34", 41, 51)]}                                                  # stands on R23
    errors, _, _ = validate(m, stand)
    assert any("not allowed" in e for e in errors), errors


def test_the_fixed_track_validator_refuses_a_corrupted_schedule():
    from adapters.fixed_track_adapter import DATA, validate
    work = Path(tempfile.mkdtemp())
    rows = (DATA / "donors" / "D1_donor.csv").read_text().splitlines()
    assert validate("D1", DATA / "donors" / "D1_donor.csv")[0] == 2220
    header, first = rows[0], rows[1].split(",")
    first[3] = str(int(first[3]) - 50)                  # the first task starts 50 minutes before its release
    bad = work / "bad.csv"
    bad.write_text("\n".join([header, ",".join(first)] + rows[2:]) + "\n")
    value, msg = validate("D1", bad)
    assert value is None and msg


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name, flush=True)
