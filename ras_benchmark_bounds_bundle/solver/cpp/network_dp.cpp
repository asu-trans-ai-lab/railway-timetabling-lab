// Unrestricted physical-network space-time DP for the clean resource LR/B&B.
//
// The executable intentionally has no route-list input.  It reads physical
// arcs and dynamically chooses outgoing moves. Branches can restrict spatial
// arc use, entry windows, or exact (arc, direction, entry, exit) movements.
//
// Protocol: all input files are small TSV files with a header.  The output is
// one TSV row per train request.  Time is discretized by --time-step; every
// transition is snapped forward to the next grid point, and therefore time
// strictly increases.  Physical running cost still uses the configured
// directional traversal time, while arrival/MOW/price-cell membership uses
// the snapped transition interval.

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

constexpr double INF = std::numeric_limits<double>::infinity();
constexpr double EPS = 1e-9;

using std::string;
using std::vector;

vector<string> split(const string& value, char separator) {
    vector<string> out;
    string part;
    for (char c : value) {
        if (c == separator) {
            out.push_back(part);
            part.clear();
        } else {
            part.push_back(c);
        }
    }
    out.push_back(part);
    return out;
}

string trim(string value) {
    while (!value.empty() && (value.back() == '\r' || value.back() == ' ' || value.back() == '\t')) {
        value.pop_back();
    }
    std::size_t start = 0;
    while (start < value.size() && (value[start] == ' ' || value[start] == '\t')) ++start;
    return value.substr(start);
}

double number(const string& value, double fallback = 0.0) {
    if (value.empty()) return fallback;
    try { return std::stod(value); } catch (...) { return fallback; }
}

int integer(const string& value, int fallback = 0) {
    if (value.empty()) return fallback;
    try { return std::stoi(value); } catch (...) { return fallback; }
}

bool truthy(const string& value) {
    string x = value;
    std::transform(x.begin(), x.end(), x.begin(), [](unsigned char c) {
        return static_cast<char>(std::toupper(c));
    });
    return x == "1" || x == "TRUE" || x == "YES";
}

struct Tsv {
    vector<string> header;
    vector<vector<string>> rows;
};

Tsv read_tsv(const string& path) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot open " + path);
    Tsv result;
    string line;
    if (!std::getline(input, line)) return result;
    result.header = split(line, '\t');
    while (std::getline(input, line)) {
        if (!line.empty()) result.rows.push_back(split(line, '\t'));
    }
    return result;
}

std::unordered_map<string, int> indices(const vector<string>& header) {
    std::unordered_map<string, int> result;
    for (int i = 0; i < static_cast<int>(header.size()); ++i) result[trim(header[i])] = i;
    return result;
}

string field(const vector<string>& row, const std::unordered_map<string, int>& ix,
             const string& name) {
    auto it = ix.find(name);
    if (it == ix.end() || it->second < 0 || it->second >= static_cast<int>(row.size())) return "";
    return trim(row[it->second]);
}

struct Arc {
    int id = 0;
    int a = 0;
    int b = 0;
    double length = 0.0;
    bool bidirectional = false;
    string type;
    double speed_ab = 1.0;
    double speed_ba = 1.0;
};

struct Edge {
    int to = 0;
    int arc_id = 0;
    bool ab = true;
};

struct Train {
    string id;
    double entry = 0.0;
    int origin = 0;
    int destination = 0;
    double smult = 1.0;
    double terminal_want = NAN;
};

// A space-time exclusion: this train may not ENTER `arc_id` in direction
// `ab` (or either direction when `any_direction`) with an entry time inside
// [t_lo, t_hi).  Enforced by deleting the transition, exactly like the
// arc-level PROHIBIT relation -- never by a finite penalty.
struct ForbiddenWindow {
    int arc_id = -1;
    bool ab = true;
    bool any_direction = true;
    double t_lo = 0.0;
    double t_hi = 0.0;
};

struct Branch {
    std::unordered_set<int> required;
    std::unordered_set<int> prohibited;
    vector<ForbiddenWindow> windows;
    vector<ForbiddenWindow> events;  // t_lo/t_hi are exact entry/exit, not a window
};

struct Mask {
    std::uint64_t lo = 0;
    std::uint64_t hi = 0;

    bool operator==(const Mask& other) const { return lo == other.lo && hi == other.hi; }
    void set(int bit) {
        if (bit < 64) lo |= (1ULL << bit);
        else hi |= (1ULL << (bit - 64));
    }
};

struct MaskHash {
    std::size_t operator()(const Mask& mask) const {
        return static_cast<std::size_t>(mask.lo ^ (mask.hi + 0x9e3779b97f4a7c15ULL +
                                                   (mask.lo << 6) + (mask.lo >> 2)));
    }
};

struct NodeMask {
    int node = 0;
    Mask mask;
    int history_state = 0;
    bool operator==(const NodeMask& other) const { return node == other.node && mask == other.mask && history_state == other.history_state; }
};

struct NodeMaskHash {
    std::size_t operator()(const NodeMask& key) const {
        return static_cast<std::size_t>(key.node * 1000003ULL) ^ MaskHash{}(key.mask)
               ^ (static_cast<std::size_t>(key.history_state) * 0x5bf03635ULL);
    }
};

struct PriceKey {
    int arc = 0;
    int bin = 0;
    bool operator==(const PriceKey& other) const { return arc == other.arc && bin == other.bin; }
};

struct PriceHash {
    std::size_t operator()(const PriceKey& key) const {
        return static_cast<std::size_t>(key.arc * 1000003ULL) ^
               static_cast<std::size_t>(key.bin + 0x9e3779b9);
    }
};

// Physical cross-train coupling event: the exact object the verified coupling
// model prices.  Family A charges once at ENTRY, so the key is the physical
// arc, the direction of travel, and the entry tick.  It is deliberately NOT
// the (arc, price-bin) protected-support key used by the legacy --lambda
// channel, which has no direction and spans the whole protected interval.
struct EventKey {
    int arc = 0;
    int tick = 0;
    bool ab = true;
    bool operator==(const EventKey& other) const {
        return arc == other.arc && tick == other.tick && ab == other.ab;
    }
};

struct EventHash {
    std::size_t operator()(const EventKey& key) const {
        std::size_t value = static_cast<std::size_t>(key.arc) * 1000003ULL;
        value ^= static_cast<std::size_t>(key.tick) + 0x9e3779b9ULL + (value << 6) + (value >> 2);
        return value ^ (key.ab ? 0x5bf03635ULL : 0ULL);
    }
};

using EventPrices = std::unordered_map<EventKey, double, EventHash>;

struct Label {
    double generalized = INF;
    double physical = INF;
    double lambda = 0.0;
    double physical_dual = 0.0;
    double time = 0.0;
    int tick = 0;
    int node = 0;
    Mask mask;
    int history_state = 0;
    int previous = -1;
    int arc_id = -1;
    bool ab = true;
    double wait = 0.0;
    double start = 0.0;
    double exit = 0.0;
};

struct Config {
    double bin_minutes = 30.0;
    double time_step = 0.5;
    double horizon = 1440.0;
    double max_wait = 180.0;
    double wait_step = 5.0;
    double departure_slack = 240.0;
    double departure_step = 5.0;
    double headway = 3.0;
    double origin_wait_cost = 1.0;
    double running_cost = 1.0;
    double siding_wait_cost = 1.0;
    double early_cost = 1.0;
    double late_cost = 1.0;
    // Exact incumbent-based g+h pruning.  Admissible, so it never removes an
    // optimal trajectory; exposed as a switch only for regression testing.
    bool gh_pruning = true;
};

struct Result {
    string id;
    bool feasible = false;
    int best_label = -1;
    double generalized = INF;
    double physical = INF;
    double lambda = 0.0;
    double physical_dual = 0.0;
    double origin_wait_cost = 0.0;
    double running_cost = 0.0;
    double siding_wait_cost = 0.0;
    double early_cost = 0.0;
    double late_cost = 0.0;
    double departure = NAN;
    double arrival = NAN;
    string reason;
    long long history_trie_nodes = 0;
    long long history_states_seen = 0;
    long long labels_pruned_history = 0;
    long long labels_generated = 0;
    long long labels_retained = 0;
    long long labels_pruned_dominance = 0;
    long long labels_pruned_mow = 0;
    long long labels_pruned_branch = 0;
    long long labels_pruned_horizon = 0;
    long long labels_pruned_bound = 0;
    long long transitions_considered = 0;
    long long transitions_accepted = 0;
    long long transitions_pruned_corridor = 0;
    long long max_layer_labels = 0;
};

struct TrainDomain {
    bool corridor = false;
    std::unordered_map<int, unsigned> directions; // AB=1, BA=2
    std::unordered_set<int> nodes;
    bool allows(int arc_id, bool ab) const {
        if (!corridor) return true;
        const auto it = directions.find(arc_id);
        return it != directions.end() && (it->second & (ab ? 1u : 2u));
    }
};

// Anchored complete-path trie. State 0 is absorbing divergence, root is 1.
// No failure links: a suffix may never restart an already diverged match.
struct HistoryTrie {
    vector<std::unordered_map<string, int>> edges{2};
    std::unordered_set<int> terminal;
    int advance(int state, const string& token) const {
        if (!state) return 0;
        auto it = edges.at(state).find(token);
        return it == edges.at(state).end() ? 0 : it->second;
    }
    void add(const vector<string>& tokens) {
        int state = 1;
        for (const auto& token : tokens) {
            int next = advance(state, token);
            if (!next) {
                next = static_cast<int>(edges.size());
                edges[state][token] = next;
                edges.emplace_back();
            }
            state = next;
        }
        terminal.insert(state);
    }
};
string history_start(int origin, int destination, int departure) {
    return "S," + std::to_string(origin) + "," + std::to_string(destination) + "," + std::to_string(departure);
}
string history_move(int arc, bool ab, int start, int end, int wait) {
    return "M," + std::to_string(arc) + "," + (ab ? "1" : "0") + "," +
        std::to_string(start) + "," + std::to_string(end) + "," + std::to_string(wait);
}
string history_end(int destination, int arrival) {
    return "E," + std::to_string(destination) + "," + std::to_string(arrival);
}

struct ParsedInputs {
    std::unordered_map<int, Arc> arcs;
    std::unordered_map<int, vector<Edge>> graph;
    vector<Train> trains;
    std::unordered_map<string, Branch> branches;
    std::unordered_map<string, TrainDomain> domains;
    std::unordered_map<string, HistoryTrie> histories;
    std::unordered_map<PriceKey, double, PriceHash> prices;
    // Per-train, already aggregated by the caller:
    //   price_i(e) = sum over coupling constraints k incident to (i, e) of mu_k.
    // The DP never sees another train, a pair, or a clique.
    std::unordered_map<string, EventPrices> event_prices;
    std::unordered_map<int, vector<std::pair<double, double>>> mow;
};

bool interval_overlap(double a0, double a1, double b0, double b1) {
    return a0 < b1 - EPS && b0 < a1 - EPS;
}

bool mow_free(const ParsedInputs& input, int arc_id, double start, double exit) {
    auto it = input.mow.find(arc_id);
    if (it == input.mow.end()) return true;
    for (const auto& window : it->second) {
        if (interval_overlap(start, exit, window.first, window.second)) return false;
    }
    return true;
}

double traversal_minutes(const Arc& arc, double smult, bool ab) {
    const double speed = (ab ? arc.speed_ab : arc.speed_ba) * std::max(smult, 1e-9);
    if (speed <= 0.0) return INF;
    return 60.0 * arc.length / speed;
}

// True when entering `arc_id` in direction `ab` at `entry` is excluded.
bool entry_forbidden(const Branch& branch, int arc_id, bool ab, double entry) {
    for (const ForbiddenWindow& w : branch.windows) {
        if (w.arc_id != arc_id) continue;
        if (!w.any_direction && w.ab != ab) continue;
        if (entry >= w.t_lo - EPS && entry < w.t_hi - EPS) return true;
    }
    return false;
}

bool event_forbidden(const Branch& branch, int arc_id, bool ab, double entry, double exit) {
    for (const ForbiddenWindow& e : branch.events) {
        if (e.arc_id == arc_id && e.ab == ab &&
            std::abs(entry - e.t_lo) <= EPS && std::abs(exit - e.t_hi) <= EPS) return true;
    }
    return false;
}

int snap_tick(double minute, const Config& cfg) {
    return static_cast<int>(std::ceil(minute / cfg.time_step - EPS));
}

double grid_time(int tick, const Config& cfg) { return tick * cfg.time_step; }

int first_price_bin(double start, const Config& cfg) {
    const double ratio = start / cfg.bin_minutes;
    return std::max(0, static_cast<int>(std::floor(ratio + EPS)));
}

int last_price_bin(double exit, const Config& cfg) {
    const double protected_end = exit + std::max(0.0, cfg.headway);
    return std::max(-1, static_cast<int>(std::ceil(protected_end / cfg.bin_minutes - EPS)) - 1);
}

double lambda_cost(const ParsedInputs& input, int arc_id, double start, double exit,
                   const Config& cfg, string* cells = nullptr) {
    double total = 0.0;
    vector<string> touched;
    const int first = first_price_bin(start, cfg);
    const int last = last_price_bin(exit, cfg);
    for (int bin = first; bin <= last; ++bin) {
        auto it = input.prices.find({arc_id, bin});
        if (it != input.prices.end()) total += it->second;
        touched.push_back(std::to_string(arc_id) + ":" + std::to_string(bin));
    }
    if (cells) {
        std::ostringstream out;
        for (std::size_t i = 0; i < touched.size(); ++i) {
            if (i) out << ',';
            out << touched[i];
        }
        *cells = out.str();
    }
    return total;
}

double schedule_cost(double arrival, double target, const Config& cfg,
                     double* early, double* late) {
    if (!std::isfinite(target)) {
        *early = 0.0;
        *late = 0.0;
        return 0.0;
    }
    *early = std::max(0.0, target - arrival);
    *late = std::max(0.0, arrival - target);
    return cfg.early_cost * *early + cfg.late_cost * *late;
}

Branch branch_for(const ParsedInputs& input, const string& train_id) {
    auto it = input.branches.find(train_id);
    return it == input.branches.end() ? Branch{} : it->second;
}

// Reverse shortest travel time to the destination.  ``prohibited`` removes the
// branch-forbidden arcs, so the bound stays admissible AND tightens under a
// PROHIBIT restriction instead of silently reporting the unrestricted value.
// REQUIRE is deliberately ignored: ignoring it can only lower the bound, which
// keeps it admissible.
std::unordered_map<int, double> shortest_remaining_time(
    const ParsedInputs& input, const Train& train,
    const std::unordered_set<int>& prohibited) {
    std::unordered_map<int, vector<std::pair<int, double>>> reverse;
    for (const auto& item : input.graph) {
        const int source = item.first;
        for (const Edge& edge : item.second) {
            if (prohibited.count(edge.arc_id)) continue;
            const double tau = traversal_minutes(
                input.arcs.at(edge.arc_id), train.smult, edge.ab);
            if (std::isfinite(tau) && tau > 0.0) reverse[edge.to].push_back({source, tau});
        }
    }
    std::unordered_map<int, double> distance;
    using QueueItem = std::pair<double, int>;
    std::priority_queue<QueueItem, vector<QueueItem>, std::greater<QueueItem>> queue;
    distance[train.destination] = 0.0;
    queue.push({0.0, train.destination});
    while (!queue.empty()) {
        const auto [value, node] = queue.top();
        queue.pop();
        if (value > distance[node] + EPS) continue;
        for (const auto& [previous, tau] : reverse[node]) {
            const double candidate = value + tau;
            auto it = distance.find(previous);
            if (it == distance.end() || candidate < it->second - EPS) {
                distance[previous] = candidate;
                queue.push({candidate, previous});
            }
        }
    }
    return distance;
}

Result solve_one(const ParsedInputs& input, const Train& train, const Config& cfg,
                 vector<Label>* all_labels_out = nullptr) {
    Result result;
    result.id = train.id;
    const Branch branch = branch_for(input, train.id);
    static const HistoryTrie empty_history;
    const auto history_it = input.histories.find(train.id);
    const HistoryTrie& history = history_it == input.histories.end() ? empty_history : history_it->second;
    const bool has_history = !history.terminal.empty();
    result.history_trie_nodes = has_history ? history.edges.size() - 1 : 0;
    std::unordered_set<int> history_states_seen;
    const auto domain_it = input.domains.find(train.id);
    const TrainDomain domain = domain_it == input.domains.end() ? TrainDomain{} : domain_it->second;
    if (domain.corridor && (!domain.nodes.count(train.origin) || !domain.nodes.count(train.destination))) {
        result.reason = "no legal trajectory in configured train domain";
        return result;
    }
    for (int resource : branch.required) {
        if (branch.prohibited.count(resource)) {
            result.reason = "contradictory required/prohibited resource";
            return result;
        }
    }

    vector<int> required(branch.required.begin(), branch.required.end());
    std::sort(required.begin(), required.end());
    if (required.size() > 128) {
        result.reason = "more than 128 required resources is unsupported by the fixed mask";
        return result;
    }
    std::unordered_map<int, int> required_bit;
    for (int i = 0; i < static_cast<int>(required.size()); ++i) required_bit[required[i]] = i;
    Mask full_mask;
    for (int i = 0; i < static_cast<int>(required.size()); ++i) full_mask.set(i);

    static const EventPrices kNoEventPrices;
    const auto event_price_it = input.event_prices.find(train.id);
    const EventPrices& train_event_prices =
        event_price_it == input.event_prices.end() ? kNoEventPrices : event_price_it->second;

    const int max_tick = static_cast<int>(std::floor(cfg.horizon / cfg.time_step + EPS));
    if (max_tick < 0 || train.entry > cfg.horizon + EPS) {
        result.reason = "entry is beyond horizon";
        return result;
    }
    const auto remaining_time = shortest_remaining_time(input, train, branch.prohibited);
    if (!remaining_time.count(train.origin)) {
        result.reason = "destination is unreachable in the physical network";
        return result;
    }
    vector<std::unordered_map<NodeMask, int, NodeMaskHash>> states(max_tick + 1);
    vector<Label> labels;
    labels.reserve(1024);
    auto insert_label = [&](Label label, std::unordered_map<NodeMask, int, NodeMaskHash>& layer) {
        ++result.labels_generated;
        NodeMask key{label.node, label.mask, label.history_state};
        if (has_history) {
            history_states_seen.insert(label.history_state);
            result.history_states_seen = history_states_seen.size();
        }
        auto old = layer.find(key);
        if (old != layer.end()) {
            if (labels[old->second].generalized <= label.generalized + 1e-12) {
                ++result.labels_pruned_dominance;
                return;
            }
        }
        const int index = static_cast<int>(labels.size());
        labels.push_back(std::move(label));
        if (old != layer.end()) old->second = index;
        else layer.emplace(key, index);
        ++result.labels_retained;
    };

    const int departures = std::max(0, static_cast<int>(std::floor(
        cfg.departure_slack / cfg.departure_step + EPS)));
    for (int k = 0; k <= departures; ++k) {
        const double requested_departure = train.entry + k * cfg.departure_step;
        const int tick = snap_tick(requested_departure, cfg);
        if (tick < 0 || tick > max_tick) continue;
        const double departure = grid_time(tick, cfg);
        Label initial;
        initial.generalized = cfg.origin_wait_cost * (departure - train.entry);
        initial.physical = initial.generalized;
        initial.time = departure;
        initial.tick = tick;
        initial.node = train.origin;
        initial.mask = Mask{};
        initial.history_state = has_history ? history.advance(1, history_start(train.origin, train.destination, tick)) : 0;
        insert_label(std::move(initial), states[tick]);
    }

    // Admissible lower bound on the remaining GENERALIZED cost from a state.
    //   * running: running_cost * (restriction-aware shortest remaining time)
    //   * schedule: the arrival can be no earlier than time + h_time, so if that
    //     already passes the terminal target the late cost is unavoidable;
    //     otherwise zero is a valid lower bound.
    //   * event prices are >= 0, so omitting them can only lower the bound.
    // Every term is a lower bound, so the total is admissible and pruning a
    // state whose g + h already exceeds the incumbent cannot discard an optimum.
    auto remaining_bound = [&](int node, double moment) -> double {
        auto it = remaining_time.find(node);
        if (it == remaining_time.end()) return INF;
        const double travel = it->second;
        double bound = cfg.running_cost * travel;
        if (std::isfinite(train.terminal_want)) {
            const double earliest_arrival = moment + travel;
            if (earliest_arrival > train.terminal_want) {
                bound += cfg.late_cost * (earliest_arrival - train.terminal_want);
            }
        }
        return bound;
    };

    int best_label = -1;
    double best_total = INF;
    double best_early = 0.0;
    double best_late = 0.0;
    long long labels_pruned_bound = 0;
    long long transitions_considered = 0;
    long long transitions_accepted = 0;
    std::size_t max_layer_labels = 0;
    for (int tick = 0; tick <= max_tick; ++tick) {
        if (states[tick].empty()) continue;
        max_layer_labels = std::max(max_layer_labels, states[tick].size());
        const auto current = states[tick];
        for (const auto& entry : current) {
            const int label_index = entry.second;
            // Expanding a state appends to ``labels``.  Keep a value copy here
            // because a push_back may reallocate the vector and invalidate a
            // reference to its current element.
            const Label state = labels[label_index];
            if (state.tick != tick) continue;
            auto remaining_it = remaining_time.find(state.node);
            if (remaining_it == remaining_time.end() ||
                state.time + remaining_it->second > cfg.horizon + EPS) {
                // Destination unreachable under the restrictions, or the
                // horizon can no longer be met.
                ++result.labels_pruned_horizon;
                continue;
            }
            if (cfg.gh_pruning && std::isfinite(best_total)) {
                const double estimate = state.generalized + remaining_bound(state.node, state.time);
                // Strict inequality keeps ties, preserving deterministic route
                // selection against the reference DP.
                if (estimate > best_total + 1e-10) {
                    ++labels_pruned_bound;
                    continue;
                }
            }
            if (state.node == train.destination) {
                if (!(state.mask == full_mask)) continue;
                if (state.history_state && history.terminal.count(history.advance(
                        state.history_state, history_end(train.destination, tick)))) {
                    ++result.labels_pruned_history;
                    continue; // Absorbing destination: never extend a banned completion.
                }
                double early = 0.0;
                double late = 0.0;
                const double terminal = schedule_cost(
                    state.time, train.terminal_want, cfg, &early, &late);
                const double total = state.generalized + terminal;
                if (total < best_total - 1e-12 ||
                    (std::abs(total - best_total) <= 1e-12 && label_index < best_label)) {
                    best_total = total;
                    best_label = label_index;
                    best_early = early;
                    best_late = late;
                }
                continue;  // destination is a terminal for this train request
            }
            auto graph_it = input.graph.find(state.node);
            if (graph_it == input.graph.end()) continue;
            for (const Edge& edge : graph_it->second) {
                // Hard directed subnetwork membership, before any temporal label.
                // Count outgoing-arc opportunities; wait variants are expanded only
                // for allowed arcs and remain in transitions_considered below.
                if (!domain.allows(edge.arc_id, edge.ab)) {
                    ++result.transitions_pruned_corridor;
                    continue;
                }
                if (branch.prohibited.count(edge.arc_id)) {
                    ++result.labels_pruned_branch;
                    continue;
                }
                // Space-time exclusion.  `state.time` IS this transition's
                // entry time, so the branch removes exactly the occupancy the
                // validator complained about, and the label can never re-enter
                // the frontier by another route.
                if (!branch.windows.empty()
                    && entry_forbidden(branch, edge.arc_id, edge.ab, state.time)) {
                    ++result.labels_pruned_branch;
                    continue;
                }
                const Arc& arc = input.arcs.at(edge.arc_id);
                const double tau = traversal_minutes(arc, train.smult, edge.ab);
                if (!(tau > 0.0) || !std::isfinite(tau)) continue;
                vector<double> waits{0.0};
                if (arc.type == "S" && cfg.wait_step > 0.0 && cfg.max_wait > 0.0) {
                    const int wait_count = static_cast<int>(std::floor(
                        cfg.max_wait / cfg.wait_step + EPS));
                    for (int w = 1; w <= wait_count; ++w) waits.push_back(w * cfg.wait_step);
                }
                for (double wait : waits) {
                    ++transitions_considered;
                    const double start = state.time;
                    const double requested_exit = start + wait + tau;
                    const int next_tick = snap_tick(requested_exit, cfg);
                    if (next_tick <= tick || next_tick > max_tick) {
                        ++result.labels_pruned_horizon;
                        continue;
                    }
                    const double exit = grid_time(next_tick, cfg);
                    // Exit includes siding dwell and grid snapping. Reject this
                    // exact movement before any label or finite penalty enters DP.
                    if (event_forbidden(branch, edge.arc_id, edge.ab, start, exit)) {
                        ++result.labels_pruned_branch;
                        continue;
                    }
                    if (!mow_free(input, arc.id, start, exit)) {
                        ++result.labels_pruned_mow;
                        continue;
                    }
                    Mask next_mask = state.mask;
                    auto bit = required_bit.find(arc.id);
                    if (bit != required_bit.end()) next_mask.set(bit->second);
                    string unused_cells;
                    const double price = lambda_cost(
                        input, arc.id, start, exit, cfg, &unused_cells);
                    // The physical coupling event is (arc, direction, entry tick).
                    // ``tick`` IS the entry tick of this transition, and the
                    // price is added exactly once -- never per protected cell,
                    // never over the exit or headway interval.
                    double event_price = 0.0;
                    if (!train_event_prices.empty()) {
                        const auto found = train_event_prices.find(
                            EventKey{arc.id, tick, edge.ab});
                        if (found != train_event_prices.end()) event_price = found->second;
                    }
                    const double wait_cost = arc.type == "S"
                        ? wait * cfg.siding_wait_cost : 0.0;
                    const double next_generalized = state.generalized
                        + cfg.running_cost * tau + wait_cost + price + event_price;
                    if (cfg.gh_pruning && std::isfinite(best_total)) {
                        const double estimate = next_generalized + remaining_bound(edge.to, exit);
                        if (estimate > best_total + 1e-10) {
                            ++labels_pruned_bound;
                            continue;
                        }
                    }
                    ++transitions_accepted;
                    Label next;
                    next.generalized = next_generalized;
                    next.physical = state.physical + cfg.running_cost * tau + wait_cost;
                    next.lambda = state.lambda + price;
                    next.physical_dual = state.physical_dual + event_price;
                    next.time = exit;
                    next.tick = next_tick;
                    next.node = edge.to;
                    next.mask = next_mask;
                    next.history_state = state.history_state ? history.advance(state.history_state,
                        history_move(arc.id, edge.ab, tick, next_tick,
                                     static_cast<int>(std::llround(wait / cfg.wait_step)))) : 0;
                    next.previous = label_index;
                    next.arc_id = arc.id;
                    next.ab = edge.ab;
                    next.wait = wait;
                    next.start = start;
                    next.exit = exit;
                    insert_label(std::move(next), states[next_tick]);
                }
            }
        }
    }

    if (best_label < 0) {
        result.labels_pruned_bound = labels_pruned_bound;
        result.transitions_considered = transitions_considered;
        result.transitions_accepted = transitions_accepted;
        result.max_layer_labels = static_cast<long long>(max_layer_labels);
        result.reason = domain.corridor ? "no legal trajectory in configured train domain"
                                        : "no legal physical-network trajectory satisfies restrictions";
        if (all_labels_out) *all_labels_out = std::move(labels);
        return result;
    }
    result.labels_pruned_bound = labels_pruned_bound;
    result.transitions_considered = transitions_considered;
    result.transitions_accepted = transitions_accepted;
    result.max_layer_labels = static_cast<long long>(max_layer_labels);
    result.feasible = true;
    result.best_label = best_label;
    result.generalized = best_total;
    result.physical = labels[best_label].physical + cfg.early_cost * best_early +
                      cfg.late_cost * best_late;
    result.lambda = labels[best_label].lambda;
    result.physical_dual = labels[best_label].physical_dual;
    result.origin_wait_cost = 0.0;
    result.running_cost = 0.0;
    result.siding_wait_cost = 0.0;
    result.early_cost = cfg.early_cost * best_early;
    result.late_cost = cfg.late_cost * best_late;

    vector<int> chain;
    for (int index = best_label; index >= 0; index = labels[index].previous) chain.push_back(index);
    std::reverse(chain.begin(), chain.end());
    if (!chain.empty()) {
        result.departure = labels[chain.front()].time;
        result.arrival = labels[chain.back()].time;
        result.origin_wait_cost = labels[chain.front()].physical;
    }
    for (std::size_t i = 1; i < chain.size(); ++i) {
        const Label& leg = labels[chain[i]];
        const Arc& arc = input.arcs.at(leg.arc_id);
        result.running_cost += cfg.running_cost * traversal_minutes(arc, train.smult, leg.ab);
        if (arc.type == "S") result.siding_wait_cost += leg.wait * cfg.siding_wait_cost;
    }
    if (all_labels_out) *all_labels_out = std::move(labels);
    return result;
}

string join_ints(const vector<int>& values) {
    std::ostringstream out;
    for (std::size_t i = 0; i < values.size(); ++i) {
        if (i) out << ',';
        out << values[i];
    }
    return out.str();
}

string join_bools(const vector<bool>& values) {
    std::ostringstream out;
    for (std::size_t i = 0; i < values.size(); ++i) {
        if (i) out << ',';
        out << (values[i] ? "1" : "0");
    }
    return out.str();
}

string join_strings(const vector<string>& values) {
    std::ostringstream out;
    for (std::size_t i = 0; i < values.size(); ++i) {
        if (i) out << ',';
        out << values[i];
    }
    return out.str();
}

string join_semicolon(const vector<string>& values) {
    std::ostringstream out;
    for (std::size_t i = 0; i < values.size(); ++i) {
        if (i) out << ';';
        out << values[i];
    }
    return out.str();
}

string join_doubles(const vector<double>& values) {
    std::ostringstream out;
    out << std::setprecision(17);
    for (std::size_t i = 0; i < values.size(); ++i) {
        if (i) out << ',';
        out << values[i];
    }
    return out.str();
}

void write_header(std::ostream& out) {
    out << "train_id\tstatus\tfeasible\tgeneralized_cost\tphysical_cost\tlambda_cost\t"
           "physical_dual_cost\t"
           "origin_wait_cost\trunning_cost\tsiding_wait_cost\tearly_arrival_cost\t"
           "late_arrival_cost\tdeparture_min\tarrival_min\tarc_ids\tnode_ids\t"
           "ab_flags\ttrack_types\twaits\tlegs\tresources\tlambda_cells\tdiagnostics\t"
           "infeasibility_reason\n";
}

void write_result(std::ostream& out, const ParsedInputs& input, const Train& train,
                  const Config& cfg, const Result& result, const vector<Label>& labels) {
    out << std::setprecision(17);
    out << train.id << '\t' << (result.feasible ? "OK" : "INFEASIBLE") << '\t'
        << (result.feasible ? 1 : 0) << '\t'
        << (result.feasible ? result.generalized : INF) << '\t'
        << (result.feasible ? result.physical : INF) << '\t'
        << (result.feasible ? result.lambda : 0.0) << '\t'
        << (result.feasible ? result.physical_dual : 0.0) << '\t'
        << (result.feasible ? result.origin_wait_cost : 0.0) << '\t'
        << (result.feasible ? result.running_cost : 0.0) << '\t'
        << (result.feasible ? result.siding_wait_cost : 0.0) << '\t'
        << (result.feasible ? result.early_cost : 0.0) << '\t'
        << (result.feasible ? result.late_cost : 0.0) << '\t'
        << (result.feasible ? result.departure : NAN) << '\t'
        << (result.feasible ? result.arrival : NAN) << '\t';
    vector<int> chain;
    for (int index = result.best_label; index >= 0; index = labels[index].previous) chain.push_back(index);
    std::reverse(chain.begin(), chain.end());
    vector<int> arcs;
    vector<int> nodes;
    vector<bool> directions;
    vector<string> types;
    vector<double> waits;
    vector<string> legs;
    vector<string> cells;
    std::unordered_set<int> used;
    if (!chain.empty()) nodes.push_back(labels[chain.front()].node);
    for (std::size_t i = 1; i < chain.size(); ++i) {
        const Label& leg = labels[chain[i]];
        const Arc& arc = input.arcs.at(leg.arc_id);
        arcs.push_back(leg.arc_id);
        nodes.push_back(leg.node);
        directions.push_back(leg.ab);
        types.push_back(arc.type);
        waits.push_back(leg.wait);
        std::ostringstream leg_text;
        leg_text << std::setprecision(17) << leg.arc_id << ',' << leg.start << ',' << leg.exit;
        legs.push_back(leg_text.str());
        string touched;
        lambda_cost(input, leg.arc_id, leg.start, leg.exit, cfg, &touched);
        if (!touched.empty()) cells.push_back(touched);
        used.insert(leg.arc_id);
    }
    vector<int> resources(used.begin(), used.end());
    std::sort(resources.begin(), resources.end());
    vector<string> diagnostics{
        "history_trie_nodes=" + std::to_string(result.history_trie_nodes),
        "history_states_seen=" + std::to_string(result.history_states_seen),
        "labels_pruned_history=" + std::to_string(result.labels_pruned_history),
        "labels_generated=" + std::to_string(result.labels_generated),
        "labels_retained=" + std::to_string(result.labels_retained),
        "labels_pruned_dominance=" + std::to_string(result.labels_pruned_dominance),
        "labels_pruned_mow=" + std::to_string(result.labels_pruned_mow),
        "labels_pruned_branch=" + std::to_string(result.labels_pruned_branch),
        "labels_pruned_horizon=" + std::to_string(result.labels_pruned_horizon),
        "labels_pruned_bound=" + std::to_string(result.labels_pruned_bound),
        "transitions_considered=" + std::to_string(result.transitions_considered),
        "transitions_accepted=" + std::to_string(result.transitions_accepted),
        "transitions_pruned_corridor=" + std::to_string(result.transitions_pruned_corridor),
        // Alias of the historical mixed spatial/event branch counter; not an
        // additional independent count to sum with labels_pruned_branch.
        "transitions_pruned_branch=" + std::to_string(result.labels_pruned_branch),
        "max_layer_labels=" + std::to_string(result.max_layer_labels),
        "gh_pruning=" + std::string(cfg.gh_pruning ? "1" : "0"),
        "state_mask_semantics=required_resources_only",
        "forbidden_windows=" + std::to_string(
            input.branches.count(train.id)
                ? input.branches.at(train.id).windows.size() : 0u),
        "route_domain=" + std::string(input.domains.count(train.id) && input.domains.at(train.id).corridor
                                       ? "path_k_corridor" : "full_physical_network"),
    };
    out << join_ints(arcs) << '\t' << join_ints(nodes) << '\t' << join_bools(directions) << '\t'
        << join_strings(types) << '\t' << join_doubles(waits) << '\t' << join_semicolon(legs) << '\t'
        << join_ints(resources) << '\t' << join_semicolon(cells) << '\t'
        << join_strings(diagnostics) << '\t' << result.reason << '\n';
}

ParsedInputs read_inputs(const string& network_path, const string& request_path,
                         const string& lambda_path, const string& mow_path,
                         const string& branch_path, const string& event_price_path, const string& domain_path) {
    ParsedInputs input;
    {
        Tsv tsv = read_tsv(network_path);
        auto ix = indices(tsv.header);
        for (const auto& row : tsv.rows) {
            Arc arc;
            arc.id = integer(field(row, ix, "arc_id"));
            arc.a = integer(field(row, ix, "a"));
            arc.b = integer(field(row, ix, "b"));
            arc.length = number(field(row, ix, "length"));
            arc.bidirectional = truthy(field(row, ix, "bidirectional"));
            arc.type = field(row, ix, "track_type");
            arc.speed_ab = number(field(row, ix, "speed_ab"), 1.0);
            arc.speed_ba = number(field(row, ix, "speed_ba"), arc.speed_ab);
            if (arc.id <= 0 || arc.a == arc.b || !(arc.length > 0.0)) {
                throw std::runtime_error("invalid physical arc in " + network_path);
            }
            if (!(arc.speed_ab > 0.0) || !(arc.speed_ba > 0.0)) {
                throw std::runtime_error("non-positive directional speed on arc " + std::to_string(arc.id));
            }
            input.arcs[arc.id] = arc;
            input.graph[arc.a].push_back({arc.b, arc.id, true});
            if (arc.bidirectional) input.graph[arc.b].push_back({arc.a, arc.id, false});
        }
        for (auto& item : input.graph) {
            std::sort(item.second.begin(), item.second.end(), [](const Edge& left, const Edge& right) {
                return std::tie(left.to, left.arc_id, left.ab) < std::tie(right.to, right.arc_id, right.ab);
            });
        }
    }
    {
        Tsv tsv = read_tsv(request_path);
        auto ix = indices(tsv.header);
        for (const auto& row : tsv.rows) {
            Train train;
            train.id = field(row, ix, "train_id");
            train.entry = number(field(row, ix, "entry_min"));
            train.origin = integer(field(row, ix, "origin"));
            train.destination = integer(field(row, ix, "destination"));
            train.smult = number(field(row, ix, "smult"), 1.0);
            const string target = field(row, ix, "terminal_want");
            if (!target.empty()) train.terminal_want = number(target);
            if (train.id.empty()) throw std::runtime_error("request has empty train_id");
            input.trains.push_back(std::move(train));
        }
    }
    if (!domain_path.empty()) {
        Tsv tsv = read_tsv(domain_path);
        const auto ix = indices(tsv.header);
        for (const auto& row : tsv.rows) {
            const string train = field(row, ix, "train_id");
            const string mode = field(row, ix, "mode");
            if (train.empty() || (mode != "full_network" && mode != "path_k_corridor"))
                throw std::runtime_error("invalid train domain declaration");
            const bool corridor = mode == "path_k_corridor";
            const auto previous = input.domains.find(train);
            if (previous != input.domains.end() && previous->second.corridor != corridor)
                throw std::runtime_error("inconsistent train domain modes");
            auto& domain = input.domains[train];
            domain.corridor = corridor;
            const string arc_text = field(row, ix, "arc_id");
            const string direction = field(row, ix, "direction");
            const string node_text = field(row, ix, "node_id");
            if (!node_text.empty()) {
                if (!corridor || !arc_text.empty() || !direction.empty())
                    throw std::runtime_error("invalid corridor node row");
                domain.nodes.insert(integer(node_text, -1));
            }
            if (arc_text.empty()) {
                if (!direction.empty()) throw std::runtime_error("domain direction without arc");
                continue; // explicit declaration preserves empty corridor semantics
            }
            const int arc = integer(arc_text, -1);
            if (!corridor || !input.arcs.count(arc) || (direction != "AB" && direction != "BA") ||
                (direction == "BA" && !input.arcs.at(arc).bidirectional))
                throw std::runtime_error("invalid directed corridor movement");
            domain.directions[arc] |= direction == "AB" ? 1u : 2u;
        }
        for (const auto& entry : input.domains) {
            const auto& domain = entry.second;
            for (const auto& movement : domain.directions) {
                const auto& arc = input.arcs.at(movement.first);
                if (!domain.nodes.count(arc.a) || !domain.nodes.count(arc.b))
                    throw std::runtime_error("corridor movement endpoints missing from nodes");
            }
        }
        for (const auto& train : input.trains)
            if (!input.domains.count(train.id))
                throw std::runtime_error("missing domain for requested train " + train.id);
    }
    if (!lambda_path.empty()) {
        Tsv tsv = read_tsv(lambda_path);
        auto ix = indices(tsv.header);
        for (const auto& row : tsv.rows) {
            const int arc = integer(field(row, ix, "arc_id"));
            const int bin = integer(field(row, ix, "time_bin"));
            const double price = number(field(row, ix, "price"));
            if (price < -EPS || !std::isfinite(price)) throw std::runtime_error("invalid lambda price");
            input.prices[{arc, bin}] = std::max(0.0, price);
        }
    }
    if (!mow_path.empty()) {
        Tsv tsv = read_tsv(mow_path);
        auto ix = indices(tsv.header);
        for (const auto& row : tsv.rows) {
            const int arc = integer(field(row, ix, "arc_id"));
            const double start = number(field(row, ix, "start_min"));
            const double end = number(field(row, ix, "end_min"));
            if (end > start) input.mow[arc].push_back({start, end});
        }
    }
    if (!event_price_path.empty()) {
        Tsv tsv = read_tsv(event_price_path);
        auto ix = indices(tsv.header);
        for (const auto& row : tsv.rows) {
            const string train = field(row, ix, "train_id");
            const int arc = integer(field(row, ix, "arc_id"));
            const string direction = field(row, ix, "direction");
            const int tick = integer(field(row, ix, "entry_tick"), -1);
            const double price = number(field(row, ix, "price"));
            if (train.empty() || tick < 0 ||
                (direction != "AB" && direction != "BA")) {
                throw std::runtime_error("invalid physical event price row");
            }
            if (price < -EPS || !std::isfinite(price)) {
                throw std::runtime_error("physical event price must be finite and >= 0");
            }
            EventPrices& table = input.event_prices[train];
            EventKey key{arc, tick, direction == "AB"};
            table[key] += std::max(0.0, price);
        }
    }
    if (!branch_path.empty()) {
        Tsv tsv = read_tsv(branch_path);
        auto ix = indices(tsv.header);
        for (const auto& row : tsv.rows) {
            const string train = field(row, ix, "train_id");
            const string relation = field(row, ix, "relation");
            const int resource = integer(field(row, ix, "resource_id"), -1);
            if (train.empty() || resource < 0
                || (relation != "REQUIRE" && relation != "PROHIBIT"
                    && relation != "FORBID_WINDOW" && relation != "FORBID_EVENT")) {
                throw std::runtime_error("invalid clean resource branch row");
            }
            if (relation == "REQUIRE") {
                input.branches[train].required.insert(resource);
            } else if (relation == "PROHIBIT") {
                input.branches[train].prohibited.insert(resource);
            } else {
                ForbiddenWindow window;
                window.arc_id = resource;
                const string direction = field(row, ix, "direction");
                if (direction != "AB" && direction != "BA" &&
                    !(relation == "FORBID_WINDOW" && (direction.empty() || direction == "ANY"))) {
                    throw std::runtime_error("invalid forbidden movement direction");
                }
                window.any_direction = (direction.empty() || direction == "ANY");
                window.ab = (direction == "AB");
                window.t_lo = number(field(row, ix, "t_lo"));
                window.t_hi = number(field(row, ix, "t_hi"));
                if (!std::isfinite(window.t_lo) || !std::isfinite(window.t_hi) ||
                    window.t_lo < 0.0 || !(window.t_hi > window.t_lo)) {
                    throw std::runtime_error("forbidden window must have t_hi > t_lo");
                }
                if (relation == "FORBID_EVENT") input.branches[train].events.push_back(window);
                else input.branches[train].windows.push_back(window);
            }
        }
    }
    return input;
}

// Reject malformed certificate transport rather than treating it as infeasibility.
int history_integer(const string& value) {
    std::size_t used = 0;
    const int parsed = std::stoi(value, &used);
    if (used != value.size()) throw std::runtime_error("invalid history integer");
    return parsed;
}
void read_histories(ParsedInputs& input, const string& path, const Config& cfg) {
    if (path.empty()) return;
    const Tsv tsv = read_tsv(path);
    const auto ix = indices(tsv.header);
    for (const auto& row : tsv.rows) {
        const string id = field(row, ix, "train_id");
        auto train = std::find_if(input.trains.begin(), input.trains.end(),
                                 [&](const Train& t) { return t.id == id; });
        if (train == input.trains.end()) throw std::runtime_error("unknown history train");
        auto val = [&](const string& key) { return history_integer(field(row, ix, key)); };
        const int origin = val("origin"), destination = val("destination");
        const int departure = val("departure_tick"), arrival = val("arrival_tick");
        if (origin != train->origin || destination != train->destination || departure < 0 || arrival < departure ||
            number(field(row, ix, "time_step")) != cfg.time_step ||
            number(field(row, ix, "wait_step")) != cfg.wait_step || field(row, ix, "trajectory_id").empty())
            throw std::runtime_error("history context/grid mismatch");
        vector<string> tokens{history_start(origin, destination, departure)};
        int node = origin, previous = departure;
        const string movements = field(row, ix, "movements");
        if (!movements.empty()) for (const auto& raw : split(movements, ';')) {
            const auto parts = split(raw, ',');
            if (parts.size() != 5) throw std::runtime_error("invalid history movement");
            const int arc = history_integer(parts[0]), ab = history_integer(parts[1]);
            const int start = history_integer(parts[2]), end = history_integer(parts[3]), wait = history_integer(parts[4]);
            auto it = input.arcs.find(arc);
            if (it == input.arcs.end() || (ab != 0 && ab != 1) || start != previous || end <= start || wait < 0 || node == destination)
                throw std::runtime_error("invalid history trajectory");
            const Arc& a = it->second;
            if ((ab ? a.a : a.b) != node || (!ab && !a.bidirectional))
                throw std::runtime_error("disconnected history trajectory");
            node = ab ? a.b : a.a;
            previous = end;
            tokens.push_back(history_move(arc, ab, start, end, wait));
        }
        if (node != destination || previous != arrival) throw std::runtime_error("incomplete history trajectory");
        tokens.push_back(history_end(destination, arrival));
        input.histories[id].add(tokens);
    }
}

string option(const std::unordered_map<string, string>& args, const string& key, const string& fallback = "") {
    auto it = args.find(key);
    return it == args.end() ? fallback : it->second;
}

int main_impl(int argc, char** argv) {
    std::unordered_map<string, string> args;
    for (int i = 1; i + 1 < argc; i += 2) args[argv[i]] = argv[i + 1];
    const string network_path = option(args, "--network");
    const string request_path = option(args, "--requests");
    const string output_path = option(args, "--output", "-");
    if (network_path.empty() || request_path.empty()) {
        std::cerr << "usage: network_dp --network FILE --requests FILE --lambda FILE "
                     "--mow FILE --branch FILE --event-price FILE --output FILE "
                     "[options]\n";
        return 2;
    }
    Config cfg;
    cfg.bin_minutes = number(option(args, "--bin-minutes", "30"), 30.0);
    cfg.time_step = number(option(args, "--time-step", "0.5"), 0.5);
    cfg.horizon = number(option(args, "--horizon", "1440"), 1440.0);
    cfg.max_wait = number(option(args, "--max-wait", "180"), 180.0);
    cfg.wait_step = number(option(args, "--wait-step", "5"), 5.0);
    cfg.departure_slack = number(option(args, "--departure-slack", "240"), 240.0);
    cfg.departure_step = number(option(args, "--departure-step", "5"), 5.0);
    cfg.headway = number(option(args, "--headway", "3"), 3.0);
    cfg.origin_wait_cost = number(option(args, "--origin-wait-cost", "1"), 1.0);
    cfg.running_cost = number(option(args, "--running-cost", "1"), 1.0);
    cfg.siding_wait_cost = number(option(args, "--siding-wait-cost", "1"), 1.0);
    cfg.early_cost = number(option(args, "--early-cost", "1"), 1.0);
    cfg.late_cost = number(option(args, "--late-cost", "1"), 1.0);
    cfg.gh_pruning = option(args, "--gh-pruning", "1") != "0";
    if (!(cfg.time_step > 0.0) || !(cfg.bin_minutes > 0.0) || !(cfg.departure_step > 0.0) ||
        !(cfg.wait_step > 0.0) || cfg.horizon < 0.0) {
        throw std::runtime_error("invalid clean DP time configuration");
    }
    ParsedInputs input = read_inputs(
        network_path, request_path, option(args, "--lambda"), option(args, "--mow"),
        option(args, "--branch"), option(args, "--event-price"), option(args, "--domain"));
    read_histories(input, option(args, "--history"), cfg);
    std::ofstream output_file;
    std::ostream* output = &std::cout;
    if (!output_path.empty() && output_path != "-") {
        output_file.open(output_path);
        if (!output_file) throw std::runtime_error("cannot open output " + output_path);
        output = &output_file;
    }
    write_header(*output);
    for (const Train& train : input.trains) {
        vector<Label> labels;
        Result result = solve_one(input, train, cfg, &labels);
        write_result(*output, input, train, cfg, result, labels);
    }
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        return main_impl(argc, argv);
    } catch (const std::exception& error) {
        std::cerr << "network_dp error: " << error.what() << '\n';
        return 1;
    }
}
