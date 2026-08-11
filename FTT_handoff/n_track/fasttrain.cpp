// ============================================================================
//  FastTrain (clean reimplementation) — Meng & Zhou (2014) N-track timetabling
//  Lagrangian-relaxation lower bound + priority-rule upper bound.
//
//  Portable, standard C++17 only. No MFC, no <windows.h>, no MSVC secure-CRT.
//  Faithful to the original FastTrain semantics:
//    * objective            = sum_f | arrival_f - intended_f |   (arrival deviation)
//    * link run time         = max(1, (int)(length*60/(speed_dir*mult) + 1))
//    * per-train SP cost      = sum of resource prices over occupied (link,time) cells
//                               + |arrival - intended| on the destination arc
//    * free departure slack   : depart anywhere in [entry, entry+1+slack] at zero cost
//    * dwell                   : only on siding links (link_type==4), up to maxwait
//    * headway=h               : a traversal occupies link cells [enter, exit+h);
//                                priced window [enter, exit+h-1] inclusive
//    * capacity                : per (link,time) cell; MOW windows set capacity 0
//    * LR dual update          : step = max(minstep, 1/(k+1)); price += step*(usage-cap);
//                                clamp >=0; relax-and-cut (drop a price unused for >mem iters)
//    * LB                      = max_k [ sum_f minCost_f - sum_{cells: price<=1e4} price ]
//    * UB                      = priority-rule (deviation-ratio order) sequential schedule
//
//  Build:  g++ -O2 -std=c++17 -o fasttrain fasttrain.cpp
//  Run:    ./fasttrain <data_dir>   (dir with input_node/link/train_info/MOW.csv + FTSettings.ini)
// ============================================================================
#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <climits>
#include <string>
#include <vector>
#include <map>
#include <queue>
#include <algorithm>
#include <fstream>
#include <sstream>
#include <memory>
#include <chrono>
using namespace std;

static const double INF = 1e18;
static const double MAXP = 1e6;   // blocked-cell price (MOW)

// ---------------------------------------------------------------- data
struct Link { int a, b, cap, ltype; double length, vFT, vTF; bool bidir; };
struct Train { string id; int o, d, entry; double smult, intended; };

struct Settings {
    int horizon = 1440, interval = 1, minute = 1, paths = 10, maxiter = 10;
    int mem = 5, maxwait = 120, slack = 1200, headway = 3;
    double minstep = 0.01;
    // MonotoneRouting=1 (corridor instances): a train may only move toward its destination
    // in node-index order — no hold-and-reverse ("folded") trajectories. This makes the SP's
    // dual cost equal its deduped occupancy, so the LR lower bound is exact w.r.t. the same
    // feasibility semantics the validator enforces. Leave 0 for general networks (RAS Set 3).
    bool monotone = false;
};

// ---------------------------------------------------------------- B&B branching restrictions (B1)
// A node's restrictions: per link, a list of half-open [t0,t1) windows during which the *current*
// train being routed may NOT occupy that link. A traversal of link l occupying cells
// [enter, exit+headway) is rejected if that occupancy window intersects any forbidden window of l.
// Building two children that forbid the symmetric meet/pass windows of a conflicting train pair is
// the 2-way disjunction in BRANCH_AND_BOUND_PLAN.md (§2).
using TrainRestr   = vector<vector<pair<int,int>>>;    // [nL] -> forbidden [t0,t1) windows for ONE train
using Restrictions = vector<TrainRestr>;               // [nTrain][nL] -> per-train forbidden windows
static Restrictions emptyR;                            // sized to [nTrain][nL] once data is known

// Relaxed primal captured at a B&B node (last LR iteration): per-train arrivals + occupied cells,
// the aggregate usage, the worst capacity violation, and the true deviation objective of that primal.
struct Solution {
    bool routable = true;                  // false if some train has no feasible path under restrictions
    vector<int> arr;                       // [nTrain] arrival (−1 if unrouted)
    vector<vector<pair<int,int>>> occ;     // [nTrain] occupied (link,time) cells
    vector<vector<short>> usage;           // [nL][T]
    double dev = 0;                        // Σ|arr−intended| of this primal
    int maxviol = 0;                       // max over cells of usage−cap
    vector<int> ubArr;                     // arrivals of the best feasible priority-rule UB at this node
    vector<vector<pair<int,int>>> ubOcc;   // [nTrain] occupied cells of that UB schedule (for export)
};

static vector<string> split(const string& s, char d) {
    vector<string> out; string cur; stringstream ss(s);
    while (getline(ss, cur, d)) out.push_back(cur);
    return out;
}
static string trim(const string& s) {
    size_t i = s.find_first_not_of(" \t\r\n"); if (i == string::npos) return "";
    size_t j = s.find_last_not_of(" \t\r\n"); return s.substr(i, j - i + 1);
}
static string lower(string s) { for (auto& c : s) c = tolower((unsigned char)c); return s; }

// tiny case-insensitive INI reader (replaces GetPrivateProfileString)
static map<string, string> read_ini(const string& path) {
    map<string, string> kv; ifstream f(path); string line, sec;
    while (getline(f, line)) {
        string t = trim(line);
        if (t.empty() || t[0] == ';' || t[0] == '#') continue;
        if (t[0] == '[') { sec = lower(trim(t.substr(1, t.find(']') - 1))); continue; }
        size_t eq = t.find('=');
        if (eq == string::npos) continue;
        kv[sec + "." + lower(trim(t.substr(0, eq)))] = trim(t.substr(eq + 1));
    }
    return kv;
}
static int ini_i(map<string, string>& kv, const string& k, int def) {
    auto it = kv.find(lower(k)); return it == kv.end() ? def : atoi(it->second.c_str());
}
static double ini_f(map<string, string>& kv, const string& k, double def) {
    auto it = kv.find(lower(k)); return it == kv.end() ? def : atof(it->second.c_str());
}

// ---------------------------------------------------------------- globals (network)
static vector<Link> links;
static vector<Train> trains;
static map<int, int> num2idx;             // node number -> 0-based index
static vector<int> idx2num;
static int nNodes = 0, nL = 0, T = 0;
static Settings cfg;
static string g_dir = ".";                // data directory (for incumbent timetable export)
// adjacency: node idx -> list of (to_idx, link_idx, a_to_b)
static vector<vector<tuple<int, int, bool>>> adj;
static vector<vector<double>> price;       // [nL][T]
static vector<vector<double>> pref;        // [nL][T+1] prefix sums of price
static vector<vector<short>> usage;        // [nL][T]
static vector<vector<short>> cap;          // [nL][T]
static vector<vector<int>> lastUse;        // [nL][T]

static int node_idx(int num) {
    auto it = num2idx.find(num);
    if (it != num2idx.end()) return it->second;
    int id = nNodes++; num2idx[num] = id; idx2num.push_back(num); return id;
}

static bool read_data(const string& dir) {
    // nodes
    { ifstream f(dir + "/input_node.csv"); if (!f) { printf("missing input_node.csv\n"); return false; }
      string ln; getline(f, ln);
      while (getline(f, ln)) { ln = trim(ln); if (ln.empty()) continue; node_idx(atoi(ln.c_str())); } }
    // links: from,to,length,vFT,vTF,cap,ltype,bidir
    { ifstream f(dir + "/input_link.csv"); if (!f) { printf("missing input_link.csv\n"); return false; }
      string ln; getline(f, ln);
      while (getline(f, ln)) { auto c = split(ln, ','); if (c.size() < 8) continue;
          Link L; L.a = node_idx(atoi(c[0].c_str())); L.b = node_idx(atoi(c[1].c_str()));
          L.length = atof(c[2].c_str()); L.vFT = atof(c[3].c_str()); L.vTF = atof(c[4].c_str());
          L.cap = max(1, atoi(c[5].c_str())); L.ltype = atoi(c[6].c_str());
          L.bidir = atoi(c[7].c_str()) == 1; links.push_back(L); } }
    // trains: id,origin,dest,dir,TOB,len,hazmat,smult,entry,cstop,early,crun,intended
    { ifstream f(dir + "/input_train_info.csv"); if (!f) { printf("missing input_train_info.csv\n"); return false; }
      string ln; getline(f, ln);
      while (getline(f, ln)) { auto c = split(ln, ','); if (c.size() < 13) continue;
          Train t; t.id = trim(c[0]); t.o = node_idx(atoi(c[1].c_str())); t.d = node_idx(atoi(c[2].c_str()));
          t.smult = atof(c[7].c_str()); t.entry = (int)lround(atof(c[8].c_str()) * cfg.minute);
          t.intended = atof(c[12].c_str()); trains.push_back(t); } }
    nL = (int)links.size();
    return true;
}

static int travel(const Link& L, bool ab, double sm) {
    double v = (ab ? L.vFT : L.vTF) * max(sm, 1e-6);
    if (v <= 0 || L.length <= 0) return 1;
    return max(1, (int)(L.length * 60.0 / v + 1.0));   // original: max(1, int(a+1))
}

static void build_network() {
    adj.assign(nNodes, {});
    for (int i = 0; i < nL; i++) {
        adj[links[i].a].push_back({links[i].b, i, true});
        if (links[i].bidir) adj[links[i].b].push_back({links[i].a, i, false});
    }
    cap.assign(nL, vector<short>(T, 0));
    for (int i = 0; i < nL; i++) for (int t = 0; t < T; t++) cap[i][t] = (short)links[i].cap;
    // MOW: capacity 0 in window
    ifstream f; // input_MOW handled in read_data caller below
}

// ---------------------------------------------------------------- TDSP
// Two exact implementations of the per-train time-dependent shortest path over (node,time) states,
// sharing identical transition rules (free-departure seeding, siding dwell loop, headway occupancy,
// dual = prefix-sum price window, |arrival−intended| on the destination arc, hard-capacity `residual`,
// and B1 forbidden windows). Either gives a VALID LR lower bound (the subproblem is solved to optimality):
//   * tdsp_dag      — the time-expanded graph is a DAG (every arc strictly increases time, travel ≥ 1),
//                     so a single sweep in increasing time order is exact and heap-free: O(states+arcs).
//                     This is the fast default (`--sp dag`).
//   * tdsp_dijkstra — the classical label-setting Dijkstra (`--sp dijkstra`), kept for verification and
//                     for any future model change that breaks the strictly-increasing-time (DAG) property.
static bool g_useDag = true;                              // --sp dag (default) | dijkstra

struct PQItem { double c; int n, t; bool operator>(const PQItem& o) const { return c > o.c; } };

// classical Dijkstra over (node,time)
static bool tdsp_dijkstra(const Train& tr, double& outCost, int& outArr,
                          vector<pair<int,int>>* occ, vector<vector<short>>* residual,
                          const TrainRestr* forbid) {
    static vector<double> dist; static vector<int> pn, pt, pl;
    long N = (long)nNodes * T;
    dist.assign(N, INF); pn.assign(N, -1); pt.assign(N, -1); pl.assign(N, -1);
    auto ID = [&](int n, int t) { return (long)n * T + t; };
    priority_queue<PQItem, vector<PQItem>, greater<PQItem>> pq;
    int e0 = tr.entry, hi0 = min(T, tr.entry + 1 + cfg.slack);
    for (int t = e0; t < hi0; t++) { dist[ID(tr.o, t)] = 0.0; pq.push({0.0, tr.o, t}); }
    int arr = -1;
    while (!pq.empty()) {
        PQItem it = pq.top(); pq.pop();
        long id = ID(it.n, it.t);
        if (it.c > dist[id]) continue;
        if (it.n == tr.d) { arr = it.t; break; }
        for (auto& [m, li, ab] : adj[it.n]) {
            if (cfg.monotone && ((m > it.n) != (tr.d > tr.o))) continue;   // corridor: no reversing
            const Link& L = links[li];
            int tt = travel(L, ab, tr.smult);
            int mw = (L.ltype == 4) ? cfg.maxwait : 0;
            for (int s = 0; s <= mw; s++) {
                int arrive = it.t + s + tt, hi = arrive + cfg.headway - 1;
                if (hi >= T) break;
                bool blocked = false;
                if (residual) for (int b = it.t; b < arrive + cfg.headway; b++)
                    if ((*residual)[li][b] <= 0) { blocked = true; break; }
                if (blocked) continue;
                // B1: reject if occupancy window [it.t, arrive+headway) hits a forbidden window of link li
                if (forbid) { bool hit = false;
                    for (auto& fw : (*forbid)[li])
                        if (it.t < fw.second && fw.first < arrive + cfg.headway) { hit = true; break; }
                    if (hit) continue; }
                double dual = pref[li][hi + 1] - pref[li][it.t];
                double cc = it.c + dual + (m == tr.d ? fabs(arrive - tr.intended) : 0.0);
                long nid = ID(m, arrive);
                if (cc < dist[nid]) { dist[nid] = cc; pn[nid] = it.n; pt[nid] = it.t; pl[nid] = li;
                                      pq.push({cc, m, arrive}); }
            }
        }
    }
    if (arr < 0) return false;
    outCost = dist[ID(tr.d, arr)]; outArr = arr;
    if (occ) { occ->clear(); int cn = tr.d, ct = arr;
        while (pn[ID(cn, ct)] != -1) { int li = pl[ID(cn, ct)], pnn = pn[ID(cn, ct)], ptt = pt[ID(cn, ct)];
            for (int b = ptt; b < ct + cfg.headway; b++) if (b < T) occ->push_back({li, b});
            cn = pnn; ct = ptt; } }
    return true;
}

// time-layered DAG dynamic program (heap-free; identical transitions to tdsp_dijkstra)
static bool tdsp_dag(const Train& tr, double& outCost, int& outArr,
                     vector<pair<int,int>>* occ, vector<vector<short>>* residual,
                     const TrainRestr* forbid) {
    static vector<double> dist; static vector<int> pn, pt, pl;
    long N = (long)nNodes * T;
    dist.assign(N, INF); pn.assign(N, -1); pt.assign(N, -1); pl.assign(N, -1);
    auto ID = [&](int n, int t) { return (long)n * T + t; };
    int e0 = tr.entry, hi0 = min(T, tr.entry + 1 + cfg.slack);
    for (int t = e0; t < hi0; t++) dist[ID(tr.o, t)] = 0.0;   // free-departure seeding (same as Dijkstra)
    // sweep states in increasing time order; every arc goes to a strictly later time, so when we reach
    // layer t every (n,t) already holds its final label and can be expanded once.
    for (int t = e0; t < T; t++) {
        long base = (long)0 * T + t;                          // (0,t); stride T per node
        for (int n = 0; n < nNodes; n++, base += T) {
            double dn = dist[base];
            if (dn >= INF) continue;
            if (n == tr.d) continue;                          // absorbing destination (match Dijkstra)
            for (auto& [m, li, ab] : adj[n]) {
                if (cfg.monotone && ((m > n) != (tr.d > tr.o))) continue;  // corridor: no reversing
                const Link& L = links[li];
                int tt = travel(L, ab, tr.smult);
                int mw = (L.ltype == 4) ? cfg.maxwait : 0;
                for (int s = 0; s <= mw; s++) {
                    int arrive = t + s + tt, hi = arrive + cfg.headway - 1;
                    if (hi >= T) break;
                    bool blocked = false;
                    if (residual) for (int b = t; b < arrive + cfg.headway; b++)
                        if ((*residual)[li][b] <= 0) { blocked = true; break; }
                    if (blocked) continue;
                    if (forbid) { bool hit = false;
                        for (auto& fw : (*forbid)[li])
                            if (t < fw.second && fw.first < arrive + cfg.headway) { hit = true; break; }
                        if (hit) continue; }
                    double dual = pref[li][hi + 1] - pref[li][t];
                    double cc = dn + dual + (m == tr.d ? fabs(arrive - tr.intended) : 0.0);
                    long nid = ID(m, arrive);
                    if (cc < dist[nid]) { dist[nid] = cc; pn[nid] = n; pt[nid] = t; pl[nid] = li; }
                }
            }
        }
    }
    int arr = -1; double best = INF;                          // min-cost destination arrival
    for (int t = e0; t < T; t++) { double d = dist[ID(tr.d, t)]; if (d < best) { best = d; arr = t; } }
    if (arr < 0) return false;
    outCost = best; outArr = arr;
    if (occ) { occ->clear(); int cn = tr.d, ct = arr;
        while (pn[ID(cn, ct)] != -1) { int li = pl[ID(cn, ct)], pnn = pn[ID(cn, ct)], ptt = pt[ID(cn, ct)];
            for (int b = ptt; b < ct + cfg.headway; b++) if (b < T) occ->push_back({li, b});
            cn = pnn; ct = ptt; } }
    return true;
}

// dispatcher — keeps the call sites unchanged
static bool tdsp(const Train& tr, double& outCost, int& outArr,
                 vector<pair<int,int>>* occ, vector<vector<short>>* residual,
                 const TrainRestr* forbid = nullptr) {
    return g_useDag ? tdsp_dag(tr, outCost, outArr, occ, residual, forbid)
                    : tdsp_dijkstra(tr, outCost, outArr, occ, residual, forbid);
}

static void rebuild_prefix() {
    pref.assign(nL, vector<double>(T + 1, 0.0));
    for (int i = 0; i < nL; i++) for (int t = 0; t < T; t++) pref[i][t + 1] = pref[i][t] + price[i][t];
}

// ---------------------------------------------------------------- priority-rule UB
static double priority_ub(const vector<double>& freeTravel, const vector<int>& curArr,
                          const Restrictions& R, vector<int>* arrOut = nullptr,
                          vector<vector<pair<int,int>>>* occOut = nullptr) {
    vector<vector<short>> resid = cap;                 // remaining capacity
    rebuild_prefix();                                  // not used (price irrelevant for UB), but keep pref valid
    vector<vector<double>> zero(nL, vector<double>(T, 0.0)); zero.swap(price); rebuild_prefix();
    vector<int> order(trains.size()); for (size_t i = 0; i < order.size(); i++) order[i] = (int)i;
    sort(order.begin(), order.end(), [&](int a, int b) {
        double ra = freeTravel[a] > 0 ? (curArr[a] - trains[a].entry - freeTravel[a]) / freeTravel[a] : 0;
        double rb = freeTravel[b] > 0 ? (curArr[b] - trains[b].entry - freeTravel[b]) / freeTravel[b] : 0;
        return ra < rb; });
    if (arrOut) arrOut->assign(trains.size(), -1);
    if (occOut) occOut->assign(trains.size(), {});
    double total = 0;
    for (int ti : order) {
        double c; int a; vector<pair<int,int>> occ;
        if (!tdsp(trains[ti], c, a, &occ, &resid, &R[ti])) { total += MAXP;
            fprintf(stderr, "[UB] train %s unroutable in residual capacity\n", trains[ti].id.c_str()); continue; }
        total += fabs(a - trains[ti].intended);
        sort(occ.begin(), occ.end()); occ.erase(unique(occ.begin(), occ.end()), occ.end());
        if (arrOut) (*arrOut)[ti] = a;                     // dedupe: a folded (hold-and-reverse)
        if (occOut) (*occOut)[ti] = occ;                   // path covers a cell twice; one train
        for (auto& [li, b] : occ) if (resid[li][b] > 0) resid[li][b]--;   // = one occupancy
    }
    zero.swap(price); rebuild_prefix();                // restore prices
    return total;
}

// ---------------------------------------------------------------- node bounding (B2)
// Run `iters` subgradient LR iterations subject to node restrictions R, warm-starting from whatever
// the global `price` currently holds (the parent's multipliers). Returns the node lower bound
// (best over the iterations); sets ubOut to the best priority-rule UB found (a feasible incumbent
// consistent with R). This is the cheap, strong bound the B&B calls at every node.
static double bound_node(const Restrictions& R, int iters,
                         const vector<double>& freeTravel, double& ubOut, bool verbose,
                         Solution* solOut = nullptr) {
    lastUse.assign(nL, vector<int>(T, 0));             // relax-and-cut memory is per node
    double bestLB = -INF, bestUB = INF; vector<int> bestUBarr;
    for (int k = 0; k < iters; k++) {
        bool last = (k == iters - 1);
        rebuild_prefix();
        usage.assign(nL, vector<short>(T, 0));
        double trip = 0; vector<int> curArr(trains.size(), 0);
        if (last && solOut) { solOut->routable = true; solOut->arr.assign(trains.size(), -1);
                              solOut->occ.assign(trains.size(), {}); solOut->dev = 0; }
        for (size_t i = 0; i < trains.size(); i++) {
            double c; int a; vector<pair<int,int>> occ;
            if (!tdsp(trains[i], c, a, &occ, nullptr, &R[i])) {
                trip += MAXP; if (last && solOut) solOut->routable = false; continue; }
            trip += c; curArr[i] = a;
            sort(occ.begin(), occ.end()); occ.erase(unique(occ.begin(), occ.end()), occ.end());
            for (auto& [li, b] : occ) usage[li][b]++;
            if (last && solOut) { solOut->arr[i] = a; solOut->occ[i] = occ;
                                  solOut->dev += fabs(a - trains[i].intended); }
        }
        double priceSum = 0; int maxviol = INT_MIN;
        for (int i = 0; i < nL; i++) for (int t = 0; t < T; t++) {
            if (price[i][t] <= 1e4) priceSum += price[i][t] * cap[i][t];   // L(rho) subtracts rho*CAP:
            // on the original all-cap-1 Meng-Zhou networks *1 was implicit; consolidated corridor
            // blocks have cap 2-3, and omitting the factor inflated the LB (caught by LB>UB on harrod)
            maxviol = max(maxviol, usage[i][t] - cap[i][t]);
        }
        double lb = trip - priceSum; bestLB = max(bestLB, lb);
        if (last) {                                        // B5: UB heuristic only once per node (a full
            vector<int> ua;                                // train-pass) instead of every LR iteration
            vector<vector<pair<int,int>>> uo;
            double ub = priority_ub(freeTravel, curArr, R, &ua, solOut ? &uo : nullptr);
            if (ub < bestUB) { bestUB = ub; bestUBarr = ua; if (solOut) solOut->ubOcc = std::move(uo); }
        }
        if (last && solOut) { solOut->usage = usage; solOut->maxviol = maxviol; }
        double step = max(cfg.minstep, 1.0 / (k + 1.0));
        for (int i = 0; i < nL; i++) for (int t = 0; t < T; t++) {
            if (usage[i][t] > 0) lastUse[i][t] = k;
            double g = usage[i][t] - cap[i][t];
            double p = price[i][t] + step * g; if (p < 0) p = 0;
            if (cap[i][t] > 0 && (k - lastUse[i][t]) > cfg.mem) p = 0;
            if (cap[i][t] == 0) p = MAXP;
            price[i][t] = p;
        }
        double gap = bestUB < 1e17 ? 100 * (bestUB - bestLB) / bestUB : 100;
        if (verbose)
            printf("%4d %10.1f %10.1f %7.1f%% %9d %8.3f\n", k + 1, bestLB, bestUB, gap, maxviol, step);
    }
    if (solOut) solOut->ubArr = bestUBarr;
    ubOut = bestUB; return bestLB;
}

// ---------------------------------------------------------------- B1 self-test
// Verify the restriction mechanism: route train 0 freely, then forbid its earliest-occupied link
// during a window around that occupancy, and confirm the re-solved path uses NO forbidden cell.
static int selftest_forbid(const vector<double>& freeTravel) {
    if (trains.empty()) { printf("B1 self-test: no trains\n"); return 1; }
    rebuild_prefix();
    Restrictions R = emptyR;
    double c0; int a0; vector<pair<int,int>> occ0;
    if (!tdsp(trains[0], c0, a0, &occ0, nullptr, &R[0])) { printf("B1 self-test: train 0 infeasible\n"); return 1; }
    int li = -1, t0 = INT_MAX;
    for (auto& [l, b] : occ0) if (b < t0) { t0 = b; li = l; }
    const int W = 60;
    R[0][li].push_back({t0, t0 + W});
    double c1; int a1; vector<pair<int,int>> occ1;
    bool ok1 = tdsp(trains[0], c1, a1, &occ1, nullptr, &R[0]);
    printf("B1 self-test: train %s, forbid link %d during [%d,%d)\n", trains[0].id.c_str(), li, t0, t0 + W);
    printf("  free   : arrival=%d cost=%.1f (earliest occupied cell: link %d @ t=%d)\n", a0, c0, li, t0);
    if (!ok1) { printf("  forbid : infeasible (link is a required cut at that time)\n  RESULT: PASS (restriction enforced)\n"); return 0; }
    bool clean = true;
    for (auto& [l, b] : occ1) if (l == li && b >= t0 && b < t0 + W) { clean = false; break; }
    printf("  forbid : arrival=%d cost=%.1f, arrival-not-earlier=%d\n", a1, c1, (int)(a1 >= a0));
    printf("  RESULT: %s (re-solved path uses no forbidden cell on link %d)\n", clean ? "PASS" : "FAIL", li);
    return clean ? 0 : 2;
}

// ---------------------------------------------------------------- branch SELECTION criterion (B3)
// There is no single "exact" branching rule: which conflict to fix first is a HEURISTIC criterion, and
// any choice keeps the B&B valid (the disjunction mechanism below is what guarantees soundness). This is
// the lever where the intelligence lives -- "first-thing-first" (earliest), "most-violated", a completion-
// time-style choice, or (B6) a screening/learned score. We expose it so it can be swapped freely.
//   The same separation as classic machine/chain-scheduling B&B: the *bound criterion* + the *branch
//   selection* are design choices per objective; the act of fixing one event and blocking the resource
//   is the invariant B&B step.
// Selection criteria. DUAL and STRONG are the B6 research levers:
//   DUAL   = gradient-weighted screening score: rank a conflict by ρ_{l,t}·(usage−cap), i.e. the LR dual
//            price (the LB's sensitivity / gradient w.r.t. that resource's capacity) times the violation.
//            Branching where the bound is most price-sensitive should tighten the LB fastest -- the
//            cheap screening proxy for strong branching (the thesis of nt_screen.py, here with TRUE duals).
//   STRONG = top-K dual-screened look-ahead: screen the K highest-DUAL conflicts, cheaply bound both
//            children of each (a 1-iter warm LR), branch on the one maximising the worse child LB. The
//            "screen-before-certify" oracle; DUAL is judged against it (does DUAL pick what STRONG would?).
enum BranchCrit { BC_MOST_VIOLATED = 0, BC_EARLIEST = 1, BC_LATEST = 2, BC_DUAL = 3, BC_STRONG = 4 };
static const char* crit_name(int c) {
    return c == BC_EARLIEST ? "earliest" : c == BC_LATEST ? "latest" :
           c == BC_DUAL ? "dual-screen" : c == BC_STRONG ? "strong(top-K dual)" : "most-violated";
}

// How to split a chosen conflict into two children (the DISJUNCTION, distinct from the selection above):
//   SEGMENT (Zhou & Zhong 2005 EJOR, Alg.3 enumeration rule): fix ONE train's segment on the contested
//     link and force the OTHER to yield past it; LR re-solves the yielding trains to offset around the
//     committed segment. The two children are the two meet/pass ORDERS (i-first vs j-first). On
//     block-occupancy single-track this is exhaustive (two trains on a cap-1 link cannot overlap, so one
//     whole occupancy window precedes the other), and it advances the schedule a whole segment per branch.
//   CELL: forbid one train from just the conflict cell (l,t*) (a minimal, always-valid fallback split).
enum BranchSplit { SPLIT_SEGMENT = 0, SPLIT_CELL = 1 };
static const char* split_name(int s) { return s == SPLIT_CELL ? "cell" : "segment"; }

// Train f's contiguous occupancy window [lo,hi] on link L in this primal (segment = the committed reservation).
static pair<int,int> train_window(const Solution& s, int f, int L) {
    int lo = INT_MAX, hi = INT_MIN;
    for (auto& [l, b] : s.occ[f]) if (l == L) { lo = min(lo, b); hi = max(hi, b); }
    return {lo, hi};
}

// Build a child's restrictions from the parent's: commit `commit`'s segment on link L and push `yield`
// past it (segment split), or just forbid `yield` from the conflict cell (cell split). Single-sourced so
// normal branching and strong-branching look-ahead use identical child semantics.
static Restrictions child_restr(const Restrictions& parentR, const Solution& sol, int split,
                                int commit, int yield, int L, int tt) {
    Restrictions R = parentR;
    if (split == SPLIT_CELL) R[yield][L].push_back({tt, tt + 1});
    else { auto w = train_window(sol, commit, L); R[yield][L].push_back({w.first, w.second + 1}); }
    return R;
}

// Trains ti,tj occupying cell (L,tt) in this primal (first two). Returns false if fewer than two.
static bool trains_at(const Solution& s, int L, int tt, int& ti, int& tj) {
    ti = tj = -1;
    for (size_t f = 0; f < s.occ.size(); f++) {
        for (auto& [l, b] : s.occ[f]) if (l == L && b == tt) {
            if (ti < 0) ti = (int)f; else { tj = (int)f; break; } }
        if (tj >= 0) break;
    }
    return (ti >= 0 && tj >= 0);
}

// Select one violated capacity cell (l,t*) by the chosen criterion, and the first two trains occupying it.
// Returns false if the primal is already capacity-feasible (no conflict to fix). Selection is heuristic;
// it does NOT affect correctness, only the shape/size of the tree.
static bool find_conflict(const Solution& s, int crit, int& L, int& tt, int& ti, int& tj) {
    L = -1; tt = -1; double best = -1e18;
    for (int i = 0; i < nL; i++) for (int t = 0; t < T; t++) {
        int v = s.usage[i][t] - cap[i][t];
        if (v <= 0) continue;
        double score;                                        // higher = preferred
        if (crit == BC_EARLIEST)     score = -(double)t;     // fix the earliest conflict first
        else if (crit == BC_LATEST)  score =  (double)t;     // fix the latest (completion-time flavour)
        else if (crit == BC_DUAL)    score = price[i][t] * v + 1e-6 * v;  // gradient-weighted (dual·violation)
        else                         score = v * 1e6 - t;    // most-violated, earliest tie-break
        if (score > best) { best = score; L = i; tt = t; }
    }
    if (L < 0) return false;                                 // capacity-feasible primal
    ti = tj = -1;
    for (size_t f = 0; f < s.occ.size(); f++) {
        for (auto& [l, b] : s.occ[f]) if (l == L && b == tt) {
            if (ti < 0) ti = (int)f; else { tj = (int)f; break; } }
        if (tj >= 0) break;
    }
    return (ti >= 0 && tj >= 0);
}

// ---------------------------------------------------------------- B&B node + driver (B4)
struct Node {
    Restrictions R;                                     // per-train forbidden windows
    shared_ptr<vector<vector<double>>> warm;            // warm-start multipliers (parent's post-bound price)
    double parentLB;                                    // valid subtree lower bound (key for best-first)
};
struct NodeCmp { bool operator()(const Node& a, const Node& b) const { return a.parentLB > b.parentLB; } };

// Best-first LR-based branch-and-bound. Each node: set price = warm, run bound_node under the node's
// restrictions, fathom by bound/feasibility/infeasibility, else BRANCH.
//   Branch = SELECT a conflict (heuristic criterion `crit` -- swappable, no effect on correctness) then
//   FIX it with a 2-way disjunction (`split`): SEGMENT commits one train's segment and offsets the other
//   (Zhou & Zhong 2005 enumeration), CELL forbids one train from the conflict cell. The disjunction is the
//   sound, invariant step ("fix one train's segment, block the resource, offset the others"); its union
//   covers every capacity-feasible schedule, so whatever criterion picks the conflict the search stays
//   valid. On small instances the tree exhausts -> proven optimum; on real instances we run it under a
//   node/gap budget, keep the best incumbent, and lean on a good criterion (later: B6 screening).
struct BnbCfg { int nodeBudget, rootIters, childIters, crit, split, strongK, strongIters; double gapTol, timeLimit; };

// B6: choose the conflict to branch on. For STRONG, screen the top-K cells by DUAL score then bound both
// children of each (cheap warm look-ahead) and pick the one maximising the worse child LB. Returns the
// chosen (L,tt,ti,tj). `price` holds the node's multipliers; we save/restore it around look-ahead bounds.
static bool select_strong(const Solution& sol, const Restrictions& parentR, const BnbCfg& B,
                          const vector<double>& freeTravel, int& L, int& tt, int& ti, int& tj) {
    // gather violated cells with their dual score
    vector<pair<double,pair<int,int>>> cells;             // (score, (l,t))
    for (int i = 0; i < nL; i++) for (int t = 0; t < T; t++) {
        int v = sol.usage[i][t] - cap[i][t];
        if (v > 0) cells.push_back({price[i][t] * v + 1e-6 * v, {i, t}});
    }
    if (cells.empty()) return false;
    int K = min((int)cells.size(), B.strongK);
    partial_sort(cells.begin(), cells.begin() + K, cells.end(),
                 [](auto& a, auto& b) { return a.first > b.first; });
    vector<vector<double>> saved = price;                 // restore point for look-ahead bounds
    double bestScore = -INF; bool found = false;
    for (int c = 0; c < K; c++) {
        int Lc = cells[c].second.first, tc = cells[c].second.second, ic, jc;
        if (!trains_at(sol, Lc, tc, ic, jc)) continue;
        Restrictions RA = child_restr(parentR, sol, B.split, ic, jc, Lc, tc);
        Restrictions RB = child_restr(parentR, sol, B.split, jc, ic, Lc, tc);
        double ubx;
        price = saved; double lbA = bound_node(RA, B.strongIters, freeTravel, ubx, false);
        price = saved; double lbB = bound_node(RB, B.strongIters, freeTravel, ubx, false);
        double s = min(lbA, lbB);                          // raise the worse child the most
        if (s > bestScore) { bestScore = s; L = Lc; tt = tc; ti = ic; tj = jc; found = true; }
    }
    price = saved;                                         // restore node multipliers for real branching
    return found;
}

static void run_bnb(const BnbCfg& B, const vector<double>& freeTravel) {
    auto t0 = chrono::steady_clock::now();
    auto elapsed = [&]{ return chrono::duration<double>(chrono::steady_clock::now() - t0).count(); };
    priority_queue<Node, vector<Node>, NodeCmp> open;
    open.push({emptyR, make_shared<vector<vector<double>>>(price), -INF});
    double globalUB = INF, globalLB = -INF;
    vector<int> incumbent; vector<vector<pair<int,int>>> incOcc;
    int nodes = 0, branched = 0; const char* stop = "(budget/gap stop)";
    printf("%6s %8s %10s %10s %10s %7s %6s %7s\n",
           "node", "nodeLB", "globalLB", "globalUB", "incV", "gap", "open", "secs");
    while (!open.empty()) {
        if (nodes >= B.nodeBudget) { stop = "(node budget)"; break; }
        if (B.timeLimit > 0 && elapsed() > B.timeLimit) { stop = "(time limit)"; break; }
        Node n = open.top(); open.pop();
        if (n.parentLB >= globalUB - 1e-6) continue;        // fathom by bound (queue key already exceeds UB)
        globalLB = max(globalLB, n.parentLB);               // best-first: popped key is the global LB
        price = *n.warm;                                    // warm-start from parent's multipliers
        int iters = (nodes == 0) ? B.rootIters : B.childIters;   // B5: root converges, children re-tighten
        Solution sol; double ub;
        double lb = bound_node(n.R, iters, freeTravel, ub, false, &sol);
        nodes++;
        if (!sol.routable) continue;                        // infeasible node -> fathom
        if (ub < globalUB) { globalUB = ub; incumbent = sol.ubArr; incOcc = sol.ubOcc; }  // heuristic feasible incumbent
        if (sol.maxviol <= 0 && sol.dev < globalUB) {       // relaxed primal is itself feasible -> exact
            globalUB = sol.dev; incumbent = sol.arr; incOcc = sol.occ; }
        double gap = (globalUB < 1e17 && globalLB > -1e17) ? (globalUB - globalLB) / globalUB : 1.0;
        if (nodes <= 20 || nodes % 25 == 0) {
            printf("%6d %8.1f ", nodes, lb);
            if (globalLB > -1e17) printf("%10.1f", globalLB); else printf("%10s", "-inf");
            printf(" %10.1f %10.1f %6.1f%% %6d %7.1f\n",
                   globalUB, (incumbent.empty()? -1.0 : globalUB), 100*gap, (int)open.size(), elapsed());
        }
        if (globalLB > -1e17 && gap <= B.gapTol) { stop = "(gap target)"; break; }
        if (lb >= globalUB - 1e-6) continue;                // fathom by bound
        int L, tt, ti, tj;
        bool ok = (B.crit == BC_STRONG) ? select_strong(sol, n.R, B, freeTravel, L, tt, ti, tj)
                                        : find_conflict(sol, B.crit, L, tt, ti, tj);
        if (!ok) continue;                                  // no conflict -> feasible leaf, recorded
        auto warm = make_shared<vector<vector<double>>>(price);
        Node A{child_restr(n.R, sol, B.split, ti, tj, L, tt), warm, lb};  // commit i; j yields
        Node B2{child_restr(n.R, sol, B.split, tj, ti, L, tt), warm, lb}; // commit j; i yields
        open.push(std::move(A)); open.push(std::move(B2)); branched++;
    }
    if (open.empty()) { globalLB = globalUB; stop = "(PROVEN OPTIMAL — tree exhausted)"; }
    double gap = globalUB < 1e17 ? (globalUB - globalLB) / globalUB : 1.0;
    printf("\nB&B done: nodes=%d branched=%d  LB=%.1f  UB=%.1f  gap=%.2f%%  %.1fs  %s\n",
           nodes, branched, globalLB, globalUB, 100 * gap, elapsed(), stop);
    if (!incumbent.empty()) {
        printf("incumbent arrivals:");
        for (size_t i = 0; i < incumbent.size() && i < 12; i++) printf(" %d", incumbent[i]);
        if (incumbent.size() > 12) printf(" ...");
        printf("\n");
    }
    // Export the incumbent's per-train link occupancies (maximal consecutive-minute runs per link)
    // for time-space plotting: train_id,link,from,to,t_first,t_last  (window includes headway tail).
    if (!incOcc.empty()) {
        string path = g_dir + "/internal_timetable/incumbent_timetable.csv";
        ofstream f(path);
        f << "train_id,link,from_node,to_node,t_first,t_last,arrival,intended\n";
        for (size_t i = 0; i < incOcc.size(); i++) {
            auto cells = incOcc[i];
            sort(cells.begin(), cells.end());
            for (size_t k = 0; k < cells.size(); ) {
                size_t j = k;
                while (j + 1 < cells.size() && cells[j+1].first == cells[k].first
                       && cells[j+1].second == cells[j].second + 1) j++;
                const Link& L = links[cells[k].first];
                f << trains[i].id << ',' << cells[k].first << ',' << idx2num[L.a] << ','
                  << idx2num[L.b] << ',' << cells[k].second << ',' << cells[j].second << ','
                  << (i < incumbent.size() ? incumbent[i] : -1) << ',' << trains[i].intended << "\n";
                k = j + 1;
            }
        }
        printf("incumbent timetable exported: %s\n", path.c_str());
    }
}

// ---------------------------------------------------------------- LR driver
int main(int argc, char** argv) {
    string dir = "."; bool selftest = false, bnb = false; int nodeBudget = 500; double gapTol = 0.005;
    int branchCrit = BC_MOST_VIOLATED, branchSplit = SPLIT_SEGMENT;
    int rootIters = -1, childIters = 3, strongK = 5, strongIters = 1; double timeLimit = 0;
    for (int i = 1; i < argc; i++) {
        string a = argv[i];
        if (a == "--selftest") selftest = true;
        else if (a == "--bnb") bnb = true;
        else if (a == "--nodes" && i + 1 < argc) nodeBudget = atoi(argv[++i]);
        else if (a == "--gap" && i + 1 < argc) gapTol = atof(argv[++i]);
        else if (a == "--time" && i + 1 < argc) timeLimit = atof(argv[++i]);
        else if (a == "--root-iters" && i + 1 < argc) rootIters = atoi(argv[++i]);
        else if (a == "--child-iters" && i + 1 < argc) childIters = atoi(argv[++i]);
        else if (a == "--strong-k" && i + 1 < argc) strongK = atoi(argv[++i]);
        else if (a == "--branch" && i + 1 < argc) { string b = argv[++i];
            branchCrit = (b == "earliest") ? BC_EARLIEST : (b == "latest") ? BC_LATEST :
                         (b == "dual") ? BC_DUAL : (b == "strong") ? BC_STRONG : BC_MOST_VIOLATED; }
        else if (a == "--split" && i + 1 < argc) { string b = argv[++i];
            branchSplit = (b == "cell") ? SPLIT_CELL : SPLIT_SEGMENT; }
        else if (a == "--sp" && i + 1 < argc) { string b = argv[++i];
            g_useDag = (b != "dijkstra"); }
        else dir = a;
    }
    g_dir = dir;
    auto kv = read_ini(dir + "/FTSettings.ini");
    cfg.horizon = ini_i(kv, "optimization.optimizationhorizon", 1440);
    cfg.minute  = max(1, ini_i(kv, "optimization.minutedivision", 1));
    cfg.maxiter = ini_i(kv, "lagrangian.maxnumberoflriterations", 10);
    cfg.minstep = ini_f(kv, "lagrangian.minimumstepsize", 0.01);
    cfg.mem     = ini_i(kv, "lagrangian.numberofiterationswithmemory", 5);
    cfg.maxwait = ini_i(kv, "lagrangian.maxtrainwaitingtime", 120) * cfg.minute;
    cfg.slack   = ini_i(kv, "lagrangian.maxslacktimeatdeparture", 1200) * cfg.minute;
    cfg.headway = ini_i(kv, "lagrangian.safetyheadway", 3) * cfg.minute;
    cfg.monotone = ini_i(kv, "optimization.monotonerouting", 0) != 0;
    if (cfg.maxiter > 200) cfg.maxiter = 40;           // shipped ini has 10000; cap for a finite run
    T = cfg.horizon * cfg.minute;

    if (!read_data(dir)) return 1;
    build_network();
    emptyR.assign(trains.size(), TrainRestr(nL));       // empty per-train restrictions (root node)
    // MOW
    { ifstream f(dir + "/input_MOW.csv"); string ln; if (f) { getline(f, ln);
        while (getline(f, ln)) { auto c = split(ln, ','); if (c.size() < 4) continue;
            int A = atoi(c[0].c_str()), B = atoi(c[1].c_str());
            int st = (int)atof(c[2].c_str()), en = (int)ceil(atof(c[3].c_str()));
            for (int i = 0; i < nL; i++) {
                int an = idx2num[links[i].a], bn = idx2num[links[i].b];
                if ((an == A && bn == B) || (an == B && bn == A))
                    for (int t = st; t < min(T, en); t++) cap[i][t] = 0;
            } } } }

    price.assign(nL, vector<double>(T, 0.0));
    for (int i = 0; i < nL; i++) for (int t = 0; t < T; t++) if (cap[i][t] == 0) price[i][t] = MAXP;
    lastUse.assign(nL, vector<int>(T, 0));

    printf("FastTrain (clean C++): nodes=%d links=%d trains=%d horizon=%d headway=%d sp=%s\n",
           nNodes, nL, (int)trains.size(), T, cfg.headway, g_useDag ? "dag" : "dijkstra");

    // iteration-0 free-flow per-train deviation + free travel time (for UB ranking & LB baseline)
    rebuild_prefix();
    vector<double> freeTravel(trains.size(), 0);
    double ff = 0;
    for (size_t i = 0; i < trains.size(); i++) {
        double c; int a; if (tdsp(trains[i], c, a, nullptr, nullptr)) {
            ff += c; freeTravel[i] = a - trains[i].entry; }
        else ff += MAXP;
    }
    printf("free-flow total arrival-deviation (baseline) = %.0f\n\n", ff);

    if (selftest) return selftest_forbid(freeTravel);

    if (bnb) {
        if (rootIters < 0) rootIters = cfg.maxiter;
        BnbCfg B{nodeBudget, rootIters, childIters, branchCrit, branchSplit, strongK, strongIters,
                 gapTol, timeLimit};
        printf("=== LR-based branch-and-bound (best-first) ===\n"
               "nodeBudget=%d time=%.0fs gapTol=%.1f%% select=%s split=%s rootIters=%d childIters=%d\n\n",
               nodeBudget, timeLimit, 100 * gapTol, crit_name(branchCrit), split_name(branchSplit),
               rootIters, childIters);
        run_bnb(B, freeTravel);
        return 0;
    }

    printf("%4s %10s %10s %8s %9s %8s\n", "iter", "LB", "UB", "gap", "maxviol", "step");
    // Root node = LR with empty restrictions (B2). The B&B driver (B3-B4) will instead call
    // bound_node() repeatedly with each node's restrictions, warm-starting `price` from the parent.
    double ub; double lb = bound_node(emptyR, cfg.maxiter, freeTravel, ub, true);
    printf("\nBEST  LB=%.1f  UB=%.1f  gap=%.1f%%   (Meng-Zhou C++ ref: LB 1844->2359, UB ~4153)\n",
           lb, ub, ub < 1e17 ? 100 * (ub - lb) / ub : 100);
    return 0;
}
