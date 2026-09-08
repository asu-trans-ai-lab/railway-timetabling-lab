#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  printf '%s\n' 'Missing .venv. Run ./setup.sh first.' >&2
  exit 1
fi
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m tests.test_pipeline
