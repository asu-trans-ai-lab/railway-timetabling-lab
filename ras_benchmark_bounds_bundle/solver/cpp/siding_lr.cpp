// siding_lr.cpp -- the siding_rules model on resources (docs/SIDING_RULES_NOTE.md): a train stands only at its
// origin or inside a resource with two tracks; cost = running + ALPHA * origin minutes + BETA * standing minutes.
//
//   g++ -O2 -std=c++17 -Wall -Wextra -o build/siding_lr.exe cpp/siding_lr.cpp
//   build/siding_lr.exe INSTANCE --mode ub [--time-cap S] [--seed N] [--out schedule.csv] [--anneal T0] [--max-ruin m]
//                                     [--start schedule.csv] [--phase-moves p]
//   build/siding_lr.exe INSTANCE --mode lb --ub U --phase --phase-heuristic-every N [--lb-time-cap S] --out best.csv
//   build/siding_lr.exe INSTANCE --mode lb --ub U [--phase] [--meet] [--iters K] [--theta x] [--patience P] [--no-windows]
//                                        [--heuristic-every N --out schedule.csv] [--dump-columns F --dump-every N]
//   build/siding_lr.exe INSTANCE --mode price --ub U --duals FILE [--phase] [--no-windows]
//   build/siding_lr.exe INSTANCE --mode probe --ub U --phase --meet [--k-root K] [--k-node K] [--restrict F]
//                                        [--part i --parts n]
//   build/siding_lr.exe INSTANCE --mode bb --ub U [--phase] [--rule phase|interval|cell|meet|block] [--points m] [--time-cap S] [--k-root K]
//                                        [--k-node K] [--theta-node x] [--patience-node P] [--out schedule.csv]
//                                        [--node-heuristic 1|2|3 --node-heuristic-every N] [--plunge P --k-dive K]
//                                        [--split N FILE | --nodes-in FILE] [--shared-ub FILE]
//
// The instance file is written by benchmark/siding_rules/model.py (tokens, whitespace separated):
//   HEADWAY h ALPHA a BETA b UB u  RESOURCES m { name tracks }...  BLOCKS nb { len r... }...
//   TRAINS n { id release dir terminal_origin legs { resource minutes may_stand }... nthrough block... }...
//
// Section 2 of the note is train_dp(); section 3 lower_bound() without --phase; section 4 with it (phase_dp());
// section 5 upper_bound().

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <queue>
#include <numeric>
#include <random>
#include <sstream>
#include <string>
#include <vector>
#include <climits>
#include <cstdio>
#include <unistd.h>

using namespace std;

// ========================================================
// 1. DATA
// ========================================================

const double INF = 1e30;

int g_H = 0, g_Alpha = 1, g_Beta = 2, g_UB = 0;
vector<string> g_ResName;
vector<int> g_Tracks;
vector<vector<int>> g_Block;                     // resources, west -> east

struct Leg { int res, p; bool stand; bool pocket = false; };   // pocket: stands clear, holding nothing (may_stand 2)
struct TrainData
{
    string id;
    int release = 0, dir = 1;
    bool terminal = true;                         // held outside the territory: holds nothing before departure
    vector<Leg> legs;                             // in travel order
    vector<int> through;                          // block indices
};
vector<TrainData> g_T;

struct Pair { int k, b, dir, leg, len; };         // train k through block b: enters at leg, holds it len = sum p + H
vector<Pair> g_Pairs;
vector<vector<int>> g_PairAtLeg;                  // [train][leg] -> pair index or -1

bool read_instance(const char* path)
{
    ifstream in(path);
    if (!in) return false;
    string key;
    while (in >> key)
    {
        if (key == "HEADWAY") in >> g_H;
        else if (key == "ALPHA") in >> g_Alpha;
        else if (key == "BETA") in >> g_Beta;
        else if (key == "UB") in >> g_UB;
        else if (key == "RESOURCES")
        {
            int m; in >> m;
            g_ResName.resize(m); g_Tracks.resize(m);
            for (int r = 0; r < m; r++) in >> g_ResName[r] >> g_Tracks[r];
        }
        else if (key == "BLOCKS")
        {
            int nb; in >> nb;
            g_Block.resize(nb);
            for (int b = 0; b < nb; b++)
            {
                int len; in >> len;
                g_Block[b].resize(len);
                for (int j = 0; j < len; j++) in >> g_Block[b][j];
            }
        }
        else if (key == "TRAINS")
        {
            int n; in >> n;
            g_T.resize(n);
            for (int k = 0; k < n; k++)
            {
                auto& T = g_T[k];
                int term, legs, nt;
                in >> T.id >> T.release >> T.dir >> term >> legs;
                T.terminal = term != 0;
                T.legs.resize(legs);
                for (int i = 0; i < legs; i++)
                {
                    int s;
                    in >> T.legs[i].res >> T.legs[i].p >> s;
                    T.legs[i].stand = s != 0;
                    T.legs[i].pocket = s == 2;
                    if (T.legs[i].p < 1) { fprintf(stderr, "run time < 1\n"); return false; }
                }
                in >> nt;
                T.through.resize(nt);
                for (int j = 0; j < nt; j++) in >> T.through[j];
            }
        }
        else { fprintf(stderr, "unknown key %s\n", key.c_str()); return false; }
    }
    // block entries: the leg on the block's west resource (eastbound) or east resource (westbound)
    g_PairAtLeg.assign(g_T.size(), {});
    for (int k = 0; k < (int)g_T.size(); k++)
    {
        auto& T = g_T[k];
        g_PairAtLeg[k].assign(T.legs.size(), -1);
        for (int b : T.through)
        {
            int entry_res = T.dir > 0 ? g_Block[b].front() : g_Block[b].back();
            int leg = -1, len = g_H;
            for (int i = 0; i < (int)T.legs.size(); i++)
            {
                if (T.legs[i].res == entry_res) leg = i;
                if (find(g_Block[b].begin(), g_Block[b].end(), T.legs[i].res) != g_Block[b].end())
                {
                    len += T.legs[i].p;
                    // a pocket after the block's last leg is allowed: the train stands clear once it has left
                    // the block, so its through interval is still [entry, entry + sum p + H)
                    bool exit_pocket = T.legs[i].pocket && T.legs[i].res == (T.dir > 0 ? g_Block[b].back() : g_Block[b].front());
                    if (T.legs[i].stand && !exit_pocket) { fprintf(stderr, "standing inside a block\n"); return false; }
                }
            }
            if (leg < 0) { fprintf(stderr, "train %s: block %d not on its path\n", T.id.c_str(), b); return false; }
            g_PairAtLeg[k][leg] = (int)g_Pairs.size();
            g_Pairs.push_back({k, b, T.dir, leg, len});
        }
    }
    return !g_T.empty() && !g_Tracks.empty();
}

// ========================================================
// 2. CELL COSTS
// ========================================================

enum { FREE, PRICED, BLOCKED, HYBRID };               // HYBRID: full cells infinite, others omega * lambda
int g_Mode = FREE;
int g_W = 0;                                      // cells per resource
vector<double> g_Lambda, g_LamPre;                // [r * W + t], prefix [r * (W + 1) + t]
vector<int> g_Occ, g_FullPre;                     // counts; prefix of (count >= tracks)
bool g_Phase = false;
double g_Omega = 1.0;
vector<vector<double>> g_Mu, g_MuPre;             // per pair

// Branch-and-bound restrictions (note section 9), each on one train; empty outside --mode bb.
int g_CurTrain = -1;                              // the train train_dp is solving
vector<vector<vector<int>>> g_ForbidCell;         // [k][r] sorted minutes k may not hold r
vector<vector<pair<int, int>>> g_ForbidEntry;     // [pair] minutes [lo, hi] the train may not enter its block
vector<vector<pair<int, int>>> g_ForbidDep;       // [k] minutes [lo, hi] k may not depart

inline bool in_spans(const vector<pair<int, int>>& v, int x)
{
    for (auto& sp : v) if (sp.first <= x && x <= sp.second) return true;
    return false;
}

inline double cells(int r, int a, int b)          // sum of cell costs over [a, b) of resource r
{
    if (a >= b) return 0.0;
    if (a < 0 || b > g_W) return INF;
    if (g_CurTrain >= 0 && !g_ForbidCell.empty())
    {
        const auto& v = g_ForbidCell[g_CurTrain][r];
        if (!v.empty())
        {
            auto it = lower_bound(v.begin(), v.end(), a);
            if (it != v.end() && *it < b) return INF;
        }
    }
    if (g_Mode == FREE) return 0.0;
    if (g_Mode == PRICED)
    {
        const double* pre = &g_LamPre[(size_t)r * (g_W + 1)];
        return pre[b] - pre[a];
    }
    const int* pre = &g_FullPre[(size_t)r * (g_W + 1)];
    if (pre[b] - pre[a] > 0) return INF;
    if (g_Mode == BLOCKED) return 0.0;
    const double* lp = &g_LamPre[(size_t)r * (g_W + 1)];
    return g_Omega * (lp[b] - lp[a]);
}

inline double block_charge(int k, int leg, int s)  // entering a block at s costs the prices of its interval
{
    int q = g_PairAtLeg[k][leg];
    if (q < 0) return 0.0;
    if (!g_ForbidEntry.empty() && in_spans(g_ForbidEntry[q], s)) return INF;
    if (!g_Phase || g_Mode != PRICED) return 0.0;
    int e = s + g_Pairs[q].len;
    if (e > g_W) return INF;
    return g_MuPre[q][e] - g_MuPre[q][s];
}

// Meet rows (note section 14), dualized: for an eastbound i, a westbound j and a run R of single-track resources both
// hold, z = 1 when i clears R before j enters it:  A_j(t) - D_i(t - H) + z <= 1 (alpha_t), A_i(t) - D_j(t - H) - z <= 0
// (beta_t), with A (D) = 1 once the train has entered (left) R; z falls along the corridor (one meet per pair).
// A train pays suffix sums of the multipliers at the minute it enters R and gets them back H after it leaves.
bool g_Meet = false;
struct MeetRun { int i, j, ei, xi, ej, xj; vector<double> a, b; };   // legs where i (j) enters and leaves R
vector<MeetRun> g_Runs;
vector<vector<int>> g_MeetPairs;                  // runs of one pair, west to east
vector<vector<vector<double>>> g_EnterPrice, g_ExitPrice;   // [k][leg][t], empty when the leg bounds no run

inline double meet_enter(int k, int leg, int t)
{
    if (!g_Meet || g_Mode != PRICED) return 0.0;
    const auto& v = g_EnterPrice[k][leg];
    return v.empty() || t < 0 || t >= (int)v.size() ? 0.0 : v[t];
}

inline double meet_exit(int k, int leg, int t)
{
    if (!g_Meet || g_Mode != PRICED) return 0.0;
    const auto& v = g_ExitPrice[k][leg];
    return v.empty() || t < 0 || t >= (int)v.size() ? 0.0 : v[t];
}

// Meet branching (note section 15): per opposing pair the meet position m lies in g_MeetRange[pair] (z = 1 on the runs
// before m); what the fixed orders imply for the trains' times is propagated into earliest entries and latest exits
// per leg, which every DP honours (empty outside a meet tree).
vector<array<int, 2>> g_MeetRange;
vector<vector<int>> g_MinEnter, g_MaxExit;       // [k][leg]
inline bool enter_ok(int k, int leg, int t) { return g_MinEnter.empty() || t >= g_MinEnter[k][leg]; }
inline bool exit_ok(int k, int leg, int t) { return g_MaxExit.empty() || t <= g_MaxExit[k][leg]; }

void build_lambda_prefix()
{
    int m = (int)g_Tracks.size();
    g_LamPre.assign((size_t)m * (g_W + 1), 0.0);
    for (int r = 0; r < m; r++)
    {
        double* pre = &g_LamPre[(size_t)r * (g_W + 1)];
        const double* lam = &g_Lambda[(size_t)r * g_W];
        for (int t = 0; t < g_W; t++) pre[t + 1] = pre[t] + lam[t];
    }
    for (size_t q = 0; q < g_Mu.size(); q++)
    {
        g_MuPre[q][0] = 0.0;
        for (int t = 0; t < g_W; t++) g_MuPre[q][t + 1] = g_MuPre[q][t] + g_Mu[q][t];
    }
}

void rebuild_full(int r)
{
    int* pre = &g_FullPre[(size_t)r * (g_W + 1)];
    const int* occ = &g_Occ[(size_t)r * g_W];
    pre[0] = 0;
    for (int t = 0; t < g_W; t++) pre[t + 1] = pre[t] + (occ[t] >= g_Tracks[r] ? 1 : 0);
}

// ========================================================
// 3. THE TRAIN DP  (note section 2)
// ========================================================

struct Trip
{
    double cost = INF;
    vector<array<int, 3>> legs;                   // resource, entry, exit
};

// train_dp
//   meaning:   the least cost of train k ending by minute Tk under the current cell costs, and the trip
//   state:     (i, t) -- head at the end of leg i at minute t, still holding its resource
//   output:    +inf when nothing fits
double train_dp(int k, int Tk, Trip* trip)
{
    const auto& T = g_T[k];
    int n = (int)T.legs.size(), r = T.release;
    if (Tk < r) return INF;
    g_CurTrain = k;
    const vector<pair<int, int>>* no_dep = g_ForbidDep.empty() ? nullptr : &g_ForbidDep[k];
    int L = Tk + 1;
    // F: head at the end of leg i at minute t, holding its resource; G: after a pocket leg, standing clear of it
    vector<double> F((size_t)n * L, INF), G((size_t)n * L, INF);
    vector<signed char> pred((size_t)n * L, 0);   // F: 1 depart, 2 stand, 3 run on from F[i-1], 4 run on from G[i-1]
    vector<signed char> predG((size_t)n * L, 0);  // G: 5 released at arrival (from F), 6 stand on
    double best = INF;
    int best_t = -1;
    int r0 = T.legs[0].res, p0 = T.legs[0].p;
    for (int t = 0; t <= Tk; t++)
    {
        if (t >= r && t + p0 <= Tk && !(no_dep && in_spans(*no_dep, t)) && enter_ok(k, 0, t))
        {
            double c = (double)g_Alpha * (t - r) + p0 + cells(r0, t, t + p0) + block_charge(k, 0, t) + meet_enter(k, 0, t);
            if (!T.terminal) c += cells(r0, r, t);
            size_t at = (size_t)0 * L + t + p0;
            if (c < F[at]) { F[at] = c; pred[at] = 1; }
        }
        for (int i = 0; i < n; i++)
        {
            size_t here = (size_t)i * L + t;
            double v = F[here];
            if (v < INF)
            {
                int res = T.legs[i].res;
                if (T.legs[i].stand && !T.legs[i].pocket && t + 1 <= Tk)
                {
                    double c = v + g_Beta + cells(res, t, t + 1);
                    size_t at = here + 1;
                    if (c < F[at]) { F[at] = c; pred[at] = 2; }
                }
                if (exit_ok(k, i, t))
                {
                    double tail = cells(res, t, t + g_H) + meet_exit(k, i, t);
                    if (i == n - 1)
                    {
                        if (v + tail < best) { best = v + tail; best_t = t; }
                    }
                    else
                    {
                        if (T.legs[i].pocket && v + tail < G[here]) { G[here] = v + tail; predG[here] = 5; }
                        int p = T.legs[i + 1].p;
                        if (t + p <= Tk && enter_ok(k, i + 1, t))
                        {
                            double c = v + tail + p + cells(T.legs[i + 1].res, t, t + p) + block_charge(k, i + 1, t)
                                       + meet_enter(k, i + 1, t);
                            size_t at = (size_t)(i + 1) * L + t + p;
                            if (c < F[at]) { F[at] = c; pred[at] = 3; }
                        }
                    }
                }
            }
            double w = G[here];                      // standing clear: no cells; leaves by running on
            if (w < INF && i < n - 1)
            {
                if (t + 1 <= Tk && w + g_Beta < G[here + 1]) { G[here + 1] = w + g_Beta; predG[here + 1] = 6; }
                int p = T.legs[i + 1].p;
                if (t + p <= Tk && enter_ok(k, i + 1, t))
                {
                    double c = w + p + cells(T.legs[i + 1].res, t, t + p) + block_charge(k, i + 1, t)
                               + meet_enter(k, i + 1, t);
                    size_t at = (size_t)(i + 1) * L + t + p;
                    if (c < F[at]) { F[at] = c; pred[at] = 4; }
                }
            }
        }
    }
    if (best >= INF) return INF;
    if (trip)
    {
        trip->cost = best;
        trip->legs.clear();
        int i = n - 1, t = best_t, exit_time = best_t;
        bool inG = false;
        while (true)
        {
            if (inG)
            {
                int how = predG[(size_t)i * L + t];
                if (how == 6) { t--; continue; }
                inG = false;                         // 5: released at arrival t, same minute
                continue;
            }
            int how = pred[(size_t)i * L + t];
            if (how == 2) { t--; continue; }
            int entry = t - T.legs[i].p;
            trip->legs.push_back({T.legs[i].res, entry, exit_time});
            if (how == 1) break;
            inG = how == 4;
            i--; t = entry; exit_time = entry;
        }
        reverse(trip->legs.begin(), trip->legs.end());
    }
    return best;
}

// the minute a leg's resource is let go: its exit, or its arrival when the train then stands clear (pocket)
inline int release_of(int k, const Trip& trip, int i)
{
    return g_T[k].legs[i].pocket ? trip.legs[i][1] + g_T[k].legs[i].p : trip.legs[i][2];
}

// the cells a trip holds: [entry, release + H) per leg, from release on the first leg when the origin is inside
template <class F> void for_each_hold(int k, const Trip& trip, F f)
{
    for (int i = 0; i < (int)trip.legs.size(); i++)
    {
        int start = (i == 0 && !g_T[k].terminal) ? g_T[k].release : trip.legs[i][1];
        f(trip.legs[i][0], start, release_of(k, trip, i) + g_H);
    }
}

// build_meet: the runs of every opposing pair (single-track resources both hold, contiguous in both paths) and zero
// multipliers on minutes 0..W
void build_meet()
{
    g_Runs.clear();
    g_MeetPairs.clear();
    int n = (int)g_T.size();
    for (int i = 0; i < n; i++)
    {
        if (g_T[i].dir < 0) continue;
        for (int j = 0; j < n; j++)
        {
            if (g_T[j].dir > 0) continue;
            vector<int> leg_j(g_Tracks.size(), -1);
            for (int l = 0; l < (int)g_T[j].legs.size(); l++) leg_j[g_T[j].legs[l].res] = l;
            vector<vector<int>> runs;
            vector<int> cur;
            for (int l = 0; l < (int)g_T[i].legs.size(); l++)
            {
                int r = g_T[i].legs[l].res;
                bool inside = g_Tracks[r] == 1 && leg_j[r] >= 0;
                // a run ends where the legs stop being contiguous in j's path, or where either train can stand
                // clear between them (a pocket: the other one passes there, so the order may change)
                if (inside && !cur.empty() && (leg_j[r] != leg_j[g_T[i].legs[cur.back()].res] - 1 ||
                                               g_T[i].legs[cur.back()].pocket || g_T[j].legs[leg_j[r]].pocket))
                {
                    runs.push_back(cur);
                    cur.clear();
                }
                if (inside) cur.push_back(l);
                else if (!cur.empty()) { runs.push_back(cur); cur.clear(); }
            }
            if (!cur.empty()) runs.push_back(cur);
            if (runs.empty()) continue;
            g_MeetPairs.push_back({});
            for (auto& legs : runs)
            {
                MeetRun R;
                R.i = i; R.j = j;
                R.ei = legs.front(); R.xi = legs.back();
                R.ej = leg_j[g_T[i].legs[legs.back()].res];
                R.xj = leg_j[g_T[i].legs[legs.front()].res];
                R.a.assign(g_W + 1, 0.0);
                R.b.assign(g_W + 1, 0.0);
                g_MeetPairs.back().push_back((int)g_Runs.size());
                g_Runs.push_back(R);
            }
        }
    }
    g_EnterPrice.assign(n, {});
    g_ExitPrice.assign(n, {});
    for (int k = 0; k < n; k++)
    {
        g_EnterPrice[k].assign(g_T[k].legs.size(), {});
        g_ExitPrice[k].assign(g_T[k].legs.size(), {});
    }
}

// meet_prices: the train prices of the current multipliers
void meet_prices()
{
    for (auto& per : g_EnterPrice) for (auto& v : per) v.clear();
    for (auto& per : g_ExitPrice) for (auto& v : per) v.clear();
    auto touch = [](vector<double>& v) { if (v.empty()) v.assign(g_W + 1, 0.0); };
    vector<double> Sa(g_W + 2), Sb(g_W + 2);
    for (auto& R : g_Runs)
    {
        Sa[g_W + 1] = Sb[g_W + 1] = 0.0;
        for (int t = g_W; t >= 0; t--) { Sa[t] = Sa[t + 1] + R.a[t]; Sb[t] = Sb[t + 1] + R.b[t]; }
        if (Sa[0] <= 0.0 && Sb[0] <= 0.0) continue;
        auto& ej = g_EnterPrice[R.j][R.ej]; auto& xi = g_ExitPrice[R.i][R.xi];
        auto& ei = g_EnterPrice[R.i][R.ei]; auto& xj = g_ExitPrice[R.j][R.xj];
        touch(ej); touch(xi); touch(ei); touch(xj);
        for (int t = 0; t <= g_W; t++)
        {
            ej[t] += Sa[t];                                   // j enters at t: sum of alpha from t on
            ei[t] += Sb[t];                                   // i enters at t: sum of beta from t on
            if (t + g_H <= g_W) { xi[t] -= Sa[t + g_H]; xj[t] -= Sb[t + g_H]; }
        }
    }
}

// meet_value: the z part of L plus its constant; z (per run) the minimizer: a prefix of each pair's runs set to 1
double meet_value(vector<char>& z)
{
    z.assign(g_Runs.size(), 0);
    double total = 0.0;
    for (auto& runs : g_MeetPairs)
    {
        size_t pidx = &runs - &g_MeetPairs[0];
        int lo = g_MeetRange.empty() ? 0 : g_MeetRange[pidx][0];
        int hi = g_MeetRange.empty() ? (int)runs.size() : g_MeetRange[pidx][1];
        double pre = 0.0, best = INF;
        int arg = 0;
        if (lo == 0) { best = 0.0; arg = 0; }
        for (int m = 0; m < (int)runs.size(); m++)
        {
            const auto& R = g_Runs[runs[m]];
            double sa = 0.0, sb = 0.0;
            for (int t = 0; t <= g_W; t++) { sa += R.a[t]; sb += R.b[t]; }
            total -= sa;
            pre += sa - sb;
            if (m + 1 >= lo && m + 1 <= hi && pre < best) { best = pre; arg = m + 1; }
        }
        for (int m = 0; m < arg; m++) z[runs[m]] = 1;
        total += best;
    }
    return total;
}

// meet_step: the subgradient's squared norm (update = false) or the projected step (update = true)
double meet_step(const vector<Trip>& trips, const vector<char>& z, double step, bool update)
{
    double norm = 0.0;
    for (size_t r = 0; r < g_Runs.size(); r++)
    {
        auto& R = g_Runs[r];
        int enter_j = trips[R.j].legs[R.ej][1], leave_i = release_of(R.i, trips[R.i], R.xi);
        int enter_i = trips[R.i].legs[R.ei][1], leave_j = release_of(R.j, trips[R.j], R.xj);
        for (int t = 0; t <= g_W; t++)
        {
            double g1 = (t >= enter_j) - (t >= leave_i + g_H) + z[r] - 1.0;
            double g2 = (t >= enter_i) - (t >= leave_j + g_H) - (double)z[r];
            if (update)
            {
                R.a[t] = max(0.0, R.a[t] + step * g1);
                R.b[t] = max(0.0, R.b[t] + step * g2);
            }
            else
            {
                if (!(R.a[t] <= 0.0 && g1 < 0.0)) norm += g1 * g1;
                if (!(R.b[t] <= 0.0 && g2 < 0.0)) norm += g2 * g2;
            }
        }
    }
    return norm;
}

// propagate_meets: earliest entry e and latest exit x of every leg from release, windows Tk and the orders every
// pair's meet range fixes (z = 1 on runs before lo, z = 0 from hi on); false when a leg has no room left
bool propagate_meets(const vector<int>& Tk)
{
    int n = (int)g_T.size();
    g_MinEnter.assign(n, {});
    g_MaxExit.assign(n, {});
    for (int k = 0; k < n; k++)
    {
        int L = (int)g_T[k].legs.size();
        g_MinEnter[k].assign(L, g_T[k].release);
        g_MaxExit[k].assign(L, Tk[k]);
    }
    auto p = [](int k, int l) { return g_T[k].legs[l].p; };
    for (int round = 0; round < 200; round++)
    {
        bool changed = false;
        for (int k = 0; k < n; k++)
        {
            auto& e = g_MinEnter[k];
            auto& x = g_MaxExit[k];
            int L = (int)e.size();
            for (int l = 0; l + 1 < L; l++)
                if (e[l] + p(k, l) > e[l + 1]) { e[l + 1] = e[l] + p(k, l); changed = true; }
            for (int l = L - 1; l > 0; l--)
                if (x[l] - p(k, l) < x[l - 1]) { x[l - 1] = x[l] - p(k, l); changed = true; }
            for (int l = 0; l < L; l++) if (e[l] + p(k, l) > x[l]) return false;
        }
        for (size_t pi = 0; pi < g_MeetPairs.size(); pi++)
        {
            int lo = g_MeetRange[pi][0], hi = g_MeetRange[pi][1];
            const auto& runs = g_MeetPairs[pi];
            for (int m = 0; m < (int)runs.size(); m++)
            {
                const auto& R = g_Runs[runs[m]];
                auto& ei = g_MinEnter[R.i]; auto& xi = g_MaxExit[R.i];
                auto& ej = g_MinEnter[R.j]; auto& xj = g_MaxExit[R.j];
                if (m < lo)                            // i clears R, then j enters
                {
                    int need = ei[R.xi] + p(R.i, R.xi) + g_H;
                    if (need > ej[R.ej]) { ej[R.ej] = need; changed = true; }
                    int by = xj[R.ej] - p(R.j, R.ej) - g_H;
                    if (by < xi[R.xi]) { xi[R.xi] = by; changed = true; }
                }
                else if (m >= hi)                      // j clears R, then i enters
                {
                    int need = ej[R.xj] + p(R.j, R.xj) + g_H;
                    if (need > ei[R.ei]) { ei[R.ei] = need; changed = true; }
                    int by = xi[R.ei] - p(R.i, R.ei) - g_H;
                    if (by < xj[R.xj]) { xj[R.xj] = by; changed = true; }
                }
            }
        }
        if (!changed) return true;
    }
    return true;
}

// Block-pair branching (note section 19). A block is a run of a train's legs it cannot stop inside: it ends after a
// leg the train may stand after (or the last leg), so one start S fixes every leg entry of it. Two blocks of
// different trains that share 1-track resources on rigid holds conflict exactly for a set of start differences
// D = S_y - S_x; its complement gives the pair's alternatives. A node fixes some pairs to one alternative and may bound
// some starts; the difference constraints propagate to earliest / latest starts, which become the legs' earliest
// entries and latest exits for every DP.
struct Blk { int k, a, c, len; vector<int> off; };
vector<Blk> g_Blk;
vector<vector<int>> g_BlkOf;                       // [k] -> block indices in travel order
struct BPair { int x, y; vector<array<int, 2>> forb, alts; };
vector<BPair> g_BPairs;
vector<array<int, 2>> g_BPairChoice;               // per pair: fixed [lo, hi] of S_y - S_x, or {INT_MIN, INT_MIN}
vector<array<int, 2>> g_BlkBound;                  // per block: node bounds on S
vector<int> g_BlkE, g_BlkL;                        // propagated earliest / latest starts

void build_blocks()
{
    g_Blk.clear(); g_BlkOf.assign(g_T.size(), {}); g_BPairs.clear();
    for (int k = 0; k < (int)g_T.size(); k++)
    {
        const auto& T = g_T[k];
        int a = 0;
        for (int l = 0; l < (int)T.legs.size(); l++)
            if (T.legs[l].stand || l + 1 == (int)T.legs.size())
            {
                Blk b; b.k = k; b.a = a; b.c = l; b.len = 0;
                for (int i = a; i <= l; i++) { b.off.push_back(b.len); b.len += T.legs[i].p; }
                g_BlkOf[k].push_back((int)g_Blk.size());
                g_Blk.push_back(b);
                a = l + 1;
            }
    }
    // rigid holds: [S + off, S + off + p + H) on a 1-track resource, unless the leg's hold is not fixed by S
    auto rigid = [](const Blk& b, int i) {
        const auto& T = g_T[b.k];
        int l = b.a + i;
        if (l == 0 && !T.terminal) return false;                   // held from release
        if (l == b.c && T.legs[l].stand && !T.legs[l].pocket) return false;   // stands holding it
        return g_Tracks[T.legs[l].res] == 1;
    };
    for (int x = 0; x < (int)g_Blk.size(); x++)
        for (int y = x + 1; y < (int)g_Blk.size(); y++)
        {
            const Blk& X = g_Blk[x]; const Blk& Y = g_Blk[y];
            if (X.k == Y.k) continue;
            vector<array<int, 2>> iv;
            for (int i = 0; i <= X.c - X.a; i++)
            {
                if (!rigid(X, i)) continue;
                int rx = g_T[X.k].legs[X.a + i].res, px = g_T[X.k].legs[X.a + i].p;
                for (int j = 0; j <= Y.c - Y.a; j++)
                {
                    if (!rigid(Y, j) || g_T[Y.k].legs[Y.a + j].res != rx) continue;
                    int py = g_T[Y.k].legs[Y.a + j].p;
                    iv.push_back({X.off[i] - Y.off[j] - py - g_H + 1, X.off[i] + px + g_H - Y.off[j] - 1});
                }
            }
            if (iv.empty()) continue;
            sort(iv.begin(), iv.end());
            vector<array<int, 2>> merged;
            for (auto& v : iv)
                if (!merged.empty() && v[0] <= merged.back()[1] + 1) merged.back()[1] = max(merged.back()[1], v[1]);
                else merged.push_back(v);
            BPair P; P.x = x; P.y = y; P.forb = merged;
            const int BIG = 1 << 28;
            int cur = -BIG;
            for (auto& f : merged) { if (f[0] > cur) P.alts.push_back({cur, f[0] - 1}); cur = max(cur, f[1] + 1); }
            P.alts.push_back({cur, BIG});
            g_BPairs.push_back(P);
        }
    printf("BLOCKS %zu block pairs %zu\n", g_Blk.size(), g_BPairs.size());
}

// propagate_blocks: earliest / latest starts from releases, windows, the chain, the node's pair choices and bounds;
// false when some block has no start left. Writes the legs' earliest entries / latest exits.
bool propagate_blocks(const vector<int>& Tk)
{
    int nb = (int)g_Blk.size();
    g_BlkE.assign(nb, 0); g_BlkL.assign(nb, 0);
    for (int b = 0; b < nb; b++)
    {
        const Blk& B = g_Blk[b];
        const auto& T = g_T[B.k];
        int before = 0, after = 0;
        for (int l = 0; l < B.a; l++) before += T.legs[l].p;
        for (int l = B.a; l < (int)T.legs.size(); l++) after += T.legs[l].p;
        g_BlkE[b] = max(T.release + before, g_BlkBound[b][0]);
        g_BlkL[b] = min(Tk[B.k] - after, g_BlkBound[b][1]);
    }
    struct Edge { int from, to, w; };                 // S_to - S_from >= w
    vector<Edge> E;
    for (auto& ids : g_BlkOf)
        for (size_t i = 0; i + 1 < ids.size(); i++) E.push_back({ids[i], ids[i + 1], g_Blk[ids[i]].len});
    for (size_t p = 0; p < g_BPairs.size(); p++)
    {
        auto ch = g_BPairChoice[p];
        if (ch[0] == INT32_MIN) continue;
        E.push_back({g_BPairs[p].x, g_BPairs[p].y, ch[0]});           // S_y - S_x >= lo
        if (ch[1] < (1 << 28)) E.push_back({g_BPairs[p].y, g_BPairs[p].x, -ch[1]});   // S_x - S_y >= -hi
    }
    for (int round = 0; round <= nb + 1; round++)
    {
        bool changed = false;
        for (auto& e : E)
        {
            if (g_BlkE[e.from] + e.w > g_BlkE[e.to]) { g_BlkE[e.to] = g_BlkE[e.from] + e.w; changed = true; }
            if (g_BlkL[e.to] - e.w < g_BlkL[e.from]) { g_BlkL[e.from] = g_BlkL[e.to] - e.w; changed = true; }
        }
        for (int b = 0; b < nb; b++) if (g_BlkE[b] > g_BlkL[b]) return false;
        if (!changed) break;
    }
    int n = (int)g_T.size();
    g_MinEnter.assign(n, {}); g_MaxExit.assign(n, {});
    for (int k = 0; k < n; k++)
    {
        g_MinEnter[k].assign(g_T[k].legs.size(), g_T[k].release);
        g_MaxExit[k].assign(g_T[k].legs.size(), Tk[k]);
    }
    for (int b = 0; b < nb; b++)
    {
        const Blk& B = g_Blk[b];
        const auto& T = g_T[B.k];
        for (int l = B.a; l <= B.c; l++)
        {
            int off = B.off[l - B.a];
            g_MinEnter[B.k][l] = g_BlkE[b] + off;
            bool stands_holding = l == B.c && T.legs[l].stand && !T.legs[l].pocket;
            if (!stands_holding) g_MaxExit[B.k][l] = min(Tk[B.k], g_BlkL[b] + off + T.legs[l].p);
        }
    }
    return true;
}

// block_prop_bound: every train's least cost within the node's propagated windows -- it cannot depart before its first
// block's earliest start nor arrive before its last block's earliest start plus that block's length:
// cost_k >= run_k + min(alpha, beta) (arrival - release - run_k) + (alpha - min) (departure - release)
double block_prop_bound()
{
    double total = 0.0;
    int a = g_Alpha, b = g_Beta, lo = min(a, b);
    for (int k = 0; k < (int)g_T.size(); k++)
    {
        const auto& ids = g_BlkOf[k];
        if (ids.empty()) continue;
        int run = 0;
        for (auto& l : g_T[k].legs) run += l.p;
        int dep = g_BlkE[ids.front()], arr = g_BlkE[ids.back()] + g_Blk[ids.back()].len;
        total += run + (double)lo * (arr - g_T[k].release - run) + (double)(a - lo) * (dep - g_T[k].release);
    }
    return total;
}

// ========================================================
// 4. THE PHASE DP  (note section 4)
// ========================================================

// phase_dp
//   meaning:   Phi_b(mu) = max over phase plans of sum_{d,t} g(d,t) w_d(t), one direction green at a time, every
//              green run >= gmin_d; the plan in g (g[0] east, g[1] west)
double phase_dp(int b, vector<char> g[2])
{
    vector<double> w[2] = {vector<double>(g_W, 0.0), vector<double>(g_W, 0.0)};
    int gmin[2] = {INT32_MAX, INT32_MAX};
    for (int q = 0; q < (int)g_Pairs.size(); q++)
    {
        if (g_Pairs[q].b != b) continue;
        int d = g_Pairs[q].dir > 0 ? 0 : 1;
        gmin[d] = min(gmin[d], g_Pairs[q].len);
        for (int t = 0; t < g_W; t++) w[d][t] += g_Mu[q][t];
    }
    vector<double> P[2] = {vector<double>(g_W + 1, 0.0), vector<double>(g_W + 1, 0.0)};
    for (int d = 0; d < 2; d++)
        for (int t = 0; t < g_W; t++) P[d][t + 1] = P[d][t] + w[d][t];
    vector<double> V(g_W + 1, 0.0);
    vector<int> choice(g_W + 1, -1), from(g_W + 1, -1);
    double run_best[2] = {-INF, -INF};
    int run_arg[2] = {-1, -1};
    for (int h = 1; h <= g_W; h++)
    {
        double best = V[h - 1];
        int ch = -1, fr = -1;
        for (int d = 0; d < 2; d++)
        {
            if (gmin[d] == INT32_MAX) continue;
            int s = h - gmin[d];
            if (s >= 0 && V[s] - P[d][s] > run_best[d]) { run_best[d] = V[s] - P[d][s]; run_arg[d] = s; }
            if (run_arg[d] >= 0 && run_best[d] + P[d][h] > best) { best = run_best[d] + P[d][h]; ch = d; fr = run_arg[d]; }
        }
        V[h] = best; choice[h] = ch; from[h] = fr;
    }
    g[0].assign(g_W, 0); g[1].assign(g_W, 0);
    for (int h = g_W; h > 0; )
    {
        if (choice[h] < 0) { h--; continue; }
        for (int t = from[h]; t < h; t++) g[choice[h]][t] = 1;
        h = from[h];
    }
    return V[g_W];
}

// ========================================================
// 5. LOWER BOUND  (note sections 3 and 4)
// ========================================================

vector<double> g_Free;                            // DP_k(0)

double true_cost(int k, const Trip& trip)         // running + alpha * origin + beta * standing, from the legs
{
    const auto& T = g_T[k];
    double c = (double)g_Alpha * (trip.legs[0][1] - T.release);
    for (int i = 0; i < (int)trip.legs.size(); i++)
        c += T.legs[i].p + (double)g_Beta * (trip.legs[i][2] - trip.legs[i][1] - T.legs[i].p);
    return c;
}

void rebuild_full(int r);
template <class F> void for_each_hold(int k, const Trip& trip, F f);

// lagrangian_heuristic
//   meaning:   insert the trains in the order of their priced departures, each by its DP against the cells already
//              full, with omega * lambda as a soft price; the schedule's true cost (INF if some train did not fit)
double lagrangian_heuristic(const vector<Trip>& priced, const vector<int>& Tk, vector<Trip>& out)
{
    int n = (int)g_T.size(), m = (int)g_Tracks.size();
    vector<int> order(n);
    iota(order.begin(), order.end(), 0);
    stable_sort(order.begin(), order.end(), [&](int a, int b)
    {
        if (g_T[a].terminal != g_T[b].terminal) return !g_T[a].terminal;
        return priced[a].legs[0][1] < priced[b].legs[0][1];
    });
    int saved_mode = g_Mode;
    g_Mode = HYBRID;
    g_Occ.assign((size_t)m * g_W, 0);
    g_FullPre.assign((size_t)m * (g_W + 1), 0);
    out.assign(n, Trip());
    double total = 0.0;
    for (int k : order)
    {
        if (train_dp(k, Tk[k], &out[k]) >= INF) { total = INF; break; }
        total += true_cost(k, out[k]);
        vector<char> touched(m, 0);
        for_each_hold(k, out[k], [&](int r, int a, int b)
        {
            for (int t = a; t < b && t < g_W; t++) g_Occ[(size_t)r * g_W + t]++;
            touched[r] = 1;
        });
        for (int r = 0; r < m; r++) if (touched[r]) rebuild_full(r);
    }
    g_Mode = saved_mode;
    return total;
}

// phase_slot_heuristic  (note section 11)
//   meaning:   a timetable that follows the phase plan of the current multipliers: a train may enter a block only when its
//              whole interval avoids the other direction's green (loose) or lies in its own green (strict); inserted one at
//              a time by the train DP against the full cells, in the order of the priced departures; a train that fits no
//              slot is inserted again without its phase restrictions
//   output:    the cost of the timetable, +inf when a train fits nowhere; out holds it
double phase_slot_heuristic(const vector<Trip>& priced, const vector<vector<char>>& plan, const vector<int>& Tk,
                            bool strict, vector<Trip>& out, int* freed)
{
    int n = (int)g_T.size(), m = (int)g_Tracks.size();
    vector<vector<int>> green(plan.size());        // prefix counts of each direction's green
    for (size_t j = 0; j < plan.size(); j++)
    {
        green[j].assign(g_W + 1, 0);
        for (int t = 0; t < g_W; t++) green[j][t + 1] = green[j][t] + (t < (int)plan[j].size() && plan[j][t] ? 1 : 0);
    }
    vector<vector<pair<int, int>>> forbid(g_Pairs.size());
    for (int q = 0; q < (int)g_Pairs.size(); q++)
    {
        const auto& P = g_Pairs[q];
        int d = P.dir > 0 ? 0 : 1;
        const auto& own = green[2 * P.b + d];
        const auto& other = green[2 * P.b + 1 - d];
        int lo = -1;
        for (int s = 0; s < g_W; s++)
        {
            int e = min(s + P.len, g_W);
            bool bad = strict ? own[e] - own[s] < e - s : other[e] - other[s] > 0;
            if (bad && lo < 0) lo = s;
            if (!bad && lo >= 0) { forbid[q].push_back({lo, s - 1}); lo = -1; }
        }
        if (lo >= 0) forbid[q].push_back({lo, g_W - 1});
    }
    vector<int> order(n);
    iota(order.begin(), order.end(), 0);
    stable_sort(order.begin(), order.end(), [&](int a, int b)
    {
        if (g_T[a].terminal != g_T[b].terminal) return !g_T[a].terminal;
        return priced[a].legs[0][1] < priced[b].legs[0][1];
    });
    int saved_mode = g_Mode;
    g_Mode = BLOCKED;
    g_Occ.assign((size_t)m * g_W, 0);
    g_FullPre.assign((size_t)m * (g_W + 1), 0);
    out.assign(n, Trip());
    double total = 0.0;
    *freed = 0;
    auto node_entry = g_ForbidEntry;               // inside the tree: the node's restrictions, kept and restored
    auto node_cell = g_ForbidCell;
    auto node_dep = g_ForbidDep;
    auto node_min = g_MinEnter;
    auto node_max = g_MaxExit;
    for (int k : order)
    {
        g_ForbidEntry = node_entry;
        if (g_ForbidEntry.empty()) g_ForbidEntry.assign(g_Pairs.size(), {});
        for (int q : g_PairAtLeg[k])
            if (q >= 0) g_ForbidEntry[q].insert(g_ForbidEntry[q].end(), forbid[q].begin(), forbid[q].end());
        double v = train_dp(k, Tk[k], &out[k]);
        if (v >= INF)                              // no slot of the plan fits: this train alone goes free
        {
            g_ForbidEntry.clear(); g_ForbidCell.clear(); g_ForbidDep.clear(); g_MinEnter.clear(); g_MaxExit.clear();
            (*freed)++;
            v = train_dp(k, Tk[k], &out[k]);
            g_ForbidCell = node_cell; g_ForbidDep = node_dep; g_MinEnter = node_min; g_MaxExit = node_max;
        }
        if (v >= INF) { total = INF; break; }
        total += true_cost(k, out[k]);
        vector<char> touched(m, 0);
        for_each_hold(k, out[k], [&](int r, int a, int b)
        {
            for (int t = a; t < b && t < g_W; t++) g_Occ[(size_t)r * g_W + t]++;
            touched[r] = 1;
        });
        for (int r = 0; r < m; r++) if (touched[r]) rebuild_full(r);
    }
    g_ForbidEntry = node_entry;
    g_CurTrain = -1;
    g_Mode = saved_mode;
    return total;
}

void write_schedule_of(const char* path, const vector<Trip>& sched);
int g_HeurEvery = 0;
int g_PhaseHeurEvery = 0;
double g_LbTimeCap = 0.0;                         // --lb-time-cap S: stop the subgradient after S seconds                        // --phase-heuristic-every N: note section 11
int g_Points = 2;                                 // --points m: checkpoints of rule I (note section 10)
const char* g_Out = nullptr;
FILE* g_Dump = nullptr;                           // --dump-columns FILE --dump-every N: the priced trips, for a master
int g_DumpEvery = 10;

void print_trip(FILE* f, int k, const Trip& trip)
{
    fprintf(f, "TRIP %d %.6f %zu", k, trip.cost, trip.legs.size());
    for (auto& l : trip.legs) fprintf(f, " %d %d %d", l[0], l[1], l[2]);
    fprintf(f, "\n");
}

void free_run()
{
    g_Mode = FREE;
    g_Free.assign(g_T.size(), 0.0);
    for (int k = 0; k < (int)g_T.size(); k++)
    {
        int span = 0;
        for (auto& l : g_T[k].legs) span += l.p;
        g_W = g_T[k].release + span + g_H + 2;
        g_Free[k] = train_dp(k, g_T[k].release + span, nullptr);
    }
}

int lower_bound_mode(int iterations, double theta, int patience, bool windows)
{
    auto t0 = chrono::steady_clock::now();
    free_run();
    double free_total = accumulate(g_Free.begin(), g_Free.end(), 0.0);
    printf("LB0 %.0f\n", free_total);
    if (g_UB <= 0) { fprintf(stderr, "--ub required\n"); return 2; }
    int n = (int)g_T.size(), m = (int)g_Tracks.size();
    vector<int> Tk(n);
    int max_release = 0, max_p = 0;
    for (auto& T : g_T) { max_release = max(max_release, T.release); for (auto& l : T.legs) max_p = max(max_p, l.p); }
    for (int k = 0; k < n; k++)
        Tk[k] = windows ? g_T[k].release + g_UB - 1 - (int)llround(free_total - g_Free[k]) : max_release + g_UB;
    int axis_all = max_release + 1;                // a serial schedule fits: the heuristic's axis (note section 5)
    for (auto& T : g_T) { for (auto& l : T.legs) axis_all += l.p; axis_all += g_H + 1; }
    vector<int> Th(n, axis_all);
    g_W = max(*max_element(Tk.begin(), Tk.end()), g_HeurEvery > 0 || g_PhaseHeurEvery > 0 ? axis_all : 0) + g_H + max_p + 2;
    g_Mode = PRICED;
    g_Lambda.assign((size_t)m * g_W, 0.0);
    g_Mu.assign(g_Phase ? g_Pairs.size() : 0, vector<double>(g_W, 0.0));
    g_MuPre.assign(g_Mu.size(), vector<double>(g_W + 1, 0.0));
    if (g_Meet) { build_meet(); printf("MEET pairs %zu runs %zu\n", g_MeetPairs.size(), g_Runs.size()); }
    vector<char> zmeet;
    vector<Trip> trips(n);
    vector<int> usage((size_t)m * g_W);
    double best = -INF, heur_best = INF;
    int since = 0, it = 0;
    for (it = 0; it <= iterations; it++)
    {
        build_lambda_prefix();
        if (g_Meet) meet_prices();
        double sum = 0.0;
        bool empty = false;
        for (int k = 0; k < n; k++)
        {
            double v = train_dp(k, Tk[k], &trips[k]);
            if (v >= INF) { empty = true; break; }
            sum += v;
        }
        if (empty) { best = g_UB; printf("ITER %d windows admit no schedule better than UB\n", it); break; }
        if (g_Dump && it % g_DumpEvery == 0)
            for (int k = 0; k < n; k++) print_trip(g_Dump, k, trips[k]);
        double lam = 0.0;
        for (int r = 0; r < m; r++)
            for (int t = 0; t < g_W; t++) lam += g_Tracks[r] * g_Lambda[(size_t)r * g_W + t];
        vector<vector<char>> plan(g_Block.size() * 2);
        double phi = 0.0;
        if (g_Phase)
            for (int b = 0; b < (int)g_Block.size(); b++) phi += phase_dp(b, &plan[2 * b]);
        double L = sum - lam - phi + (g_Meet ? meet_value(zmeet) : 0.0);
        if (L > best + 1e-9) { best = L; since = 0; } else if (++since >= patience) { theta *= 0.5; since = 0; }
        if (g_HeurEvery > 0 && it % g_HeurEvery == 0)
            for (double omega : {0.0, 0.5, 1.0})
            {
                g_Omega = omega;
                vector<Trip> sched;
                double v = lagrangian_heuristic(trips, Th, sched);
                if (v < heur_best - 1e-9)
                {
                    heur_best = v;
                    printf("HEUR UB %.0f at iteration %d omega %.1f\n", v, it, omega);
                    if (g_Out) write_schedule_of(g_Out, sched);
                    if (v < g_UB) g_UB = (int)llround(v);   // the Polyak target; the windows stay those of the first UB
                }
            }
        if (g_Phase && g_PhaseHeurEvery > 0 && it % g_PhaseHeurEvery == 0)
            for (bool strict : {false, true})
            {
                vector<Trip> sched;
                int freed = 0;
                double v = phase_slot_heuristic(trips, plan, Th, strict, sched, &freed);
                if (v < heur_best - 1e-9)
                {
                    heur_best = v;
                    printf("PHASE UB %.0f at iteration %d %s freed %d seconds %.1f\n", v, it, strict ? "strict" : "loose",
                           freed, chrono::duration<double>(chrono::steady_clock::now() - t0).count());
                    fflush(stdout);
                    if (g_Out) write_schedule_of(g_Out, sched);
                    if (v < g_UB) g_UB = (int)llround(v);
                }
            }
        if (it % 25 == 0 || it == iterations)
            printf("ITER %d L %.6f best %.6f theta %.4g\n", it, L, best, theta);
        if (ceil(best - 1e-6) >= g_UB || it == iterations) break;
        if (g_LbTimeCap > 0 && chrono::duration<double>(chrono::steady_clock::now() - t0).count() >= g_LbTimeCap) break;
        // subgradients
        fill(usage.begin(), usage.end(), 0);
        for (int k = 0; k < n; k++)
            for_each_hold(k, trips[k], [&](int r, int a, int b) { for (int t = a; t < b && t < g_W; t++) usage[(size_t)r * g_W + t]++; });
        double norm = 0.0;
        for (int r = 0; r < m; r++)
            for (int t = 0; t < g_W; t++)
            {
                size_t c = (size_t)r * g_W + t;
                double gr = usage[c] - g_Tracks[r];
                if (g_Lambda[c] <= 0.0 && gr < 0.0) continue;
                norm += gr * gr;
            }
        vector<vector<char>> inside(g_Mu.size());
        for (int q = 0; q < (int)g_Mu.size(); q++)
        {
            auto& P = g_Pairs[q];
            inside[q].assign(g_W, 0);
            int s = trips[P.k].legs[P.leg][1];
            for (int t = s; t < s + P.len && t < g_W; t++) inside[q][t] = 1;
            const auto& g = plan[2 * P.b + (P.dir > 0 ? 0 : 1)];
            for (int t = 0; t < g_W; t++)
            {
                double gr = inside[q][t] - g[t];
                if (g_Mu[q][t] <= 0.0 && gr < 0.0) continue;
                norm += gr * gr;
            }
        }
        if (g_Meet) norm += meet_step(trips, zmeet, 0.0, false);
        if (norm <= 0.0) break;
        double step = theta * max(g_UB - L, 1e-6) / norm;
        if (g_Meet) meet_step(trips, zmeet, step, true);
        for (int r = 0; r < m; r++)
            for (int t = 0; t < g_W; t++)
            {
                size_t c = (size_t)r * g_W + t;
                g_Lambda[c] = max(0.0, g_Lambda[c] + step * (usage[c] - g_Tracks[r]));
            }
        for (int q = 0; q < (int)g_Mu.size(); q++)
        {
            const auto& g = plan[2 * g_Pairs[q].b + (g_Pairs[q].dir > 0 ? 0 : 1)];
            for (int t = 0; t < g_W; t++) g_Mu[q][t] = max(0.0, g_Mu[q][t] + step * (inside[q][t] - g[t]));
        }
    }
    double secs = chrono::duration<double>(chrono::steady_clock::now() - t0).count();
    int lb = (int)min((double)g_UB, ceil(best - 1e-6));
    printf("RESULT mode lb phase %d windows %d L %.6f LB %d UB %d LB0 %.0f iterations %d seconds %.1f heuristic %.0f\n",
           g_Phase ? 1 : 0, windows ? 1 : 0, best, lb, g_UB, free_total, it, secs, heur_best >= INF ? -1.0 : heur_best);
    return 0;
}

// price_mode  (note section 8)
//   meaning:   the two pricing problems of the master at given duals: every train's best trip under lambda (and mu),
//              every block's best plan under mu, and L(lambda, mu) there; the axis and windows exactly as --mode lb
//   duals:     lines "L r t value" (lambda of cell (r, t)) and "M q t value" (mu of pair q at t); with --meet also
//              "A run t value" / "B run t value" (alpha / beta of meet run `run` at t, note section 14); absent = 0
//   --meet:    prints the runs (RUN run i j ei xi ej xj) and each opposing pair's runs west to east (MEETPAIR), prices
//              the trains with the meet multipliers and adds the z part to L
//   --price-heuristic: the Lagrangian heuristic at these prices (priced departure order, omega 0 / 0.5 / 1), HEUR and
//              the best schedule to --out
bool g_PriceHeur = false;
int price_mode(const char* duals, bool windows)
{
    free_run();
    double free_total = accumulate(g_Free.begin(), g_Free.end(), 0.0);
    if (g_UB <= 0) { fprintf(stderr, "--ub required\n"); return 2; }
    int n = (int)g_T.size(), m = (int)g_Tracks.size();
    vector<int> Tk(n);
    int max_release = 0, max_p = 0;
    for (auto& T : g_T) { max_release = max(max_release, T.release); for (auto& l : T.legs) max_p = max(max_p, l.p); }
    for (int k = 0; k < n; k++)
        Tk[k] = windows ? g_T[k].release + g_UB - 1 - (int)llround(free_total - g_Free[k]) : max_release + g_UB;
    g_W = *max_element(Tk.begin(), Tk.end()) + g_H + max_p + 2;
    g_Mode = PRICED;
    g_Lambda.assign((size_t)m * g_W, 0.0);
    g_Mu.assign(g_Phase ? g_Pairs.size() : 0, vector<double>(g_W, 0.0));
    g_MuPre.assign(g_Mu.size(), vector<double>(g_W + 1, 0.0));
    if (g_Meet) build_meet();
    ifstream in(duals);
    if (!in) { fprintf(stderr, "cannot read %s\n", duals); return 2; }
    string kind;
    long outside = 0;
    while (in >> kind)
    {
        int a, t; double v;
        in >> a >> t >> v;
        if (v < 0.0) { fprintf(stderr, "negative dual\n"); return 2; }
        if (t < 0 || t >= g_W) { outside++; continue; }
        if (kind == "L" && a >= 0 && a < m) g_Lambda[(size_t)a * g_W + t] = v;
        else if (kind == "M" && g_Phase && a >= 0 && a < (int)g_Mu.size()) g_Mu[a][t] = v;
        else if ((kind == "A" || kind == "B") && g_Meet && a >= 0 && a < (int)g_Runs.size())
            (kind == "A" ? g_Runs[a].a : g_Runs[a].b)[t] = v;
        else if (kind != "M" && kind != "A" && kind != "B") { fprintf(stderr, "bad dual line\n"); return 2; }
    }
    build_lambda_prefix();
    if (g_Meet)
    {
        meet_prices();
        for (int r = 0; r < (int)g_Runs.size(); r++)
        {
            const auto& R = g_Runs[r];
            printf("RUN %d %d %d %d %d %d %d\n", r, R.i, R.j, R.ei, R.xi, R.ej, R.xj);
        }
        for (int p = 0; p < (int)g_MeetPairs.size(); p++)
        {
            printf("MEETPAIR %d", p);
            for (int r : g_MeetPairs[p]) printf(" %d", r);
            printf("\n");
        }
    }
    printf("AXIS %d LB0 %.0f OUTSIDE %ld\n", g_W, free_total, outside);
    for (int k = 0; k < n; k++) printf("WINDOW %d %d\n", k, Tk[k]);
    for (int q = 0; q < (int)g_Pairs.size(); q++)
        printf("PAIR %d %d %d %d %d %d\n", q, g_Pairs[q].k, g_Pairs[q].b, g_Pairs[q].dir, g_Pairs[q].leg, g_Pairs[q].len);
    double sum = 0.0;
    vector<Trip> priced(n);
    for (int k = 0; k < n; k++)
    {
        Trip& trip = priced[k];
        double v = train_dp(k, Tk[k], &trip);
        if (v >= INF) { printf("EMPTY %d\n", k); return 0; }
        sum += v;
        print_trip(stdout, k, trip);
    }
    double lam = 0.0;
    for (int r = 0; r < m; r++)
        for (int t = 0; t < g_W; t++) lam += g_Tracks[r] * g_Lambda[(size_t)r * g_W + t];
    double phi = 0.0;
    if (g_Phase)
        for (int b = 0; b < (int)g_Block.size(); b++)
        {
            vector<char> g[2];
            double v = phase_dp(b, g);
            phi += v;
            printf("PHASE %d %.6f\n", b, v);
            for (int d = 0; d < 2; d++)
                for (int t = 0; t < g_W; )
                {
                    if (!g[d][t]) { t++; continue; }
                    int e = t;
                    while (e < g_W && g[d][e]) e++;
                    printf("G %d %d %d %d\n", b, d, t, e);
                    t = e;
                }
        }
    vector<char> zmeet;
    double meet = g_Meet ? meet_value(zmeet) : 0.0;
    printf("LAGR %.6f\n", sum - lam - phi + meet);
    if (g_PriceHeur)
    {
        double best = INF;
        for (double omega : {0.0, 0.5, 1.0})
        {
            g_Omega = omega;
            vector<Trip> sched;
            double v = lagrangian_heuristic(priced, Tk, sched);
            if (v < best - 1e-9)
            {
                best = v;
                if (g_Out) write_schedule_of(g_Out, sched);
            }
        }
        g_Omega = 1.0;
        printf("HEUR %.0f\n", best >= INF ? -1.0 : best);
    }
    return 0;
}

// ========================================================
// 5b. BRANCH AND BOUND  (note section 9)
// ========================================================

struct Restr { int type, k, a, lo, hi; };         // 0 forbid cell (a = r, lo = t); 1 forbid block entry (a = pair,
                                                  // [lo, hi]); 2 forbid departure ([lo, hi]); 3 meet position of
                                                  // opposing pair k in [lo, hi]
struct Duals { vector<pair<int, float>> lam, mu, ma, mb; }; // sparse: lam r * W + t, mu q * W + t, meet run * (W + 1) + t

struct BBNode
{
    int id, depth, key;                           // key: the parent's bound, a bound on this node
    vector<Restr> res;
    shared_ptr<Duals> warm;
};

void apply_restrictions(const vector<Restr>& res)
{
    int n = (int)g_T.size(), m = (int)g_Tracks.size();
    g_ForbidCell.assign(n, vector<vector<int>>(m));
    g_ForbidEntry.assign(g_Pairs.size(), {});
    g_ForbidDep.assign(n, {});
    g_BPairChoice.assign(g_BPairs.size(), {INT32_MIN, INT32_MIN});
    g_BlkBound.assign(g_Blk.size(), {INT32_MIN / 2, INT32_MAX / 2});
    g_MeetRange.assign(g_MeetPairs.size(), {0, 0});
    for (size_t pi = 0; pi < g_MeetPairs.size(); pi++) g_MeetRange[pi] = {0, (int)g_MeetPairs[pi].size()};
    for (auto& x : res)
    {
        if (x.type == 0) g_ForbidCell[x.k][x.a].push_back(x.lo);
        else if (x.type == 1) g_ForbidEntry[x.a].push_back({x.lo, x.hi});
        else if (x.type == 2) g_ForbidDep[x.k].push_back({x.lo, x.hi});
        else if (x.type == 4) g_BPairChoice[x.k] = {x.lo, x.hi};     // block pair k: S_y - S_x in [lo, hi]
        else if (x.type == 5)                                          // block k: S in [lo, hi]
        {
            g_BlkBound[x.k][0] = max(g_BlkBound[x.k][0], x.lo);
            g_BlkBound[x.k][1] = min(g_BlkBound[x.k][1], x.hi);
        }
        else                                          // 3: meet position of pair k within [lo, hi]
        {
            g_MeetRange[x.k][0] = max(g_MeetRange[x.k][0], x.lo);
            g_MeetRange[x.k][1] = min(g_MeetRange[x.k][1], x.hi);
        }
    }
    for (auto& per : g_ForbidCell) for (auto& v : per) sort(v.begin(), v.end());
}

shared_ptr<Duals> save_duals()
{
    auto d = make_shared<Duals>();
    for (size_t c = 0; c < g_Lambda.size(); c++) if (g_Lambda[c] > 1e-9) d->lam.push_back({(int)c, (float)g_Lambda[c]});
    for (size_t q = 0; q < g_Mu.size(); q++)
        for (int t = 0; t < g_W; t++) if (g_Mu[q][t] > 1e-9) d->mu.push_back({(int)(q * g_W + t), (float)g_Mu[q][t]});
    for (size_t r = 0; r < g_Runs.size(); r++)
        for (int t = 0; t <= g_W; t++)
        {
            if (g_Runs[r].a[t] > 1e-9) d->ma.push_back({(int)(r * (g_W + 1) + t), (float)g_Runs[r].a[t]});
            if (g_Runs[r].b[t] > 1e-9) d->mb.push_back({(int)(r * (g_W + 1) + t), (float)g_Runs[r].b[t]});
        }
    return d;
}

void load_duals(const Duals* d)
{
    fill(g_Lambda.begin(), g_Lambda.end(), 0.0);
    for (auto& v : g_Mu) fill(v.begin(), v.end(), 0.0);
    for (auto& R : g_Runs) { fill(R.a.begin(), R.a.end(), 0.0); fill(R.b.begin(), R.b.end(), 0.0); }
    if (!d) return;
    for (auto& e : d->lam) g_Lambda[e.first] = e.second;
    for (auto& e : d->mu) g_Mu[e.first / g_W][e.first % g_W] = e.second;
    for (auto& e : d->ma) g_Runs[e.first / (g_W + 1)].a[e.first % (g_W + 1)] = e.second;
    for (auto& e : d->mb) g_Runs[e.first / (g_W + 1)].b[e.first % (g_W + 1)] = e.second;
}

// node_lr
//   meaning:   max_j L(lambda_j, mu_j) over K subgradient steps from the loaded duals, under the node's restrictions;
//              the trips at the best step in `best_trips`, its duals left loaded; +inf if a train has no trip
double node_lr(const vector<int>& Tk, int K, double theta, int patience, int ub, vector<Trip>& best_trips)
{
    int n = (int)g_T.size(), m = (int)g_Tracks.size();
    vector<Trip> trips(n);
    vector<int> usage((size_t)m * g_W);
    double best = -INF;
    shared_ptr<Duals> best_duals;
    int since = 0;
    vector<char> zmeet;
    for (int it = 0; it <= K; it++)
    {
        build_lambda_prefix();
        if (g_Meet) meet_prices();
        double sum = 0.0;
        for (int k = 0; k < n; k++)
        {
            double v = train_dp(k, Tk[k], &trips[k]);
            if (v >= INF) { g_CurTrain = -1; return INF; }
            sum += v;
        }
        double lam = 0.0;
        for (int r = 0; r < m; r++)
            for (int t = 0; t < g_W; t++) lam += g_Tracks[r] * g_Lambda[(size_t)r * g_W + t];
        vector<vector<char>> plan(g_Block.size() * 2);
        double phi = 0.0;
        if (g_Phase)
            for (int b = 0; b < (int)g_Block.size(); b++) phi += phase_dp(b, &plan[2 * b]);
        double L = sum - lam - phi + (g_Meet ? meet_value(zmeet) : 0.0);
        if (L > best + 1e-9) { best = L; best_trips = trips; best_duals = save_duals(); since = 0; }
        else if (++since >= patience) { theta *= 0.5; since = 0; }
        if (ceil(best - 1e-6) >= ub || it == K) break;
        fill(usage.begin(), usage.end(), 0);
        for (int k = 0; k < n; k++)
            for_each_hold(k, trips[k], [&](int r, int a, int b) { for (int t = a; t < b && t < g_W; t++) usage[(size_t)r * g_W + t]++; });
        double norm = 0.0;
        for (int r = 0; r < m; r++)
            for (int t = 0; t < g_W; t++)
            {
                size_t c = (size_t)r * g_W + t;
                double gr = usage[c] - g_Tracks[r];
                if (g_Lambda[c] <= 0.0 && gr < 0.0) continue;
                norm += gr * gr;
            }
        vector<vector<char>> inside(g_Mu.size());
        for (int q = 0; q < (int)g_Mu.size(); q++)
        {
            auto& P = g_Pairs[q];
            inside[q].assign(g_W, 0);
            int s0 = trips[P.k].legs[P.leg][1];
            for (int t = s0; t < s0 + P.len && t < g_W; t++) inside[q][t] = 1;
            const auto& g = plan[2 * P.b + (P.dir > 0 ? 0 : 1)];
            for (int t = 0; t < g_W; t++)
            {
                double gr = inside[q][t] - g[t];
                if (g_Mu[q][t] <= 0.0 && gr < 0.0) continue;
                norm += gr * gr;
            }
        }
        if (g_Meet) norm += meet_step(trips, zmeet, 0.0, false);
        if (norm <= 0.0) break;
        double step = theta * max(ub - L, 1e-6) / norm;
        if (g_Meet) meet_step(trips, zmeet, step, true);
        for (int r = 0; r < m; r++)
            for (int t = 0; t < g_W; t++)
            {
                size_t c = (size_t)r * g_W + t;
                g_Lambda[c] = max(0.0, g_Lambda[c] + step * (usage[c] - g_Tracks[r]));
            }
        for (int q = 0; q < (int)g_Mu.size(); q++)
        {
            const auto& g = plan[2 * g_Pairs[q].b + (g_Pairs[q].dir > 0 ? 0 : 1)];
            for (int t = 0; t < g_W; t++) g_Mu[q][t] = max(0.0, g_Mu[q][t] + step * (inside[q][t] - g[t]));
        }
    }
    g_CurTrain = -1;
    load_duals(best_duals.get());
    return best;
}

int g_Plunge = 0;                                 // --plunge P: dive after branching; a new dive every P popped nodes
int g_KDive = 5;                                  // --k-dive K: Lagrangian steps at a dive node (warm-started)
int g_SplitN = 0;                                 // --split N FILE: stop when N nodes are open, write them to FILE
const char* g_SplitFile = nullptr;
const char* g_NodesIn = nullptr;                  // --nodes-in FILE: start from these nodes instead of the root
const char* g_SharedUB = nullptr;                 // --shared-ub FILE: an incumbent value shared between processes
int g_NodeHeur = 0;                               // --node-heuristic bits: 1 Lagrangian repair, 2 phase-slot construction
int g_NodeHeurEvery = 1;                          // --node-heuristic-every N: at the root and every N-th node

int bb_mode(double time_cap, const string& rule, int k_root, int k_node, double theta_node, int patience_node,
            const char* out)
{
    auto t0 = chrono::steady_clock::now();
    auto elapsed = [&] { return chrono::duration<double>(chrono::steady_clock::now() - t0).count(); };
    free_run();
    double free_total = accumulate(g_Free.begin(), g_Free.end(), 0.0);
    if (g_UB <= 0) { fprintf(stderr, "--ub required\n"); return 2; }
    int n = (int)g_T.size(), m = (int)g_Tracks.size();
    int ub0 = g_UB;
    vector<int> Tk(n);
    int max_p = 0;
    for (auto& T : g_T) for (auto& l : T.legs) max_p = max(max_p, l.p);
    for (int k = 0; k < n; k++) Tk[k] = g_T[k].release + ub0 - 1 - (int)llround(free_total - g_Free[k]);
    g_W = *max_element(Tk.begin(), Tk.end()) + g_H + max_p + 2;
    g_Mode = PRICED;
    g_Lambda.assign((size_t)m * g_W, 0.0);
    g_Mu.assign(g_Phase ? g_Pairs.size() : 0, vector<double>(g_W, 0.0));
    g_MuPre.assign(g_Mu.size(), vector<double>(g_W + 1, 0.0));
    if (g_Meet) { build_meet(); printf("MEET pairs %zu runs %zu\n", g_MeetPairs.size(), g_Runs.size()); }
    if (rule == "block") build_blocks();
    int ub = ub0;
    vector<Trip> best_sched;
    auto cmp = [](const BBNode* a, const BBNode* b) { return a->key != b->key ? a->key > b->key : a->depth < b->depth; };
    priority_queue<BBNode*, vector<BBNode*>, decltype(cmp)> open(cmp);
    if (g_NodesIn)                                   // a share of an open list written by --split (parallel B&B)
    {
        ifstream in(g_NodesIn);
        int key, depth, nr;
        while (in >> key >> depth >> nr)
        {
            vector<Restr> res(nr);
            for (auto& x : res) in >> x.type >> x.k >> x.a >> x.lo >> x.hi;
            open.push(new BBNode{0, depth, key, res, nullptr});
        }
        printf("NODES IN %zu\n", open.size());
    }
    else open.push(new BBNode{0, 0, (int)free_total, {}, nullptr});
    auto read_shared = [&]() -> int {
        if (!g_SharedUB) return INT32_MAX;
        ifstream in(g_SharedUB);
        int v;
        return (in >> v) ? v : INT32_MAX;
    };
    auto write_shared = [&](int v) {
        if (!g_SharedUB || v >= read_shared()) return;
        string tmp = string(g_SharedUB) + ".tmp" + to_string((long)getpid());
        { ofstream o(tmp); o << v << "\n"; }
        rename(tmp.c_str(), g_SharedUB);
    };
    auto new_ub = [&](int v) {                       // windows of the new incumbent: every better schedule fits
        ub = v; g_UB = ub;
        for (int k = 0; k < n; k++) Tk[k] = min(Tk[k], g_T[k].release + ub - 1 - (int)llround(free_total - g_Free[k]));
    };
    BBNode* next = nullptr;                           // the child a dive continues with
    shared_ptr<Duals> first_warm;
    long popped = 0;
    long examined = 0, pruned = 0, infeasible = 0, created = 1, feasible_nodes = 0;
    long by_phase = 0, by_cell = 0, by_dep = 0, by_meet = 0, by_prop = 0, prop_wins = 0;
    int root_lb = -1;
    double last_report = -1e9;
    auto global_lb = [&]() {
        int v = ub;
        if (!open.empty()) v = min(v, open.top()->key);
        if (next) v = min(v, next->key);
        return v;
    };
    while ((!open.empty() || next) && elapsed() < time_cap)
    {
        if (g_SplitN > 0 && !next && (int)open.size() >= g_SplitN) break;
        BBNode* node;
        bool diving = next != nullptr;
        if (diving) { node = next; next = nullptr; }
        else { node = open.top(); open.pop(); popped++; }
        if (node->key >= ub) { pruned++; delete node; continue; }
        bool dive_here = g_Plunge > 0 && (diving || (popped - 1) % g_Plunge == 0);
        examined++;
        apply_restrictions(node->res);
        if (rule == "meet" && !propagate_meets(Tk)) { infeasible++; delete node; continue; }
        if (rule == "block" && !propagate_blocks(Tk)) { infeasible++; delete node; continue; }
        int prop_lb = rule == "block" ? (int)ceil(block_prop_bound() - 1e-6) : 0;   // note section 21
        if (prop_lb >= ub) { pruned++; by_prop++; delete node; continue; }
        if (!node->warm && first_warm) node->warm = first_warm;   // loaded nodes share the first one's duals
        load_duals(node->warm.get());
        vector<Trip> trips;
        bool root = node->depth == 0 || !node->warm;     // no warm duals: a root-like start (also a loaded node)
        int K = root ? k_root : (diving ? g_KDive : k_node);
        double L = node_lr(Tk, K, root ? 1.0 : theta_node, root ? 100 : patience_node, ub, trips);
        if (L >= INF) { infeasible++; delete node; continue; }
        int lb = max(max(node->key, (int)ceil(L - 1e-6)), prop_lb);
        if (prop_lb > (int)ceil(L - 1e-6)) prop_wins++;
        if (root && root_lb < 0) { root_lb = lb; printf("ROOT L %.6f LB %d\n", L, lb); }
        if (lb >= ub) { pruned++; delete node; continue; }
        auto warm = save_duals();
        if (root && !first_warm) first_warm = warm;
        if (g_NodeHeur && (root || diving || examined % g_NodeHeurEvery == 0))   // note section 12: primal side
        {
            load_duals(warm.get());
            build_lambda_prefix();
            vector<vector<char>> plan(g_Block.size() * 2);
            if (g_Phase) for (int b = 0; b < (int)g_Block.size(); b++) phase_dp(b, &plan[2 * b]);
            vector<pair<double, vector<Trip>>> found;
            if (g_NodeHeur & 1)                          // Lagrangian repair: priced order, full cells blocked
                for (double omega : {0.0, 0.5, 1.0})
                {
                    g_Omega = omega;
                    vector<Trip> sched;
                    double v = lagrangian_heuristic(trips, Tk, sched);
                    found.push_back({v, sched});
                }
            if ((g_NodeHeur & 2) && g_Phase)             // phase-slot construction (note section 11)
                for (bool strict : {false, true})
                {
                    vector<Trip> sched;
                    int freed = 0;
                    double v = phase_slot_heuristic(trips, plan, Tk, strict, sched, &freed);
                    found.push_back({v, sched});
                }
            g_Mode = PRICED;
            for (auto& [v, sched] : found)
                if (v < ub - 1e-9)
                {
                    new_ub((int)llround(v)); best_sched = sched;
                    printf("UB %d %.1f node %ld heuristic%s\n", ub, elapsed(), examined, diving ? " dive" : "");
                    fflush(stdout);
                    if (out) write_schedule_of(out, best_sched);
                    write_shared(ub);
                }
            if (lb >= ub) { pruned++; delete node; continue; }
        }
        // conflicts of the trips
        vector<int> usage((size_t)m * g_W, 0);
        for (int k = 0; k < n; k++)
            for_each_hold(k, trips[k], [&](int r, int a, int b) { for (int t = a; t < b && t < g_W; t++) usage[(size_t)r * g_W + t]++; });
        vector<vector<Restr>> kids;
        int prefer = 0;                                  // the child a dive takes: the least change to the trips
        // rule P: the (block, minute) with the most opposite through pairs inside; rule I: m checkpoints around it
        if (rule == "block")                             // the deepest conflict of an undecided block pair
        {
            vector<int> S(g_Blk.size());
            for (size_t b = 0; b < g_Blk.size(); b++) S[b] = trips[g_Blk[b].k].legs[g_Blk[b].a][1];
            long best_depth = 0; int bp = -1; long worst_viol = 0; int vp = -1;
            for (size_t pi = 0; pi < g_BPairs.size(); pi++)
            {
                const auto& P = g_BPairs[pi];
                int d = S[P.y] - S[P.x];
                if (g_BPairChoice[pi][0] != INT32_MIN)
                {
                    long v = max((long)g_BPairChoice[pi][0] - d, (long)d - g_BPairChoice[pi][1]);
                    if (v > worst_viol) { worst_viol = v; vp = (int)pi; }
                    continue;
                }
                for (auto& f : P.forb)
                    if (f[0] <= d && d <= f[1])
                    {
                        long depth = min(d - f[0], f[1] - d) + 1;
                        if (depth > best_depth) { best_depth = depth; bp = (int)pi; }
                    }
            }
            if (bp >= 0)
            {
                const auto& P = g_BPairs[bp];
                int dlo = g_BlkE[P.y] - g_BlkL[P.x], dhi = g_BlkL[P.y] - g_BlkE[P.x];
                int d = S[P.y] - S[P.x];
                long best_shift = LONG_MAX;
                for (auto& alt : P.alts)
                {
                    if (alt[1] < dlo || alt[0] > dhi) continue;          // impossible under the node's windows
                    vector<Restr> r2 = node->res;
                    r2.push_back({4, bp, 0, alt[0], alt[1]});
                    long shift = d < alt[0] ? (long)alt[0] - d : (d > alt[1] ? (long)d - alt[1] : 0);
                    if (shift < best_shift) { best_shift = shift; prefer = (int)kids.size(); }
                    kids.push_back(r2);
                }
                by_meet++;
                if (kids.empty()) { infeasible++; delete node; continue; }
            }
            else if (vp >= 0)                            // a decided pair the relaxation still breaks: split a start
            {
                const auto& P = g_BPairs[vp];
                int b = g_BlkL[P.x] > g_BlkE[P.x] ? P.x : P.y;
                int v = S[b];
                int cut = (v >= g_BlkE[b] && v < g_BlkL[b]) ? v : (g_BlkE[b] + g_BlkL[b]) / 2;
                vector<Restr> a2 = node->res, b2 = node->res;
                a2.push_back({5, b, 0, INT32_MIN / 2, cut});
                b2.push_back({5, b, 0, cut + 1, INT32_MAX / 2});
                kids.push_back(a2); kids.push_back(b2);
                prefer = v <= cut ? 0 : 1;
                by_dep++;
            }
        }
        if (rule == "meet")                              // the opposing pair whose trips overlap most in a run
        {
            long best_score = 0; int bp = -1;
            for (size_t pi = 0; pi < g_MeetPairs.size(); pi++)
            {
                if (g_MeetRange[pi][0] >= g_MeetRange[pi][1]) continue;
                long score = 0;
                int last_order = 1;                      // i first on the western runs, then j first: monotone
                for (int m = 0; m < (int)g_MeetPairs[pi].size(); m++)
                {
                    const auto& R = g_Runs[g_MeetPairs[pi][m]];
                    int in_i = trips[R.i].legs[R.ei][1], out_i = release_of(R.i, trips[R.i], R.xi) + g_H;
                    int in_j = trips[R.j].legs[R.ej][1], out_j = release_of(R.j, trips[R.j], R.xj) + g_H;
                    int order = out_i <= in_j ? 1 : (out_j <= in_i ? 0 : -1);
                    if (order < 0) score += min(out_i, out_j) - max(in_i, in_j);
                    else if (order > last_order) score += 1000;
                    if (order >= 0) last_order = order;
                }
                if (score > best_score) { best_score = score; bp = (int)pi; }
            }
            if (bp >= 0)
            {
                for (int mpos = g_MeetRange[bp][0]; mpos <= g_MeetRange[bp][1]; mpos++)
                {
                    vector<Restr> r2 = node->res;
                    r2.push_back({3, bp, 0, mpos, mpos});
                    kids.push_back(r2);
                }
                by_meet++;
            }
        }
        if (kids.empty() && (rule == "phase" || rule == "interval" || rule == "meet" || rule == "block"))
        {
            long best_score = 0; int bb = -1, bt = -1;
            for (int b = 0; b < (int)g_Block.size(); b++)
            {
                vector<int> cnt[2] = {vector<int>(g_W + 1, 0), vector<int>(g_W + 1, 0)};
                for (int q = 0; q < (int)g_Pairs.size(); q++)
                {
                    if (g_Pairs[q].b != b) continue;
                    int d = g_Pairs[q].dir > 0 ? 0 : 1, s0 = trips[g_Pairs[q].k].legs[g_Pairs[q].leg][1];
                    cnt[d][max(0, s0)]++;
                    cnt[d][min(g_W, s0 + g_Pairs[q].len)]--;
                }
                int e = 0, w = 0;
                for (int t = 0; t < g_W; t++)
                {
                    e += cnt[0][t]; w += cnt[1][t];
                    if ((long)e * w > best_score) { best_score = (long)e * w; bb = b; bt = t; }
                }
            }
            if (bb >= 0)
            {
                vector<int> points = {bt};
                if (rule != "phase")                     // note section 10: checkpoints L apart, L = shortest through interval
                {
                    int L = INT32_MAX;
                    for (auto& P : g_Pairs) if (P.b == bb) L = min(L, P.len);
                    points.clear();
                    for (int j = 0; j < g_Points; j++)
                    {
                        int t = bt + ((2 * j - (g_Points - 1)) * L) / 2;
                        t = max(0, min(g_W - 1, t));
                        if (points.empty() || t > points.back()) points.push_back(t);
                    }
                }
                int m2 = (int)points.size();
                for (int mask = 0; mask < (1 << m2); mask++)   // child: direction (mask bit j) holds the block at points[j]
                {
                    vector<Restr> r2 = node->res;
                    for (int j = 0; j < m2; j++)
                    {
                        int d = (mask >> j) & 1, t = points[j];
                        for (int q = 0; q < (int)g_Pairs.size(); q++)
                            if (g_Pairs[q].b == bb && (g_Pairs[q].dir > 0 ? 0 : 1) != d)
                                r2.push_back({1, g_Pairs[q].k, q, t - g_Pairs[q].len + 1, t});
                    }
                    kids.push_back(r2);
                }
                by_phase++;
            }
        }
        if (kids.empty())                                // rule C: the earliest over-used cell
        {
            int cr = -1, ct = -1;
            for (int t = 0; t < g_W && cr < 0; t++)
                for (int r = 0; r < m; r++)
                    if (usage[(size_t)r * g_W + t] > g_Tracks[r]) { cr = r; ct = t; break; }
            if (cr >= 0)
            {
                vector<int> holders;
                for (int k = 0; k < n && (int)holders.size() < g_Tracks[cr] + 1; k++)
                {
                    bool h = false;
                    for_each_hold(k, trips[k], [&](int r, int a, int b) { if (r == cr && a <= ct && ct < b) h = true; });
                    if (h) holders.push_back(k);
                }
                for (int k : holders) { vector<Restr> r2 = node->res; r2.push_back({0, k, cr, ct, ct}); kids.push_back(r2); }
                by_cell++;
            }
        }
        if (kids.empty())                                // the trips are a schedule
        {
            feasible_nodes++;
            double cost = 0.0;
            for (int k = 0; k < n; k++) cost += true_cost(k, trips[k]);
            if (cost < ub - 1e-9)
            {
                new_ub((int)llround(cost)); best_sched = trips;
                printf("UB %d %.1f node %ld\n", ub, elapsed(), examined);
                if (out) write_schedule_of(out, best_sched);
                write_shared(ub);
            }
            if (lb < ub)                                 // departure split on the most delayed train
            {
                int kk = 0; double worst = -1;
                for (int k = 0; k < n; k++)
                {
                    double d = true_cost(k, trips[k]) - g_Free[k];
                    if (d > worst) { worst = d; kk = k; }
                }
                int dep = trips[kk].legs[0][1];
                vector<Restr> a = node->res, b = node->res;
                a.push_back({2, kk, 0, dep + 1, INT32_MAX / 2});     // depart <= dep
                b.push_back({2, kk, 0, 0, dep});                     // depart >= dep + 1
                kids.push_back(a); kids.push_back(b);
                prefer = -1;                             // a dive ends at a conflict-free node: it found its schedule
                by_dep++;
            }
        }
        for (int c = 0; c < (int)kids.size(); c++)
        {
            auto* child = new BBNode{(int)created++, node->depth + 1, lb, kids[c], warm};
            if (dive_here && c == prefer) next = child;   // go on down this child now; its siblings wait in the queue
            else open.push(child);
        }
        delete node;
        if (elapsed() - last_report >= 5.0)
        {
            int sh = read_shared();
            if (sh < ub) { new_ub(sh); printf("UB %d %.1f shared\n", ub, elapsed()); }
        }
        if (elapsed() - last_report >= 10.0)
        {
            last_report = elapsed();
            printf("BB %.0f s examined %ld open %zu LB %d UB %d\n", elapsed(), examined, open.size(), global_lb(), ub);
            fflush(stdout);
        }
    }
    int final_lb = global_lb();
    bool split_done = false;
    if (g_SplitN > 0 && g_SplitFile && (!open.empty() || next))   // write the open list for the parallel workers
    {
        split_done = true;
        if (next) { open.push(next); next = nullptr; }
        FILE* f = fopen(g_SplitFile, "w");
        size_t written = 0;
        while (!open.empty())
        {
            BBNode* x = open.top(); open.pop();
            if (x->key < ub)
            {
                fprintf(f, "%d %d %zu", x->key, x->depth, x->res.size());
                for (auto& r : x->res) fprintf(f, " %d %d %d %d %d", r.type, r.k, r.a, r.lo, r.hi);
                fprintf(f, "\n");
                written++;
            }
            delete x;
        }
        fclose(f);
        printf("SPLIT %zu nodes written\n", written);
    }
    printf("RESULT mode bb rule %s phase %d status %s LB %d UB %d root %d examined %ld created %ld pruned %ld "
           "infeasible %ld feasible %ld branch_phase %ld branch_cell %ld branch_dep %ld seconds %.1f branch_meet %ld "
           "pruned_by_propagation %ld propagation_above_lagrangian %ld\n",
           rule.c_str(), g_Phase ? 1 : 0, split_done ? "SPLIT" : (open.empty() && !next ? "PROVEN" : "TIME_CAP"),
           final_lb, ub, root_lb, examined,
           created, pruned, infeasible, feasible_nodes, by_phase, by_cell, by_dep, elapsed(), by_meet, by_prop, prop_wins);
    while (!open.empty()) { delete open.top(); open.pop(); }
    delete next;
    return 0;
}

// probe_mode  (note section 16): root probing of meet positions. After the root Lagrangian (meet rows, the given
// restriction file of meet ranges "pair lo hi"), every position m of every pair of this part (pair % parts == part)
// is fixed in turn -- its orders propagated, a Lagrangian warm-started from the root duals -- and reported:
//   PROBE pair m L        (L = +inf when propagation proves the position impossible)
// A pair's positions partition the problem, so min_m L is a bound; the driver combines the parts.
int probe_mode(int k_root, int k_probe, double theta_node, int patience_node, const char* restrict_file, int part,
               int parts)
{
    auto t0 = chrono::steady_clock::now();
    free_run();
    double free_total = accumulate(g_Free.begin(), g_Free.end(), 0.0);
    if (g_UB <= 0 || !g_Meet) { fprintf(stderr, "--ub and --meet required\n"); return 2; }
    int n = (int)g_T.size(), m = (int)g_Tracks.size();
    vector<int> Tk(n);
    int max_p = 0;
    for (auto& T : g_T) for (auto& l : T.legs) max_p = max(max_p, l.p);
    for (int k = 0; k < n; k++) Tk[k] = g_T[k].release + g_UB - 1 - (int)llround(free_total - g_Free[k]);
    g_W = *max_element(Tk.begin(), Tk.end()) + g_H + max_p + 2;
    g_Mode = PRICED;
    g_Lambda.assign((size_t)m * g_W, 0.0);
    g_Mu.assign(g_Phase ? g_Pairs.size() : 0, vector<double>(g_W, 0.0));
    g_MuPre.assign(g_Mu.size(), vector<double>(g_W + 1, 0.0));
    build_meet();
    vector<Restr> base;
    if (restrict_file)
    {
        ifstream in(restrict_file);
        int pi, lo, hi;
        while (in >> pi >> lo >> hi) base.push_back({3, pi, 0, lo, hi});
    }
    printf("PAIRS %zu\n", g_MeetPairs.size());
    for (size_t pi = 0; pi < g_MeetPairs.size(); pi++)
        printf("PAIR %zu %s %s %zu\n", pi, g_T[g_Runs[g_MeetPairs[pi][0]].i].id.c_str(),
               g_T[g_Runs[g_MeetPairs[pi][0]].j].id.c_str(), g_MeetPairs[pi].size());
    apply_restrictions(base);
    if (!propagate_meets(Tk)) { printf("ROOT INFEASIBLE\n"); return 0; }
    vector<Trip> trips;
    double L0 = node_lr(Tk, k_root, 1.0, 100, g_UB, trips);
    printf("ROOT L %.6f seconds %.1f\n", L0, chrono::duration<double>(chrono::steady_clock::now() - t0).count());
    fflush(stdout);
    auto warm = save_duals();
    auto ranges = g_MeetRange;
    for (size_t pi = 0; pi < g_MeetPairs.size(); pi++)
    {
        if ((int)(pi % parts) != part) continue;
        for (int pos = ranges[pi][0]; pos <= ranges[pi][1]; pos++)
        {
            vector<Restr> res = base;
            res.push_back({3, (int)pi, 0, pos, pos});
            apply_restrictions(res);
            double L = INF;
            if (propagate_meets(Tk))
            {
                load_duals(warm.get());
                L = node_lr(Tk, k_probe, theta_node, patience_node, g_UB, trips);
            }
            if (L >= INF) printf("PROBE %zu %d inf\n", pi, pos);
            else printf("PROBE %zu %d %.6f\n", pi, pos, L);
            fflush(stdout);
        }
    }
    printf("RESULT mode probe part %d parts %d seconds %.1f\n", part, parts,
           chrono::duration<double>(chrono::steady_clock::now() - t0).count());
    return 0;
}

// ========================================================
// 6. UPPER BOUND  (note section 5)
// ========================================================

int g_Axis = 0;
vector<Trip> g_Sched;

void hold(int k, const Trip& trip, int delta)
{
    vector<char> touched(g_Tracks.size(), 0);
    for_each_hold(k, trip, [&](int r, int a, int b)
    {
        for (int t = a; t < b && t < g_W; t++) g_Occ[(size_t)r * g_W + t] += delta;
        touched[r] = 1;
    });
    for (int r = 0; r < (int)g_Tracks.size(); r++) if (touched[r]) rebuild_full(r);
}

// A train that starts inside the network is on its first resource from release whatever is decided, at least until
// release + p_0 + H: while it is not inserted that much is held for it, so an earlier train cannot run over it.
void pending_hold(int k, int delta)
{
    const auto& T = g_T[k];
    if (T.terminal) return;
    int r = T.legs[0].res;
    for (int t = T.release; t < T.release + T.legs[0].p + g_H && t < g_W; t++) g_Occ[(size_t)r * g_W + t] += delta;
    rebuild_full(r);
}

bool insert(int k)
{
    Trip trip;
    pending_hold(k, -1);
    if (train_dp(k, g_Axis, &trip) >= INF) { pending_hold(k, +1); return false; }
    g_Sched[k] = trip;
    hold(k, trip, +1);
    return true;
}

void remove(int k) { hold(k, g_Sched[k], -1); pending_hold(k, +1); }

double total() { double s = 0; for (auto& t : g_Sched) s += t.cost; return s; }

void write_schedule_of(const char* path, const vector<Trip>& sched)
{
    FILE* f = fopen(path, "w");
    fprintf(f, "train_id,index,resource,entry,exit\n");
    for (int k = 0; k < (int)g_T.size(); k++)
        for (int i = 0; i < (int)sched[k].legs.size(); i++)
            fprintf(f, "%s,%d,%s,%d,%d\n", g_T[k].id.c_str(), i, g_ResName[sched[k].legs[i][0]].c_str(),
                    sched[k].legs[i][1], sched[k].legs[i][2]);
    fclose(f);
}

void write_schedule(const char* path)
{
    FILE* f = fopen(path, "w");
    fprintf(f, "train_id,index,resource,entry,exit\n");
    for (int k = 0; k < (int)g_T.size(); k++)
        for (int i = 0; i < (int)g_Sched[k].legs.size(); i++)
            fprintf(f, "%s,%d,%s,%d,%d\n", g_T[k].id.c_str(), i, g_ResName[g_Sched[k].legs[i][0]].c_str(),
                    g_Sched[k].legs[i][1], g_Sched[k].legs[i][2]);
    fclose(f);
}

const char* g_Start = nullptr;

// read_schedule: train_id,index,resource,entry,exit (as write_schedule) into g_Sched, each trip priced by true_cost
bool read_schedule(const char* path)
{
    ifstream in(path);
    if (!in) return false;
    map<string, int> train_of, res_of;
    for (int k = 0; k < (int)g_T.size(); k++) train_of[g_T[k].id] = k;
    for (int r = 0; r < (int)g_ResName.size(); r++) res_of[g_ResName[r]] = r;
    string line;
    getline(in, line);
    while (getline(in, line))
    {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        stringstream ss(line);
        string id, idx, res, a, b;
        getline(ss, id, ','); getline(ss, idx, ','); getline(ss, res, ','); getline(ss, a, ','); getline(ss, b, ',');
        if (!train_of.count(id) || !res_of.count(res)) return false;
        auto& legs = g_Sched[train_of[id]].legs;
        size_t i = (size_t)stoi(idx);
        if (legs.size() <= i) legs.resize(i + 1);
        legs[i] = {res_of[res], stoi(a), stoi(b)};
    }
    for (int k = 0; k < (int)g_T.size(); k++)
    {
        if (g_Sched[k].legs.size() != g_T[k].legs.size()) return false;
        g_Sched[k].cost = true_cost(k, g_Sched[k]);
    }
    return true;
}

double g_T0 = 0.0;                                // --anneal: starting temperature (0 = accept only not-worse)
int g_MaxRuin = 4;                                // --max-ruin: largest number of trains taken out

double g_PhaseMove = 0.0;                          // --phase-moves p: share of moves in the phase-plan neighbourhood
bool last_phase_move = false;

// phase_move  (note section 11.1)
//   meaning:   a move in the space of phase plans. In a random block, the entries of the current timetable, in time order,
//              form green runs (maximal same-direction platoons). Take two adjacent runs A, B (and the next run C of A's
//              direction, half of the time) out of the timetable; the orders tried to put them back are whole platoons:
//              B A (the other direction goes first), A B, and with C also A C B (the two platoons of A's direction merged
//              into one green) and B A C. Within a platoon the trains keep their order.
//   output:    pick (the trains taken out) and perms (the orders); both empty when the block has fewer than two runs
void phase_move(mt19937& rng, vector<int>& pick, vector<vector<int>>& perms)
{
    last_phase_move = false;
    int b = (int)(rng() % g_Block.size());
    vector<array<int, 3>> entries;                 // entry minute, direction, train
    for (auto& P : g_Pairs)
        if (P.b == b) entries.push_back({g_Sched[P.k].legs[P.leg][1], P.dir, P.k});
    sort(entries.begin(), entries.end());
    vector<vector<int>> runs;
    for (size_t j = 0; j < entries.size(); j++)
    {
        if (j == 0 || entries[j][1] != entries[j - 1][1]) runs.push_back({});
        runs.back().push_back(entries[j][2]);
    }
    if (runs.size() < 2) return;
    int j = (int)(rng() % (runs.size() - 1));
    const auto& A = runs[j];
    const auto& B = runs[j + 1];
    bool withC = j + 2 < (int)runs.size() && rng() % 2;
    auto cat = [](initializer_list<const vector<int>*> parts)
    {
        vector<int> v;
        for (auto* p : parts) v.insert(v.end(), p->begin(), p->end());
        return v;
    };
    if (withC)
    {
        const auto& C = runs[j + 2];
        if (A.size() + B.size() + C.size() > 16) return;
        perms = {cat({&A, &C, &B}), cat({&B, &A, &C}), cat({&A, &B, &C})};
    }
    else
    {
        if (A.size() + B.size() > 16) return;
        perms = {cat({&B, &A}), cat({&A, &B})};
    }
    pick = perms[0];
    last_phase_move = true;
}

int upper_bound_mode(double time_cap, unsigned seed, const char* out)
{
    auto t0 = chrono::steady_clock::now();
    auto elapsed = [&] { return chrono::duration<double>(chrono::steady_clock::now() - t0).count(); };
    int n = (int)g_T.size(), m = (int)g_Tracks.size();
    int max_release = 0, max_p = 0;
    g_Axis = 0;
    for (auto& T : g_T)
    {
        max_release = max(max_release, T.release);
        for (auto& l : T.legs) { g_Axis += l.p; max_p = max(max_p, l.p); }
        g_Axis += g_H + 1;
    }
    g_Axis += max_release + 1;
    g_W = g_Axis + g_H + max_p + 2;
    g_Mode = BLOCKED;
    g_Occ.assign((size_t)m * g_W, 0);
    g_FullPre.assign((size_t)m * (g_W + 1), 0);
    for (int r = 0; r < m; r++) rebuild_full(r);
    g_Sched.assign(n, Trip());
    vector<int> order(n);
    iota(order.begin(), order.end(), 0);
    stable_sort(order.begin(), order.end(), [](int a, int b)
    {
        if (g_T[a].terminal != g_T[b].terminal) return !g_T[a].terminal;   // trains already inside go first
        return g_T[a].release < g_T[b].release;
    });
    mt19937 rng(seed);
    bool built = false;
    if (g_Start)                                   // --start FILE: begin from a given timetable (note section 11)
    {
        if (!read_schedule(g_Start)) { fprintf(stderr, "cannot read start %s\n", g_Start); return 2; }
        fill(g_Occ.begin(), g_Occ.end(), 0);
        for (int k = 0; k < n; k++) hold(k, g_Sched[k], +1);
        built = true;
    }
    for (int attempt = 0; attempt < 1000 && !built; attempt++)
    {
        fill(g_Occ.begin(), g_Occ.end(), 0);
        for (int k = 0; k < n; k++) pending_hold(k, +1);
        for (int r = 0; r < m; r++) rebuild_full(r);
        built = true;
        for (int k : order) if (!insert(k)) { built = false; break; }
        if (!built) shuffle(order.begin(), order.end(), rng);
    }
    if (!built) { fprintf(stderr, "insertion failed in 1000 orders\n"); return 3; }
    double current = total(), best = current;
    vector<Trip> best_sched = g_Sched;
    printf("UB %.0f %.1f initial\n", current, elapsed());
    long moves = 0, accepted = 0, phase_moves = 0, phase_gains = 0;
    while (elapsed() < time_cap)
    {
        moves++;
        vector<int> pick;
        vector<vector<int>> perms;
        last_phase_move = false;
        if (g_PhaseMove > 0.0 && uniform_real_distribution<double>(0.0, 1.0)(rng) < g_PhaseMove)
            phase_move(rng, pick, perms);
        if (pick.empty())
        {
            int size = 2 + (int)(rng() % (g_MaxRuin - 1));
            size = min(size, n);
            int k0 = (int)(rng() % n);
            pick.push_back(k0);
            if (rng() % 2)                             // related: the trains departing nearest to k0
            {
                vector<pair<int, int>> near;
                for (int k = 0; k < n; k++)
                    if (k != k0) near.push_back({abs(g_Sched[k].legs[0][1] - g_Sched[k0].legs[0][1]), k});
                sort(near.begin(), near.end());
                for (int j = 0; (int)pick.size() < size && j < (int)near.size(); j++) pick.push_back(near[j].second);
            }
            else
                while ((int)pick.size() < size)
                {
                    int k = (int)(rng() % n);
                    if (find(pick.begin(), pick.end(), k) == pick.end()) pick.push_back(k);
                }
            sort(pick.begin(), pick.end());
            if (size <= 3) do perms.push_back(pick); while (next_permutation(pick.begin(), pick.end()));
            else for (int j = 0; j < 8; j++) { shuffle(pick.begin(), pick.end(), rng); perms.push_back(pick); }
        }
        else phase_moves++;
        bool is_phase_move = last_phase_move;
        vector<int> removed_ids = pick;
        vector<Trip> saved;
        for (int k : pick) { saved.push_back(g_Sched[k]); remove(k); }
        double removed = 0;
        for (auto& t : saved) removed += t.cost;
        double best_add = INF;
        vector<Trip> best_trips;
        vector<int> best_perm;
        for (auto& perm : perms)
        {
            double add = 0;
            bool ok = true;
            vector<int> done;
            for (int k : perm)
            {
                if (!insert(k)) { ok = false; break; }
                add += g_Sched[k].cost;
                done.push_back(k);
            }
            if (ok && add < best_add - 1e-9)
            {
                best_add = add; best_perm = perm; best_trips.clear();
                for (int k : perm) best_trips.push_back(g_Sched[k]);
            }
            for (int k : done) remove(k);
        }
        double temperature = g_T0 > 0.0 ? g_T0 * pow(0.01, fmod(elapsed(), 30.0) / 30.0) : 0.0;   // cools over 30 s, then again
        bool take = best_add <= removed + 1e-9;
        if (!take && best_add < INF && temperature > 0.0)
            take = uniform_real_distribution<double>(0.0, 1.0)(rng) < exp(-(best_add - removed) / temperature);
        if (take)                                  // not worse (or annealing lets it through): take it
        {
            for (int j = 0; j < (int)best_perm.size(); j++)
            {
                g_Sched[best_perm[j]] = best_trips[j];
                pending_hold(best_perm[j], -1);
                hold(best_perm[j], best_trips[j], +1);
            }
            current += best_add - removed;
            accepted++;
            if (current < best - 1e-9)
            {
                if (removed_ids.size() && is_phase_move) phase_gains++;
                best = current; best_sched = g_Sched;
                printf("UB %.0f %.1f move %ld\n", best, elapsed(), moves);
                fflush(stdout);
            }
        }
        else                                       // worse: put the removed trips back exactly
            for (int j = 0; j < (int)removed_ids.size(); j++)
            {
                g_Sched[removed_ids[j]] = saved[j];
                pending_hold(removed_ids[j], -1);
                hold(removed_ids[j], saved[j], +1);
            }
    }
    g_Sched = best_sched;
    if (out) write_schedule(out);
    printf("RESULT mode ub UB %.0f moves %ld accepted %ld seconds %.1f\n", best, moves, accepted, elapsed());
    printf("PHASE MOVES %ld improving %ld\n", phase_moves, phase_gains);
    return 0;
}

// ========================================================
// 7. MAIN
// ========================================================

int main(int argc, char** argv)
{
    if (argc < 2) { fprintf(stderr, "usage: siding_lr INSTANCE --mode ub|lb ...\n"); return 2; }
    if (!read_instance(argv[1])) { fprintf(stderr, "cannot read %s\n", argv[1]); return 2; }
    string mode = "ub";
    double time_cap = 60.0, theta = 1.0;
    int iterations = 300, patience = 20, ub = -1;
    unsigned seed = 1;
    bool windows = true;
    const char* out = nullptr;
    const char* duals = nullptr;
    const char* restrict_file = nullptr;
    int part = 0, parts = 1;
    string rule = "phase";
    int k_root = 2000, k_node = 60, patience_node = 10;
    double theta_node = 0.25;
    for (int a = 2; a < argc; a++)
    {
        string s = argv[a];
        auto next = [&] { if (a + 1 >= argc) { fprintf(stderr, "%s needs a value\n", s.c_str()); exit(2); } return argv[++a]; };
        if (s == "--mode") mode = next();
        else if (s == "--time-cap") time_cap = atof(next());
        else if (s == "--seed") seed = (unsigned)atoi(next());
        else if (s == "--out") { out = next(); g_Out = out; }
        else if (s == "--heuristic-every") g_HeurEvery = atoi(next());
        else if (s == "--duals") duals = next();
        else if (s == "--rule") rule = next();
        else if (s == "--points") g_Points = max(1, min(6, atoi(next())));
        else if (s == "--k-root") k_root = atoi(next());
        else if (s == "--k-node") k_node = atoi(next());
        else if (s == "--theta-node") theta_node = atof(next());
        else if (s == "--patience-node") patience_node = atoi(next());
        else if (s == "--dump-columns") { g_Dump = fopen(next(), "w"); if (!g_Dump) return 2; }
        else if (s == "--dump-every") g_DumpEvery = max(1, atoi(next()));
        else if (s == "--ub") ub = atoi(next());
        else if (s == "--phase") g_Phase = true;
        else if (s == "--meet") g_Meet = true;
        else if (s == "--price-heuristic") g_PriceHeur = true;
        else if (s == "--iters") iterations = atoi(next());
        else if (s == "--theta") theta = atof(next());
        else if (s == "--patience") patience = atoi(next());
        else if (s == "--no-windows") windows = false;
        else if (s == "--anneal") g_T0 = atof(next());
        else if (s == "--start") g_Start = next();
        else if (s == "--restrict") restrict_file = next();
        else if (s == "--part") part = atoi(next());
        else if (s == "--parts") parts = max(1, atoi(next()));
        else if (s == "--node-heuristic") g_NodeHeur = atoi(next());
        else if (s == "--plunge") g_Plunge = atoi(next());
        else if (s == "--k-dive") g_KDive = max(0, atoi(next()));
        else if (s == "--split") { g_SplitN = atoi(next()); g_SplitFile = next(); }
        else if (s == "--nodes-in") g_NodesIn = next();
        else if (s == "--shared-ub") g_SharedUB = next();
        else if (s == "--node-heuristic-every") g_NodeHeurEvery = max(1, atoi(next()));
        else if (s == "--phase-moves") g_PhaseMove = atof(next());
        else if (s == "--phase-heuristic-every") g_PhaseHeurEvery = atoi(next());
        else if (s == "--lb-time-cap") g_LbTimeCap = atof(next());
        else if (s == "--max-ruin") g_MaxRuin = max(2, atoi(next()));
        else { fprintf(stderr, "unknown option %s\n", s.c_str()); return 2; }
    }
    if (ub >= 0) g_UB = ub;
    printf("INSTANCE trains %zu resources %zu blocks %zu pairs %zu H %d alpha %d beta %d\n",
           g_T.size(), g_Tracks.size(), g_Block.size(), g_Pairs.size(), g_H, g_Alpha, g_Beta);
    if (mode == "lb") { int rc = lower_bound_mode(iterations, theta, patience, windows); if (g_Dump) fclose(g_Dump); return rc; }
    if (mode == "price") return duals ? price_mode(duals, windows) : 2;
    if (mode == "bb") return bb_mode(time_cap, rule, k_root, k_node, theta_node, patience_node, out);
    if (mode == "ub") return upper_bound_mode(time_cap, seed, out);
    if (mode == "probe") return probe_mode(k_root, k_node, theta_node, patience_node, restrict_file, part, parts);
    fprintf(stderr, "unknown mode %s\n", mode.c_str());
    return 2;
}
