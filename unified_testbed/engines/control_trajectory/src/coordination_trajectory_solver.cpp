// Coordination-agent + trajectory-agent prototype for railway joint-space optimization.
//
// The CoordinationAgent owns:
//   * hard-conflict detection and interaction graph construction;
//   * conflict-connected hyper-agent groups;
//   * resource prices and local precedence penalties;
//   * quadratic/proximal corridor parameters;
//   * accept/reject decisions and iteration records.
//
// TrajectoryAgent and HyperTrajectoryAgent own:
//   * physically feasible individual trajectories;
//   * synchronous pair/triple joint-state dynamic programming;
//   * immutable hard non-overlap/resource/swap constraints.
//
// Input: nodes.csv, arcs.csv, agents.csv
// Output: summary.csv, iterations.csv, control_actions.csv, schedules.csv, components.csv

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <queue>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

using std::array;
using std::string;
using std::vector;
static constexpr double INF = 1e100;
static constexpr int MAXG = 3;

static vector<string> split_csv(const string& line) {
    vector<string> out; string cur; bool quote = false;
    for (char c : line) {
        if (c == '"') quote = !quote;
        else if (c == ',' && !quote) { out.push_back(cur); cur.clear(); }
        else cur.push_back(c);
    }
    out.push_back(cur); return out;
}

struct Node { int id=0, x=0, y=0, resource=-1; string kind; };
struct Arc { int id=0, u=0, v=0, resource=-1; double cost=1.0; };
struct Agent {
    int id=0, start=0, goal=0, release=0, preferred=0, deadline=0, interval=1;
    string train_class;
    double wait_cost=.2, tardy=1.0, prox=1.0, spacing=.15, desired=2.0;
};
struct Net { vector<Node> nodes; vector<Arc> arcs; vector<vector<int>> out; vector<Agent> agents; };

static Net load_instance(const string& dir) {
    Net N;
    {
        std::ifstream f(dir + "/nodes.csv"); if (!f) throw std::runtime_error("missing nodes.csv");
        string line; std::getline(f, line);
        while (std::getline(f, line)) {
            if (line.empty()) continue;
            auto a = split_csv(line); Node n;
            n.id = std::stoi(a[0]); n.x = std::stoi(a[1]); n.y = std::stoi(a[2]);
            n.resource = std::stoi(a[3]); n.kind = a.size() > 4 ? a[4] : "track";
            if ((int)N.nodes.size() <= n.id) N.nodes.resize(n.id + 1);
            N.nodes[n.id] = n;
        }
    }
    N.out.assign(N.nodes.size(), {});
    {
        std::ifstream f(dir + "/arcs.csv"); if (!f) throw std::runtime_error("missing arcs.csv");
        string line; std::getline(f, line);
        while (std::getline(f, line)) {
            if (line.empty()) continue;
            auto a = split_csv(line); Arc e;
            e.id = std::stoi(a[0]); e.u = std::stoi(a[1]); e.v = std::stoi(a[2]);
            e.cost = std::stod(a[3]); e.resource = std::stoi(a[4]);
            if ((int)N.arcs.size() <= e.id) N.arcs.resize(e.id + 1);
            N.arcs[e.id] = e;
            N.out[e.u].push_back(e.id);
        }
    }
    {
        std::ifstream f(dir + "/agents.csv"); if (!f) throw std::runtime_error("missing agents.csv");
        string line; std::getline(f, line);
        while (std::getline(f, line)) {
            if (line.empty()) continue;
            auto a = split_csv(line); Agent k;
            k.id = std::stoi(a[0]); k.start = std::stoi(a[1]); k.goal = std::stoi(a[2]);
            k.release = std::stoi(a[3]); k.preferred = std::stoi(a[4]); k.deadline = std::stoi(a[5]);
            k.interval = std::max(1, std::stoi(a[6])); k.train_class = a[7];
            k.wait_cost = std::stod(a[8]); k.tardy = std::stod(a[9]); k.prox = std::stod(a[10]);
            k.spacing = std::stod(a[11]); k.desired = std::stod(a[12]);
            if ((int)N.agents.size() <= k.id) N.agents.resize(k.id + 1);
            N.agents[k.id] = k;
        }
    }
    return N;
}

static vector<int> bfs_dist(const Net& N, int goal) {
    vector<vector<int>> rev(N.nodes.size());
    for (auto &e : N.arcs) rev[e.v].push_back(e.u);
    vector<int> d(N.nodes.size(), 1000000000); std::queue<int> q; d[goal] = 0; q.push(goal);
    while (!q.empty()) {
        int v = q.front(); q.pop();
        for (int u : rev[v]) if (d[u] > d[v] + 1) { d[u] = d[v] + 1; q.push(u); }
    }
    return d;
}

static vector<int> shortest_path(const Net& N, const Agent& A) {
    vector<double> d(N.nodes.size(), INF); vector<int> par(N.nodes.size(), -1);
    using P = std::pair<double,int>; std::priority_queue<P,vector<P>,std::greater<P>> pq;
    d[A.start] = 0; pq.push({0, A.start});
    while (!pq.empty()) {
        auto [du,u] = pq.top(); pq.pop(); if (du != d[u]) continue; if (u == A.goal) break;
        for (int eid : N.out[u]) {
            auto &e = N.arcs[eid]; double nd = du + e.cost;
            if (nd < d[e.v] - 1e-12) { d[e.v] = nd; par[e.v] = u; pq.push({nd,e.v}); }
        }
    }
    if (!std::isfinite(d[A.goal])) return {};
    vector<int> p; for (int v=A.goal; v!=-1; v=par[v]) p.push_back(v);
    std::reverse(p.begin(),p.end()); return p;
}

struct Schedule { vector<int> node; double cost=0; bool feasible=true; };

static double schedule_cost(const Net& N, const Agent& A, const vector<int>& path) {
    (void)N;
    double c = 0;
    for (int t=0; t+1<(int)path.size(); ++t) {
        if (path[t+1] == path[t]) c += (path[t] == A.goal ? 0.0 : A.wait_cost);
        else c += 1.0;
        if (t+1 > A.preferred && path[t+1] != A.goal) c += A.tardy;
    }
    return c;
}

class TrajectoryAgent {
public:
    TrajectoryAgent(const Net& net, int agent_id) : N(net), id(agent_id) {}
    Schedule generate_individual(int H) const {
        const Agent &A = N.agents[id]; Schedule S; S.node.assign(H+1, A.start);
        auto p = shortest_path(N,A); if (p.empty()) { S.feasible=false; return S; }
        int idx=0, cool=0;
        for (int t=0; t<H; ++t) {
            int cur=S.node[t], nxt=cur;
            if (cur==A.goal) nxt=cur;
            else if (t<A.release) nxt=cur;
            else if (cool>0) { nxt=cur; --cool; }
            else if (idx+1<(int)p.size()) { ++idx; nxt=p[idx]; cool=A.interval-1; }
            S.node[t+1]=nxt;
        }
        S.cost=schedule_cost(N,A,S.node); S.feasible=(S.node[H]==A.goal); return S;
    }
private:
    const Net& N; int id;
};

struct Conflict { int a=-1,b=-1,t=-1,type=0,resource=-1,node=-1; }; // 1 node/resource, 2 swap, 3 near

static vector<Conflict> detect_conflicts(const Net& N, const vector<Schedule>& S, int H,
                                         double radius, bool include_near) {
    vector<Conflict> C;
    for (int i=0;i<(int)S.size();++i) for (int j=i+1;j<(int)S.size();++j) {
        for (int t=0;t<=H;++t) {
            int u=S[i].node[t], v=S[j].node[t];
            bool hard=(u==v); int ru=N.nodes[u].resource, rv=N.nodes[v].resource;
            if (ru>=0 && ru==rv) hard=true;
            if (hard) { C.push_back({i,j,t,1,ru>=0?ru:rv,u==v?u:-1}); continue; }
            if (t<H && S[i].node[t]==S[j].node[t+1] && S[j].node[t]==S[i].node[t+1]) {
                C.push_back({i,j,t,2,-1,-1}); continue;
            }
            if (include_near) {
                double dx=N.nodes[u].x-N.nodes[v].x, dy=N.nodes[u].y-N.nodes[v].y;
                if (std::sqrt(dx*dx+dy*dy)<=radius) C.push_back({i,j,t,3,-1,-1});
            }
        }
    }
    return C;
}

struct DSU {
    vector<int> p; explicit DSU(int n):p(n){std::iota(p.begin(),p.end(),0);} 
    int f(int x){return p[x]==x?x:p[x]=f(p[x]);}
    void u(int a,int b){a=f(a);b=f(b);if(a!=b)p[b]=a;}
};
static vector<vector<int>> components(int n,const vector<Conflict>& C) {
    DSU d(n); for(auto&c:C)d.u(c.a,c.b); std::map<int,vector<int>> m;
    for(int i=0;i<n;i++) m[d.f(i)].push_back(i);
    vector<vector<int>> o;
    for(auto&kv:m) o.push_back(kv.second);
    return o;
}

static uint64_t key_rt(int resource,int t) {
    return (uint64_t)(uint32_t)(resource+1) << 32 | (uint32_t)t;
}
static uint64_t key_art(int agent,int resource,int t) {
    uint64_t a=(uint64_t)(agent & 0xFFFF), r=(uint64_t)((resource+1)&0xFFFFFF), tt=(uint64_t)(t&0xFFFFFF);
    return (a<<48) | (r<<24) | tt;
}

struct ControlModel {
    double budget=6.0, rho=.18, cross_weight=.08;
    const std::unordered_map<uint64_t,double>* resource_prices=nullptr;
    const std::unordered_map<uint64_t,double>* precedence_penalties=nullptr;
};

struct JState {
    array<int,MAXG> prev{}, node{}, cool{}; int g=0;
    bool operator==(JState const&o)const{return g==o.g&&prev==o.prev&&node==o.node&&cool==o.cool;}
};
struct JHash {
    size_t operator()(JState const&s)const noexcept {
        size_t h=s.g; for(int i=0;i<s.g;i++){h=h*1000003u+(unsigned)(s.prev[i]+2);h=h*1000003u+(unsigned)(s.node[i]+1);h=h*97u+(unsigned)s.cool[i];} return h;
    }
};
struct Label { double cost=INF; int pred=-1; JState st; };
struct GroupResult {
    bool feasible=false; double obj=INF; long long generated=0,transitions=0,screened=0;
    vector<vector<int>> paths; double wall=0;
};
struct Move { int prev,node,cool; double cost; bool moved; };

static vector<Move> moves_for(const Net& N,const Agent& A,const JState&s,int i,int t,const vector<int>&dist) {
    vector<Move> m; int cur=s.node[i],prev=s.prev[i],cool=s.cool[i];
    if(cur==A.goal){m.push_back({prev,cur,0,0,false});return m;}
    if(t<A.release){m.push_back({prev,cur,cool,A.wait_cost,false});return m;}
    if(cool>0){m.push_back({prev,cur,cool-1,A.wait_cost,false});return m;}
    m.push_back({prev,cur,0,A.wait_cost,false});
    for(int eid:N.out[cur]){
        auto&e=N.arcs[eid]; bool dead=N.out[cur].size()==1; if(e.v==prev&&!dead)continue;
        if(dist[e.v]>=1000000000 || dist[e.v]>=dist[cur])continue;
        if(t+1+dist[e.v]*A.interval>A.deadline)continue;
        m.push_back({cur,e.v,A.interval-1,e.cost,true});
    }
    return m;
}

static bool hard_ok(const Net&N,const JState&old,const array<Move,MAXG>&mv,int g) {
    for(int i=0;i<g;i++)for(int j=i+1;j<g;j++){
        int ni=mv[i].node,nj=mv[j].node;
        if(ni==nj)return false;
        int ri=N.nodes[ni].resource,rj=N.nodes[nj].resource;
        if(ri>=0&&ri==rj)return false;
        if(old.node[i]==nj&&old.node[j]==ni&&ni!=nj)return false;
    }
    return true;
}
static double dist2xy(const Net&N,int u,int v){double dx=N.nodes[u].x-N.nodes[v].x,dy=N.nodes[u].y-N.nodes[v].y;return dx*dx+dy*dy;}

class HyperTrajectoryAgent {
public:
    explicit HyperTrajectoryAgent(const Net& net):N(net){}
    GroupResult solve(const vector<int>& ids,const vector<Schedule>& ref,int H,bool corridor,const ControlModel& M) const {
        auto tic=std::chrono::steady_clock::now(); GroupResult R; int g=ids.size(); if(g<1||g>MAXG)return R;
        vector<vector<int>> dists(g); for(int i=0;i<g;i++)dists[i]=bfs_dist(N,N.agents[ids[i]].goal);
        vector<vector<Label>> layers(H+1); vector<std::unordered_map<JState,int,JHash>> idx(H+1);
        JState st;st.g=g;for(int i=0;i<g;i++){st.prev[i]=-1;st.node[i]=N.agents[ids[i]].start;st.cool[i]=0;}
        layers[0].push_back({0,-1,st});idx[0][st]=0;R.generated=1;
        for(int t=0;t<H;t++){
            for(int li=0;li<(int)layers[t].size();li++){
                auto &lab=layers[t][li]; array<vector<Move>,MAXG> opts;
                for(int i=0;i<g;i++)opts[i]=moves_for(N,N.agents[ids[i]],lab.st,i,t,dists[i]);
                array<Move,MAXG> choice;
                std::function<void(int)> rec=[&](int q){
                    if(q<g){for(auto&m:opts[q]){choice[q]=m;rec(q+1);}return;}
                    R.transitions++;if(!hard_ok(N,lab.st,choice,g))return;
                    JState ns;ns.g=g;double add=0,prox=0;
                    for(int i=0;i<g;i++){
                        ns.prev[i]=choice[i].prev;ns.node[i]=choice[i].node;ns.cool[i]=choice[i].cool;add+=choice[i].cost;
                        auto&A=N.agents[ids[i]];if(t+1>A.preferred&&ns.node[i]!=A.goal)add+=A.tardy;
                        int rr=ref[ids[i]].node[std::min(t+1,(int)ref[ids[i]].node.size()-1)];prox+=A.prox*dist2xy(N,ns.node[i],rr);
                        int resource=N.nodes[ns.node[i]].resource;
                        if(resource>=0&&M.resource_prices){auto it=M.resource_prices->find(key_rt(resource,t+1));if(it!=M.resource_prices->end())add+=it->second;}
                        if(resource>=0&&M.precedence_penalties){auto it=M.precedence_penalties->find(key_art(ids[i],resource,t+1));if(it!=M.precedence_penalties->end())add+=it->second;}
                    }
                    if(corridor&&M.rho*prox>M.budget+1e-12){R.screened++;return;}
                    add+=.5*M.rho*prox;
                    for(int i=0;i<g;i++)for(int j=i+1;j<g;j++){
                        auto&A=N.agents[ids[i]];auto&B=N.agents[ids[j]];
                        double dx=N.nodes[ns.node[i]].x-N.nodes[ns.node[j]].x,dy=N.nodes[ns.node[i]].y-N.nodes[ns.node[j]].y;
                        double dd=std::sqrt(dx*dx+dy*dy),des=.5*(A.desired+B.desired),w=.5*(A.spacing+B.spacing);
                        double shortfall=std::max(0.0,des-dd);add+=.5*w*shortfall*shortfall;
                        double dm=(choice[i].moved?1.0:0.0)-(choice[j].moved?1.0:0.0);add+=.5*M.cross_weight*dm*dm;
                    }
                    double nc=lab.cost+add;auto it=idx[t+1].find(ns);
                    if(it==idx[t+1].end()){int ni=layers[t+1].size();idx[t+1][ns]=ni;layers[t+1].push_back({nc,li,ns});R.generated++;}
                    else if(nc<layers[t+1][it->second].cost-1e-12){layers[t+1][it->second].cost=nc;layers[t+1][it->second].pred=li;}
                };rec(0);
            }
            if(layers[t+1].empty())break;
        }
        int best=-1;double bc=INF;
        for(int i=0;i<(int)layers[H].size();i++){
            bool ok=true;for(int q=0;q<g;q++)if(layers[H][i].st.node[q]!=N.agents[ids[q]].goal)ok=false;
            if(ok&&layers[H][i].cost<bc){bc=layers[H][i].cost;best=i;}
        }
        if(best>=0){R.feasible=true;R.obj=bc;R.paths.assign(g,vector<int>(H+1));int cur=best;
            for(int t=H;t>=0;t--){auto&L=layers[t][cur];for(int q=0;q<g;q++)R.paths[q][t]=L.st.node[q];cur=L.pred;}
        }
        R.wall=std::chrono::duration<double>(std::chrono::steady_clock::now()-tic).count();return R;
    }
private: const Net& N;
};

static double population_objective(const Net&N,const vector<Schedule>&S){double z=0;for(auto&a:N.agents)z+=schedule_cost(N,a,S[a.id].node);return z;}
static string join_ids(const vector<int>&v){std::ostringstream o;for(size_t i=0;i<v.size();++i){if(i)o<<'|';o<<v[i];}return o.str();}
static string basename_of(string p){while(!p.empty()&&(p.back()=='/'||p.back()=='\\'))p.pop_back();auto q=p.find_last_of("/\\");return q==string::npos?p:p.substr(q+1);}

struct ControlActionRecord {
    int round=0, group_id=0, group_size=0, conflicts_before=0, conflicts_after=0, priority=-1;
    bool accepted=false, reduced_feasible=false, full_feasible=false;
    double budget=0,rho=0,reduced_obj=INF,full_obj=INF,reduced_wall=0,full_wall=0;
    long long reduced_states=0,reduced_trans=0,screened=0,full_states=0,full_trans=0;
    string agents;
};
struct IterationRecord {
    int round=0,hard_before=0,hard_after=0,active_groups=0,largest_group=0,accepted=0,rejected=0;
    double objective_before=0,objective_after=0,price_l1=0;
};

class CoordinationAgent {
public:
    CoordinationAgent(const Net&net,int horizon,double base_budget,double rho,double radius,double cross,
                      int max_group,int full_limit,int max_rounds,double price_step,double precedence_weight)
        :N(net),H(horizon),base_budget(base_budget),rho(rho),radius(radius),cross(cross),max_group(max_group),
         full_limit(full_limit),max_rounds(max_rounds),price_step(price_step),precedence_weight(precedence_weight),hyper(net){}

    vector<Schedule> initialize_trajectory_agents() const {
        vector<Schedule>S(N.agents.size());for(auto&a:N.agents)S[a.id]=TrajectoryAgent(N,a.id).generate_individual(H);return S;
    }

    void run(vector<Schedule>&S){
        for(int round=0;round<max_rounds;++round){
            auto hard=detect_conflicts(N,S,H,0,false);if(hard.empty())break;
            IterationRecord ir;ir.round=round;ir.hard_before=hard.size();ir.objective_before=population_objective(N,S);
            update_resource_prices(hard);
            auto groups=select_groups(hard);ir.active_groups=groups.size();for(auto&g:groups)ir.largest_group=std::max(ir.largest_group,(int)g.size());
            int gid=0;
            for(auto&grp:groups){
                ControlActionRecord ar;ar.round=round;ar.group_id=gid++;ar.group_size=grp.size();ar.agents=join_ids(grp);ar.conflicts_before=detect_conflicts(N,S,H,0,false).size();
                ar.priority=select_priority(grp);build_precedence_penalties(grp,hard,ar.priority);
                ControlModel M;M.rho=rho;M.cross_weight=cross;M.resource_prices=&resource_prices;M.precedence_penalties=&precedence_penalties;
                int local_conflicts=count_internal_conflicts(grp,hard);M.budget=base_budget*(1.0+0.20*std::max(0,local_conflicts-1));ar.budget=M.budget;ar.rho=M.rho;
                auto red=hyper.solve(grp,S,H,true,M);ar.reduced_feasible=red.feasible;ar.reduced_obj=red.obj;ar.reduced_states=red.generated;ar.reduced_trans=red.transitions;ar.screened=red.screened;ar.reduced_wall=red.wall;
                GroupResult full;if(round==0&&(int)grp.size()<=full_limit){full=hyper.solve(grp,S,H,false,M);ar.full_feasible=full.feasible;ar.full_obj=full.obj;ar.full_states=full.generated;ar.full_trans=full.transitions;ar.full_wall=full.wall;}
                auto backup=S;bool accepted=false;
                if(red.feasible){for(int q=0;q<(int)grp.size();q++){S[grp[q]].node=red.paths[q];S[grp[q]].cost=schedule_cost(N,N.agents[grp[q]],red.paths[q]);S[grp[q]].feasible=true;}
                    int after=detect_conflicts(N,S,H,0,false).size();double oldobj=population_objective(N,backup),newobj=population_objective(N,S);
                    accepted=(after<ar.conflicts_before)||(after==ar.conflicts_before&&newobj<oldobj-1e-9);
                    if(!accepted)S=backup;
                }
                ar.accepted=accepted;ar.conflicts_after=detect_conflicts(N,S,H,0,false).size();actions.push_back(ar);
                if(accepted)ir.accepted++;else ir.rejected++;
            }
            ir.hard_after=detect_conflicts(N,S,H,0,false).size();ir.objective_after=population_objective(N,S);ir.price_l1=price_l1();iterations.push_back(ir);
            if(ir.hard_after>=ir.hard_before||ir.accepted==0)break;
        }
    }

    const vector<ControlActionRecord>& action_records()const{return actions;}
    const vector<IterationRecord>& iteration_records()const{return iterations;}
    double price_l1()const{double s=0;for(auto&kv:resource_prices)s+=std::abs(kv.second);return s;}

private:
    const Net&N;int H;double base_budget,rho,radius,cross;int max_group,full_limit,max_rounds;double price_step,precedence_weight;
    HyperTrajectoryAgent hyper;
    std::unordered_map<uint64_t,double>resource_prices,precedence_penalties;
    vector<ControlActionRecord>actions;vector<IterationRecord>iterations;

    void update_resource_prices(const vector<Conflict>&hard){
        for(auto&c:hard)if(c.resource>=0)resource_prices[key_rt(c.resource,c.t)]+=price_step;
    }
    int select_priority(const vector<int>&grp)const{
        int p=grp.front();for(int a:grp){auto&A=N.agents[a],&P=N.agents[p];if(A.deadline<P.deadline||(A.deadline==P.deadline&&A.id<P.id))p=a;}return p;
    }
    void build_precedence_penalties(const vector<int>&grp,const vector<Conflict>&hard,int priority){
        precedence_penalties.clear();std::set<int>G(grp.begin(),grp.end());
        for(auto&c:hard){if(!G.count(c.a)||!G.count(c.b)||c.resource<0)continue;int loser=(c.a==priority?c.b:(c.b==priority?c.a:std::max(c.a,c.b)));
            for(int dt=-1;dt<=1;++dt)if(c.t+dt>=0)precedence_penalties[key_art(loser,c.resource,c.t+dt)]+=precedence_weight;
        }
    }
    int count_internal_conflicts(const vector<int>&grp,const vector<Conflict>&hard)const{
        std::set<int>G(grp.begin(),grp.end());int n=0;for(auto&c:hard)if(G.count(c.a)&&G.count(c.b))n++;return n;
    }
    vector<vector<int>>select_groups(const vector<Conflict>&hard)const{
        auto comps=components(N.agents.size(),hard);vector<vector<int>>groups;std::map<std::pair<int,int>,int>pc;
        for(auto&c:hard){int a=std::min(c.a,c.b),b=std::max(c.a,c.b);pc[{a,b}]++;}
        vector<std::pair<int,std::pair<int,int>>>ranked;for(auto&kv:pc)ranked.push_back({kv.second,kv.first});std::sort(ranked.rbegin(),ranked.rend());
        vector<char>used(N.agents.size(),0);
        for(auto&comp:comps){if(comp.size()<=1)continue;if((int)comp.size()<=max_group){groups.push_back(comp);for(int a:comp)used[a]=1;continue;}
            std::set<int>in(comp.begin(),comp.end());for(auto&rp:ranked){int a=rp.second.first,b=rp.second.second;if(in.count(a)&&in.count(b)&&!used[a]&&!used[b]){groups.push_back({a,b});used[a]=used[b]=1;}}
        }
        return groups;
    }
};

int main(int argc,char**argv){
    if(argc<3){std::cerr<<"usage: coordination_trajectory_solver <instance_dir> <output_dir> [--budget=V] [--rho=V] [--radius=V] [--max-group=3] [--full-limit=3] [--max-rounds=8] [--price-step=.25] [--precedence=.75]\n";return 2;}
    string idir=argv[1],odir=argv[2];double budget=6.0,rho=.18,radius=0,cross=.08,price_step=.25,precedence=.75;int maxg=3,full_limit=3,max_rounds=8;
    for(int i=3;i<argc;i++){string a=argv[i];if(a.rfind("--budget=",0)==0)budget=std::stod(a.substr(9));else if(a.rfind("--rho=",0)==0)rho=std::stod(a.substr(6));else if(a.rfind("--radius=",0)==0)radius=std::stod(a.substr(9));else if(a.rfind("--max-group=",0)==0)maxg=std::stoi(a.substr(12));else if(a.rfind("--full-limit=",0)==0)full_limit=std::stoi(a.substr(13));else if(a.rfind("--max-rounds=",0)==0)max_rounds=std::stoi(a.substr(13));else if(a.rfind("--price-step=",0)==0)price_step=std::stod(a.substr(13));else if(a.rfind("--precedence=",0)==0)precedence=std::stod(a.substr(13));}
    std::system(("mkdir -p '"+odir+"'").c_str());Net N=load_instance(idir);int H=0;for(auto&a:N.agents)H=std::max(H,a.deadline);
    CoordinationAgent control(N,H,budget,rho,radius,cross,maxg,full_limit,max_rounds,price_step,precedence);
    auto S=control.initialize_trajectory_agents();auto initial=detect_conflicts(N,S,H,0,false);double initial_obj=population_objective(N,S);
    control.run(S);auto final=detect_conflicts(N,S,H,0,false);double final_obj=population_objective(N,S);
    auto&A=control.action_records();auto&I=control.iteration_records();
    long long rs=0,rt=0,fs=0,ft=0,screen=0;double rw=0,fw=0,maxgap=0;int accepted=0,rejected=0,exact=0,compared=0;
    for(auto&r:A){rs+=r.reduced_states;rt+=r.reduced_trans;fs+=r.full_states;ft+=r.full_trans;screen+=r.screened;rw+=r.reduced_wall;fw+=r.full_wall;if(r.accepted)accepted++;else rejected++;if(r.full_feasible&&r.reduced_feasible){compared++;double gap=std::abs(r.full_obj-r.reduced_obj);maxgap=std::max(maxgap,gap);if(gap<=1e-8)exact++;}}
    string name=basename_of(idir);
    {
        std::ofstream f(odir+"/summary.csv");
        f<<"instance,n_nodes,n_arcs,n_agents,horizon,initial_conflicts,final_conflicts,initial_objective,final_objective,control_iterations,control_actions,accepted_actions,rejected_actions,resource_price_l1,reduced_states,reduced_transitions,screened_candidates,reduced_wall_s,full_states_sampled,full_transitions_sampled,full_wall_s,full_comparisons,exact_objective_matches,max_objective_gap,budget,rho,price_step,precedence_weight\n";
        f<<name<<','<<N.nodes.size()<<','<<N.arcs.size()<<','<<N.agents.size()<<','<<H<<','<<initial.size()<<','<<final.size()<<','<<initial_obj<<','<<final_obj<<','<<I.size()<<','<<A.size()<<','<<accepted<<','<<rejected<<','<<control.price_l1()<<','<<rs<<','<<rt<<','<<screen<<','<<std::setprecision(10)<<rw<<','<<fs<<','<<ft<<','<<fw<<','<<compared<<','<<exact<<','<<maxgap<<','<<budget<<','<<rho<<','<<price_step<<','<<precedence<<"\n";
    }
    {
        std::ofstream f(odir+"/iterations.csv");f<<"round,hard_conflicts_before,hard_conflicts_after,objective_before,objective_after,active_groups,largest_group,accepted_actions,rejected_actions,resource_price_l1\n";
        for(auto&r:I)f<<r.round<<','<<r.hard_before<<','<<r.hard_after<<','<<r.objective_before<<','<<r.objective_after<<','<<r.active_groups<<','<<r.largest_group<<','<<r.accepted<<','<<r.rejected<<','<<r.price_l1<<"\n";
    }
    {
        std::ofstream f(odir+"/control_actions.csv");
        f<<"round,group_id,agents,group_size,priority_agent,budget,rho,conflicts_before,conflicts_after,accepted,reduced_feasible,full_feasible,reduced_states,reduced_transitions,screened,full_states,full_transitions,reduced_objective,full_objective,reduced_wall_s,full_wall_s\n";
        for(auto&r:A)f<<r.round<<','<<r.group_id<<','<<r.agents<<','<<r.group_size<<','<<r.priority<<','<<r.budget<<','<<r.rho<<','<<r.conflicts_before<<','<<r.conflicts_after<<','<<(r.accepted?1:0)<<','<<(r.reduced_feasible?1:0)<<','<<(r.full_feasible?1:0)<<','<<r.reduced_states<<','<<r.reduced_trans<<','<<r.screened<<','<<r.full_states<<','<<r.full_trans<<','<<r.reduced_obj<<','<<r.full_obj<<','<<r.reduced_wall<<','<<r.full_wall<<"\n";
    }
    {
        std::ofstream f(odir+"/schedules.csv");f<<"agent_id,time,node_id,x,y,resource_id,train_class\n";
        for(auto&a:N.agents)for(int t=0;t<=H;t++){int v=S[a.id].node[t];f<<a.id<<','<<t<<','<<v<<','<<N.nodes[v].x<<','<<N.nodes[v].y<<','<<N.nodes[v].resource<<','<<a.train_class<<"\n";}
    }
    std::cerr<<name<<": agents="<<N.agents.size()<<" initial_conflicts="<<initial.size()<<" final_conflicts="<<final.size()<<" actions="<<A.size()<<" accepted="<<accepted<<" exact="<<exact<<"/"<<compared<<"\n";
    return final.empty()?0:1;
}
