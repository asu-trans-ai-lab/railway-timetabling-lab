// gsc_types.h — FROZEN data structures for the group-supercolumn CG prototype (DESIGN_FREEZE.md).
// Standard C++17 only, consistent with n_track/fasttrain.cpp conventions. Do not extend these
// structs without a freeze revision; downstream code may assume exactly these fields.
#pragma once
#include <cstdint>
#include <vector>
#include <utility>

namespace gsc {

// sparse resource footprint: (resource_cell_row, amount) pairs, row space owned by the master
struct SparseVector {
    std::vector<std::pair<int, double>> nz;
};

// compressed sufficient service state  q = (kappa, f, a_active, tau_remain)
struct CompletionState {
    int      completed_prefix;        // kappa: ordered prefix of finished tasks
    uint64_t frontier_mask;           // f: bitmask over the bounded active frontier
    int      active_task;             // a_active: task currently executing (-1 if none)
    int      remaining_service_time;  // tau_remain: bins left on active task (0 if none)
    bool operator==(const CompletionState& o) const {
        return completed_prefix == o.completed_prefix && frontier_mask == o.frontier_mask &&
               active_task == o.active_task && remaining_service_time == o.remaining_service_time;
    }
};

struct AgentState {
    int node;
    int time;
    int movement_mode;                // e.g. run / dwell / hold-at-origin
    int speed_state;
    CompletionState service;
    int local_precedence_state;
};

struct JointState {
    int group_id;
    int time;
    std::vector<AgentState> agents;   // ordered by the group's agent_ids
    uint64_t route_lock_signature;    // hash of currently held route locks
    uint64_t shared_resource_signature;
};

struct GroupPartition {
    int version;                                  // bumped on every merge/split
    std::vector<std::vector<int>> disjoint_groups; // union = all agents, pairwise disjoint
};

struct GroupSuperColumn {
    int group_id;
    int partition_version;                        // INVARIANT: selectable only if current
    std::vector<int> agent_ids;
    std::vector<std::vector<AgentState>> trajectories; // one per agent, time-ordered
    SparseVector resource_footprint;              // A_Gp
    double physical_cost;                         // C_Gp
    double reduced_cost;                          // FO: C + lambda^T A - pi_G   (certifies)
    double quadratic_value;                       // finite-step companion value  (selects)
};

// donor-aware finite move  p0 -> p  (quadratic companion valuation, DESIGN_FREEZE #6)
struct FiniteMove {
    int donor_column_id;
    int candidate_column_id;
    SparseVector resource_change;                 // d_Gp = A_Gp - A_Gp0
    double physical_cost_change;                  // dC
    double first_order_value;                     // lambda^T d + grad Phi^T d
    double curvature_value;                       // 1/2 d^T H_Phi d
    double proximal_value;                        // rho_G * D(p, p0), transition-additive
};

struct PricingCertificate {
    enum class Level {
        RestrictedSpace,        // L1: no improving column in searched space
        ExactFixedPartition,    // L2: exact group DPs, current disjoint partition certified
        GlobalExact             // L3: + no hard conflict, no improving merge, complete search
    };
    Level level;
    double minimum_reduced_cost;
    bool corridor_exact;        // no heuristic corridor was active
    bool group_space_complete;  // exhaustive feasible transitions
};

} // namespace gsc
