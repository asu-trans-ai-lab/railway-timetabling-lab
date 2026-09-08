#include <algorithm>
#include <cmath>
#include <cstddef>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <queue>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

namespace lite {
constexpr double EPS = 1e-9;

struct Task {
    int gid = -1;
    int job = -1;
    int task_index = -1;
    int resource = -1;
    double duration_raw = 0.0;
    std::string train;
};

struct Job {
    std::string train;
    std::string train_type;
    double release_raw = 0.0;
    std::vector<int> tasks;
};

struct Instance {
    std::string name;
    double time_step = 1.0;
    double headway_raw = 3.0;
    std::vector<Job> jobs;
    std::vector<Task> tasks;

    double snap_up(double x) const {
        if (x <= EPS) return 0.0;
        return std::ceil(x / time_step - EPS) * time_step;
    }
    double headway() const { return snap_up(headway_raw); }
    double duration(int gid) const { return snap_up(tasks.at(gid).duration_raw); }
    double free_run() const {
        double value = 0.0;
        for (const auto& task : tasks) value += duration(task.gid);
        return value;
    }
};

struct Reservation {
    int resource = -1;
    int first = -1;
    int second = -1;
    auto tie() const { return std::tie(resource, first, second); }
    bool operator<(const Reservation& other) const { return tie() < other.tie(); }
    bool operator==(const Reservation& other) const { return tie() == other.tie(); }
};

struct Schedule {
    bool acyclic = false;
    std::vector<double> start;
    std::vector<double> end;
    double objective = std::numeric_limits<double>::infinity();
    double free_run = std::numeric_limits<double>::infinity();
    double delay = std::numeric_limits<double>::infinity();
    std::string reason;
};

struct Conflict {
    int resource = -1;
    int left = -1;
    int right = -1;
    double time = 0.0;
};

std::vector<std::string> split_tab(const std::string& line) {
    std::vector<std::string> out;
    std::stringstream ss(line);
    std::string item;
    while (std::getline(ss, item, '\t')) out.push_back(item);
    return out;
}

Instance load_instance(const std::string& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open input: " + path);
    std::string line;
    if (!std::getline(in, line)) throw std::runtime_error("empty input");
    const auto header = split_tab(line);
    std::map<std::string, int> col;
    for (int i = 0; i < static_cast<int>(header.size()); ++i) col[header[i]] = i;
    for (const auto& needed : {"instance","time_step_min","headway_min","train_id","release_min","train_type","task_index","resource_id","duration_min"}) {
        if (!col.count(needed)) throw std::runtime_error(std::string("missing column: ") + needed);
    }
    Instance inst;
    std::map<std::string, int> job_index;
    bool first_row = true;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        auto row = split_tab(line);
        if (row.size() < header.size()) row.resize(header.size());
        if (first_row) {
            inst.name = row[col["instance"]];
            inst.time_step = std::stod(row[col["time_step_min"]]);
            inst.headway_raw = std::stod(row[col["headway_min"]]);
            first_row = false;
        }
        const std::string train = row[col["train_id"]];
        int j;
        auto it = job_index.find(train);
        if (it == job_index.end()) {
            j = static_cast<int>(inst.jobs.size());
            job_index[train] = j;
            Job job;
            job.train = train;
            job.release_raw = std::stod(row[col["release_min"]]);
            job.train_type = row[col["train_type"]];
            inst.jobs.push_back(job);
        } else {
            j = it->second;
        }
        Task task;
        task.gid = static_cast<int>(inst.tasks.size());
        task.job = j;
        task.task_index = std::stoi(row[col["task_index"]]);
        task.resource = std::stoi(row[col["resource_id"]]);
        task.duration_raw = std::stod(row[col["duration_min"]]);
        task.train = train;
        inst.tasks.push_back(task);
        inst.jobs[j].tasks.push_back(task.gid);
    }
    if (inst.jobs.empty() || inst.tasks.empty()) throw std::runtime_error("no tasks loaded");
    if (!(inst.time_step > 0.0) || inst.headway_raw < 0.0) throw std::runtime_error("invalid time/headway");
    for (auto& job : inst.jobs) {
        std::sort(job.tasks.begin(), job.tasks.end(), [&](int a, int b){ return inst.tasks[a].task_index < inst.tasks[b].task_index; });
        for (int k = 0; k < static_cast<int>(job.tasks.size()); ++k) {
            if (inst.tasks[job.tasks[k]].task_index != k) throw std::runtime_error("task indices must be 0..m-1 by train");
        }
    }
    return inst;
}

std::vector<Reservation> canonical(std::vector<Reservation> values) {
    std::sort(values.begin(), values.end());
    values.erase(std::unique(values.begin(), values.end()), values.end());
    return values;
}

Schedule schedule_from_reservations(const Instance& inst, const std::vector<Reservation>& reservations) {
    const int n = static_cast<int>(inst.tasks.size());
    const int source = n;
    struct Edge { int v; double lag; };
    std::vector<std::vector<Edge>> out(n + 1);
    std::vector<int> indegree(n + 1, 0);
    auto add = [&](int u, int v, double lag) {
        out[u].push_back({v, lag});
        ++indegree[v];
    };
    for (const auto& job : inst.jobs) {
        add(source, job.tasks.front(), inst.snap_up(job.release_raw));
        for (int k = 0; k + 1 < static_cast<int>(job.tasks.size()); ++k) {
            add(job.tasks[k], job.tasks[k+1], inst.duration(job.tasks[k]));
        }
    }
    for (const auto& r : reservations) {
        if (r.first < 0 || r.second < 0 || r.first >= n || r.second >= n) throw std::runtime_error("bad reservation task id");
        if (inst.tasks[r.first].resource != r.resource || inst.tasks[r.second].resource != r.resource) throw std::runtime_error("reservation/resource mismatch");
        add(r.first, r.second, inst.duration(r.first) + inst.headway());
    }
    std::priority_queue<int, std::vector<int>, std::greater<int>> ready;
    for (int i = 0; i <= n; ++i) if (indegree[i] == 0) ready.push(i);
    std::vector<double> dist(n + 1, -std::numeric_limits<double>::infinity());
    dist[source] = 0.0;
    int visited = 0;
    while (!ready.empty()) {
        int u = ready.top(); ready.pop(); ++visited;
        for (const auto& e : out[u]) {
            if (std::isfinite(dist[u])) dist[e.v] = std::max(dist[e.v], dist[u] + e.lag);
            if (--indegree[e.v] == 0) ready.push(e.v);
        }
    }
    Schedule result;
    result.free_run = inst.free_run();
    if (visited != n + 1) {
        result.reason = "reservation decisions create a cyclic task/resource order";
        return result;
    }
    result.acyclic = true;
    result.start.resize(n);
    result.end.resize(n);
    std::vector<double> completion(inst.jobs.size(), 0.0);
    for (int gid = 0; gid < n; ++gid) {
        result.start[gid] = dist[gid];
        result.end[gid] = dist[gid] + inst.duration(gid);
        completion[inst.tasks[gid].job] = std::max(completion[inst.tasks[gid].job], result.end[gid]);
    }
    result.objective = 0.0;
    for (int j = 0; j < static_cast<int>(inst.jobs.size()); ++j) {
        result.objective += completion[j] - inst.snap_up(inst.jobs[j].release_raw);
    }
    result.delay = std::max(0.0, result.objective - result.free_run);
    return result;
}

std::vector<Conflict> conflicts(const Instance& inst, const Schedule& s) {
    std::map<int, std::vector<int>> by_resource;
    for (const auto& task : inst.tasks) by_resource[task.resource].push_back(task.gid);
    std::vector<Conflict> out;
    const double h = inst.headway();
    for (auto& [resource, gids] : by_resource) {
        std::sort(gids.begin(), gids.end(), [&](int a, int b){
            return std::tie(s.start[a], s.end[a], a) < std::tie(s.start[b], s.end[b], b);
        });
        for (int x = 0; x < static_cast<int>(gids.size()); ++x) {
            int a = gids[x];
            for (int y = x + 1; y < static_cast<int>(gids.size()); ++y) {
                int b = gids[y];
                if (inst.tasks[a].job == inst.tasks[b].job) continue;
                if (s.start[a] < s.end[b] + h - EPS && s.start[b] < s.end[a] + h - EPS) {
                    out.push_back({resource, a, b, std::min(s.start[a], s.start[b])});
                }
            }
        }
    }
    std::sort(out.begin(), out.end(), [](const Conflict& a, const Conflict& b){
        return std::tie(a.time,a.resource,a.left,a.right) < std::tie(b.time,b.resource,b.left,b.right);
    });
    return out;
}

std::pair<double,int> safe_frontier(const Instance& inst, const Schedule& s, const std::vector<Conflict>& cs) {
    if (cs.empty()) {
        double frontier = 0.0;
        for (double e : s.end) frontier = std::max(frontier, e);
        return {frontier, static_cast<int>(inst.tasks.size())};
    }
    double frontier = cs.front().time;
    for (const auto& c : cs) frontier = std::min(frontier, c.time);
    int safe = 0;
    for (int gid = 0; gid < static_cast<int>(inst.tasks.size()); ++gid) {
        if (s.end[gid] + inst.headway() <= frontier + EPS) ++safe;
    }
    return {frontier,safe};
}

std::string task_label(const Instance& inst, int gid) {
    const auto& t = inst.tasks[gid];
    return t.train + ":" + std::to_string(t.task_index);
}

std::string reservation_label(const Instance& inst, const Reservation& r) {
    return "R" + std::to_string(r.resource) + ": " + task_label(inst,r.first) + " BEFORE " + task_label(inst,r.second);
}

std::string conflict_label(const Instance& inst, const Schedule& s, const Conflict& c) {
    std::ostringstream os;
    os << "R" << c.resource << ": " << task_label(inst,c.left) << " [" << s.start[c.left] << "," << s.end[c.left]
       << ") vs " << task_label(inst,c.right) << " [" << s.start[c.right] << "," << s.end[c.right] << ")";
    return os.str();
}

std::vector<Reservation> priority_reservations(const Instance& inst) {
    std::vector<int> order(inst.jobs.size());
    for (int j=0;j<static_cast<int>(order.size());++j) order[j]=j;
    std::sort(order.begin(),order.end(),[&](int a,int b){
        if (std::abs(inst.jobs[a].release_raw-inst.jobs[b].release_raw)>EPS) return inst.jobs[a].release_raw<inst.jobs[b].release_raw;
        return inst.jobs[a].train<inst.jobs[b].train;
    });
    std::vector<int> rank(inst.jobs.size());
    for (int i=0;i<static_cast<int>(order.size());++i) rank[order[i]]=i;
    std::map<int,std::vector<int>> by_resource;
    for (const auto& task:inst.tasks) by_resource[task.resource].push_back(task.gid);
    std::vector<Reservation> rs;
    for (const auto& [resource,gids]:by_resource) {
        for (int x=0;x<static_cast<int>(gids.size());++x) for (int y=x+1;y<static_cast<int>(gids.size());++y) {
            int a=gids[x],b=gids[y];
            if (inst.tasks[a].job==inst.tasks[b].job) continue;
            if (rank[inst.tasks[a].job]<rank[inst.tasks[b].job]) rs.push_back({resource,a,b});
            else rs.push_back({resource,b,a});
        }
    }
    return canonical(std::move(rs));
}

struct Node {
    int id=-1,parent=-1,depth=0;
    std::vector<Reservation> reservations;
    bool has_branch=false;
    Reservation branch;
    Schedule schedule;
    std::vector<Conflict> conflicts;
    double lb=std::numeric_limits<double>::infinity();
    double safe_frontier=0.0;
    int safe_tasks=0;
    std::string status="OPEN";
    std::string reason;
};

void solve_node(const Instance& inst, Node& node) {
    node.schedule=schedule_from_reservations(inst,node.reservations);
    if (!node.schedule.acyclic) {
        node.status="PRUNED_CYCLE";
        node.reason=node.schedule.reason;
        return;
    }
    node.lb=node.schedule.objective;
    node.conflicts=conflicts(inst,node.schedule);
    auto [frontier,safe]=safe_frontier(inst,node.schedule,node.conflicts);
    node.safe_frontier=frontier; node.safe_tasks=safe;
    if (node.conflicts.empty()) node.status="FEASIBLE";
}

struct Summary {
    std::string status;
    double root_lb=0.0,global_lb=0.0,initial_ub=0.0,ub=0.0;
    int generated=0,expanded=0,pruned_bound=0,pruned_cycle=0,feasible=0;
    Schedule best;
    std::vector<Reservation> best_reservations;
    std::vector<Node> nodes;
};

Summary solve_bb(const Instance& inst, int max_nodes) {
    if (max_nodes<1) throw std::runtime_error("max_nodes must be >=1");
    Summary sum;
    auto initial_rs=priority_reservations(inst);
    auto initial=schedule_from_reservations(inst,initial_rs);
    if (!initial.acyclic || !conflicts(inst,initial).empty()) throw std::runtime_error("priority UB construction failed");
    sum.initial_ub=sum.ub=initial.objective; sum.best=initial; sum.best_reservations=initial_rs;

    Node root; root.id=0; root.parent=-1; solve_node(inst,root);
    sum.root_lb=root.lb; sum.nodes.push_back(root);

    using Key=std::tuple<double,int,double,int>; // LB, -safe, -frontier, node id
    std::priority_queue<Key,std::vector<Key>,std::greater<Key>> open;
    if (sum.nodes[0].status=="FEASIBLE") {
        sum.nodes[0].status="FATHOMED_FEASIBLE"; ++sum.feasible;
        if (sum.nodes[0].lb<sum.ub-EPS) {sum.ub=sum.nodes[0].lb;sum.best=sum.nodes[0].schedule;sum.best_reservations=sum.nodes[0].reservations;}
    } else if (sum.nodes[0].status=="OPEN" && sum.nodes[0].lb<sum.ub-EPS) {
        open.push({sum.nodes[0].lb,-sum.nodes[0].safe_tasks,-sum.nodes[0].safe_frontier,0});
    } else if (sum.nodes[0].status=="OPEN") {
        sum.nodes[0].status="PRUNED_BOUND"; ++sum.pruned_bound;
    }

    bool budget=false;
    while (!open.empty()) {
        auto [_,__,___,id]=open.top(); open.pop();
        Node& node=sum.nodes[id];
        if (node.status!="OPEN") continue;
        if (node.lb>=sum.ub-EPS) {node.status="PRUNED_BOUND";++sum.pruned_bound;continue;}
        if (node.conflicts.empty()) {
            node.status="FATHOMED_FEASIBLE";++sum.feasible;
            if (node.lb<sum.ub-EPS) {sum.ub=node.lb;sum.best=node.schedule;sum.best_reservations=node.reservations;}
            continue;
        }
        if (static_cast<int>(sum.nodes.size())+2>max_nodes) {budget=true;open.push({node.lb,-node.safe_tasks,-node.safe_frontier,id});break;}
        Conflict c=node.conflicts.front();
        const int parent_depth = node.depth;
        const double parent_lb = node.lb;
        const auto parent_reservations = node.reservations;
        node.status="EXPANDED";++sum.expanded;
        // Do not retain a reference to sum.nodes[id] across push_back: vector
        // reallocation would invalidate it.  Freeze the parent state first.
        for (auto [first,second]:{std::pair<int,int>{c.left,c.right},std::pair<int,int>{c.right,c.left}}) {
            Reservation r{c.resource,first,second};
            Node child; child.id=static_cast<int>(sum.nodes.size());child.parent=id;child.depth=parent_depth+1;child.has_branch=true;child.branch=r;
            child.reservations=parent_reservations;child.reservations.push_back(r);child.reservations=canonical(std::move(child.reservations));
            solve_node(inst,child);
            if (child.status=="PRUNED_CYCLE") {++sum.pruned_cycle;sum.nodes.push_back(std::move(child));continue;}
            if (child.lb+EPS<parent_lb) throw std::runtime_error("reservation lowered LB");
            if (child.status=="FEASIBLE") {
                child.status="FATHOMED_FEASIBLE";++sum.feasible;
                if (child.lb<sum.ub-EPS) {sum.ub=child.lb;sum.best=child.schedule;sum.best_reservations=child.reservations;}
                sum.nodes.push_back(std::move(child));continue;
            }
            if (child.lb>=sum.ub-EPS) {child.status="PRUNED_BOUND";++sum.pruned_bound;sum.nodes.push_back(std::move(child));continue;}
            int cid=child.id;sum.nodes.push_back(std::move(child));
            const Node& cref=sum.nodes[cid];
            open.push({cref.lb,-cref.safe_tasks,-cref.safe_frontier,cid});
        }
    }
    double glb=sum.ub;
    bool live=false;
    for (const auto& node:sum.nodes) if (node.status=="OPEN") {glb=std::min(glb,node.lb);live=true;}
    sum.global_lb=glb;sum.generated=static_cast<int>(sum.nodes.size());
    sum.status=live||!open.empty()?(budget?"NODE_BUDGET_EXHAUSTED":"UNRESOLVED"):"PROVEN_OPTIMAL";
    if (sum.status=="PROVEN_OPTIMAL") sum.global_lb=sum.ub;
    return sum;
}

void write_tree(const Instance& inst,const Summary& s,const std::string& path) {
    std::ofstream out(path);
    for (const auto& n:s.nodes) {
        for (int d=0;d<n.depth;++d) out<<"  ";
        out<<"N"<<n.id<<" parent="<<n.parent<<" ";
        if (n.has_branch) out<<reservation_label(inst,n.branch); else out<<"ROOT";
        out<<" | LB="<<n.lb<<" | status="<<n.status<<" | conflicts="<<n.conflicts.size()
           <<" | safe="<<n.safe_tasks<<"/"<<inst.tasks.size()<<"@"<<n.safe_frontier;
        if (!n.conflicts.empty()) out<<" | next="<<conflict_label(inst,n.schedule,n.conflicts.front());
        out<<"\n";
    }
}

void write_summary(const Instance& inst,const Summary& s,const std::string& path) {
    std::ofstream out(path);
    out<<std::fixed<<std::setprecision(6);
    out<<"{\n"
       <<"  \"instance\": \""<<inst.name<<"\",\n"
       <<"  \"status\": \""<<s.status<<"\",\n"
       <<"  \"trains\": "<<inst.jobs.size()<<",\n"
       <<"  \"tasks\": "<<inst.tasks.size()<<",\n"
       <<"  \"time_step_min\": "<<inst.time_step<<",\n"
       <<"  \"requested_headway_min\": "<<inst.headway_raw<<",\n"
       <<"  \"effective_headway_min\": "<<inst.headway()<<",\n"
       <<"  \"root_lb\": "<<s.root_lb<<",\n"
       <<"  \"initial_ub\": "<<s.initial_ub<<",\n"
       <<"  \"global_lb\": "<<s.global_lb<<",\n"
       <<"  \"incumbent_ub\": "<<s.ub<<",\n"
       <<"  \"absolute_gap\": "<<std::max(0.0,s.ub-s.global_lb)<<",\n"
       <<"  \"free_run_total\": "<<s.best.free_run<<",\n"
       <<"  \"total_delay\": "<<s.best.delay<<",\n"
       <<"  \"nodes_generated\": "<<s.generated<<",\n"
       <<"  \"nodes_expanded\": "<<s.expanded<<",\n"
       <<"  \"nodes_pruned_bound\": "<<s.pruned_bound<<",\n"
       <<"  \"nodes_pruned_cycle\": "<<s.pruned_cycle<<",\n"
       <<"  \"feasible_leaves\": "<<s.feasible<<"\n"
       <<"}\n";
}

} // namespace lite

int main(int argc,char** argv) {
    try {
        std::string input,output="cpp_summary.json",tree="cpp_bb_tree.txt";
        int max_nodes=100000;
        for (int i=1;i<argc;++i) {
            std::string arg=argv[i];
            auto take=[&](){ if (i+1>=argc) throw std::runtime_error("missing value after "+arg); return std::string(argv[++i]); };
            if (arg=="--input") input=take();
            else if (arg=="--output") output=take();
            else if (arg=="--tree") tree=take();
            else if (arg=="--max-nodes") max_nodes=std::stoi(take());
            else if (arg=="--help") {std::cout<<"reservation_bb --input instance.tsv [--output summary.json] [--tree tree.txt] [--max-nodes N]\n";return 0;}
            else throw std::runtime_error("unknown argument: "+arg);
        }
        if (input.empty()) throw std::runtime_error("--input is required");
        auto inst=lite::load_instance(input);
        auto summary=lite::solve_bb(inst,max_nodes);
        lite::write_summary(inst,summary,output);
        lite::write_tree(inst,summary,tree);
        std::cout<<"status="<<summary.status<<" root_lb="<<summary.root_lb<<" UB="<<summary.ub
                 <<" global_lb="<<summary.global_lb<<" nodes="<<summary.generated<<" delay="<<summary.best.delay<<"\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr<<"ERROR: "<<e.what()<<"\n";
        return 2;
    }
}
