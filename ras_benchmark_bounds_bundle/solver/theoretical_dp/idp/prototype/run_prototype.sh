#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
python -m unittest prototype.resource_reservation_lite.tests.test_lite prototype.resource_reservation_lite.tests.test_cpp_reference -v
python -m prototype.resource_reservation_lite.run_all_cases --output prototype/results/idealized
