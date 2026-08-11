"""Generate input_train_info.csv for the Harrod (2011) BNSF-style instance.

Traffic per Harrod Table 5 / section 6.4 (flexible-freight, mixed classes):
  - 10 WB + 10 EB general freight, dispatch headway 26 min (10 round trips)
  - every 3rd freight departure in each direction is an intermodal (speed_mult 1.25,
    approximating Table 7 timings)
  - 1 passenger train each direction (speed_mult 1.5, approximating Table 4)
Objective convention of fasttrain: intended_arrival = entry + class free-running time,
so deviation counts delay caused by conflicts only.
"""
L = [12.1, 10.1, 4.5, 11.7, 6.3, 11.5]
VFT = [41.49, 36.73, 49.09, 28.65, 24.39, 27.06]   # WB (from->to)
VTF = [58.08, 63.79, 60.00, 61.04, 39.79, 41.82]   # EB (to->from)

def run(l, v, m):                      # fasttrain: max(1, int(l*60/(v*m)+1))
    return max(1, int(l * 60.0 / (v * m) + 1.0))

def freerun(speeds, m):
    return sum(run(l, v, m) for l, v in zip(L, speeds))

classes = {"F": 1.0, "I": 1.25, "P": 1.5}
for c, m in classes.items():
    print(f"# class {c}: WB freerun {freerun(VFT, m)}  EB freerun {freerun(VTF, m)}")

rows = []
def add(tid, o, d, direc, m, entry, speeds):
    rows.append(f"{tid},{o},{d},{direc},0,0,0,{m},{entry},0,0,0,{entry + freerun(speeds, m)}")

for k in range(10):                                  # WB: node 1 -> 7
    dep = 26 * k
    cls = "I" if k % 3 == 2 else "F"                 # every 3rd departure intermodal
    add(f"WB{k+1}{cls}", 1, 7, 1, classes[cls], dep, VFT)
for k in range(10):                                  # EB: node 7 -> 1, staggered half-headway
    dep = 13 + 26 * k
    cls = "I" if k % 3 == 2 else "F"
    add(f"EB{k+1}{cls}", 7, 1, 2, classes[cls], dep, VTF)
add("WBP", 1, 7, 1, classes["P"], 120, VFT)          # 1 passenger per direction
add("EBP", 7, 1, 2, classes["P"], 150, VTF)

hdr = "train_id,origin,dest,dir,TOB,length,hazmat,speed_mult,entry,cost_stop,cost_early,cost_run,intended_arrival"
open("input_train_info.csv", "w").write(hdr + "\n" + "\n".join(rows) + "\n")
print(f"wrote {len(rows)} trains")
