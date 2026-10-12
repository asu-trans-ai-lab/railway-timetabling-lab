"""Timetable feedback to the RAS 2026 blocking solver (Prof. Zhou, Oct 8): the blocking plan is made by the blocking
solver (ras_release_solver.py, Version A); the train timetable only returns feedback to it.

Loop
  1. blocking plan (solution_result.json)  ->  cars on every L3 link, by direction and commodity type
  2. each ANL corridor (path_manifest.csv maps its resources to L3 links): cars / 70 days / cars per train ->
     trains per day by direction and class (Z intermodal 150 cars, 1.25 x speed; M merchandise 100 / auto 90, 1.0 x;
     G grain / coal 120, 0.85 x; the constants of FTT_handoff/data/blocking_layer.py), released evenly over 24 h
  3. the day's timetable: DP (our sequential insertion) and E1 (CP-SAT), checked by the validator
  4. four kinds of feedback per corridor resource, written per L3 link:
     (1) capacity   the largest multiple s* of today's trains the corridor can run with every train's delay
                    <= MaxTrainWaitingTime (DP timetable, bisection on s); s* x trains x cars per train x 70 days
                    -> link capacity (cars per 70-day cycle)
     (2) price      CPLEX marginals of the day's space-time LP (experiments/cplex_duals.py): lambda(r, t) = minutes
                    of total delay one more track-minute of r at t saves. A train (and each of its cars) entering
                    r adds about mean_t lambda(r, t) x (p_r + H) minutes; spread over the L3 links of r by length
                    and added to the routing free-flow time (vdf_fftt). Compared with the Fluid Queue price of the
                    same resource (Q_after = max(0, Q + A - Cq), w = Q / mu, lambda_hat = alpha theta tau0
                    (w / tau0)^beta; alpha = theta = 1, tau0 = 30, beta = 4, 30-minute intervals)
     (3) run time   scheduled minutes on r (running + waiting) against free running -> effective free_speed
     (4) cut        corridors where s* < 1 (today's plan cannot be timetabled within the waiting limit): the corridor
                    links get the capacity of (1) and the solver's C5 overflow penalty is raised so it acts as a
                    hard constraint
  5. a feedback copy of the L3 dataset (link.csv changed) -> ras_release_solver.py -> the JSON is checked with the
     official validator v2.0 against the ORIGINAL inputs -> step 2-3 again on the new plan (before / after)

    python -m experiments.blocking_feedback schedule --plan <solution_result.json> --tag base
    python -m experiments.blocking_feedback apply --tag base --kinds capacity,price,runtime,cut
    python -m experiments.blocking_feedback loop      (all of it, base -> each feedback -> after)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
WORK = PACKAGE.parent / "blocking_feedback"
SOLVER = WORK / "dev" / "code" / "solver" / "ras_release_solver.py"
L3 = Path(os.environ.get("RAS_L3_V21", Path.home() / "Downloads/RAS_Version_A_blocking_solver_current_2026-09-25"
                                                    "/official_release_v2_1/datasets/l3"))
VALIDATOR = Path(os.environ.get("RAS_VALIDATOR", Path.home() / "Downloads/RAS_Version_A_blocking_solver_current_2026-09-25"
                                "/official_release_v2_1/scoring/fast_validator_v2_0.py"))
CORR_DATA = PACKAGE.parent / "FTT_handoff" / "data"
OUT = PACKAGE / "results" / "feedback"
CORRIDORS = ["overland", "panhandle", "transcon_clovis", "prb_joint", "pocahontas", "hiline", "cn_icmain", "gulf",
             "moffat", "cpkc_midcon", "bnsf_columbia", "csx_bo", "csx_waterlevel", "ns_chicago", "ns_crescent",
             "ns_heartland", "up_sunset", "up_texas"]
CLASS = {"intermodal": ("Z", 150, 1.25), "merchandise": ("M", 100, 1.0), "manifest": ("M", 100, 1.0),
         "automobile": ("M", 90, 1.0), "auto": ("M", 90, 1.0), "grain": ("G", 120, 0.85), "coal": ("G", 120, 0.85)}
CYCLE_DAYS = 70
STRESS_TRAINS = 150                      # stress test at min(s*, 150 trains): the LP stays small enough
FQ = {"alpha": 1.0, "theta": 1.0, "tau0": 30.0, "beta": 4.0, "max_wait": 180.0, "step": 30}


# ----------------------------------------------------------------------------------------------- 1. plan -> flows
def link_flows(plan: Path) -> dict:
    """(u, v) directed node pair -> {commodity type: cars over the cycle}."""
    out = json.load(open(plan))["outputs"]
    btype, vol = {}, defaultdict(lambda: defaultdict(float))
    for r in out["2 Blocking Sequence"]:
        for b in str(r["blocking_sequence"]).split(" -> "):
            if b.strip():
                vol[int(b)][str(r["commodity_type"]).lower()] += float(r["volume"])
    flow = defaultdict(lambda: defaultdict(float))
    for r in out["3 Block Route"]:
        nodes = [int(x) for x in str(r["physical_path_nodes"]).split(" -> ") if x.strip()]
        for u, v in zip(nodes, nodes[1:]):
            for t, c in vol[int(r["block_id"])].items():
                flow[(u, v)][t] += c
    return flow


def corridor_dir(name: str) -> Path:
    return CORR_DATA / f"{name}_native" if (CORR_DATA / f"{name}_native").exists() else CORR_DATA / name


def manifest(name: str):
    """L3 links of the corridor, each with the corridor resource (input_link row) it belongs to."""
    base = corridor_dir(name)
    rows = list(csv.DictReader(open(base / "path_manifest.csv")))
    links = list(csv.DictReader(open(base / "input_link.csv")))
    edges, x = [], 0.0
    for i, l in enumerate(links):
        edges.append((x, x + float(l["length"]), i))
        x += float(l["length"])
    for r in rows:
        mid = (float(r["mp_start"]) + float(r["mp_end"])) / 2
        r["resource"] = next((i for a, b, i in edges if a - 1e-6 <= mid <= b + 1e-6), edges[-1][2])
    return rows, links


def demand(name: str, flow: dict) -> dict:
    """{(dir, class letter, speed): trains per day}; dir +1 = manifest order (east in the corridor model)."""
    rows, _ = manifest(name)
    cars = defaultdict(float)
    for d, key in ((1, lambda r: (int(r["from_node_id"]), int(r["to_node_id"]))),
                   (-1, lambda r: (int(r["to_node_id"]), int(r["from_node_id"])))):
        for r in rows:
            for t, c in flow.get(key(r), {}).items():
                cars[(d, t)] += c / len(rows)                 # mean over the corridor's links
    out = defaultdict(float)
    for (d, t), c in cars.items():
        cls, cpt, mult = CLASS.get(t, ("M", 100, 1.0))
        out[(d, cls, mult, cpt)] += c / CYCLE_DAYS / cpt
    return dict(out)


# ------------------------------------------------------------------------------------------- 2. day's instance
def day_model(name: str, dem: dict, scale: float = 1.0):
    """The corridor with the day's trains: round(scale x trains per day) per (dir, class), released evenly."""
    from adapters.anl_corridor import corridor_model
    src = corridor_dir(name)
    tmp = Path(tempfile.mkdtemp(prefix="fbday_"))
    for f in ("FTSettings.ini", "input_link.csv", "input_node.csv"):
        shutil.copy(src / f, tmp / f)
    nodes = [int(r["node_id"]) for r in csv.DictReader(open(src / "input_node.csv"))] \
        if "node_id" in open(src / "input_node.csv").readline() else None
    links = list(csv.DictReader(open(src / "input_link.csv")))
    first, last = int(links[0]["from"]), int(links[-1]["to"])
    trains, cars_of = [], {}
    for d in (1, -1):
        mine = [(cls, mult, cpt, scale * n) for (dd, cls, mult, cpt), n in dem.items() if dd == d]
        total = round(sum(x[3] for x in mine))            # largest remainder: keep the direction's total
        cnt = [int(x[3]) for x in mine]
        for i in sorted(range(len(mine)), key=lambda i: -(mine[i][3] - int(mine[i][3])))[:max(0, total - sum(cnt))]:
            cnt[i] += 1
        seq = []                                          # interleave the classes evenly over the day
        for (cls, mult, cpt, _), n in zip(mine, cnt):
            seq += [((j + 0.5) / n, cls, mult, cpt) for j in range(n)]
        seq = [x[1:] for x in sorted(seq)]
        for j, (cls, mult, cpt) in enumerate(seq):
            entry = int(1440 * (j + 0.5) / len(seq))
            tid = f"{'EB' if d == 1 else 'WB'}{j + 1:03d}{cls}"
            trains.append((tid, first if d == 1 else last, last if d == 1 else first, d, mult, entry))
            cars_of[tid] = cpt
    with open(tmp / "input_train_info.csv", "w") as f:
        f.write("train_id,origin,dest,dir,TOB,length,hazmat,speed_mult,entry,cost_stop,cost_early,cost_run,"
                "intended_arrival\n")
        for tid, o, de, d, mult, entry in trains:
            f.write(f"{tid},{o},{de},{d},0,0,0,{mult},{entry},0,0,0,{entry}\n")
    m, info = corridor_model(name, base=tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    return m, info, cars_of


def dp_schedule(model):
    from solver.python.siding_kernel import greedy_ub
    from solver.python.siding_model import write_instance
    from solver.python.siding_validate import read_schedule
    work = Path(tempfile.mkdtemp(prefix="fbdp_"))
    U = greedy_ub(write_instance(model, work / "i.txt"), work / "dp.csv")
    sched = read_schedule(work / "dp.csv")
    shutil.rmtree(work, ignore_errors=True)
    return U, sched


def delays(model, sched) -> dict:
    out = {}
    for t in model.trains:
        legs = sched[t.train_id]
        out[t.train_id] = legs[-1][3] - t.release - sum(p for _, p, _ in t.path)
    return out


def max_wait(name: str) -> int:
    import configparser
    ini = configparser.ConfigParser()
    ini.read(corridor_dir(name) / "FTSettings.ini")
    for sec in ini.sections():
        if "MaxTrainWaitingTime" in ini[sec]:
            return int(float(ini[sec]["MaxTrainWaitingTime"]))
    return 120


# ------------------------------------------------------------------------------------------------ 3. feedback
def capacity_scale(name: str, dem: dict, lo=0.05, hi=40.0, iters=10) -> float:
    """Largest s with every train's DP delay <= MaxTrainWaitingTime (bisection; the DP timetable is a feasible one,
    so s* is a safe lower estimate of what the corridor can carry)."""
    limit = max_wait(name)

    def ok(s):
        m, _, _ = day_model(name, dem, s)
        if not m.trains:
            return True
        _, sch = dp_schedule(m)
        return max(delays(m, sch).values()) <= limit
    if ok(1.0):                                   # today's trains fit: search above 1 (the DP is not monotone in s,
        lo = 1.0                                  # so a dip below 1 must not turn into a cut)
    elif not ok(lo):
        return 0.0
    if ok(hi):
        return hi
    for _ in range(iters):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    return lo


def fluid_prices(model, sched=None) -> dict:
    """Fluid Queue price per (resource, 30-min interval) from the free-running arrivals at each resource."""
    H, step = model.headway, FQ["step"]
    arrivals = defaultdict(lambda: defaultdict(float))
    p_of = defaultdict(list)
    for t in model.trains:
        clock = t.release
        for r, p, _ in t.path:
            arrivals[r][int(clock // step)] += 1
            p_of[r].append(p)
            clock += p
    out = {}
    for r, arr in arrivals.items():
        tracks = model.resources[r].tracks
        pbar = sum(p_of[r]) / len(p_of[r])
        mu = tracks / (pbar + H)                     # trains per minute
        Cq = mu * step
        Q = 0.0
        for n in range(0, max(arr) + 24):
            Q = max(0.0, Q + arr.get(n, 0.0) - Cq)
            w = min(Q / mu, FQ["max_wait"])
            out[(r, n)] = FQ["alpha"] * FQ["theta"] * FQ["tau0"] * (w / FQ["tau0"]) ** FQ["beta"]
    return out


def lp_prices(model, U, max_slack: int):
    """CPLEX marginals of the day's space-time LP (every train's delay <= max_slack, the corridor's waiting limit):
    lambda(r, t) >= 0, and the LP value."""
    from experiments.cplex_duals import cplex_lp
    from solver.python.lp_spacetime import build
    b = build(model, U, max_slack=max_slack)
    work = Path(tempfile.mkdtemp(prefix="fblp_"))
    obj, duals = cplex_lp(b["h"], work)
    shutil.rmtree(work, ignore_errors=True)
    lam = {}
    for row, d in duals.items():
        if row >= b["cap_row0"]:
            r, t = divmod(row - b["cap_row0"], b["T"])
            if -d > 1e-9:
                lam[(r, t)] = -d
    return lam, obj


def spearman(x, y) -> float | None:
    n = len(x)
    if n < 3:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        rk = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                rk[order[k]] = (i + j) / 2
            i = j + 1
        return rk
    a, b = ranks(x), ranks(y)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((p - ma) * (q - mb) for p, q in zip(a, b))
    va, vb = sum((p - ma) ** 2 for p in a), sum((q - mb) ** 2 for q in b)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else None


def capacity_per_direction(s: float, cars_dir: dict) -> dict:
    """s* x cars by direction; a direction with less than half the traffic still gets half of the total (both
    directions share a single track, so today's split should not starve the lighter one)."""
    total = sum(cars_dir.values())
    return {f"{d:+d}": round(s * max(v, total / 2)) for d, v in cars_dir.items()}


def recompute_capacity(tag: str) -> None:
    """Redo (1) and (4) in schedule_<tag>.json (DP only, minutes) after a change to the capacity rule."""
    f = OUT / f"schedule_{tag}.json"
    rows = json.loads(f.read_text())
    flow = None
    for row in rows:
        if row.get("empty") or row.get("error") or not row.get("trains"):
            continue
        if flow is None:
            flow = link_flows(Path(row.get("plan") or WORK / "runs" / "base_l3" / "solution_result.json"))
        dem = demand(row["corridor"], flow)
        s = capacity_scale(row["corridor"], dem)
        cars_dir = defaultdict(float)
        for (d, c, _, cpt), n in dem.items():
            cars_dir[d] += n * cpt * CYCLE_DAYS
        row.update(capacity_scale=round(s, 3), cut=s < 1.0, capacity_cars_per_cycle=capacity_per_direction(s, cars_dir))
        print(row["corridor"], row["capacity_scale"], row["cut"], row["capacity_cars_per_cycle"], flush=True)
    f.write_text(json.dumps(rows, indent=1))


def corridor_feedback(name: str, flow: dict, e1_seconds: float = 60, stress: bool = True) -> dict:
    from experiments.mip_check import run_e1
    from solver.python.siding_validate import validate
    dem = demand(name, flow)
    model, info, cars_of = day_model(name, dem)
    row = {"corridor": name, "trains_per_day": {f"{d:+d}{c}": round(n, 2) for (d, c, _, _), n in dem.items()},
           "trains": len(model.trains), "single_track_pct": info["single_track_pct"], "miles": info["miles"]}
    if not model.trains:
        row["empty"] = True
        return row
    U, dp = dp_schedule(model)
    TT0 = sum(sum(p for _, p, _ in t.path) for t in model.trains)
    e1 = run_e1(model, e1_seconds)
    sched = e1["schedule"] if e1.get("check") == "PASS" and e1["value"] <= U else dp
    value = e1["value"] if sched is not dp else U
    dl = delays(model, sched)
    row.update(dp_value=U, e1_value=e1.get("value"), e1_status=e1.get("status"), TT0=TT0, delay=value - TT0,
               max_delay=max(dl.values()), wait_limit=max_wait(name))
    # (3) run time per resource and direction
    _, links = manifest(name)
    runt = defaultdict(lambda: [0.0, 0.0])                # (r, dir) -> [scheduled minutes, free minutes]
    for t in model.trains:
        for (i, rname, e, x), (r, p, _) in zip(sched[t.train_id], t.path):
            runt[(r, t.direction)][0] += x - e
            runt[(r, t.direction)][1] += p
    row["runtime_ratio"] = {f"{r}:{d:+d}": round(a / b, 4) for (r, d), (a, b) in runt.items() if b > 0}
    # (2) prices: CPLEX marginals vs Fluid Queue
    lam, lp = lp_prices(model, U, max_wait(name))
    H = model.headway
    pbar = defaultdict(list)
    for t in model.trains:
        for r, p, _ in t.path:
            pbar[r].append(p)
    price = {}
    for r in range(len(model.resources)):
        tot = sum(v for (rr, t), v in lam.items() if rr == r)
        pm = (sum(pbar[r]) / len(pbar[r])) if pbar[r] else 0.0
        price[r] = tot / 1440.0 * (pm + H)                # minutes a train entering r adds, on average
    fq = fluid_prices(model)
    step = FQ["step"]
    lam_iv = defaultdict(float)
    for (r, t), v in lam.items():
        lam_iv[(r, t // step)] += v
    keys = sorted(set(fq) | set(lam_iv))
    row.update(lp=lp, lp_priced_cells=len(lam), price_minutes={str(r): round(v, 4) for r, v in price.items() if v},
               fluid_vs_lp_spearman=spearman([lam_iv.get(k, 0.0) for k in keys], [fq.get(k, 0.0) for k in keys]),
               fluid_vs_lp_spearman_by_resource=spearman(
                   [sum(v for (r, _), v in lam_iv.items() if r == rr) for rr in range(len(model.resources))],
                   [sum(v for (r, _), v in fq.items() if r == rr) for rr in range(len(model.resources))]))
    # (1) capacity and (4) cut
    s = capacity_scale(name, dem)
    fits = row["max_delay"] <= row["wait_limit"]          # today's trains, by the E1 (or DP) timetable we have
    if fits:
        s = max(s, 1.0)                                   # the DP-based search can be pessimistic: never a cut then
    row.update(capacity_scale=round(s, 3), cut=not fits)
    # stress test: the same comparison of prices at the capacity (s* x today's trains), where queues exist
    s_stress = min(s, STRESS_TRAINS / max(1, len(model.trains))) if stress else 0
    if s_stress > 0:
        ms, _, _ = day_model(name, dem, s_stress)
        if ms.trains:
            Us, _ = dp_schedule(ms)
            lam_s, lp_s = lp_prices(ms, Us, max_wait(name))
            fq_s = fluid_prices(ms)
            iv = defaultdict(float)
            for (r, t), v in lam_s.items():
                iv[(r, t // step)] += v
            ks = sorted(set(fq_s) | set(iv))
            row["stress"] = {"scale": round(s_stress, 3), "trains": len(ms.trains), "dp_value": Us, "lp": lp_s, "priced_cells": len(lam_s),
                             "fluid_vs_lp_spearman": spearman([iv.get(k, 0.0) for k in ks],
                                                              [fq_s.get(k, 0.0) for k in ks]),
                             "fluid_vs_lp_spearman_by_resource": spearman(
                                 [sum(v for (r, _), v in iv.items() if r == rr) for rr in range(len(ms.resources))],
                                 [sum(v for (r, _), v in fq_s.items() if r == rr) for rr in range(len(ms.resources))]),
                             "lp_mass": round(sum(lam_s.values()), 2), "fluid_mass": round(sum(fq_s.values()), 2)}
    cars_dir = defaultdict(float)
    for (d, c, _, cpt), n in dem.items():
        cars_dir[d] += n * cpt * CYCLE_DAYS
    row["cars_per_cycle"] = {f"{d:+d}": round(v) for d, v in cars_dir.items()}
    row["capacity_cars_per_cycle"] = capacity_per_direction(s, cars_dir)
    errs, total, _ = validate(model, sched)
    row["check"] = "PASS" if not errs and total == value else f"FAIL {errs[:1]}"
    return row


def schedule_all(plan: Path, tag: str, e1_seconds: float = 60, stress: bool = True) -> list:
    flow = link_flows(plan)
    rows = []
    OUT.mkdir(parents=True, exist_ok=True)
    for c in CORRIDORS:
        t0 = time.time()
        p = subprocess.run([sys.executable, "-m", "experiments.blocking_feedback", "_corridor", str(plan), c,
                            str(e1_seconds), "1" if stress else "0"], cwd=PACKAGE, capture_output=True, text=True)
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        row = json.loads(lines[-1]) if lines else {"corridor": c, "error": p.stderr[-400:]}
        row["seconds"] = round(time.time() - t0, 1)
        print(json.dumps({k: row.get(k) for k in ("corridor", "trains", "delay", "max_delay", "capacity_scale", "cut",
                                                   "fluid_vs_lp_spearman", "check", "error")}), flush=True)
        rows.append(row)
        (OUT / f"schedule_{tag}.json").write_text(json.dumps(rows, indent=1))
    return rows


# ------------------------------------------------------------------------------------- 4. feedback -> dataset
def feedback_dataset(tag: str, kinds: list[str], prev: Path | None = None) -> Path:
    """A copy of the L3 dataset whose link.csv carries the chosen feedback. prev: the previous round's feedback
    dataset; its capacities are kept (a capacity once fed back stays), prices and run times are recomputed from the
    original L3 speeds."""
    rows = json.loads((OUT / f"schedule_{tag}.json").read_text())
    dst = WORK / "datasets" / f"{tag}_{'+'.join(kinds)}"
    dst.mkdir(parents=True, exist_ok=True)
    for f in L3.iterdir():
        if f.is_file() and f.name != "link.csv":
            shutil.copy(f, dst / f.name)
    with open(L3 / "link.csv", newline="") as f:
        rd = csv.DictReader(f)
        fields = list(rd.fieldnames)
        links = list(rd)
    if prev is not None:                                      # keep the capacities fed back in the previous round
        with open(prev / "link.csv", newline="") as f:
            prev_cap = {r["link_id"]: r["capacity"] for r in csv.DictReader(f)}
        for l in links:
            l["capacity"] = prev_cap.get(l["link_id"], l["capacity"])
    by_pair = {(int(l["from_node_id"]), int(l["to_node_id"])): l for l in links}
    if "vdf_fftt" not in fields and "price" in kinds:
        fields.append("vdf_fftt")
    changed = defaultdict(int)
    for row in rows:
        if row.get("empty") or row.get("error"):
            continue
        mrows, clinks = manifest(row["corridor"])
        res_len = defaultdict(float)
        for r in mrows:
            res_len[r["resource"]] += float(r["length"])
        for r in mrows:
            for d, (u, v) in ((1, (int(r["from_node_id"]), int(r["to_node_id"]))),
                              (-1, (int(r["to_node_id"]), int(r["from_node_id"])))):
                l = by_pair.get((u, v))
                if l is None:
                    continue
                res = r["resource"]
                if "capacity" in kinds or ("cut" in kinds and row["cut"]):
                    cap = row["capacity_cars_per_cycle"].get(f"{d:+d}")
                    if cap is None:                          # no traffic that way today: the other direction's
                        cap = max(row["capacity_cars_per_cycle"].values())
                    old = float(l.get("capacity") or 1e9)
                    if 0 < cap < old:                        # feedback only tightens the published capacity
                        l["capacity"] = str(float(cap))
                        changed["capacity"] += 1
                if "runtime" in kinds:
                    ratio = row["runtime_ratio"].get(f"{res}:{d:+d}")
                    if ratio and ratio > 1.0 and l.get("free_speed"):
                        l["free_speed"] = str(float(l["free_speed"]) / ratio)
                        changed["runtime"] += 1
                if "price" in kinds:
                    pm = row["price_minutes"].get(str(res), 0.0) * float(r["length"]) / max(res_len[res], 1e-9)
                    fs = float(l.get("free_speed") or 60.0) or 60.0
                    base = float(l["length"]) / fs * 60.0
                    l["vdf_fftt"] = str(base + pm)
                    changed["price"] += 1 if pm > 0 else 0
    if "price" in kinds:
        for l in links:                                      # keep the free-flow time everywhere else
            if not l.get("vdf_fftt"):
                fs = float(l.get("free_speed") or 60.0) or 60.0
                l["vdf_fftt"] = str(float(l["length"]) / fs * 60.0)
    with open(dst / "link.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(links)
    (dst / "feedback_changes.json").write_text(json.dumps(changed))
    return dst


def run_blocking(dataset: Path, name: str, extra: list[str] | None = None) -> Path:
    out = WORK / "runs" / name
    log = WORK / "runs" / f"{name}.log"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w") as f:
        subprocess.run([sys.executable, str(SOLVER), "--dataset", str(dataset), "--output-dir", str(out),
                        "--instance-name", name] + (extra or []), stdout=f, stderr=subprocess.STDOUT)
    shutil.rmtree(out / "_step6_cg", ignore_errors=True)       # the kernel's working files (large)
    return out / "solution_result.json"


def official_check(plan: Path) -> dict:
    """The official validator v2.0 on the plan with the ORIGINAL L3 inputs (feedback changes stay internal)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ras_fast_validator", VALIDATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = json.load(open(plan))
    orig = json.load(open(WORK / "runs" / "base_l3" / "solution_result.json"))["inputs"]
    data["inputs"] = orig
    with open(L3 / "setting.csv") as f:                       # the official settings ($2,500 per block), not the
        for r in csv.DictReader(f):                           # solver's own defaults copied into its JSON
            try:
                data["inputs"]["settings"][r["parameter"]] = float(r["value"])
            except (KeyError, ValueError):
                pass
    ok, res = mod.fast_validate_payload(data, od_distance_matrix_path=PACKAGE / "no_od_matrix.csv")
    return {"valid": bool(ok), "failed": [k for k, v in res["checks"].items() if not v.get("pass")],
            "c4_violations": res["checks"]["4_min_block_volume"]["n_violations"],
            "c5_max_utilization": res["checks"]["5_link_capacity"]["max_utilization"],
            "cost": res["cost"]["total"], "cost_parts": {k: res["cost"][k] for k in ("fixed", "transport", "handling",
                                                                                     "interchange")},
            "stress": res.get("stress_metrics", {}).get("stress_score")}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "_corridor":
        print(json.dumps(corridor_feedback(sys.argv[3], link_flows(Path(sys.argv[2])), float(sys.argv[4]),
                                           stress=len(sys.argv) < 6 or sys.argv[5] == "1")))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["schedule", "apply", "loop"])
    ap.add_argument("--plan", default=str(WORK / "runs" / "base_l3" / "solution_result.json"))
    ap.add_argument("--tag", default="base")
    ap.add_argument("--kinds", default="capacity,price,runtime,cut")
    ap.add_argument("--e1-seconds", type=float, default=60)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.cmd == "schedule":
        schedule_all(Path(a.plan), a.tag, a.e1_seconds)
        return 0
    runs = {"capacity": ["capacity"], "price": ["price"], "runtime": ["runtime"], "cut": ["cut"],
            "all": ["capacity", "price", "runtime", "cut"]}
    base_rows = json.loads((OUT / f"schedule_{a.tag}.json").read_text()) if (OUT / f"schedule_{a.tag}.json").exists() else []
    if base_rows and not any(r.get("cut") for r in base_rows):
        runs.pop("cut")                                   # no corridor over capacity: the cut run equals the base
    if a.cmd == "apply":
        runs = {"+".join(a.kinds.split(",")): a.kinds.split(",")}
    if a.cmd == "loop" and not (OUT / f"schedule_{a.tag}.json").exists():
        schedule_all(Path(a.plan), a.tag, a.e1_seconds)
    summary = json.loads((OUT / "loop.json").read_text()) if (OUT / "loop.json").exists() else {}
    if "base" not in summary:
        summary["base"] = {"official": official_check(Path(a.plan))}
    for label, kinds in runs.items():
        ds = feedback_dataset(a.tag, kinds)
        extra = ["--penalty-per-c5-overflow", "100000"] if "cut" in kinds else []
        plan = run_blocking(ds, f"fb_{label}", extra)
        entry = {"kinds": kinds, "changes": json.loads((ds / "feedback_changes.json").read_text())}
        if plan.exists():
            entry["official"] = official_check(plan)
            after = schedule_all(plan, f"after_{label}", a.e1_seconds, stress=False)
            entry["after"] = {"total_delay": sum(r.get("delay") or 0 for r in after),
                              "by_corridor": {r["corridor"]: r.get("delay") for r in after},
                              "max_delay": max((r.get("max_delay") or 0 for r in after), default=0),
                              "cuts": sum(bool(r.get("cut")) for r in after)}
        else:
            entry["error"] = "no solution_result.json"
        summary[label] = entry
        print(label, json.dumps(entry)[:600], flush=True)
        (OUT / "loop.json").write_text(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
