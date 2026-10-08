"""Cumulative-flow MIP (Meng & Zhou 2014) on the resource-chain Model, solved by HiGHS: the reference optimum the
DP / Lagrangian / B&B engines are checked against. It reads the same Model the engines read, so both sides solve the
same instance, and its timetable goes through the same validator.

Variables (binary, minute-indexed): E[k, i, t] = 1 once train k has entered leg i by minute t (i = 0..L-1), and
E[k, L, t] = 1 once it has left its last leg. Leg i is entered when leg i-1 is left (the head moves on at once), so
exit_i = entry_{i+1} and one cumulative curve per leg boundary suffices.

    monotone      E[k,i,t] <= E[k,i,t+1]
    release       E[k,0,t] = 0 for t < R_k;   E[k,L,T] = 1
    run time      E[k,i+1,t] <= E[k,i,t-p_ki]          (exit >= entry + own running time)
    no standing   E[k,i+1,t]  = E[k,i,t-p_ki]          (on a leg where the train may not stand)
    occupancy     sum_k [ S_ki(t) - X_ki(t-H) ] <= tracks_r     for every resource r, minute t
                  S = entry curve (release for a non-terminal origin), X = exit curve (entry + p for a pocket leg)
    stretches     opposing trains never overlap on a single-track stretch cut into cells (first cell entry to last
                  cell exit + H), as physical_check() requires
    cost          sum_k [ run_k + alpha * origin_k + beta * standing_k ],  each a sum of (1 - E) or (E - E') terms

Different running times enter only through p_ki, so fast and slow trains share one model.
"""
from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from solver.python.siding_model import Model

PACKAGE = Path(__file__).resolve().parents[2]


def solve_isolated(model: Model, horizon: int, seconds: float = 120.0, threads: int = 4, **kw) -> dict:
    """solve() in a fresh process: OR-Tools and highspy both ship HiGHS symbols, and either one crashes the other
    when both are loaded in one process."""
    work = Path(tempfile.mkdtemp(prefix="mip_"))
    pickle.dump(model, open(work / "model.pkl", "wb"))
    subprocess.run([sys.executable, "-m", "solver.python.mip_cumflow", str(work / "model.pkl"), str(work / "out.json"),
                    str(horizon), str(seconds), str(threads), json.dumps(kw)], cwd=PACKAGE, check=True)
    out = json.loads((work / "out.json").read_text())
    if "schedule" in out:
        out["schedule"] = {k: [tuple(leg) for leg in v] for k, v in out["schedule"].items()}
    return out


CPLEX = Path(os.environ.get("CPLEX_BIN", Path.home() / "Applications/CPLEX_Studio222/cplex/bin/arm64_osx/cplex"))


def solve(model: Model, horizon: int, seconds: float = 120.0, threads: int = 4, verbose: bool = False,
          solver: str = "highs", window_ub: int | None = None, export: str | None = None,
          mip_start: dict | None = None) -> dict:
    """export: write the model to this file (.mps / .lp, by HiGHS; the objective constant is the model's offset, so a
    solver reports OBJ-E itself) and return without solving.
    mip_start: train_id -> [entry minute of every leg] + [exit minute of the last leg], a validated timetable given to CPLEX
    as its MIP start (an .mst file).
    window_ub: a known timetable value UB. Every train's own delay is at most UB - TT0 in any timetable of value <= UB
    (all delays are >= 0), so each cumulative curve is forced to 1 from earliest + (UB - TT0) on, and the objective is
    capped at UB. solver: "highs" (in process) or "cplex" (the model is written as MPS and solved by CPLEX)."""
    import highspy
    H, T = model.headway, horizon
    idx = {}                                   # (k, i, t) -> column
    lo, hi = [], []
    earliest = {}
    for k, tr in enumerate(model.trains):
        acc = tr.release
        for i in range(len(tr.path) + 1):
            earliest[(k, i)] = acc
            if i < len(tr.path):
                acc += tr.path[i][1]
            for t in range(T + 1):
                idx[(k, i, t)] = len(lo)
                lo.append(0.0)
                hi.append(0.0 if t < earliest[(k, i)] else 1.0)
        lo[idx[(k, len(tr.path), T)]] = 1.0    # every train has arrived by the horizon
    if window_ub is not None:
        slack = window_ub - sum(sum(p for _, p, _ in tr.path) for tr in model.trains)
        for (k, i, t), c in idx.items():
            if t >= earliest[(k, i)] + slack:
                lo[c] = 1.0
    if any(earliest[(k, len(tr.path))] > T for k, tr in enumerate(model.trains)):
        return {"status": "HORIZON_TOO_SHORT"}

    rows_lo, rows_hi, starts, cols, vals = [], [], [], [], []

    def row(terms, l, u):
        starts.append(len(cols))
        for c, v in terms:
            cols.append(c)
            vals.append(v)
        rows_lo.append(l)
        rows_hi.append(u)

    inf = highspy.kHighsInf
    E = lambda k, i, t: idx.get((k, i, t)) if t >= 0 else None   # noqa: E731
    for k, tr in enumerate(model.trains):
        L = len(tr.path)
        for i in range(L + 1):
            for t in range(T):
                row([(E(k, i, t), 1.0), (E(k, i, t + 1), -1.0)], -inf, 0.0)
        for i, (r, p, stand) in enumerate(tr.path):
            may_stand = int(stand) in (1, 2) or model.resources[r].siding
            for t in range(T + 1):
                prev = E(k, i, t - p)
                terms = [(E(k, i + 1, t), 1.0)] + ([(prev, -1.0)] if prev is not None else [])
                row(terms, -inf if may_stand else 0.0, 0.0)

    # occupancy: held over [start, end + H)
    holds = defaultdict(list)                  # r -> [(k, start curve, end curve, end shift)]
    for k, tr in enumerate(model.trains):
        for i, (r, p, stand) in enumerate(tr.path):
            start = ("release", tr.release) if (i == 0 and not tr.terminal_origin) else ("curve", i)
            end = (i, p) if int(stand) == 2 else (i + 1, 0)
            holds[r].append((k, start, end))
    for r, hs in holds.items():
        cap = model.resources[r].tracks
        if len(hs) <= cap:
            continue
        for t in range(T + 1):
            terms, const = [], 0.0
            for k, start, (ei, shift) in hs:
                if start[0] == "release":
                    const += 1.0 if t >= start[1] else 0.0
                else:
                    terms.append((E(k, start[1], t), 1.0))
                x = E(k, ei, t - H - shift)
                if x is not None:
                    terms.append((x, -1.0))
            row(terms, -inf, cap - const)

    # opposing trains on a single-track stretch cut into cells
    stretch = defaultdict(list)
    for r, res in enumerate(model.resources):
        if "#" in res.name and res.tracks == 1 and not res.siding:
            stretch[res.name.split("#")[0]].append(r)
    for parent, cells in stretch.items():
        span = {}
        for k, tr in enumerate(model.trains):
            on = [i for i, (r, _, _) in enumerate(tr.path) if r in cells]
            if on:
                span[k] = (min(on), max(on) + 1)
        ks = list(span)
        for a in range(len(ks)):
            for b in range(a + 1, len(ks)):
                ka, kb = ks[a], ks[b]
                if model.trains[ka].direction == model.trains[kb].direction:
                    continue
                for t in range(T + 1):
                    terms = []
                    for k in (ka, kb):
                        first, after = span[k]
                        terms.append((E(k, first, t), 1.0))
                        _, p_last, s_last = model.trains[k].path[after - 1]
                        # released at the exit of the last cell, or at its arrival when it then stands clear (pocket)
                        x = E(k, after - 1, t - H - p_last) if int(s_last) == 2 else E(k, after, t - H)
                        if x is not None:
                            terms.append((x, -1.0))
                    row(terms, -inf, 1.0)

    # cost: run + alpha * origin + beta * standing
    obj = np.zeros(len(lo))
    const = 0.0
    for k, tr in enumerate(model.trains):
        L, run = len(tr.path), sum(p for _, p, _ in tr.path)
        const += run - model.beta * run
        for t in range(T + 1):
            if t >= tr.release:                # origin minutes: sum over t >= R of (1 - E0)
                const += model.alpha
                obj[E(k, 0, t)] -= model.alpha
            obj[E(k, 0, t)] += model.beta      # elapsed after departure: sum of (E0 - EL)
            obj[E(k, L, t)] -= model.beta

    if window_ub is not None:                  # objective cut: value <= UB
        nz = np.nonzero(obj)[0]
        row([(int(c), float(obj[c])) for c in nz], -inf, window_ub - const)

    h = highspy.Highs()
    h.setOptionValue("output_flag", verbose)
    h.setOptionValue("time_limit", float(seconds))
    h.setOptionValue("threads", threads)
    h.setOptionValue("mip_rel_gap", 0.0)
    n = len(lo)
    h.addVars(n, np.array(lo), np.array(hi))
    h.changeColsIntegrality(n, np.arange(n, dtype=np.int32), np.array([highspy.HighsVarType.kInteger] * n))
    h.changeColsCost(n, np.arange(n, dtype=np.int32), obj)
    starts.append(len(cols))
    h.addRows(len(rows_lo), np.array(rows_lo), np.array(rows_hi), len(cols), np.array(starts[:-1], dtype=np.int32),
              np.array(cols, dtype=np.int32), np.array(vals))
    if export:
        h.changeObjectiveOffset(const)
        h.writeModel(str(export))
        return {"exported": str(export), "vars": n, "rows": len(rows_lo), "constant": const}
    x0 = None
    if mip_start is not None:                      # the curves of the start timetable: E = 1 from each event on
        x0 = np.zeros(n)
        for k, tr in enumerate(model.trains):
            times = mip_start[tr.train_id]
            for i in range(len(tr.path) + 1):
                for t in range(times[i], T + 1):
                    x0[idx[(k, i, t)]] = 1.0
    t0 = time.time()
    if solver == "cplex":
        status, objv, bnd, x = run_cplex(h, seconds, threads, x0)
        out = {"status": status, "seconds": round(time.time() - t0, 2), "vars": n, "rows": len(rows_lo), "solver": "cplex"}
        if x is None:
            return out
        value, bound = objv + const, bnd + const
        gap = (value - bound) / max(1e-9, abs(value))
    else:
        if x0 is not None:                         # the same MIP start for HiGHS
            sol = highspy.HighsSolution()
            sol.col_value = list(x0)
            sol.value_valid = True
            h.setSolution(sol)
        h.run()
        status = h.modelStatusToString(h.getModelStatus())
        info = h.getInfo()
        out = {"status": status, "seconds": round(time.time() - t0, 2), "vars": n, "rows": len(rows_lo), "solver": "highs"}
        if info.primal_solution_status != 2:       # no feasible point
            return out
        x = np.array(h.getSolution().col_value)
        value = info.objective_function_value + const
        bound = info.mip_dual_bound + const
        gap = info.mip_gap
    sched = {}
    for k, tr in enumerate(model.trains):
        curve = lambda i: next(t for t in range(T + 1) if x[idx[(k, i, t)]] > 0.5)   # noqa: E731
        times = [curve(i) for i in range(len(tr.path) + 1)]
        sched[tr.train_id] = [(i, model.resources[r].name, times[i], times[i + 1]) for i, (r, _, _) in enumerate(tr.path)]
    out.update(value=int(round(value)), bound=float(bound), gap=float(gap), schedule=sched,
               proven=status in ("Optimal", "integer optimal solution"))
    return out


def write_mst(path: Path, mps: Path, x0) -> None:
    """A CPLEX MIP start: the column names in the order of the MPS COLUMNS section, with the start values."""
    names, seen, section = [], set(), None
    with open(mps) as f:
        for line in f:
            if not line.startswith(" "):
                section = line.split()[0] if line.strip() else section
                if section == "RHS":
                    break
                continue
            if section == "COLUMNS":
                name = line.split()[0]
                if name not in seen and name != "MARKER" and "'MARKER'" not in line:
                    seen.add(name)
                    names.append(name)
    with open(path, "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<CPLEXSolutions version="1.2">\n'
                '<CPLEXSolution version="1.2">\n<header problemName="m" solutionName="start" solutionIndex="0"/>\n'
                '<variables>\n')
        for j, (name, v) in enumerate(zip(names, x0)):
            f.write(f'<variable name="{name}" index="{j}" value="{int(round(v))}"/>\n')
        f.write("</variables>\n</CPLEXSolution>\n</CPLEXSolutions>\n")


def run_cplex(h, seconds, threads, x0=None):
    """Write the HiGHS model as MPS, solve it with the CPLEX interactive optimizer (from the MIP start x0, if given),
    read its .sol (XML)."""
    import xml.etree.ElementTree as ET
    work = Path(tempfile.mkdtemp(prefix="cplex_"))
    h.writeModel(str(work / "m.mps"))
    cmds = [f"read {work / 'm.mps'}"]
    if x0 is not None:
        write_mst(work / "start.mst", work / "m.mps", x0)
        cmds.append(f"read {work / 'start.mst'}")
    log = subprocess.run([str(CPLEX), "-c", *cmds, f"set timelimit {seconds}", f"set threads {threads}",
                          "set mip tolerances mipgap 0", "optimize", f"write {work / 'm.sol'}", "quit"],
                         capture_output=True, text=True).stdout
    (work / "cplex.log").write_text(log)
    if not (work / "m.sol").exists():
        return "no solution", None, None, None
    root = ET.parse(work / "m.sol").getroot()
    head = root.find("header").attrib
    xs = np.zeros(h.getNumCol())
    for v in root.find("variables"):
        xs[int(v.attrib["index"])] = float(v.attrib["value"])
    import re
    status = head.get("solutionStatusString", "?")
    if "optimal" in status and "tolerance" not in status:
        bnd = float(head["objectiveValue"])                # mipgap 0: proven
    else:                                                  # time limit: the last "best bound" CPLEX printed
        m = re.findall(r"Current MIP best bound =\s*([-0-9.e+]+)", log)
        bnd = float(m[-1]) if m else float("nan")
    return status, float(head["objectiveValue"]), bnd, xs


if __name__ == "__main__":
    model_path, out_path, T, secs, thr = sys.argv[1:6]
    kw = json.loads(sys.argv[6]) if len(sys.argv) > 6 else {}
    res = solve(pickle.load(open(model_path, "rb")), int(T), float(secs), int(thr), **kw)
    Path(out_path).write_text(json.dumps(res))
