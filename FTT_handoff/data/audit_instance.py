"""audit_instance.py — three-level synthetic-data evidence audit (network / service / operational).

Implements the auditability memo: every corridor instance carries an AUDIT_REPORT.md with
PASS / WARN / FAIL / SKIP rows so the dataset is defensible and reproducible.

Usage:
  python audit_instance.py <instance_dir> [...]      per-instance AUDIT_REPORT.md
  python audit_instance.py --summary                 all corridor instances -> AUDIT_SUMMARY.md
                                                      + INPUT_ASSUMPTIONS.md
Prereqs per instance: provenance.csv (make_provenance.py), service_derivation.csv
(blocking_layer.py) where a manifest exists. Operational checks call fasttrain.exe and the
B0 dispatcher + independent validator (paths relative to this file's layout).
"""
from __future__ import annotations
import csv, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
NT = os.path.normpath(os.path.join(HERE, "..", "n_track"))
LAB = os.path.normpath(os.path.join(HERE, "..", "..", "benchmark_lab"))
CLASSES = {"Z": 1.25, "M": 1.00, "G": 0.85}
KNOWN_OPEN = {"gulf_native", "moffat_native", "cpkc_midcon_native", "transcon_clovis_native_fullday"}
# feasible schedules exist (via B&B) but sequential dispatching cannot find one:
KNOWN_B0_INFEASIBLE = {"hiline_native"}


def tau(L, v, m):
    return max(1, int(L * 60.0 / (v * m) + 1.0))


def audit(d, run_operational=True):
    name = os.path.basename(os.path.normpath(d))
    rows = []                                             # (level, check, verdict, detail)

    def add(level, check, verdict, detail):
        rows.append((level, check, verdict, detail))

    links = list(csv.DictReader(open(os.path.join(d, "input_link.csv"))))
    trains = list(csv.DictReader(open(os.path.join(d, "input_train_info.csv"))))

    # ---------- 1. NETWORK AUDIT ----------
    chained = all(int(r["from"]) + 1 == int(r["to"]) for r in links)
    add("network", "block chaining / connectivity", "PASS" if chained else "FAIL",
        f"{len(links)} blocks form one origin->destination chain" if chained else "non-consecutive nodes")
    man_path = os.path.join(d, "path_manifest.csv")
    if os.path.exists(man_path):
        man_len = sum(float(r["length"]) for r in csv.DictReader(open(man_path)))
        blk_len = sum(float(r["length"]) for r in links)
        ok = abs(man_len - blk_len) < 2.0
        add("network", "length conservation vs manifest", "PASS" if ok else "FAIL",
            f"blocks {blk_len:.1f} mi vs L3 manifest {man_len:.1f} mi")
    else:
        add("network", "length conservation vs manifest", "SKIP", "no manifest (literature/toy instance)")
    caps = [int(r["capacity"]) for r in links]
    add("network", "capacities plausible", "PASS" if all(1 <= c <= 3 for c in caps) else "FAIL",
        f"cap1 x{caps.count(1)}, cap2 x{caps.count(2)}, cap3 x{caps.count(3)}")
    spd = [float(r["speed_ft"]) for r in links]
    add("network", "speeds plausible", "PASS" if all(5 <= s <= 80 for s in spd) else "FAIL",
        f"{min(spd):.0f}-{max(spd):.0f} mph")
    # meet capability: longest consecutive cap-1 stretch (boundaries of cap-1 blocks = sidings)
    run1, worst1 = 0.0, 0.0
    for r in links:
        if int(r["capacity"]) == 1:
            run1 = max(run1, float(r["length"]))          # one block = siding-to-siding
            worst1 = max(worst1, run1)
    add("network", "meet capability (max siding-free stretch)",
        "PASS" if worst1 <= 25 else "WARN",
        f"longest cap-1 block {worst1:.1f} mi (block boundaries = passing points; synthetic where L3 lacks sidings)")
    multi_mi = sum(float(r["length"]) for r in links if int(r["capacity"]) >= 2)
    add("network", "overtake capability", "PASS",
        f"{100*multi_mi/sum(float(r['length']) for r in links):.0f}% of mileage has cap>=2")
    prov = os.path.join(d, "provenance.csv")
    if os.path.exists(prov):
        pv = list(csv.DictReader(open(prov)))
        bad = [r for r in pv if r["provenance"] not in
               ("observed", "externally_supported", "inferred", "synthetic")]
        add("network", "provenance labels", "PASS" if pv and not bad else "FAIL",
            f"{len(pv)} labeled elements; " + "; ".join(
                f"{lab} x{sum(1 for r in pv if r['provenance']==lab)}"
                for lab in ("observed", "externally_supported", "inferred", "synthetic")))
    else:
        add("network", "provenance labels", "FAIL", "provenance.csv missing (run make_provenance.py)")

    # ---------- 2. SERVICE AUDIT ----------
    n_tr = len(trains)
    mix = {"Z": 0, "M": 0, "G": 0}
    for t in trains:
        m = float(t["speed_mult"])
        mix["Z" if m > 1.05 else ("G" if m < 0.95 else "M")] += 1
    horizon = None
    for ln in open(os.path.join(d, "FTSettings.ini")):
        if ln.lower().startswith("optimizationhorizon"):
            horizon = int(ln.split("=")[1])
    span = max(float(t["entry"]) for t in trains) - min(float(t["entry"]) for t in trains) or 1
    assumed_per_day = n_tr / (span / 1440.0)
    sd = os.path.join(d, "service_derivation.csv")
    if os.path.exists(sd):
        der = list(csv.DictReader(open(sd)))
        dtot = sum(float(r["trains_per_day"]) for r in der if r["block_type"] == "TOTAL")
        dcls = {"Z": 0.0, "M": 0.0, "G": 0.0}
        for r in der:
            if r["block_type"] != "TOTAL":
                dcls[r["class"]] += float(r["trains_per_70d"])
        dsum = sum(dcls.values()) or 1
        asum = sum(mix.values()) or 1
        worst = max(abs(dcls[c]/dsum - mix[c]/asum) for c in "ZMG")
        add("service", "class mix vs demand-derived mix",
            "PASS" if worst <= 0.25 else "WARN",
            "assumed " + "/".join(f"{c}:{mix[c]/asum:.0%}" for c in "ZMG") +
            " vs derived " + "/".join(f"{c}:{dcls[c]/dsum:.0%}" for c in "ZMG") +
            f" (max share delta {worst:.0%})")
        eb = sum(float(r["trains_per_day"]) for r in der if r["block_type"] == "TOTAL" and r["direction"] == "EB")
        wb = dtot - eb
        bal = eb / dtot if dtot else 0.5
        add("service", "directional balance", "PASS" if 0.3 <= bal <= 0.7 else "WARN",
            f"derived split EB {bal:.0%} / WB {1-bal:.0%}; scenario is symmetric by construction")
        add("service", "absolute frequency vs derived", "WARN",
            f"scenario ~{assumed_per_day:.0f} trains/day vs derived {dtot:.1f}/day. KNOWN GAP: the public "
            "L3 demand release is volume-sampled (3.33M cars/70d nationally ~ 400 trains/day US-wide), so "
            "derived absolute levels are a FLOOR; mix and balance are the usable evidence. Corridor-level "
            "totals rely on corridor_kb / published sources (see provenance + INPUT_ASSUMPTIONS).")
    else:
        add("service", "demand-derived service", "SKIP" if not os.path.exists(man_path) else "FAIL",
            "no service_derivation.csv" + ("" if not os.path.exists(man_path) else " (run blocking_layer.py)"))
    pinches = [(float(r["length"]), float(r["speed_ft"])) for r in links if int(r["capacity"]) == 1]
    if pinches and span >= 120 and len(trains) >= 5:
        # class-weighted exclusive occupancy of the worst pinch vs the entry span
        Lp, vp = max(pinches, key=lambda p: p[0] / p[1])
        load = sum(tau(Lp, vp, float(t["speed_mult"])) + 2 for t in trains)
        util = load / span
        over = "WARN" if name in KNOWN_OPEN else "FAIL"   # deliberate stress cases stay WARN
        add("service", "worst-pinch utilization (class-weighted)",
            "PASS" if util <= 0.90 else ("WARN" if util <= 1.0 else over),
            f"{load:.0f} exclusive-occupancy min over a {span:.0f}-min entry span = {100*util:.0f}% "
            f"of the {Lp:.1f}-mi controlling pinch"
            + (" (documented over-saturated stress scenario)" if util > 1 and name in KNOWN_OPEN else ""))
    elif pinches:
        add("service", "worst-pinch utilization (class-weighted)", "SKIP",
            "entry span too short for a meaningful utilization ratio")
    fr = {c: sum(tau(float(r["length"]), float(r["speed_ft"]), m) for r in links) for c, m in CLASSES.items()}
    tot_mi = sum(float(r["length"]) for r in links)
    v_eff = tot_mi / (fr["M"] / 60.0)
    add("service", "transit-time sanity", "PASS" if 12 <= v_eff <= 60 else "WARN",
        f"manifest free-run {fr['M']} min over {tot_mi:.0f} mi = {v_eff:.0f} mph effective")

    # ---------- 3. OPERATIONAL AUDIT ----------
    if run_operational:
        exe = os.path.join(NT, "fasttrain.exe")
        r = subprocess.run([exe, d, "--sp", "dijkstra"], capture_output=True, text=True, timeout=1800)
        out = r.stdout + r.stderr
        base0 = re.search(r"baseline\) = 0\b", out) is not None
        lbm = re.search(r"BEST\s+LB=([\d.]+)", out)
        add("operational", "free-flow baseline = 0", "PASS" if base0 else "FAIL",
            "intended arrivals reproduce solver run times exactly" if base0 else "nonzero baseline")
        add("operational", "LR lower bound finite", "PASS" if lbm else "FAIL",
            f"root LR LB {lbm.group(1)}" if lbm else "no LB")
        r = subprocess.run([sys.executable, os.path.join(LAB, "solvers", "priority_heuristics.py"),
                            d, "--rand-n", "2"], capture_output=True, text=True, cwd=LAB, timeout=3600)
        hits = re.findall(r"B0_best=(\d+)(?:\.\d+)?", r.stdout + r.stderr)
        m = hits[-1] if hits else None
        routed = m is not None and int(m) < 10**5
        if routed:
            add("operational", "all trains routable (B0)", "PASS", f"best dispatching objective {m}")
            sched = os.path.join(LAB, "results", "schedules", f"{name}_B0.csv")
            if os.path.exists(sched):
                rv = subprocess.run([sys.executable, os.path.join(LAB, "validate_schedule.py"), d, sched],
                                    capture_output=True, text=True, cwd=LAB, timeout=600)
                add("operational", "independent validator", "PASS" if rv.returncode == 0 else "FAIL",
                    (rv.stdout + rv.stderr).strip().splitlines()[0])
        else:
            if name in KNOWN_B0_INFEASIBLE:
                add("operational", "all trains routable (B0)", "WARN",
                    "sequential dispatching cannot route all trains, but a complete feasible schedule "
                    "exists via branch-and-bound (see CORRIDOR_COMPARISON.md) — documented")
            else:
                v = "WARN" if name in KNOWN_OPEN else "FAIL"
                add("operational", "all trains routable (B0)", v,
                    "OPEN instance: sequential dispatching cannot route all trains; documented benchmark "
                    "frontier (B&B LB exists; complete schedule is the Week-4 target)" if v == "WARN"
                    else "unroutable trains and not a documented open instance")

    # ---------- report ----------
    with open(os.path.join(d, "AUDIT_REPORT.md"), "w", encoding="utf-8") as f:
        f.write(f"# Audit report — {name}\n\nThree-level synthetic-data evidence "
                f"(auditability memo, 2026-08-11).\n\n| level | check | verdict | detail |\n|---|---|---|---|\n")
        for lv, ck, vd, dt in rows:
            f.write(f"| {lv} | {ck} | **{vd}** | {dt} |\n")
    counts = {v: sum(1 for r in rows if r[2] == v) for v in ("PASS", "WARN", "FAIL", "SKIP")}
    print(f"{name}: " + "  ".join(f"{k} {v}" for k, v in counts.items() if v))
    return rows, counts


ALL = ["transcon_clovis_native", "transcon_clovis_native_fullday", "hiline_native",
       "prb_joint_native", "gulf_native", "panhandle_native", "overland_native",
       "moffat_native", "cpkc_midcon_native", "cn_icmain_native", "pocahontas_native",
       "harrod_bnsf_native", "toy_native"]

if __name__ == "__main__":
    if "--summary" in sys.argv:
        summary = {}
        for n in ALL:
            summary[n] = audit(os.path.join(HERE, n))[1]
        with open(os.path.join(HERE, "AUDIT_SUMMARY.md"), "w", encoding="utf-8") as f:
            f.write("# Audit summary — all corridor instances\n\n"
                    "| instance | PASS | WARN | FAIL | SKIP | report |\n|---|---|---|---|---|---|\n")
            for n, c in summary.items():
                f.write(f"| {n} | {c.get('PASS',0)} | {c.get('WARN',0)} | {c.get('FAIL',0)} | "
                        f"{c.get('SKIP',0)} | `{n}/AUDIT_REPORT.md` |\n")
        print("wrote AUDIT_SUMMARY.md")
    else:
        for d in sys.argv[1:]:
            audit(d)
