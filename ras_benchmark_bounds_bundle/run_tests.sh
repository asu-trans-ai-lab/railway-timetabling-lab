#!/bin/sh
set -eu
cd "$(dirname "$0")"
PYTHONDONTWRITEBYTECODE=1 python3 -B test_bundle.py
