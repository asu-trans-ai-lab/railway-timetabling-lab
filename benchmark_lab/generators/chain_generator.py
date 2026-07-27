"""
chain_generator.py — Family C1: single-track corridor instances, FEASIBLE ANSWER FIRST.

Implements the generator discipline from PLAN.md §4:
  1. infrastructure: ordered stations 0..L, one shared track per segment (cap 1, bidirectional),
     sidings (link_type 4, dwell allowed) at selected stations for meet/pass;
  2. conflict-free base timetable: the SERIAL RESOURCE SCHEDULE (one train occupies the corridor at a
     time) — the directive's guaranteed-feasible incumbent; may be poor, but finite and reproducible;
  3. planning windows: entry (release) times; intended arrival = FREE-FLOW arrival (entry + unimpeded
     run), so the deviation objective is 0 iff a train is never delayed;
  4. controlled congestion: release_spread compression + direction mix make free-flow paths conflict
     while the serial schedule stays feasible;
  5. ground truth: manifest.json with seed, params, per-train entries/free-flow/serial arrivals,
     UB_serial, and (with --solve) the exact optimum proven by fasttrain --bnb --gap 0.

Output = NATIVE FastTrain format (runs directly in fasttrain B1/B3) + optional unified dialect via
unified_testbed/convert_native.py.

Usage:
  python chain_generator.py --out ../results/instances/C1_L4_n4_seed1 \
      --segments 4 --trains 4 --sidings 1,3 --spread 12 --opposing 0.5 --seed 1 [--solve] [--unified]
"""
from __future__ import annotations
import os, sys, json, csv, math, random, argparse, subprocess, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.normpath(os.path.join(HERE, ".."))
TS = os.path.normpath(os.path.join(LAB, ".."))
FASTTRAIN = os.path.join(TS, "spectral_ttbl", "fasttrain.exe")
CONVERT = os.path.join(TS, "unified_testbed", "convert_native.py")

SEG_LEN = 2.0          # miles -> travel = max(1,int(2*60/(60*smult)+1)) = 3 min at smult 1
SID_LEN = 2.4          # siding path slightly longer
V = 60.0
SPEEDS = [1.0, 0.75]   # train classes (passenger, freight) — smult multipliers


def travel(length, smult):
    v = V * max(smult, 1e-6)
    return max(1, int(length * 60.0 / v + 1.0))


def generate(out, segments, trains, sidings, spread, opposing, seed, horizon=720, family="c1", slack=600, maxwait=120):
    rng = random.Random(seed)
    os.makedirs(out, exist_ok=True)
    os.makedirs(os.path.join(out, "summary_log"), exist_ok=True)
    os.makedirs(os.path.join(out, "internal_timetable"), exist_ok=True)
    S = segments + 1                              # stations 1..S (native node numbers)
    # nodes
    with open(os.path.join(out, "input_node.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["node_id"])
        for n in range(1, S + 1):
            w.writerow([n])
    # links: mainline segments + parallel sidings (link_type 4 = dwell allowed)
    links = []
    if family == "c2":                                   # C2: two DIRECTIONAL tracks per segment
        for seg in range(1, S):
            links.append([seg, seg + 1, SEG_LEN, V, V, 1, 1, 0])   # up track (one-way)
            links.append([seg + 1, seg, SEG_LEN, V, V, 1, 1, 0])   # down track (one-way)
    else:
        for seg in range(1, S):
            links.append([seg, seg + 1, SEG_LEN, V, V, 1, 1, 1])   # mainline, cap1, bidir
    for st in sidings:
        if 1 <= st < S:
            links.append([st, st + 1, SID_LEN, V, V, 1, 4, 1])   # siding path, dwell allowed
    with open(os.path.join(out, "input_link.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["from", "to", "length", "speed_ft", "speed_tf", "capacity", "link_type", "bidir"])
        w.writerows(links)
    # trains: alternating/random direction per `opposing` ratio, entries over `spread` window
    tr = []
    for k in range(trains):
        down = rng.random() < opposing            # down = S -> 1
        o, d = (S, 1) if down else (1, S)
        smult = SPEEDS[k % len(SPEEDS)]
        entry = int(rng.uniform(0, spread))
        ff = entry + sum(travel(SEG_LEN, smult) for _ in range(segments))
        tr.append(dict(id=f"T{k}", o=o, d=d, smult=smult, entry=entry, intended=ff))
    tr.sort(key=lambda t: t["entry"])
    with open(os.path.join(out, "input_train_info.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["train_id", "origin", "dest", "dir", "TOB", "length", "hazmat", "speed_mult",
                    "entry", "cost_stop", "cost_early", "cost_run", "intended_arrival"])
        for t in tr:
            w.writerow([t["id"], t["o"], t["d"], 1 if t["o"] < t["d"] else 0, 0, 0, 0,
                        t["smult"], t["entry"], 0, 0, 0, t["intended"]])
    with open(os.path.join(out, "input_MOW.csv"), "w", newline="") as f:
        csv.writer(f).writerow(["from", "to", "start", "end"])
    with open(os.path.join(out, "FTSettings.ini"), "w") as f:
        f.write("[optimization]\nOptimizationHorizon=%d\nMinuteDivision=1\n"
                "[lagrangian]\nMaxNumberOfLRIterations=10\nMinimumStepSize=0.01\n"
                "NumberOfIterationsWithMemory=5\nMaxTrainWaitingTime=%d\n"
                "MaxSlackTimeAtDeparture=%d\nSafetyHeadway=3\n" % (horizon, maxwait, slack))
    # serial resource schedule: one train in the corridor at a time (guaranteed feasible)
    clear = 0; serial = []
    for t in tr:
        start = max(t["entry"], clear)
        arr = start + (t["intended"] - t["entry"])          # free-flow run once inside
        serial.append(dict(id=t["id"], depart=start, arrive=arr, deviation=arr - t["intended"]))
        clear = arr + 3                                     # headway before next admission
    ub_serial = sum(s["deviation"] for s in serial)
    manifest = dict(family=("C2_double_track" if family == "c2" else "C1_single_track"), seed=seed, segments=segments, trains=trains,
                    sidings=list(sidings), release_spread=spread, opposing_ratio=opposing,
                    horizon=horizon, speeds=SPEEDS,
                    entries={t["id"]: t["entry"] for t in tr},
                    freeflow_arrivals={t["id"]: t["intended"] for t in tr},
                    serial_schedule=serial, UB_serial=ub_serial,
                    exact_optimum=None, exact_proven=False)
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def solve_exact(out, time_limit=120):
    """fasttrain --bnb --gap 0: prove the optimum, record it in the manifest."""
    t0 = time.time()
    p = subprocess.run([FASTTRAIN, out, "--bnb", "--gap", "0", "--time", str(time_limit),
                        "--nodes", "100000"], capture_output=True, text=True, timeout=time_limit + 60)
    txt = p.stdout + p.stderr
    m = re.search(r"B&B done: nodes=(\d+).*?LB=([\d.]+)\s+UB=([\d.]+)\s+gap=([\d.]+)%.*?(\(.*\))", txt)
    if not m:
        raise RuntimeError("exact solve parse failure:\n" + txt[-400:])
    proven = ("PROVEN OPTIMAL" in m.group(5)) or ("gap target" in m.group(5) and float(m.group(4)) == 0.0)
    mf = json.load(open(os.path.join(out, "manifest.json")))
    mf["exact_optimum"] = float(m.group(3)); mf["exact_proven"] = proven
    mf["exact_nodes"] = int(m.group(1)); mf["exact_wall_s"] = round(time.time() - t0, 2)
    json.dump(mf, open(os.path.join(out, "manifest.json"), "w"), indent=2)
    return mf


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--segments", type=int, default=4)
    ap.add_argument("--trains", type=int, default=4)
    ap.add_argument("--sidings", default="2", help="comma list of station indices with sidings")
    ap.add_argument("--spread", type=float, default=12)
    ap.add_argument("--opposing", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--horizon", type=int, default=720)
    ap.add_argument("--family", default="c1", choices=["c1", "c2"])
    ap.add_argument("--slack", type=int, default=600)
    ap.add_argument("--maxwait", type=int, default=120)
    ap.add_argument("--solve", action="store_true", help="prove exact optimum via fasttrain --bnb")
    ap.add_argument("--unified", action="store_true", help="also emit unified dialect")
    a = ap.parse_args()
    sid = [int(x) for x in a.sidings.split(",") if x.strip()]
    mf = generate(a.out, a.segments, a.trains, sid, a.spread, a.opposing, a.seed, a.horizon, a.family, a.slack, a.maxwait)
    print(f"generated {mf['family']} seed={mf['seed']}: {a.trains} trains, {a.segments} segments, "
          f"sidings {sid}, UB_serial={mf['UB_serial']}")
    if a.solve:
        mf = solve_exact(a.out)
        print(f"exact optimum = {mf['exact_optimum']} (proven={mf['exact_proven']}, "
              f"nodes={mf['exact_nodes']}, {mf['exact_wall_s']}s)   [UB_serial={mf['UB_serial']}]")
    if a.unified:
        udir = a.out.rstrip("/\\") + "_unified"
        subprocess.run([sys.executable, CONVERT, a.out, udir, "--horizon-pad", "120"], check=True)
        print(f"unified dialect -> {udir}")
