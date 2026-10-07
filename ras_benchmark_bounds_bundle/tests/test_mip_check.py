"""The MIP check (meeting of Oct 4): the cumulative-flow MIP reproduces optima the engines prove, and with a frozen
upper bound the serial and the parallel B&B examine the same tree."""
import subprocess
import tempfile
from pathlib import Path

from adapters.control_cells import identify_bottleneck
from adapters.toy_corridor import physical_check, toy_model
from solver.python.e3_bb import read_open, write_share
from solver.python.mip_cumflow import solve_isolated as solve
from solver.python.siding_kernel import build
from solver.python.siding_model import write_instance
from solver.python.siding_validate import validate
from experiments.check_fixed_ub import RES, TREE


def test_mip_reproduces_the_toy_optimum():
    m = toy_model(3, cells=10)
    r = solve(m, max(t.release for t in m.trains) + 160, seconds=60)
    errors, total, _ = validate(m, r["schedule"])
    assert r["proven"] and r["value"] == 174 == total and not errors + physical_check(m, r["schedule"]), r


def test_fixed_upper_bound_gives_the_same_tree_serial_and_parallel():
    work = Path(tempfile.mkdtemp())
    inst = write_instance(identify_bottleneck(toy_model(5, cells=10)), work / "toy.txt")
    base = [str(build()), str(inst), "--mode", "bb", "--ub", "340", *TREE, "--freeze-ub", "--time-cap", "60"]
    examined = lambda out: int(RES.search(out).group(4))          # noqa: E731
    serial = examined(subprocess.run(base, capture_output=True, text=True, check=True).stdout)
    split_out = subprocess.run(base + ["--split", "64", str(work / "open.txt")], capture_output=True, text=True).stdout
    duals: dict = {}
    pool = read_open(work / "open.txt", "s", duals)
    total = examined(split_out)
    for w in range(8):
        if not pool[w::8]:
            continue
        write_share(work / f"s{w}.txt", pool[w::8], duals)
        total += examined(subprocess.run(base + ["--nodes-in", str(work / f"s{w}.txt")], capture_output=True,
                                         text=True, check=True).stdout)
    assert serial == total, (serial, total)
