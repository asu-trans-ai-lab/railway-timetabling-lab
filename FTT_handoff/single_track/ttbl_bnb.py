"""
Stage A -- faithful Python port of the TrainTimetablingLite / Zhou & Zhong (2007)
single-track train-timetabling branch-and-bound, in the WAY-2 TIMETABLE FORMAT.

This REPLICATES the real working B&B (the base Simon asked to build on), not the PDF.
State = timetable  Table[train][section][0=arrive, 1=depart, 2=floor(earliest entry), 3=fix].
Waiting is implied by the floor[2] being pushed up (a train "waits" before entering a
section) -- i.e. departure differences, exactly the C++ representation.

Faithful to the C++ (def.cpp / Node.cpp / node.h):
  * Single-track line, S sections (default 8), 2 directions (0 up: sections 0..S-1;
    1 down: sections S-1..0). One train per section at a time.
  * Run time depends on train TYPE only: fast=10, slow=18 per section (both directions).
  * Single headway constant g_I (=5): used both as the conflict window and the push-back gap.
  * Schedule generation = forward sweep ExtendJob: arrive@section = depart@prev section
    (or start time), clamped up to floor[2]; depart = arrive + run.
  * Conflict = earliest-completing active task's section is wanted by >=2 active tasks
    within the headway window -> BRANCH: one child per contending train ("that train goes
    first"); losers' floor pushed to winner.depart + g_I and re-swept (meet/pass order).
  * Lower bound (toggle) = crossing-conflict (CC) based delay estimate.
  * Search = DFS (LIFO); fathom child if total_cost + LB >= incumbent.
  * Objective = sum of train completion times (exit of last section); delay = opt - pure.

Run:  python ttbl_bnb.py            # solves seeds, compares no-LB vs CC-LB node counts
"""
from __future__ import annotations
import argparse, math
from dataclasses import dataclass, field

# ---- constants (C++ railView.cpp / def.cpp) ----
S_SECTIONS = 8
G_I = 5                      # headway constant (single value; station g=0, segment h=g_I)
RUN_FAST, RUN_SLOW = 10, 18
TYPE_RATIO = 0.2            # P(fast)
REL_INTERVAL = 60          # mean same-direction departure spacing
ERLANG_ORDER = 4


# ============================================================= MSVC RNG (for reproducible instances)
class MSVCRand:
    """Reproduces MSVC rand(): seed=seed*214013+2531011; return (seed>>16)&0x7fff."""
    def __init__(self, seed):
        self.s = seed & 0xFFFFFFFF
    def rand(self):
        self.s = (self.s * 214013 + 2531011) & 0xFFFFFFFF
        return (self.s >> 16) & 0x7FFF
    def u01(self):
        return (self.rand() + 1) / 32769.0     # exclusive (0,1)


def erlang(rng, ave, order):
    if order == 10:
        return int(ave)
    prod = 1.0
    for _ in range(order):
        prod *= rng.u01()
    return int(-math.log(prod) * ave / order)


# ============================================================= instance
@dataclass
class Instance:
    n: int
    S: int
    direction: list          # per train 0/1
    ttype: list              # per train 0 fast /1 slow
    start: list              # per train release time
    def run(self, train):
        return RUN_FAST if self.ttype[train] == 0 else RUN_SLOW


def make_instance(n_trains, seed, S=S_SECTIONS, rel=REL_INTERVAL, order=ERLANG_ORDER,
                  ratio=TYPE_RATIO):
    rng = MSVCRand(seed)
    direction, ttype, start = [], [], []
    rel_up = rel_dn = 0
    while len(direction) < n_trains:
        up_iv = erlang(rng, rel, order)
        dn_iv = erlang(rng, rel, order)
        rel_up += up_iv; rel_dn += dn_iv
        t_up = 0 if rng.u01() < ratio else 1     # up train type
        t_dn = 0 if rng.u01() < ratio else 1     # down train type
        for d, ty, st in ((0, t_up, rel_up), (1, t_dn, rel_dn)):
            if len(direction) < n_trains:
                direction.append(d); ttype.append(ty); start.append(st)
    return Instance(n=n_trains, S=S, direction=direction, ttype=ttype, start=start)


# ============================================================= timetable node
def section_no(direction, index, S):
    return index if direction == 0 else S - 1 - index


@dataclass
class Task:
    train: int
    section: int
    release: int
    completion: int          # = depart + G_I (buffered)


class Node:
    __slots__ = ("table", "active", "total_cost", "active_flag")

    def __init__(self, inst):
        self.table = [[[0, 0, 0, 0] for _ in range(inst.S)] for _ in range(inst.n)]
        self.active = []     # list[Task], kept sorted by completion asc
        self.total_cost = 0
        self.active_flag = True

    def clone(self):
        nd = Node.__new__(Node)
        nd.table = [[cell[:] for cell in row] for row in self.table]
        nd.active = [Task(t.train, t.section, t.release, t.completion) for t in self.active]
        nd.total_cost = self.total_cost
        nd.active_flag = True
        return nd


# ============================================================= solver
class BnB:
    def __init__(self, inst, use_lb=True, node_limit=2_000_000, collect=False,
                 collect_cap=200_000):
        self.inst = inst
        self.use_lb = use_lb
        self.node_limit = node_limit
        self.opt = float("inf")
        self.opt_table = None
        self.n_nodes = 0
        self.pure = None
        self.root_est = 0
        # ---- low-rank collection ----
        self.collect = collect
        self.collect_cap = collect_cap
        self.col_cells = []     # list[frozenset[cell]] : train trajectories (the H columns)
        self.act_foot = []      # list[frozenset[cell]] : meet-pass action resource-time footprints
        self.act_resp = []      # list[float]           : action response = child cost delta (+LB)
        self.act_meta = []      # list[(node_cost, sec, win_train, n_conflict)]

    def _train_cells(self, table, train):
        """resource-time occupancy of a train's full trajectory (section,t cells incl. headway)."""
        inst, S = self.inst, self.inst.S
        d = inst.direction[train]; cells = set()
        for k in range(S):
            sta = section_no(d, k, S)
            a, dep = table[train][sta][0], table[train][sta][1]
            for t in range(a, dep + G_I):
                cells.add(("S", sta, t))
        return frozenset(cells)

    # --- schedule generation (forward sweep) ---
    def extend_job(self, table, train, ONo):
        inst, S = self.inst, self.inst.S
        d = inst.direction[train]; r = inst.run(train)
        for k in range(ONo, S):
            sta = section_no(d, k, S)
            if k == 0:
                t = inst.start[train]
            else:
                t = table[train][section_no(d, k - 1, S)][1]
            if t < table[train][sta][2]:
                t = table[train][sta][2]
            table[train][sta][0] = t
            table[train][sta][1] = t + r

    def total_cost(self, table):
        inst, S = self.inst, self.inst.S
        c = 0
        for i in range(inst.n):
            last = section_no(inst.direction[i], S - 1, S)
            c += table[i][last][1]
        return c

    def add_active(self, node, train, section, release, completion):
        """insert sorted by completion asc; ties: new inserts before existing (C++ '>')."""
        task = Task(train, section, release, completion)
        a = node.active
        idx = len(a)
        for i, t in enumerate(a):
            if t.completion > completion:
                idx = i; break
        a.insert(idx, task)

    def init_root(self):
        inst, S = self.inst, self.inst.S
        nd = Node(inst)
        for i in range(inst.n):
            self.extend_job(nd.table, i, 0)
        for i in range(inst.n):
            s0 = section_no(inst.direction[i], 0, S)
            self.add_active(nd, i, s0, nd.table[i][s0][0], nd.table[i][s0][1] + G_I)
        nd.total_cost = self.total_cost(nd.table)
        self.pure = nd.total_cost
        self.root_est = self.est_delay(nd.table)
        return nd

    # --- crossing-conflict lower bound (CC) ---
    def est_delay(self, table):
        inst, S = self.inst, self.inst.S
        ups = [i for i in range(inst.n) if inst.direction[i] == 0]
        dns = [i for i in range(inst.n) if inst.direction[i] == 1]
        conflicts = []
        for k in range(S):
            for i in ups:
                for j in dns:
                    if table[i][k][3] or table[j][k][3]:            # order already fixed -> no future delay
                        continue
                    ai, di = table[i][k][0], table[i][k][1]
                    aj, dj = table[j][k][0], table[j][k][1]
                    if di >= aj and dj >= ai:                       # interval overlap
                        cd = min(dj - ai, di - aj)                  # min overlap (Prop 1)
                        cct = min(di, dj)
                        conflicts.append((cct, cd, i, j))
        # Valid CC bound: over a set of INDEPENDENT (disjoint-train) crossing conflicts,
        # each forces >= h + min_overlap extra delay (Prop 1). Greedy disjoint matching,
        # earliest-completion first. (The g_I*ALLconflicts variant over-prunes -> invalid.)
        conflicts.sort(key=lambda c: c[0])
        used = set(); lb = 0
        for cct, cd, i, j in conflicts:
            if i not in used and j not in used:
                lb += G_I; used.add(i); used.add(j)    # >=1 headway per disjoint conflict (valid)
        return lb

    # --- node expansion: advance no-conflict trains; branch on first conflict ---
    def expand(self, node):
        """Return list of child nodes (empty if node completes or is pruned)."""
        inst = self.inst
        if self.collect and len(self.col_cells) < self.collect_cap:
            for i in range(inst.n):                       # log each train's current trajectory (H column)
                self.col_cells.append(self._train_cells(node.table, i))
        while True:
            if not node.active:
                # complete node -> feasible incumbent
                c = self.total_cost(node.table)
                if c < self.opt:
                    self.opt = c
                    self.opt_table = [[cell[:] for cell in row] for row in node.table]
                return []
            task0 = node.active[0]
            sec = task0.section; ct0 = task0.completion
            conflict = [t for t in node.active if t.section == sec and t.release < ct0 + G_I]
            if len(conflict) == 1:
                self._advance(node, task0)             # no conflict: fix & move on (in place)
                continue
            return self._branch(node, conflict, sec)

    def _advance(self, node, task):
        """Fix the single train on its section and advance it one section (no new node)."""
        inst, S = self.inst, self.inst.S
        d = inst.direction[task.train]
        node.table[task.train][task.section][3] = 1
        node.active.remove(task)
        cur_idx = (task.section if d == 0 else S - 1 - task.section)   # progress index
        nxt = cur_idx + 1
        if nxt < S:
            ns = section_no(d, nxt, S)
            self.add_active(node, task.train, ns,
                            node.table[task.train][ns][0], node.table[task.train][ns][1] + G_I)

    def _branch(self, node, conflict, sec):
        inst, S = self.inst, self.inst.S
        children = []
        for k in range(len(conflict)):
            win = conflict[k].train
            child = node.clone()
            comp = child.table[win][sec][1]
            child.table[win][sec][3] = 1
            for s in range(len(conflict)):
                if s == k:
                    continue
                lose = conflict[s].train
                child.table[lose][sec][0] = comp + G_I
                child.table[lose][sec][2] = comp + G_I
                d2 = inst.direction[lose]
                lose_idx = sec if d2 == 0 else S - 1 - sec
                self.extend_job(child.table, lose, lose_idx)
            child.total_cost = self.total_cost(child.table)
            lb = self.est_delay(child.table) if self.use_lb else 0
            if self.collect and len(self.act_foot) < self.collect_cap:
                comp_w = child.table[win][sec][1]
                foot = frozenset(("S", sec, t)
                                 for t in range(child.table[win][sec][0], comp_w + G_I))
                self.act_foot.append(foot)
                self.act_resp.append(float(child.total_cost - node.total_cost + lb))
                self.act_meta.append((node.total_cost, sec, win, len(conflict)))
            if child.total_cost + lb >= self.opt:
                continue                                   # fathom
            # rebuild active list: parent active tasks minus winner's current, refreshed;
            # then advance winner one section.
            new_active = []
            wtask = conflict[k]
            for t in node.active:
                if t.train == wtask.train and t.section == wtask.section:
                    continue
                sc = t.section
                new_active.append(Task(t.train, sc, child.table[t.train][sc][0],
                                       child.table[t.train][sc][1] + G_I))
            child.active = []
            for t in sorted(new_active, key=lambda z: z.completion):
                self.add_active(child, t.train, t.section, t.release, t.completion)
            # advance winner
            d = inst.direction[win]
            child.table[win][sec][3] = 1
            cur_idx = sec if d == 0 else S - 1 - sec
            nxt = cur_idx + 1
            if nxt < S:
                ns = section_no(d, nxt, S)
                self.add_active(child, win, ns, child.table[win][ns][0],
                                child.table[win][ns][1] + G_I)
            children.append(child)
        return children

    # --- DFS driver ---
    def solve(self):
        stack = [self.init_root()]
        while stack:
            if self.n_nodes >= self.node_limit:
                break
            node = stack.pop()                              # LIFO = DFS
            if not node.active_flag:
                continue
            self.n_nodes += 1
            children = self.expand(node)
            stack.extend(children)                          # last child expanded first
        return dict(opt=self.opt, pure=self.pure, delay=self.opt - self.pure,
                    root_est=self.root_est, nodes=self.n_nodes)


# ============================================================= driver
def run(n_trains=6, seeds=(1, 2, 3, 4, 5), verbose=True):
    if verbose:
        print(f"\n{'='*72}\n  Stage-A B&B port  |  single-track, S={S_SECTIONS} sections, "
              f"{n_trains} trains\n{'='*72}")
        print(f"  {'seed':>4} {'opt':>7} {'pure':>7} {'delay':>6} {'rootLB':>7} "
              f"{'nodes(noLB)':>11} {'nodes(CC-LB)':>12} {'opt==?':>7}")
    rows = []
    for s in seeds:
        inst = make_instance(n_trains, s)
        r0 = BnB(inst, use_lb=False).solve()
        r1 = BnB(inst, use_lb=True).solve()
        same = "OK" if r0["opt"] == r1["opt"] else "MISMATCH"
        rows.append((s, r1))
        if verbose:
            print(f"  {s:>4} {r1['opt']:>7} {r1['pure']:>7} {r1['delay']:>6} "
                  f"{r1['root_est']:>7} {r0['nodes']:>11} {r1['nodes']:>12} {same:>7}")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trains", type=int, default=6)
    ap.add_argument("--seeds", type=int, nargs="*", default=[1, 2, 3, 4, 5])
    args = ap.parse_args()
    run(n_trains=args.trains, seeds=tuple(args.seeds))
