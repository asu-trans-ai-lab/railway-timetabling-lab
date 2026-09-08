#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
SOURCE_ROOT = HERE / "idp"
BUNDLE_ROOT = HERE.parents[1]
CASE_DIR = SOURCE_ROOT / "prototype" / "idp_lab" / "cases"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the preserved Zhou IDP-0 through IDP-4 driver")
    parser.add_argument("--case", help="canonical case filename or path")
    parser.add_argument("--output", type=Path, default=BUNDLE_ROOT / "results" / "theoretical_dp" / "combined")
    parser.add_argument("--lr-iterations", type=int, default=80)
    args = parser.parse_args()

    command = [
        sys.executable,
        "-m",
        "prototype.idp_lab.run_idp_lab",
        "--output",
        str(args.output.resolve()),
        "--lr-iterations",
        str(args.lr_iterations),
    ]
    if args.case:
        case = Path(args.case)
        command.extend(["--case", str(case if case.is_absolute() else CASE_DIR / case)])

    env = os.environ.copy()
    env["PYTHONPATH"] = str(SOURCE_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.run(command, cwd=SOURCE_ROOT, env=env, check=True)


if __name__ == "__main__":
    main()
