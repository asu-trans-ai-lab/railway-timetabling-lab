"""The three timetabling formulations agree (solver/python/mip_formulations.py), and the blocking MIP's two
formulations agree on the practice instance."""
from pathlib import Path

import pytest

from solver.python.mip_formulations import FORMS, solve, unit_model
from solver.python.siding_validate import validate

PACKAGE = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("form", FORMS)
def test_unit_case_is_8(form):
    m = unit_model()
    r = solve(m, form, 9, "highs", 60, 1)
    errors, total, _ = validate(m, r["schedule"])
    assert r["proven"] and r["value"] == 8 and total == 8 and not errors


@pytest.mark.parametrize("form", FORMS)
def test_toy_three_trains_cells(form):
    from adapters.toy_corridor import toy_model
    m = toy_model(3, cells=10)
    r = solve(m, form, 215, "highs", 120, 1)
    assert r["proven"] and r["value"] == 174


def test_lp_arc_equals_cumulative():
    from adapters.toy_corridor import toy_model
    m = toy_model(3, cells=10)
    a, c = solve(m, "arc", 215, "highs", 60, 1), solve(m, "cumulative", 215, "highs", 60, 1)
    assert abs(a["lp"] - c["lp"]) < 1e-6


@pytest.mark.skipif(not (PACKAGE / "data/blocking/twi_sample.json").exists(), reason="practice instance not present")
def test_blocking_arc_equals_chain():
    from solver.python.blocking_mip import load, solve as bsolve
    inst = load(PACKAGE / "data/blocking/twi_sample.json")
    a, c = bsolve(inst, "arc", "highs", 120, 1), bsolve(inst, "chain", "highs", 300, 1)
    assert a["proven"] and c["proven"] and abs(a["value"] - c["value"]) < 1e-6


@pytest.mark.parametrize("key", ["corridor:moffat", "real|D1"])
def test_shared_precedence_is_exact(key):
    """One z per shared single-track run must give the same optimum as one z per resource (a corridor with
    double-track links, and the real-speed model with parallel tracks, broke earlier sharing rules)."""
    from solver.python.siding_kernel import greedy_ub
    from solver.python.siding_model import write_instance
    import tempfile
    if key.startswith("corridor:"):
        from adapters.anl_corridor import corridor_model
        m = corridor_model(key.split(":")[1])[0]
    else:
        from experiments.full_comparison import model_of
        m = model_of(key)
    w = Path(tempfile.mkdtemp())
    U = greedy_ub(write_instance(m, w / "i.txt"), w / "dp.csv")
    a, b = solve(m, "bigm", U, "highs", 300, 1, lp=False), solve(m, "bigm_shared", U, "highs", 300, 1, lp=False)
    assert a["proven"] and b["proven"] and a["value"] == b["value"]
