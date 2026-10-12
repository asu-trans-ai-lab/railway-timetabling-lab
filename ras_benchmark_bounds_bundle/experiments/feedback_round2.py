import json, sys
sys.path.insert(0, '.')
from pathlib import Path
from experiments.blocking_feedback import feedback_dataset, run_blocking, official_check, schedule_all, OUT, WORK
prev = WORK / "datasets" / "base_capacity+price+runtime+cut"
ds = feedback_dataset("after_all", ["capacity", "price", "runtime", "cut"], prev=prev)
print("changes", json.loads((ds / "feedback_changes.json").read_text()), flush=True)
plan = run_blocking(ds, "fb_round2", ["--penalty-per-c5-overflow", "100000"])
o = official_check(plan)
print("official", o, flush=True)
after = schedule_all(plan, "after_round2", 60, stress=False)
d = json.loads((OUT / "loop.json").read_text())
d["round2"] = {"kinds": ["capacity", "price", "runtime", "cut"], "from": "after_all",
               "changes": json.loads((ds / "feedback_changes.json").read_text()), "official": o,
               "after": {"total_delay": sum(r.get("delay") or 0 for r in after),
                         "by_corridor": {r["corridor"]: r.get("delay") for r in after},
                         "max_delay": max((r.get("max_delay") or 0 for r in after), default=0),
                         "cuts": sum(bool(r.get("cut")) for r in after)}}
(OUT / "loop.json").write_text(json.dumps(d, indent=1))
print("round2", d["round2"]["after"], flush=True)
