"""
make_report.py — aggregate pipeline_records.csv + instance manifests into results/REPORT.md.
Usage: python make_report.py
"""
from __future__ import annotations
import os, json, csv, math
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REC = os.path.join(HERE, "results", "pipeline_records.csv")
OUT = os.path.join(HERE, "results", "REPORT.md")
IDIR = os.path.join(HERE, "results", "instances")

ORDER = ["toy_native", "C1_L4_n4_seed1", "C1_L4_n4_seed2", "C1_L6_n6_seed3",
         "C1_L8_n8_seed11", "C1_L10_n10_seed12", "C1_L12_n12_seed13"]
MLABEL = {"B0": "B0 best heuristic", "B2": "B2 MILP (restricted, UB)", "B4": "B4 CP-SAT",
          "B5": "B5 individual CG", "B6": "B6 branch-and-price"}


def f(x, nd=1):
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return "—"
        return f"{v:.{nd}f}".rstrip("0").rstrip(".")
    except Exception:
        return "—"


def main():
    rows = list(csv.DictReader(open(REC)))
    p1 = os.path.join(HERE, "results", "p1_records.csv")
    if os.path.exists(p1):
        rows += list(csv.DictReader(open(p1)))
    by = defaultdict(list)
    for r in rows:
        by[r["instance_id"]].append(r)
    manif = {}
    for iid in ORDER:
        mp = os.path.join(IDIR, iid, "manifest.json")
        if os.path.exists(mp):
            manif[iid] = json.load(open(mp))
    lines = ["# Benchmark Lab — size-ladder report (C1 single-track corridors + toy)", "",
             "One row per method per instance; all numbers from `pipeline_records.csv` "
             "(common reporting record) and generator manifests (ground truth).", ""]
    for iid in ORDER:
        if iid not in by:
            continue
        mf = manif.get(iid, {})
        n = mf.get("trains", 3 if iid == "toy_native" else "?")
        exact = mf.get("exact_optimum")
        ubser = mf.get("UB_serial")
        hdr = f"## {iid}  (trains={n}"
        if exact is not None and mf.get("exact_proven"):
            hdr += f", exact optimum={f(exact)} PROVEN"
        elif mf.get("best_known_ub") is not None:
            hdr += f", OPEN: best-known [{f(mf['best_known_lb'])}, {f(mf['best_known_ub'])}]"
        if ubser is not None:
            hdr += f", serial UB={ubser}"
        lines += [hdr + ")", "",
                  "| method | obj (UB) | LB | proven | nodes | cols | time s |",
                  "|---|---|---|---|---|---|---|"]
        # B0: best rule only
        b0 = [r for r in by[iid] if r["method"].startswith("B0_") and r["feasible"] == "True"]
        if b0:
            best = min(b0, key=lambda r: float(r["objective"]))
            lines.append(f"| B0 best ({best['method'][3:]}) | {f(best['objective'])} | — | no | | | "
                         f"{f(best['total_runtime'])} |")
        for r in by[iid]:
            mth = r["method"]
            if mth.startswith("B0_"):
                continue
            lb = f(r["best_lower_bound"], 2)
            lines.append(f"| {mth} | {f(r['objective'])} | {lb} | "
                         f"{'YES' if r['optimality_proven'] == 'True' else 'no'} | "
                         f"{r['nodes'] or ''} | {r['columns'] or ''} | {f(r['total_runtime'])} |")
        # validity check line
        if exact is not None:
            bad = []
            for r in by[iid]:
                try:
                    lbv = float(r["best_lower_bound"])
                    if lbv > float(exact) + 1e-6:
                        bad.append((r["method"], lbv))
                    if (r["feasible"] == "True" and r["best_upper_bound"] not in ("inf", "")
                            and float(r["best_upper_bound"]) < float(exact) - 1e-6):
                        bad.append((r["method"], float(r["best_upper_bound"])))
                except Exception:
                    pass
            lines.append("")
            lines.append("**Validity vs exact:** " + ("ALL BOUNDS CONSISTENT ✓" if not bad
                         else f"VIOLATIONS: {bad} ✗"))
        lines.append("")
    with open(OUT, "w", encoding="utf-8") as fo:
        fo.write("\n".join(lines))
    print("wrote", OUT, f"({len(rows)} records, {len([i for i in ORDER if i in by])} instances)")


if __name__ == "__main__":
    main()
