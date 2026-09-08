#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys
import unittest


SOURCE_ROOT = Path(__file__).resolve().parent / "idp"
sys.path.insert(0, str(SOURCE_ROOT))

from prototype.idp_lab.tests import test_idp_lab


def main() -> None:
    idp_tests = [getattr(test_idp_lab, name) for name in dir(test_idp_lab) if name.startswith("test_")]
    for test in idp_tests:
        test()
    print(f"PASS: {len(idp_tests)} IDP test functions")

    suite = unittest.defaultTestLoader.loadTestsFromNames([
        "prototype.resource_reservation_lite.tests.test_lite",
        "prototype.resource_reservation_lite.tests.test_cpp_reference",
    ])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
