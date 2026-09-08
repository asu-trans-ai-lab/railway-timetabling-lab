from __future__ import annotations

import json
from pathlib import Path
import unittest

from prototype.resource_reservation_lite.branch_and_bound import ReservationBranchAndBound
from prototype.resource_reservation_lite.conflicts import all_conflicts
from prototype.resource_reservation_lite.io import load_instance
from prototype.resource_reservation_lite.search import beam_search

ROOT = Path(__file__).resolve().parents[1]


class LitePrototypeTests(unittest.TestCase):
    def _case(self, name: str):
        return load_instance(ROOT / "cases" / name)

    def test_case01_root_feasible(self):
        instance = self._case("case01_one_train_one_resource.json")
        result = ReservationBranchAndBound(instance).solve()
        self.assertEqual(result.status, "PROVEN_OPTIMAL")
        self.assertAlmostEqual(result.incumbent_ub, 5.0)
        self.assertEqual(all_conflicts(instance, result.best_schedule), [])

    def test_case02_binary_branch(self):
        instance = self._case("case02_two_trains_one_resource.json")
        result = ReservationBranchAndBound(instance).solve()
        self.assertEqual(result.status, "PROVEN_OPTIMAL")
        self.assertAlmostEqual(result.root_lb, 10.0)
        self.assertAlmostEqual(result.incumbent_ub, 18.0)
        self.assertAlmostEqual(result.best_schedule.total_delay, 8.0)
        branch_rows = [row for row in result.trace if row["action"] == "branch"]
        self.assertTrue(branch_rows)
        self.assertIn("R1", branch_rows[0]["selected_conflict"])

    def test_case03_recursive(self):
        instance = self._case("case03_three_trains_one_resource.json")
        result = ReservationBranchAndBound(instance).solve()
        self.assertEqual(result.status, "PROVEN_OPTIMAL")
        self.assertAlmostEqual(result.incumbent_ub, 39.0)
        self.assertAlmostEqual(result.best_schedule.total_delay, 24.0)
        self.assertGreater(result.nodes_generated, 2)

    def test_case04_forward_propagation(self):
        instance = self._case("case04_two_trains_three_resources.json")
        result = ReservationBranchAndBound(instance).solve()
        self.assertEqual(result.status, "PROVEN_OPTIMAL")
        self.assertAlmostEqual(result.incumbent_ub, 38.0)
        schedule = result.best_schedule
        starts_b = [schedule.timings[key].start_min for key in sorted(schedule.timings) if key.train_id == "B"]
        self.assertEqual(starts_b, sorted(starts_b))
        self.assertEqual(all_conflicts(instance, schedule), [])

    def test_case05_and_beam(self):
        instance = self._case("case05_three_trains_three_resources.json")
        result = ReservationBranchAndBound(instance).solve()
        self.assertEqual(result.status, "PROVEN_OPTIMAL")
        self.assertAlmostEqual(result.incumbent_ub, 69.0)
        beam = beam_search(instance, width=8)
        self.assertIsNotNone(beam.schedule)
        self.assertAlmostEqual(beam.schedule.objective_total_travel, 69.0)

    def test_time_step_snap(self):
        instance = load_instance(
            ROOT / "cases" / "case02_two_trains_one_resource.json",
            time_step_min=5.0,
            safety_headway_min=3.0,
        )
        self.assertAlmostEqual(instance.effective_headway_min, 5.0)
        result = ReservationBranchAndBound(instance).solve()
        self.assertEqual(result.status, "PROVEN_OPTIMAL")
        self.assertAlmostEqual(result.incumbent_ub, 20.0)


if __name__ == "__main__":
    unittest.main()
