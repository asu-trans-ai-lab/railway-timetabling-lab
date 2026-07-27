"""
wrap_existing.py — Step 1 of the benchmark-lab build order:
wrap the EXISTING engines so their runs land in the common reporting record (record_schema.py).

Wrapped methods (ladder IDs):
  B1  fasttrain LR                  (native instances: toy_native, RAS_set3_native)
  B3  fasttrain --bnb (LR-based precedence/segment B&B)
  B7  ras_flatland full joint DP    (unified toy instance; corridor run recorded as B7_corridor)
  P1  jtv M2 hard-feasible group supercolumn CG   (jtv internal instance)
  P2  jtv M3 quadratic companion CG

Run:  python wrap_existing.py          # writes results/records.csv (+ prints a regression check)
"""
from __future__ import annotations
import os, re, csv, json, time, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TS = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from record_schema import RunRecord, append_record, load_records

FASTTRAIN = os.path.join(TS, "spectral_ttbl", "fasttrain.exe")
JOINT = os.path.join(TS, "unified_testbed", "engines", "ras_flatland", "joint_timetable_solver.exe")
DATA = os.path.join(TS, "FTT_handoff", "data")
UNI = os.path.join(TS, "unified_testbed", "instances")
JTV_OUT = os.path.join(TS, "unified_testbed", "engines", "jtv", "outputs", "summary.json")
RECORDS = os.path.join(HERE, "results", "records.csv")


def sh(cmd, cwd=None, timeout=600):
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.stdout + p.stderr, time.time() - t0


# ---------------------------------------------------------------- B1: fasttrain LR
def wrap_b1(instance_dir, instance_id):
    out, wall = sh([FASTTRAIN, instance_dir])
    m = re.search(r"BEST\s+LB=([\d.]+)\s+UB=([\d.]+)\s+gap=([\d.]+)%", out)
    if not m:
        raise RuntimeError(f"B1 parse failure on {instance_id}:\n{out[-400:]}")
    lb, ub = float(m.group(1)), float(m.group(2))
    return RunRecord(instance_id=instance_id, method="B1_lr_fasttrain",
                     objective=ub, best_lower_bound=lb, best_upper_bound=ub,
                     feasible=True, optimality_proven=False, total_runtime=wall,
                     hard_conflicts=0, config=json.dumps({"sp": "dag", "iters": "ini"}),
                     notes="subgradient LR; UB=priority rule (feasible)")


# ---------------------------------------------------------------- B3: fasttrain --bnb
def wrap_b3(instance_dir, instance_id, extra, notes):
    out, wall = sh([FASTTRAIN, instance_dir, "--bnb"] + extra)
    m = re.search(r"B&B done: nodes=(\d+) branched=\d+\s+LB=([\d.]+)\s+UB=([\d.]+)\s+gap=([\d.]+)%.*?(\(.*\))",
                  out)
    if not m:
        raise RuntimeError(f"B3 parse failure on {instance_id}:\n{out[-400:]}")
    nodes, lb, ub, status = int(m.group(1)), float(m.group(2)), float(m.group(3)), m.group(5)
    proven = ("PROVEN OPTIMAL" in status) or ("gap target" in status and float(m.group(4)) == 0.0)
    return RunRecord(instance_id=instance_id, method="B3_lr_bnb_segment",
                     objective=ub, best_lower_bound=lb, best_upper_bound=ub,
                     feasible=True, optimality_proven=proven, total_runtime=wall,
                     nodes=nodes, hard_conflicts=0,
                     config=json.dumps({"args": extra}), notes=notes + " " + status)


# ---------------------------------------------------------------- B7: joint DP (unified)
def wrap_b7(unified_dir, instance_id, args, method, notes):
    rdir = os.path.join(HERE, "results", "b7_" + instance_id)
    os.makedirs(rdir, exist_ok=True)
    out, wall = sh([JOINT, unified_dir, rdir] + args)
    srow = list(csv.DictReader(open(os.path.join(rdir, "summary.csv"))))[0]
    final_c = int(srow["final_hard_conflicts"])
    return RunRecord(instance_id=instance_id, method=method,
                     objective=float("nan"),               # objective scale differs; conflicts are the check
                     feasible=(final_c == 0), optimality_proven=False, total_runtime=wall,
                     dp_labels=int(srow["reduced_states"]), hard_conflicts=final_c,
                     config=json.dumps({"args": args}),
                     notes=notes + f" init_conflicts={srow['initial_hard_conflicts']}")


# ---------------------------------------------------------------- P1/P2: jtv summary.json
def wrap_jtv():
    with open(JTV_OUT) as f:
        s = json.load(f)
    recs = []
    m1 = s["M1_M3_column_generation"]["M1"]
    m2, m3 = s["M1_M3_column_generation"]["M2"], s["M1_M3_column_generation"]["M3"]
    e3 = s["E3_fractional_master"]
    # E3 odd-cycle instance: individual vs group master (the convexification result)
    recs.append(RunRecord(instance_id="jtv_e3_oddcycle", method="B5_individual_master",
                          objective=e3["individual_integer"], best_lower_bound=e3["individual_integer"],
                          best_upper_bound=e3["individual_integer"], feasible=True,
                          optimality_proven=True,
                          notes=f"LP={e3['individual_lp']} integer={e3['individual_integer']} "
                                f"gap={e3['individual_integrality_gap']} (100% fractional LP)"))
    recs.append(RunRecord(instance_id="jtv_e3_oddcycle", method="P1_group_supercolumn_master",
                          objective=e3["group_integer"], best_lower_bound=e3["group_lp"],
                          best_upper_bound=e3["group_integer"], feasible=True,
                          optimality_proven=True,
                          notes=f"group LP={e3['group_lp']} = integer -> gap 0 (convexification)"))
    # M1-M3 CG ladder instance (pair case): objectives only, no bound claims across instances
    recs.append(RunRecord(instance_id="jtv_pair", method="B5_individual_cg",
                          objective=m1["objective"], best_upper_bound=float("inf"), feasible=False,
                          columns=m1["columns"], pricing_calls=m1["iterations"],
                          notes="jtv M1 (individual columns; capacity-infeasible pair remains)"))
    recs.append(RunRecord(instance_id="jtv_pair", method="P1_group_supercolumn_cg",
                          objective=m2["objective"], best_upper_bound=m2["objective"], feasible=True,
                          columns=m2["columns"], pricing_calls=m2["iterations"],
                          notes="jtv M2 hard-feasible group column = joint optimum"))
    recs.append(RunRecord(instance_id="jtv_pair", method="P2_quadratic_companion_cg",
                          objective=m3["objective"], best_upper_bound=m3["objective"], feasible=True,
                          columns=m3["columns"], pricing_calls=m3["iterations"],
                          notes=f"jtv M3; quadratic candidates={m3.get('quadratic_candidates', 0)}"))
    return recs


def main():
    if os.path.exists(RECORDS):
        os.remove(RECORDS)
    recs = []
    print("B1 LR: toy + RAS Set 3 ...")
    recs.append(wrap_b1(os.path.join(DATA, "toy_native"), "toy_native"))
    recs.append(wrap_b1(os.path.join(DATA, "RAS_set3_native"), "RAS_set3_native"))
    print("B3 B&B: toy (prove optimal) + RAS (budget) ...")
    recs.append(wrap_b3(os.path.join(DATA, "toy_native"), "toy_native",
                        ["--gap", "0"], "exact segment-enum B&B"))
    recs.append(wrap_b3(os.path.join(DATA, "RAS_set3_native"), "RAS_set3_native",
                        ["--nodes", "20", "--time", "120", "--branch", "dual"], "budgeted"))
    print("B7 joint DP: toy unified ...")
    recs.append(wrap_b7(os.path.join(UNI, "toy_native_unified"), "toy_native",
                        ["--budget=6", "--rho=0.18", "--radius=0", "--max-group=3",
                         "--full-limit=3", "--max-rounds=10"], "B7_full_joint_dp",
                        "corridor+full verify"))
    print("P1/P2/B5: jtv outputs ...")
    recs += wrap_jtv()
    for r in recs:
        append_record(RECORDS, r)
    print(f"\n{len(recs)} records -> {RECORDS}\n")
    # regression assertions against known ground truth
    rows = {(r["instance_id"], r["method"]): r for r in load_records(RECORDS)}
    ras = rows[("RAS_set3_native", "B1_lr_fasttrain")]
    # DAG SP is the default; its tie-breaking gives LB 2237.2 / UB 3580 (Dijkstra: 2278.9 / 3803 —
    # LR degeneracy, both valid; see ntrack_bnb_paper §4).
    assert abs(float(ras["best_lower_bound"]) - 2237.2) < 1.0, ras
    assert abs(float(ras["best_upper_bound"]) - 3580.0) < 1.0, ras
    toyb = rows[("toy_native", "B3_lr_bnb_segment")]
    assert toyb["optimality_proven"] == "True" and abs(float(toyb["objective"]) - 12.0) < 1e-6, toyb
    toy7 = rows[("toy_native", "B7_full_joint_dp")]
    assert toy7["hard_conflicts"] == "0", toy7
    print("REGRESSION OK: RAS B1 LB 2237.2/UB 3580 (dag) | toy B3 proven optimal 12 | toy B7 0 conflicts")


if __name__ == "__main__":
    main()
