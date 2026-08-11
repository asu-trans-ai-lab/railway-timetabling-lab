"""plot_incumbent.py — time-space (Marey) diagram of the B&B incumbent schedule.

Reads <native_dir>/internal_timetable/incumbent_timetable.csv (exported by
fasttrain --bnb) plus the native input files, reconstructs each train's
trajectory (entry-node waits shown as horizontal holds; occupancy windows
include the headway tail), and draws distance-vs-time with block boundaries.

Usage: python plot_incumbent.py <native_dir> [--out out.png]
"""
from __future__ import annotations
import csv, os, sys, argparse
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

CLASS_COLOR = {"F": "#2a78d6", "I": "#eb6834", "P": "#1baf7a", "G": "#eda100"}  # validated slots 1-4
CLASS_NAME = {"F": "general freight", "I": "intermodal", "P": "passenger", "G": "grain/bulk"}


def tclass(smult):                       # class from speed multiplier (see make_trains.py)
    if smult > 1.4:
        return "P"
    if smult > 1.05:
        return "I"
    return "F" if smult >= 0.95 else "G"


def travel(length, v, m):                # fasttrain's travel-time formula
    return max(1, int(length * 60.0 / (v * m) + 1.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--out", default=None)
    ap.add_argument("--title", default=None)
    args = ap.parse_args()
    d = args.dir

    links = {}                           # link idx (file order) -> dict
    with open(os.path.join(d, "input_link.csv")) as f:
        for i, r in enumerate(csv.DictReader(f)):
            links[i] = dict(a=int(r["from"]), b=int(r["to"]), L=float(r["length"]),
                            vFT=float(r["speed_ft"]), vTF=float(r["speed_tf"]))
    # cumulative-mile position of each node along the corridor (node numbering = corridor order)
    pos, mile = {}, 0.0
    for i in sorted(links):
        pos[links[i]["a"]] = mile
        mile += links[i]["L"]
        pos[links[i]["b"]] = mile

    smult, intended, entry = {}, {}, {}
    with open(os.path.join(d, "input_train_info.csv")) as f:
        for r in csv.DictReader(f):
            smult[r["train_id"]] = float(r["speed_mult"])
            intended[r["train_id"]] = float(r["intended_arrival"])
            entry[r["train_id"]] = float(r["entry"])

    rows = defaultdict(list)
    with open(os.path.join(d, "internal_timetable", "incumbent_timetable.csv")) as f:
        for r in csv.DictReader(f):
            rows[r["train_id"]].append(dict(link=int(r["link"]), t0=int(r["t_first"]),
                                            t1=int(r["t_last"]), arr=int(r["arrival"])))

    fig, ax = plt.subplots(figsize=(13, 6.5))
    seen_class = {}
    nlab = {"top": 0, "bot": 0}          # stagger destination labels to avoid collisions
    for tid, segs in rows.items():
        segs.sort(key=lambda s: s["t0"])
        m = smult[tid]
        c = tclass(m)
        color = CLASS_COLOR[c]
        # orient: first segment's endpoints; start node = the one NOT shared with 2nd segment
        xs, ys = [], []
        cur = None
        for k, s in enumerate(segs):
            lk = links[s["link"]]
            ends = {lk["a"], lk["b"]}
            if cur is None:              # infer start node from the next segment's shared end
                if len(segs) > 1:
                    nxt = links[segs[1]["link"]]
                    shared = ends & {nxt["a"], nxt["b"]}
                    cur = (ends - shared).pop()
                else:
                    cur = lk["a"]
            a, b = cur, (ends - {cur}).pop()
            ab = (a == lk["a"])          # direction of traversal on this link
            tt = travel(lk["L"], lk["vFT"] if ab else lk["vTF"], m)
            wait = (s["t1"] - s["t0"]) - tt          # headway=1: window = wait + tt + 1 minutes
            xs += [s["t0"], s["t0"] + max(0, wait), s["t1"]]
            ys += [pos[a], pos[a], pos[b]]
            cur = b
        lw = 2.6 if c == "P" else 1.8
        ax.plot(xs, ys, color=color, lw=lw, solid_capstyle="round",
                label=CLASS_NAME[c] if c not in seen_class else None, zorder=3 if c == "P" else 2)
        seen_class[c] = True
        # direct label + delay annotation at the destination end
        delay = segs[-1]["arr"] - intended[tid]
        lab = tid if delay == 0 else f"{tid} +{delay:.0f}"
        up = ys[-1] > ys[0]
        k = nlab["top" if up else "bot"]; nlab["top" if up else "bot"] += 1
        dy = (5 + 9 * (k % 2)) * (1 if up else -1)          # alternate two label rows
        ax.annotate(lab, (xs[-1], ys[-1]), textcoords="offset points",
                    xytext=(2, dy), fontsize=6.5, color="#555555",
                    ha="left", va="bottom" if up else "top", annotation_clip=False)

    for n, p in pos.items():             # block boundaries (crossover points)
        ax.axhline(p, color="#dddddd", lw=0.7, zorder=1)
        ax.annotate(f"node {n}", (0, p), textcoords="offset points", xytext=(-6, 0),
                    fontsize=7, color="#888888", ha="right", va="center",
                    annotation_clip=False)
    ax.set_xlabel("time (min)")
    ax.set_ylabel("milepost (mi)")
    ax.set_title(args.title or os.path.basename(os.path.normpath(d)) +
                 " — B&B incumbent timetable (labels: train +delay min)", fontsize=11)
    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.margins(x=0.02)
    ax.set_xlim(right=ax.get_xlim()[1] * 1.07)   # room for destination labels
    ax.set_ylim(-3, mile + 3)                     # room for the staggered label rows
    fig.tight_layout()
    out = args.out or os.path.join(d, "internal_timetable", "incumbent_spacetime.png")
    fig.savefig(out, dpi=160, facecolor="white")
    print("wrote", out)


if __name__ == "__main__":
    main()
