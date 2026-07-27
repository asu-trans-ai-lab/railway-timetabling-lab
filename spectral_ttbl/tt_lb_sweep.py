"""Sweep CC-LB node reduction (no-LB vs CC-LB) across train sizes & seeds.
Reports, per size: mean node reduction, mean nodes, opt-agreement, and how many runs
hit the node cap (excluded from the reduction mean)."""
import sys, time, numpy as np
import ttbl_bnb as T

SIZES = [int(x) for x in sys.argv[1].split(",")] if len(sys.argv) > 1 else [6, 8, 10, 12, 14]
SEEDS = list(range(1, 6))
NODE_LIMIT = 600_000

print(f"{'trains':>6} {'seeds_done':>10} {'mean_noLB':>10} {'mean_LB':>9} {'mean_reduc':>10} "
      f"{'opt_ok':>7} {'capped':>7}")
for n in SIZES:
    reds, n0s, n1s, ok, capped = [], [], [], 0, 0
    for s in SEEDS:
        inst = T.make_instance(n, s)
        t0 = time.time()
        r0 = T.BnB(inst, use_lb=False, node_limit=NODE_LIMIT).solve()
        r1 = T.BnB(inst, use_lb=True, node_limit=NODE_LIMIT).solve()
        hit = (r0["nodes"] >= NODE_LIMIT) or (r1["nodes"] >= NODE_LIMIT)
        if hit:
            capped += 1
            continue
        ok += int(r0["opt"] == r1["opt"])
        reds.append(1 - r1["nodes"] / max(1, r0["nodes"]))
        n0s.append(r0["nodes"]); n1s.append(r1["nodes"])
    done = len(reds)
    if done:
        print(f"{n:>6} {done:>10} {np.mean(n0s):>10.0f} {np.mean(n1s):>9.0f} "
              f"{np.mean(reds):>9.1%} {ok:>4}/{done:<2} {capped:>7}", flush=True)
    else:
        print(f"{n:>6} {0:>10} {'--':>10} {'--':>9} {'--':>10} {'0/0':>7} {capped:>7}", flush=True)
print("done")
