"""E2: kernel + LP + MILP (column generation), with meet rows as cuts and a heuristic at the master's duals.

An LP master (HiGHS) over train trips (columns), priced by the train DP of solver/cpp/siding_lr.cpp (--mode price);
optionally phase-time plans on identified bottleneck blocks (the phase DP prices them) and meet rows as cuts
(an opposing pair keeps one order along a single-track run: alpha / beta rows, z falling along the pair's runs).
Its lower bound is the Lagrangian value at the master's duals (valid for any duals); its upper bound is the greedy
insertion, the heuristic at the master's duals, or a MILP over the generated trips (HiGHS or CPLEX).

Strongest configuration (fixed-track D1-D3): independent(..., meet=True, heur_every=5, milp_solver="cplex" or "highs").
No LNS (ruin and recreate) anywhere.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import highspy
import numpy as np

from solver.python.siding_kernel import build
from solver.python.siding_model import Model
from solver.python.siding_validate import read_schedule, validate

INF = highspy.kHighsInf


def price(binary: Path, inst: Path, ub: int, duals: dict, phase: bool, windows: bool = True, meet: bool = False,
          heur_out: Path | None = None) -> dict:
    """One call of --mode price: trips, plans, pairs, L at the given duals. meet: the kernel also takes the meet
    multipliers ("A" / "B" run t) and reports the runs; heur_out: the Lagrangian heuristic at these prices (HEUR, the
    schedule written there)."""
    with tempfile.NamedTemporaryFile("w", suffix=".duals", delete=False) as f:
        for (kind, a, t), v in duals.items():
            if v > 1e-9:
                f.write(f"{kind} {a} {t} {v:.12g}\n")
        name = f.name
    cmd = [str(binary), str(inst), "--mode", "price", "--ub", str(ub), "--duals", name] + \
          (["--phase"] if phase else []) + ([] if windows else ["--no-windows"]) + (["--meet"] if meet else []) + \
          (["--price-heuristic", "--out", str(heur_out)] if heur_out is not None else [])
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    Path(name).unlink()
    res = {"trips": {}, "phase": {}, "green": {}, "pairs": {}, "windows": {}, "runs": {}, "meetpairs": {}}
    for line in out.splitlines():
        tok = line.split()
        if tok[0] == "AXIS":
            res["W"] = int(tok[1])
        elif tok[0] == "WINDOW":
            res["windows"][int(tok[1])] = int(tok[2])
        elif tok[0] == "PAIR":
            q, k, b, d, leg, length = map(int, tok[1:])
            res["pairs"][q] = (k, b, d, leg, length)
        elif tok[0] == "TRIP":
            k, n = int(tok[1]), int(tok[3])
            legs = tuple(tuple(map(int, tok[4 + 3 * i: 7 + 3 * i])) for i in range(n))
            res["trips"][k] = (float(tok[2]), legs)
        elif tok[0] == "PHASE":
            res["phase"][int(tok[1])] = float(tok[2])
            res["green"].setdefault(int(tok[1]), {0: [], 1: []})
        elif tok[0] == "G":
            b, d, s, e = map(int, tok[1:])
            res["green"][b][d].append((s, e))
        elif tok[0] == "LAGR":
            res["L"] = float(tok[1])
        elif tok[0] == "RUN":
            res["runs"][int(tok[1])] = tuple(map(int, tok[2:8]))          # i, j, ei, xi, ej, xj
        elif tok[0] == "MEETPAIR":
            res["meetpairs"][int(tok[1])] = [int(x) for x in tok[2:]]
        elif tok[0] == "HEUR":
            res["heur"] = float(tok[1])
        elif tok[0] == "EMPTY":
            raise RuntimeError("windows admit no schedule better than UB")
    return res


class Master:
    """min sum c x; one trip per train, one plan per block; cells <= tracks; in(q, t) - green(q, t) <= 0; meet cuts."""

    def __init__(self, model: Model, pairs: dict, W: int, phase: bool, runs: dict | None = None,
                 meetpairs: dict | None = None):
        self.model, self.pairs, self.W, self.phase = model, pairs, W, phase
        # meet rows as cuts: per run R of an opposing pair (i east, j west) and minute t
        #   alpha(R, t): A_j(t) - D_i(t) + z_R <= 1,   beta(R, t): A_i(t) - D_j(t) - z_R <= 0
        # A = entered R by t, D = released R by t - H; z_R in [0, 1] falls along the pair's runs (z_m >= z_m+1)
        self.runs, self.meetpairs = runs or {}, meetpairs or {}
        self.pair_of_run = {r: p for p, rs in self.meetpairs.items() for r in rs}
        self.runs_of_train = {}
        for r, (i, j, *_ ) in self.runs.items():
            self.runs_of_train.setdefault(i, []).append(r)
            self.runs_of_train.setdefault(j, []).append(r)
        self.meet_rows = {}                             # ("A" | "B", R, t) -> row
        self.rows_of_run = {}                           # R -> [(kind, t, row)]
        self.zcol = {}                                  # R -> column
        self.cols_of_train = {}                         # k -> trip columns
        self.heur_best = None                           # (value, {k: legs}) of the price heuristic, when asked
        self.h = highspy.Highs()
        self.h.setOptionValue("output_flag", False)
        self.rows = 0
        self.conv = {}
        for k in range(len(model.trains)):
            self.conv[("k", k)] = self._row(1.0, 1.0)
        self.blocks = sorted({b for (_, b, _, _, _) in pairs.values()}) if phase else []
        for b in self.blocks:
            self.conv[("b", b)] = self._row(1.0, 1.0)
        self.cell, self.couple = {}, {}                 # (r, t) -> row, (q, t) -> row
        self.cols = []                                  # ("trip", k, legs, cost) | ("plan", b, green) | ("z", R) | ("art", k, cost)
        self.seen = set()
        self.plan_green = {b: [] for b in self.blocks}  # per block: [(col, [set E, set W])]
        self.pairs_of_block = {}
        for q, (k, b, d, leg, length) in pairs.items():
            self.pairs_of_block.setdefault(b, []).append(q)
        self.pairs_of_train = {}
        for q, (k, b, d, leg, length) in pairs.items():
            self.pairs_of_train.setdefault(k, []).append(q)

    def _row(self, lo, hi, idx=(), val=()):
        self.h.addRow(lo, hi, len(idx), np.array(idx, dtype=np.int32), np.array(val, dtype=np.float64))
        self.rows += 1
        return self.rows - 1

    def trip_cost(self, k: int, legs) -> int:
        t = self.model.trains[k]
        c = self.model.alpha * (legs[0][1] - t.release)
        for (r, e, x), (_, p, _) in zip(legs, t.path):
            c += p + self.model.beta * (x - e - p)
        return c

    def holds(self, k: int, legs):
        t, H = self.model.trains[k], self.model.headway
        for i, (r, e, x) in enumerate(legs):
            start = t.release if (i == 0 and not t.terminal_origin) else e
            end = e + t.path[i][1] if t.path[i][2] == 2 else x     # 2: stands clear after the leg (a pocket)
            for tau in range(start, end + H):
                yield r, tau

    def inside(self, k: int, legs):
        for q in self.pairs_of_train.get(k, []) if self.phase else []:
            _, b, d, leg, length = self.pairs[q]
            s = legs[leg][1]
            for tau in range(s, s + length):
                yield q, tau

    def release(self, k: int, legs, i: int) -> int:
        """The minute leg i stops holding its track: exit, or entry + running time when the train stands clear."""
        t = self.model.trains[k]
        return legs[i][1] + t.path[i][1] if t.path[i][2] == 2 else legs[i][2]

    def run_times(self, k: int, legs, r: int) -> tuple[int, int]:
        """(entry into run r, release of run r) of train k on this trip."""
        i, j, ei, xi, ej, xj = self.runs[r]
        return (legs[ei][1], self.release(k, legs, xi)) if k == i else (legs[ej][1], self.release(k, legs, xj))

    def meet_coeffs(self, k: int, legs):
        """This trip's coefficients in the meet rows already in the master."""
        H = self.model.headway
        for r in self.runs_of_train.get(k, []):
            if r not in self.zcol:
                continue
            enter, leave = self.run_times(k, legs, r)
            east = k == self.runs[r][0]
            for kind, t, row in self.rows_of_run.get(r, []):
                if (kind == "A") != east:               # A: j enters (+1); B: i enters (+1)
                    if enter <= t:
                        yield row, 1.0
                elif leave + H <= t:                    # A: i released (-1); B: j released (-1)
                    yield row, -1.0

    def add_trip(self, k: int, legs) -> bool:
        key = ("trip", k, legs)
        if key in self.seen:
            return False
        self.seen.add(key)
        idx, val = [self.conv[("k", k)]], [1.0]
        for row, v in self.meet_coeffs(k, legs):
            idx.append(row); val.append(v)
        for r, tau in self.holds(k, legs):
            if (r, tau) not in self.cell:
                self.cell[(r, tau)] = self._row(-INF, float(self.model.resources[r].tracks))
            idx.append(self.cell[(r, tau)]); val.append(1.0)
        for q, tau in self.inside(k, legs):
            if (q, tau) not in self.couple:
                _, b, d, _, _ = self.pairs[q]
                pidx = [c for c, green in self.plan_green[b] if tau in green[0 if d > 0 else 1]]
                self.couple[(q, tau)] = self._row(-INF, 0.0, pidx, [-1.0] * len(pidx))
            idx.append(self.couple[(q, tau)]); val.append(1.0)
        cost = self.trip_cost(k, legs)
        self.h.addCol(float(cost), 0.0, INF, len(idx), np.array(idx, dtype=np.int32), np.array(val, dtype=np.float64))
        self.cols_of_train.setdefault(k, []).append(len(self.cols))
        self.cols.append(("trip", k, legs, cost))
        return True

    def add_plan(self, b: int, runs: dict) -> bool:
        green = [set(), set()]
        for d in (0, 1):
            for s, e in runs[d]:
                green[d].update(range(s, e))
        key = ("plan", b, frozenset(green[0]), frozenset(green[1]))
        if key in self.seen:
            return False
        self.seen.add(key)
        idx, val = [self.conv[("b", b)]], [1.0]
        for q in self.pairs_of_block[b]:
            d = 0 if self.pairs[q][2] > 0 else 1
            for tau in green[d]:
                row = self.couple.get((q, tau))
                if row is not None:
                    idx.append(row); val.append(-1.0)
        col = len(self.cols)
        self.h.addCol(0.0, 0.0, INF, len(idx), np.array(idx, dtype=np.int32), np.array(val, dtype=np.float64))
        self.cols.append(("plan", b, green))
        self.plan_green[b].append((col, green))
        return True

    def add_z(self, p: int):
        """z columns of every run of opposing pair p, with z_m - z_m+1 >= 0 along the pair."""
        rs = self.meetpairs[p]
        for r in rs:
            self.h.addCol(0.0, 0.0, 1.0, 0, np.array([], dtype=np.int32), np.array([], dtype=np.float64))
            self.zcol[r] = len(self.cols)
            self.cols.append(("z", r))
        for a, b in zip(rs, rs[1:]):
            row = self._row(0.0, INF)
            self.h.changeCoeff(row, self.zcol[a], 1.0)
            self.h.changeCoeff(row, self.zcol[b], -1.0)

    def add_meet_row(self, kind: str, r: int, t: int):
        """alpha (kind A) or beta (kind B) of run r at minute t, over every trip already in the master."""
        H = self.model.headway
        i, j = self.runs[r][:2]
        enters, released = (j, i) if kind == "A" else (i, j)
        idx, val = [self.zcol[r]], [1.0 if kind == "A" else -1.0]
        for c in self.cols_of_train.get(i, []) + self.cols_of_train.get(j, []):
            col = self.cols[c]
            enter, leave = self.run_times(col[1], col[2], r)
            if col[1] == enters and enter <= t:
                idx.append(c); val.append(1.0)
            elif col[1] == released and leave + H <= t:
                idx.append(c); val.append(-1.0)
        row = self._row(-INF, 1.0 if kind == "A" else 0.0, idx, val)
        self.meet_rows[(kind, r, t)] = row
        self.rows_of_run.setdefault(r, []).append((kind, t, row))

    def separate(self, x, max_rows: int = 400, eps: float = 1e-6) -> int:
        """Add the most violated meet row of each kind per run at the LP point x; returns the number added."""
        H, W = self.model.headway, self.W                    # minutes 0 .. W - 1: the kernel's axis
        found = []
        for r, (i, j, *_ ) in self.runs.items():
            A = {i: np.zeros(W), j: np.zeros(W)}             # entered by t
            D = {i: np.zeros(W), j: np.zeros(W)}             # released by t - H
            for c in self.cols_of_train.get(i, []) + self.cols_of_train.get(j, []):
                if x[c] <= 1e-9:
                    continue
                col = self.cols[c]
                enter, leave = self.run_times(col[1], col[2], r)
                if enter < W:
                    A[col[1]][enter] += x[c]
                if leave + H < W:
                    D[col[1]][leave + H] += x[c]
            a = np.cumsum(A[j]) - np.cumsum(D[i])            # alpha: a + z <= 1
            b = np.cumsum(A[i]) - np.cumsum(D[j])            # beta:  b - z <= 0
            ta, tb = int(np.argmax(a)), int(np.argmax(b))
            if r in self.zcol:
                z = x[self.zcol[r]] if self.zcol[r] < len(x) else 0.0
                if a[ta] + z > 1 + eps and ("A", r, ta) not in self.meet_rows:
                    found.append((a[ta] + z - 1, "A", r, ta))
                if b[tb] - z > eps and ("B", r, tb) not in self.meet_rows:
                    found.append((b[tb] - z, "B", r, tb))
            elif a[ta] + b[tb] > 1 + eps:                    # no z in [0, 1] satisfies both
                found.append((a[ta] + b[tb] - 1, "A", r, ta))
                found.append((a[ta] + b[tb] - 1, "B", r, tb))
        found.sort(reverse=True)
        added = 0
        for _, kind, r, t in found[:max_rows]:
            if r not in self.zcol:
                self.add_z(self.pair_of_run[r])
            if (kind, r, t) not in self.meet_rows:
                self.add_meet_row(kind, r, t)
                added += 1
        return added

    def add_artificial(self, k: int, cost: float):
        """A column that satisfies train k's convexity row and holds nothing, at a prohibitive cost: the master is
        feasible before any real trip exists (phase I). Never part of a schedule; the MILP ignores it."""
        self.h.addCol(float(cost), 0.0, INF, 1, np.array([self.conv[("k", k)]], dtype=np.int32), np.array([1.0]))
        self.cols.append(("art", k, cost))

    def solve(self):
        self.h.run()
        status = self.h.getModelStatus()
        assert status == highspy.HighsModelStatus.kOptimal, status
        sol = self.h.getSolution()
        rd = sol.row_dual
        duals = {}
        for (r, tau), row in self.cell.items():
            duals[("L", r, tau)] = max(0.0, -rd[row])
        for (q, tau), row in self.couple.items():
            duals[("M", q, tau)] = max(0.0, -rd[row])
        for (kind, r, tau), row in self.meet_rows.items():
            duals[(kind, r, tau)] = max(0.0, -rd[row])
        sigma = {k: rd[row] for (kind, k), row in self.conv.items() if kind == "k"}
        rho = {b: rd[row] for (kind, b), row in self.conv.items() if kind == "b"}
        return self.h.getInfo().objective_function_value, duals, sigma, rho, list(sol.col_value)


def plan_of_schedule(model: Model, pairs: dict, schedule: dict[int, tuple]) -> dict:
    """Green = the union of each direction's through intervals of a schedule."""
    runs = {}
    for q, (k, b, d, leg, length) in pairs.items():
        s = schedule[k][leg][1]
        runs.setdefault(b, {0: [], 1: []})[0 if d > 0 else 1].append((s, s + length))
    for b, per in runs.items():
        for d in (0, 1):
            other = per[1 - d]
            for s, e in per[d]:
                assert all(e2 <= s or e <= s2 for s2, e2 in other), "opposite through intervals overlap"
    return runs


def milp(master: Master, time_limit: float):
    """HiGHS: x binary over the master's trips, cell rows and one trip per train only."""
    model = master.model
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(time_limit))
    trips = [c for c in master.cols if c[0] == "trip"]
    n = len(model.trains)
    for k in range(n):
        h.addRow(1.0, 1.0, 0, np.array([], dtype=np.int32), np.array([], dtype=np.float64))
    cell = {}
    for _, k, legs, cost in trips:
        for r, tau in master.holds(k, legs):
            if (r, tau) not in cell:
                cell[(r, tau)] = n + len(cell)
                h.addRow(-INF, float(model.resources[r].tracks), 0, np.array([], dtype=np.int32),
                         np.array([], dtype=np.float64))
    for j, (_, k, legs, cost) in enumerate(trips):
        idx = [k] + [cell[rt] for rt in master.holds(k, legs)]
        h.addCol(float(cost), 0.0, 1.0, len(idx), np.array(idx, dtype=np.int32), np.ones(len(idx)))
    h.changeColsIntegrality(len(trips), np.arange(len(trips), dtype=np.int32),
                            np.array([highspy.HighsVarType.kInteger] * len(trips)))
    h.run()
    info = h.getInfo()
    status = h.getModelStatus()
    if info.primal_solution_status != 2:                    # no feasible point
        return status, None, None, info.mip_dual_bound
    x = h.getSolution().col_value
    chosen = {}
    for j, (_, k, legs, cost) in enumerate(trips):
        if x[j] > 0.5:
            chosen[k] = legs
    return status, info.objective_function_value, chosen, info.mip_dual_bound


CPLEX = os.environ.get("CPLEX_BIN", "cplex")            # the CPLEX interactive optimizer (CPLEX Studio 22.1)


def milp_cplex(master: Master, time_limit: float, threads: int = 1, workdir: Path | None = None):
    """The same restricted-master MILP as milp(), solved by the CPLEX interactive optimizer through an LP file.
    Returns (status, value, chosen, dual bound)."""
    import re as _re
    model = master.model
    trips = [c for c in master.cols if c[0] == "trip"]
    workdir = Path(workdir or tempfile.mkdtemp(prefix="cplex_"))
    workdir.mkdir(parents=True, exist_ok=True)
    lp, sol = workdir / "rmp.lp", workdir / "rmp.sol"
    cells = {}
    for j, (_, k, legs, cost) in enumerate(trips):
        for rt in master.holds(k, legs):
            cells.setdefault(rt, []).append(j)
    with open(lp, "w") as f:
        f.write("Minimize\n obj:")
        for j, (_, k, legs, cost) in enumerate(trips):
            f.write(f" + {cost} x{j}" + ("\n" if j % 20 == 19 else ""))
        f.write("\nSubject To\n")
        for k in range(len(model.trains)):
            js = [j for j, c in enumerate(trips) if c[1] == k]
            f.write(f" t{k}: " + " + ".join(f"x{j}" for j in js) + " = 1\n")
        for n, ((r, tau), js) in enumerate(cells.items()):
            if len(js) > model.resources[r].tracks:
                f.write(f" c{n}: " + " + ".join(f"x{j}" for j in js) + f" <= {model.resources[r].tracks}\n")
        f.write("Binary\n" + "\n".join(f" x{j}" for j in range(len(trips))) + "\nEnd\n")
    if sol.exists():
        sol.unlink()
    out = subprocess.run([CPLEX, "-c", f"read {lp}", f"set timelimit {time_limit:.0f}", f"set threads {threads}",
                          "set mip tolerances mipgap 0", "optimize", f"write {sol}", "y"],
                         capture_output=True, text=True).stdout
    bound = None
    m = _re.search(r"best bound\s*=\s*([-0-9.e+]+)", out) or _re.search(r"Current MIP best bound\s*=\s*([-0-9.e+]+)", out)
    if m:
        bound = float(m.group(1))
    if not sol.exists():
        return "no solution", None, None, bound
    text = sol.read_text()
    status = _re.search(r'solutionStatusString="([^"]+)"', text).group(1)
    value = float(_re.search(r'objectiveValue="([^"]+)"', text).group(1))
    chosen = {}
    for name, v in _re.findall(r'<variable name="x(\d+)"[^>]*value="([^"]+)"', text):
        if float(v) > 0.5:
            _, k, legs, _ = trips[int(name)]
            chosen[k] = legs
    if "optimal" in status and bound is None:
        bound = value
    return status, value, chosen, bound


def write_legs(model: Model, chosen: dict, path: Path) -> Path:
    with open(path, "w") as f:
        f.write("train_id,index,resource,entry,exit\n")
        for k, t in enumerate(model.trains):
            for i, (r, e, x) in enumerate(chosen[k]):
                f.write(f"{t.train_id},{i},{model.resources[r].name},{e},{x}\n")
    return path


def run(model: Model, inst: Path, ub: int, start: list[tuple[int, tuple]], phase: bool, time_cap: float,
        windows: bool = True, case: str = "", log=print, artificial: float | None = None, meet: bool = False,
        heur_every: int = 0):
    """Column generation from `start` (whose first len(trains) trips must be one feasible schedule: it also gives the
    first phase plan), or with `artificial` = a prohibitive cost, from nothing (phase I: one artificial column per train
    and an all-idle plan per block). Returns the master, the per-iteration history and whether the LP converged."""
    binary = build()
    t0 = time.time()
    first = price(binary, inst, ub, {}, phase, windows, meet)
    master = Master(model, first["pairs"], first["W"], phase, first["runs"] if meet else None,
                    first["meetpairs"] if meet else None)
    heur_csv = Path(tempfile.mkdtemp()) / "heur.csv"
    index = {res.name: r for r, res in enumerate(model.resources)}
    if artificial is not None:
        for k in range(len(model.trains)):
            master.add_artificial(k, artificial)
    for k, legs in start:
        master.add_trip(k, legs)
    if phase:
        for b in master.blocks:
            master.add_plan(b, {0: [], 1: []})                # the all-idle plan: always a phase plan
        if len(start) >= len(model.trains):
            schedule = {k: legs for k, legs in start[:len(model.trains)]}
            for b, runs in plan_of_schedule(model, first["pairs"], schedule).items():
                master.add_plan(b, runs)
    history, best_L, it = [], first["L"], 0
    while True:
        lp, duals, sigma, rho, x = master.solve()
        cuts = 0
        if meet:                                          # meet rows as cuts, a few rounds at this column pool
            for _ in range(5):
                new = master.separate(x)
                if not new:
                    break
                cuts += new
                lp, duals, sigma, rho, x = master.solve()
        ask = heur_every > 0 and it % heur_every == 0
        res = price(binary, inst, ub, duals, phase, windows, meet, heur_csv if ask else None)
        best_L = max(best_L, res["L"])
        if ask and res.get("heur", -1) > 0 and (master.heur_best is None or res["heur"] < master.heur_best[0]):
            sched = read_schedule(heur_csv)
            master.heur_best = (int(round(res["heur"])),
                                {k: tuple((index[n], e, x_) for _, n, e, x_ in sched[t.train_id])
                                 for k, t in enumerate(model.trains)})
        added = 0
        for k, (v, legs) in res["trips"].items():
            if v - sigma[k] < -1e-6 and master.add_trip(k, legs):
                added += 1
        for b, v in res["phase"].items():
            if b in rho and -v - rho[b] < -1e-6 and master.add_plan(b, res["green"][b]):
                added += 1
        n_trips = sum(1 for c in master.cols if c[0] == "trip")
        n_plans = sum(1 for c in master.cols if c[0] == "plan")
        history.append({"case": case, "algorithm": "colgen_phase" if phase else "colgen", "iteration": it,
                        "lp": lp, "L": res["L"], "best_L": best_L, "added": added, "trips": n_trips,
                        "plans": n_plans, "rows": master.rows, "cuts": cuts, "meet_rows": len(master.meet_rows),
                        "heur": master.heur_best[0] if master.heur_best else None,
                        "seconds": round(time.time() - t0, 1), "ub": ub})
        if log and (it % 5 == 0 or added == 0):
            log(f"  it {it:>4}  LP {lp:10.3f}  L(duals) {res['L']:10.3f}  best L {best_L:10.3f}  +{added:<3} "
                f"trips {n_trips:>5} plans {n_plans:>4} rows {master.rows:>7} meet rows {len(master.meet_rows):>5} "
                f"heur {master.heur_best[0] if master.heur_best else '-'}  {time.time() - t0:7.1f} s")
        if (added == 0 and cuts == 0) or time.time() - t0 > time_cap:
            break
        it += 1
    return master, history, added == 0 and cuts == 0


def solve_milp_of(milp_solver: str, inst: Path):
    if milp_solver == "cplex":
        return lambda master, t: milp_cplex(master, t, workdir=inst.parent / "cplex")
    return milp


def independent(model: Model, inst: Path, seconds: float, phase: bool, case: str, log=print,
                milp_solver: str = "highs", check=None, meet: bool = False, heur_every: int = 0) -> dict:
    """Engine 2 on its own (no schedule from outside). Stage 1: horizon = the latest release + every train run in turn;
    column generation from artificial columns; MILP over the trips (or the greedy / price-heuristic schedule when
    better) -> this engine's own UB. Stage 2: windows from that UB; column generation again; its best Lagrangian value
    at the master's duals is a valid bound on the schedules better than the UB; MILP again over all trips.
    check(model, {train: [(i, name, entry, exit)]}) -> (message, value): the validator for the model (default: the
    resource-chain validator)."""
    t0 = time.time()
    free = [sum(p for _, p, _ in t.path) for t in model.trains]
    max_release = max(t.release for t in model.trains)
    u1 = sum(f + model.headway + 1 for f in free)                  # Tk = max_release + u1 for every train
    big = 2.0 * (max_release + u1)                                  # > the cost of any real trip on this horizon
    greedy_csv = inst.with_suffix(".greedy.csv")                    # sequential insertion by the train DP, no search
    subprocess.run([str(build()), str(inst), "--mode", "ub", "--time-cap", "0", "--out", str(greedy_csv)],
                   capture_output=True, text=True, check=True)
    index = {r.name: i for i, r in enumerate(model.resources)}
    greedy = read_schedule(greedy_csv)
    greedy_start = [(k, tuple((index[n], e, x) for _, n, e, x in greedy[t.train_id])) for k, t in enumerate(model.trains)]
    m1, h1, c1 = run(model, inst, u1, greedy_start, phase, 0.35 * seconds, windows=False, case=case, log=log,
                     artificial=big, meet=meet, heur_every=heur_every)
    solve_milp = solve_milp_of(milp_solver, inst)
    status1, value1, chosen1, _ = solve_milp(m1, 0.15 * seconds)
    out = {"milp_solver": milp_solver, "greedy": (check or validate)(model, greedy)[1], "stage1_iterations": len(h1),
           "stage1_converged": c1, "stage1_lp": h1[-1]["lp"], "stage1_trips": h1[-1]["trips"], "stage1_milp": value1,
           "stage1_milp_status": str(status1)}
    if chosen1 is None:                                            # the MILP found no trip set in its time: the
        out["stage1_ub_source"] = "greedy"                         # engine's own greedy schedule is the stage-1 UB
        ub1 = int(out["greedy"])
        sched1 = dict(greedy_start)
    else:
        out["stage1_ub_source"] = "milp"
        ub1 = int(round(value1))
        sched1 = {k: chosen1[k] for k in range(len(model.trains))}
    out.update({"meet": meet, "heur_every": heur_every, "stage1_meet_rows": len(m1.meet_rows),
                "stage1_heur": m1.heur_best[0] if m1.heur_best else None})
    if m1.heur_best is not None:                                   # the price heuristic at the master's duals
        value = (check or validate)(model, {t.train_id: [(i, model.resources[r].name, e, x) for i, (r, e, x)
                                                         in enumerate(m1.heur_best[1][k])]
                                            for k, t in enumerate(model.trains)})[1]
        assert abs(value - m1.heur_best[0]) < 1e-6, (value, m1.heur_best[0])
        if m1.heur_best[0] < ub1:
            ub1, sched1 = m1.heur_best
            out["stage1_ub_source"] = "price heuristic"
    try:
        win = price(build(), inst, ub1, {}, phase, True)["windows"]
    except RuntimeError:                                            # no schedule better than ub1 fits the windows
        out.update({"ub": ub1, "schedule": sched1, "lb": ub1, "proof": "windows empty below UB",
                    "seconds": round(time.time() - t0, 1)})
        return out
    start = [(k, sched1[k]) for k in range(len(model.trains))]
    start += [(c[1], c[2]) for c in m1.cols if c[0] == "trip" and c[2][-1][2] <= win[c[1]] and c[2] != sched1[c[1]]]
    m2, h2, c2 = run(model, inst, ub1, start, phase, 0.35 * seconds, windows=True, case=case, log=log, artificial=big,
                     meet=meet, heur_every=heur_every)
    status2, value2, chosen2, _ = solve_milp(m2, max(10.0, seconds - (time.time() - t0)))
    cands = [(ub1, sched1)] + ([(int(round(value2)), chosen2)] if chosen2 is not None else [])
    if m2.heur_best is not None:
        cands.append(m2.heur_best)
    best = min(cands, key=lambda x: x[0])
    out.update({"stage2_meet_rows": len(m2.meet_rows), "stage2_heur": m2.heur_best[0] if m2.heur_best else None})
    out.update({"stage2_iterations": len(h2), "stage2_converged": c2, "stage2_lp": h2[-1]["lp"],
                "stage2_best_L": h2[-1]["best_L"], "stage2_milp": value2, "stage2_milp_status": str(status2),
                "ub": best[0], "schedule": best[1], "window_ub": ub1,
                "lb": min(ub1, int(np.ceil(h2[-1]["best_L"] - 1e-6))), "seconds": round(time.time() - t0, 1)})
    return out
