from pathlib import Path
import math

from prototype.idp_lab.global_resource_dp import solve_global_resource_dp
from prototype.idp_lab.lagrangian import run_lagrangian_relaxation
from prototype.idp_lab.physical_chain_dp import CalendarBlock, ResourceCalendar, solve_physical_chain_dp
from prototype.idp_lab.search_strategies import compare_first_ub_strategies
from prototype.resource_reservation_lite.branch_and_bound import ReservationBranchAndBound
from prototype.resource_reservation_lite.io import load_instance

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "cases"


def load(name: str):
    return load_instance(CASES / name)


def test_idp0_empty_calendar_free_run():
    inst = load("case04_two_trains_three_resources.json")
    result = solve_physical_chain_dp(inst, "A")
    assert result.feasible
    assert math.isclose(result.completion_min, 15.0)
    assert math.isclose(result.total_wait_min, 0.0)


def test_idp0_resource_calendar_pushes_train_forward():
    inst = load("case02_two_trains_one_resource.json")
    cal = ResourceCalendar.from_blocks([CalendarBlock(1, 0.0, 5.0, "A")])
    result = solve_physical_chain_dp(inst, "B", calendar=cal, horizon_min=30.0)
    assert result.feasible
    assert result.task_starts == [8.0]
    assert result.task_ends == [13.0]
    assert math.isclose(result.total_wait_min, 8.0)


def test_idp1_matches_exact_bb_on_five_cases():
    expected = {
        "case01_one_train_one_resource.json": 5.0,
        "case02_two_trains_one_resource.json": 18.0,
        "case03_three_trains_one_resource.json": 39.0,
        "case04_two_trains_three_resources.json": 38.0,
        "case05_three_trains_three_resources.json": 69.0,
    }
    for name, target in expected.items():
        inst = load(name)
        dp = solve_global_resource_dp(inst)
        bb = ReservationBranchAndBound(inst).solve()
        assert dp.feasible
        assert math.isclose(dp.objective_total_travel, target)
        assert math.isclose(dp.objective_total_travel, bb.incumbent_ub)


def test_idp3_searches_reach_finite_ub():
    inst = load("case05_three_trains_three_resources.json")
    rows = compare_first_ub_strategies(inst, beam_widths=(1, 2, 4))
    assert all(row["feasible"] for row in rows)
    assert all(row["first_ub"] is not None for row in rows)


def test_idp4_dual_is_valid_and_tight_on_case02():
    inst = load("case02_two_trains_one_resource.json")
    lr = run_lagrangian_relaxation(inst, iterations=40)
    assert lr.best_dual_bound <= lr.upper_bound + 1e-6
    assert lr.best_dual_bound >= inst.free_run_total - 1e-6
    assert lr.upper_bound - lr.best_dual_bound < 1e-4
