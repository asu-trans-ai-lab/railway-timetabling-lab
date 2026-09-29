#pragma once
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace bottleneck {
using Time = int64_t;
struct Train { std::string id; int direction; Time release; std::vector<Time> run; };
struct Model {
    std::string name;
    int sections = 6;
    Time headway = 3;
    std::vector<int> tracks, berths;
    std::vector<Train> trains;
};
struct Move { int train, index, segment; Time start, end; };
using Schedule = std::vector<std::vector<Move>>;
struct Validation { bool ok = true; Time wait = 0; std::vector<std::string> errors; };
std::vector<std::string> split(const std::string&, char = '\t');
Model read_model(const std::string&);
void write_model(const Model&, const std::string&);
Model example(int trains, int spacing, bool heterogeneous);
Time running(const Train&);
Schedule serial_seed(const Model&, const std::vector<int>&);
void write_schedule(const Model&, const Schedule&, const std::string&);
Schedule read_schedule(const Model&, const std::string&);
Validation validate(const Model&, const Schedule&);
std::string fingerprint(const Model&);

struct Options {
    bool macro = true, cache = true;
    double seconds = 300;
    uint64_t max_nodes = 0;
    int intervention = -1; // [0,m): track, [m,m+m+1): berth
};
struct Metrics {
    std::string status;
    Time lb = 0, ub = 0, horizon = 0;
    uint64_t generated = 0, expanded = 0, bound_pruned = 0, infeasible = 0;
    uint64_t relax_attempts = 0, label_updates = 0, cache_hits = 0, cache_misses = 0;
    uint64_t cache_entries = 0, peak_frontier = 0;
    double precompute_seconds = 0, search_seconds = 0, validation_seconds = 0;
    Schedule best;
};
Metrics solve(const Model&, const Options&);
void write_metrics(const Model&, const Options&, const Metrics&, const std::string&);
void write_patterns(const Model&, const Schedule&, const std::string&);
void write_templates(const Model&, Time, const std::string&);
Schedule connect_patterns(const Model&, const std::string&);
void write_hotspots(const Model&, const Schedule&, const std::string&);
} // namespace bottleneck
