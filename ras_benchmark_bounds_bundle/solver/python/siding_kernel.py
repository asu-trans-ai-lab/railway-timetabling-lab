"""Build and call the C++ kernel of E2 and E3 (solver/cpp/siding_lr.cpp).

Modes used here: `ub` (greedy insertion, the engines' first UB), `price` (E2's pricing: every train's best trip and
the Lagrangian value at given duals, optionally with meet rows and a heuristic at those prices) and `bb` (E3: branch
and bound on the Lagrangian relaxation with meet rows, block-pair branching, node heuristics and dives).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2]
SOURCE = PACKAGE / "solver" / "cpp" / "siding_lr.cpp"
BINARY = PACKAGE / "solver" / "cpp" / "build" / "siding_lr"
BUILD = ["g++", "-O2", "-std=c++17", "-Wall", "-Wextra", "-o", str(BINARY), str(SOURCE)]
RESULT_UB = re.compile(r"^RESULT mode ub UB (\d+)", re.M)
RESULT_BB = re.compile(r"^RESULT mode bb .* status (\S+) LB (\d+) UB (\d+) root (-?\d+) examined (\d+)", re.M)


def build() -> Path:
    """Compile the kernel when the binary is missing or older than the source (warnings are errors)."""
    if not BINARY.exists() or BINARY.stat().st_mtime < SOURCE.stat().st_mtime:
        BINARY.parent.mkdir(parents=True, exist_ok=True)
        tmp = BINARY.with_suffix(".new")
        finished = subprocess.run(BUILD[:-2] + [str(tmp), str(SOURCE)], capture_output=True, text=True)
        if finished.returncode != 0 or "warning" in (finished.stdout + finished.stderr).lower():
            raise RuntimeError("build failed or warned:\n" + finished.stdout + finished.stderr)
        tmp.replace(BINARY)                         # atomic: running processes keep the old binary
    return BINARY


def greedy_ub(inst: Path, out: Path) -> int:
    """Sequential insertion in release order by the train DP: no search."""
    log = subprocess.run([str(build()), str(inst), "--mode", "ub", "--time-cap", "0", "--out", str(out)],
                         capture_output=True, text=True).stdout
    return int(RESULT_UB.search(log).group(1))
