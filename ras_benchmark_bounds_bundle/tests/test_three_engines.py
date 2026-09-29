"""Regression tests for the three engines (E1 block-pair CP-SAT, E2 column generation, E3 B&B + DP).

    python -m tests.test_three_engines            (or: python -m pytest tests/test_three_engines.py)
    RUN_SLOW=1 ...                                also E2 on D1 (about 2 minutes)
    S01_INSTANCE=/path/to/S01/instances/n8 ...    also the S01 adapter against its validator

Known optima (proven by E1 and a brute-force certificate): fixed-track D1 2220, D2 4127, D3 4056; toy corridor
(adapters/toy_corridor.py): N = 3 174, N = 5 whole 363, N = 5 with 10- or 5-minute cells 340.
"""
from __future__ import annotations

import os
import tempfile
import subprocess
import sys
from functools import wraps
from pathlib import Path

from adapters.control_cells import block_demand, cellify, identify_bottleneck
from adapters.fixed_track_adapter import DATA, to_chain_instance, to_package_schedule, trains_of, validate
from adapters.toy_corridor import physical_check, toy_model
from solver.python.siding_model import read_instance, write_instance
from solver.python.siding_validate import read_schedule
from solver.python.siding_validate import validate as validate_chain

def isolated_native_solver(fn):
    """OR-Tools and highspy bundle different libhighs versions on macOS."""
    @wraps(fn)
    def check():
        subprocess.run([sys.executable, "-c",
                        f"from tests.test_three_engines import {fn.__name__}; {fn.__name__}.__wrapped__()"],
                       check=True)
    return check


ZSTAR_TOY = {(3, 10): 174, (5, 10): 340, (5, 5): 340, (5, None): 363}


def test_fixed_track_instance_matches_the_data():
    work = Path(tempfile.mkdtemp())
    for ds, n_trains in (("D1", 12), ("D2", 18), ("D3", 20)):
        model = read_instance(to_chain_instance(ds, work / f"{ds}.txt"))
        trains = trains_of(ds)
        assert len(model.trains) == len(trains) == n_trains
        for t, raw in zip(model.trains, trains):
            assert [p for _, p, _ in t.path] == raw["run"]
            assert [s == 2 for _, _, s in t.path[:-1]] == [w == 1 for w in raw["wait"]]


def test_e1_proves_d1():
    from solver.python.e1_blockpair import solve
    res = solve(DATA / "D1", DATA / "donors" / "D1_donor.csv", Path(tempfile.mkdtemp()),
                lambda sched: validate("D1", Path(sched)), seconds=60, workers=2)
    assert res["proven_optimal"] and res["ub_e"] == res["lb_e"] == 2220


def test_e3_proves_d1():
    from solver.python.e3_bb import run_single
    work = Path(tempfile.mkdtemp())
    res = run_single(to_chain_instance("D1", work / "D1.txt"), work, 300)
    assert res["proven"] and res["ub"] == 2220
    value, msg = validate("D1", to_package_schedule(Path(res["schedule"]), "D1", work / "pkg.csv"))
    assert (value, msg) == (2220, "PASS")


@isolated_native_solver
def test_e2_proves_d1_with_meet_cuts():
    if not os.environ.get("RUN_SLOW"):
        return
    from solver.python.e2_colgen import independent
    work = Path(tempfile.mkdtemp())
    inst = to_chain_instance("D1", work / "D1.txt")
    res = independent(read_instance(inst), inst, 300, False, "D1", log=None, meet=True, heur_every=5,
                      check=lambda m, s: ("PASS", validate_chain(m, s)[1]))
    assert res["lb"] == res["ub"] == 2220


def test_cells_keep_running_times_and_the_bottleneck_is_the_middle():
    for cells in (None, 10, 5):
        whole, split = toy_model(5), toy_model(5, cells=cells)
        for a, b in zip(whole.trains, split.trains):
            assert sum(p for _, p, _ in a.path) == sum(p for _, p, _ in b.path)
    m = identify_bottleneck(toy_model(8, cells=10))
    assert len(m.blocks) == 1 and {m.resources[r].name for r in m.blocks[0]} == {"R23#1", "R23#2"}
    demand = block_demand(cellify(toy_model(8), 5))
    assert max(demand) == demand[1]


def test_physical_check_catches_a_swap_inside_a_stretch():
    m = toy_model(2, cells=10)
    sched = {"E1": [(0, "R12", 0, 10), (1, "S2", 10, 11), (2, "R23#1", 11, 21), (3, "R23#2", 21, 31), (4, "S3", 31, 32),
                    (5, "R34", 32, 42)],
             "W1": [(0, "R34", 15, 25), (1, "S3", 25, 26), (2, "R23#2", 26, 36), (3, "R23#1", 36, 46), (4, "S2", 46, 47),
                    (5, "R12", 47, 57)]}
    assert physical_check(m, sched)


def test_e3_phase_time_proves_the_toy_optima():
    from experiments.run_toy_cells import run_e3
    for n, cells in ((3, 10), (5, 10), (5, 5)):
        r = run_e3(n, {10: "cells10", 5: "cells5"}[cells], "phase", 60)
        assert r["check"] == "PASS" and r["status"] == "PROVEN" and r["ub"] == ZSTAR_TOY[(n, cells)], r


def test_e1_on_the_toy_corridor():
    from experiments.run_toy_cells import run_e1
    for n, res, cells in ((5, "whole", None), (5, "cells10", 10)):
        r = run_e1(n, res, 30, workers=2)
        assert r["status"] == "OPTIMAL" and r["ub"] == ZSTAR_TOY[(n, cells)] and r["check"] == "PASS", r


@isolated_native_solver
def test_e2_meet_cuts_stay_below_the_optimum():
    from solver.python.e2_colgen import independent, write_legs
    for n, cells in ((5, 10), (5, None)):
        m = identify_bottleneck(toy_model(n, cells=cells))
        work = Path(tempfile.mkdtemp())
        inst = write_instance(m, work / "inst.txt")
        r = independent(m, inst, 60, True, "t", log=None, meet=True, heur_every=3)
        assert r["lb"] <= ZSTAR_TOY[(n, cells)] <= r["ub"]
        errors, total, _ = validate_chain(m, read_schedule(write_legs(m, r["schedule"], work / "s.csv")))
        assert not errors and total == r["ub"]


def test_s01_adapter():
    inst = os.environ.get("S01_INSTANCE")
    if not inst:
        return
    from adapters.s01_adapter import model_of, stretches, validate_s01, write_s01_schedule
    from solver.python.siding_kernel import greedy_ub
    work = Path(tempfile.mkdtemp())
    model = model_of(Path(inst))
    ub = greedy_ub(write_instance(model, work / "s01.txt"), work / "greedy.csv")
    idx = {r.name: i for i, r in enumerate(model.resources)}
    s = read_schedule(work / "greedy.csv")
    legs = {k: tuple((idx[n], e, x) for _, n, e, x in s[t.train_id]) for k, t in enumerate(model.trains)}
    wait, _ = validate_s01(Path(inst), write_s01_schedule(model, legs, work / "greedy.tsv"))
    assert wait is not None and wait + sum(sum(p for _, p, _ in t.path) for t in model.trains) == ub
    assert stretches(Path(inst))[0]["sections"] == [50, 51, 52]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name, flush=True)
