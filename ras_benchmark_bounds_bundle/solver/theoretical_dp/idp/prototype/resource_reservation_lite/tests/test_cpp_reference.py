from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from prototype.resource_reservation_lite.branch_and_bound import ReservationBranchAndBound
from prototype.resource_reservation_lite.export_tsv import export_tsv
from prototype.resource_reservation_lite.io import load_instance

ROOT = Path(__file__).resolve().parents[1]
CPP = ROOT / 'cpp'


@unittest.skipUnless(shutil.which('g++'), 'g++ is required for the C++ regression')
class CppRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['make', '-C', str(CPP), 'reservation_bb'], check=True, stdout=subprocess.DEVNULL)

    def test_cpp_matches_python_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            for case in sorted((ROOT / 'cases').glob('case0*.json')):
                instance = load_instance(case)
                py = ReservationBranchAndBound(instance).solve()
                tsv = export_tsv(case, temp / f'{case.stem}.tsv')
                out = temp / f'{case.stem}.json'
                tree = temp / f'{case.stem}.txt'
                subprocess.run([
                    str(CPP / 'reservation_bb'), '--input', str(tsv), '--output', str(out), '--tree', str(tree)
                ], check=True, stdout=subprocess.DEVNULL)
                cpp = json.loads(out.read_text())
                self.assertEqual(cpp['status'], py.status, case.name)
                self.assertAlmostEqual(cpp['root_lb'], py.root_lb, places=6, msg=case.name)
                self.assertAlmostEqual(cpp['incumbent_ub'], py.incumbent_ub, places=6, msg=case.name)
                self.assertAlmostEqual(cpp['total_delay'], py.best_schedule.total_delay, places=6, msg=case.name)
                self.assertEqual(cpp['nodes_generated'], py.nodes_generated, case.name)


if __name__ == '__main__':
    unittest.main()
