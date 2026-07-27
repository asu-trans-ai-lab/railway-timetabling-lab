// Conflict-localized quadratic joint-state DP for railway timetabling prototypes.
//
// Input directory:
//   nodes.csv, arcs.csv, agents.csv
// Output:
//   summary.csv, schedules.csv, components.csv
//
// The implementation is intentionally self-contained C++17. It supports:
//   * independent shortest-path schedules;
//   * hard cell/resource and edge-swap conflicts;
//   * conflict/coupling interaction graph;
//   * pair/triple synchronous joint-state DP;
//   * quadratic proximal and spacing terms;
//   * hard ellipsoidal corridor filtering;
//   * full-vs-reduced state-count comparison on bounded groups.

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
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
    vector<string> out; string cur; bool quote=false;
    for (char c: line) {
        if (c=='"') quote=!quote;
        else if (c==',' && !quote) { out.push_back(cur); cur.clear(); }
        else cur.push_back(c);
    }
    out.push_back(cur); return out;
}

struct Node { int id=0,x=0,y=0,resource=-1; string kind; };
struct Arc { int id=0,u=0,v=0,resource=-1; double cost=1.0; };
struct Agent {
    int id=0,start=0,goal=0,release=0,preferred=0,deadline=0,interval=1;
    string train_class;
    double wait_cost=.2,tardy=1.0,prox=1.0,spacing=.15,desired=2.0;
};
struct Net {
    vector<Node> nodes; vector<Arc> arcs; vector<vector<int>> out; vector<Agent> agents;
};

static Net load_instance(const string& dir) {
    Net N;
    {
        std::ifstream f(dir+"/nodes.csv"); if(!f) throw std::runtime_error("missing nodes.csv");
        string line; std::getline(f,line);
        while(std::getline(f,line)) { if(line.empty()) continue; auto a=split_csv(line); Node n;
            n.id=std::stoi(a[0]); n.x=std::stoi(a[1]); n.y=std::stoi(a[2]);
            n.resource=std::stoi(a[3]); n.kind=a.size()>4?a[4]:"track";
            if((int)N.nodes.size()<=n.id) N.nodes.resize(n.id+1); N.nodes[n.id]=n;
        }
    }
    N.out.assign(N.nodes.size(),{});
    {
        std::ifstream f(dir+"/arcs.csv"); if(!f) throw std::runtime_error("missing arcs.csv");
        string line; std::getline(f,line);
        while(std::getline(f,line)) { if(line.empty()) continue; auto a=split_csv(line); Arc e;
            e.id=std::stoi(a[0]); e.u=std::stoi(a[1]); e.v=std::stoi(a[2]);
            e.cost=std::stod(a[3]); e.resource=std::stoi(a[4]);
            if((int)N.arcs.size()<=e.id) N.arcs.resize(e.id+1); N.arcs[e.id]=e; N.out[e.u].push_back(e.id);
        }
    }
    {
        std::ifstream f(dir+"/agents.csv"); if(!f) throw std::runtime_error("missing agents.csv");
        string line; std::getline(f,line);
        while(std::getline(f,line)) { if(line.empty()) continue; auto a=split_csv(line); Agent k;
            k.id=std::stoi(a[0]); k.start=std::stoi(a[1]); k.goal=std::stoi(a[2]);
            k.release=std::stoi(a[3]); k.preferred=std::stoi(a[4]); k.deadline=std::stoi(a[5]);
            k.interval=std::max(1,std::stoi(a[6])); k.train_class=a[7]; k.wait_cost=std::stod(a[8]);
            k.tardy=std::stod(a[9]); k.prox=std::stod(a[10]); k.spacing=std::stod(a[11]);
            k.desired=std::stod(a[12]);
            if((int)N.agents.size()<=k.id) N.agents.resize(k.id+1); N.agents[k.id]=k;
        }
    }
    return N;
}

static vector<int> bfs_dist(const Net& N, int goal) {
    vector<vector<int>> rev(N.nodes.size());
    for(auto&e:N.arcs) rev[e.v].push_back(e.u);
    vector<int>d(N.nodes.size(),1e9); std::queue<int>q; d[goal]=0;q.push(goal);
    while(!q.empty()){int v=q.front();q.pop();for(int u:rev[v])if(d[u]>d[v]+1){d[u]=d[v]+1;q.push(u);}}
    return d;
}

static vector<int> shortest_path(const Net& N, const Agent& A) {
    vector<double>d(N.nodes.size(),INF); vector<int>par(N.nodes.size(),-1),pare(N.nodes.size(),-1);
    using P=std::pair<double,int>; std::priority_queue<P,vector<P>,std::greater<P>>pq;
    d[A.start]=0;pq.push({0,A.start});
    while(!pq.empty()){auto [du,u]=pq.top();pq.pop(); if(du!=d[u])continue; if(u==A.goal)break;
        for(int eid:N.out[u]){auto&e=N.arcs[eid]; double nd=du+e.cost; if(nd<d[e.v]-1e-12){d[e.v]=nd;par[e.v]=u;pare[e.v]=eid;pq.push({nd,e.v});}}
    }
    if(!std::isfinite(d[A.goal])) return {};
    vector<int>p; for(int v=A.goal;v!=-1;v=par[v])p.push_back(v); std::reverse(p.begin(),p.end()); return p;
}

struct Schedule { vector<int> node; double cost=0; bool feasible=true; };

static Schedule independent_schedule(const Net& N, const Agent&A, int H) {
    Schedule S; S.node.assign(H+1,A.start); auto p=shortest_path(N,A); if(p.empty()){S.feasible=false;return S;}
    int idx=0,cool=0;
    for(int t=0;t<H;t++){
        int cur=S.node[t], nxt=cur;
        if(cur==A.goal) nxt=cur;
        else if(t<A.release) nxt=cur;
        else if(cool>0){nxt=cur;cool--;}
        else if(idx+1<(int)p.size()){idx++;nxt=p[idx];cool=A.interval-1;}
        S.node[t+1]=nxt;
        S.cost += (nxt==cur ? (cur==A.goal?0:A.wait_cost) : 1.0);
        if(t+1>A.preferred && nxt!=A.goal) S.cost += A.tardy;
    }
    if(S.node[H]!=A.goal) S.feasible=false; return S;
}

struct Conflict { int a=-1,b=-1,t=-1,type=0; }; // 1 node/resource, 2 swap, 3 near-coupling
static vector<Conflict> detect_conflicts(const Net&N,const vector<Schedule>&S,int H,double radius,bool include_near){
    vector<Conflict>C;
    for(int i=0;i<(int)S.size();i++)for(int j=i+1;j<(int)S.size();j++){
        for(int t=0;t<=H;t++){
            int u=S[i].node[t],v=S[j].node[t]; bool hard=(u==v);
            int ru=N.nodes[u].resource,rv=N.nodes[v].resource;
            if(ru>=0 && ru==rv) hard=true;
            if(hard){C.push_back({i,j,t,1});continue;}
            if(t<H && S[i].node[t]==S[j].node[t+1] && S[j].node[t]==S[i].node[t+1]){
                C.push_back({i,j,t,2});continue;
            }
            if(include_near){double dx=N.nodes[u].x-N.nodes[v].x,dy=N.nodes[u].y-N.nodes[v].y;
                if(std::sqrt(dx*dx+dy*dy)<=radius) C.push_back({i,j,t,3});}
        }
    }
    return C;
}

struct DSU{vector<int>p;DSU(int n):p(n){std::iota(p.begin(),p.end(),0);}int f(int x){return p[x]==x?x:p[x]=f(p[x]);}void u(int a,int b){a=f(a);b=f(b);if(a!=b)p[b]=a;}};
static vector<vector<int>> components(int n,const vector<Conflict>&C){DSU d(n);for(auto&c:C)d.u(c.a,c.b);std::map<int,vector<int>>m;for(int i=0;i<n;i++)m[d.f(i)].push_back(i);vector<vector<int>>o;for(auto&kv:m)o.push_back(kv.second);return o;}

struct JState {
    array<int,MAXG> prev{},node{},cool{}; int g=0;
    bool operator==(JState const&o)const{return g==o.g&&prev==o.prev&&node==o.node&&cool==o.cool;}
};
struct JHash{size_t operator()(JState const&s)const noexcept{size_t h=s.g;for(int i=0;i<s.g;i++){h=h*1000003u+(unsigned)(s.prev[i]+2);h=h*1000003u+(unsigned)(s.node[i]+1);h=h*97u+(unsigned)s.cool[i];}return h;}};
struct Label {double cost=INF; int pred=-1; JState st;};
struct GroupResult {bool feasible=false;double obj=INF;long long generated=0,transitions=0,screened=0;vector<vector<int>>paths;double wall=0;};
struct Move {int prev,node,cool;double cost;bool moved;};

static vector<Move> moves_for(const Net&N,const Agent&A,const JState&s,int i,int t,const vector<int>&dist){
    vector<Move>m; int cur=s.node[i],prev=s.prev[i],cool=s.cool[i];
    if(cur==A.goal){m.push_back({prev,cur,0,0,false});return m;}
    if(t<A.release){m.push_back({prev,cur,cool,A.wait_cost,false});return m;}
    if(cool>0){m.push_back({prev,cur,cool-1,A.wait_cost,false});return m;}
    m.push_back({prev,cur,0,A.wait_cost,false});
    for(int eid:N.out[cur]){auto&e=N.arcs[eid];
        bool dead=N.out[cur].size()==1; if(e.v==prev && !dead)continue;
        if(dist[e.v]>=1000000000)continue;
        // Search the shortest-path DAG. Equal-length route alternatives are retained
        // through different outgoing arcs, but moves that increase distance-to-go are
        // excluded. This is the network-timetabling analogue of Flatland's route
        // predictor and prevents irrelevant cycling in large switch grids.
        if(dist[e.v] >= dist[cur]) continue;
        // Lower-bound arrival pruning with train speed interval.
        if(t+1 + dist[e.v]*A.interval > A.deadline)continue;
        m.push_back({cur,e.v,A.interval-1,e.cost,true});
    }
    return m;
}

static bool hard_ok(const Net&N,const JState&old,const array<Move,MAXG>&mv,int g){
    for(int i=0;i<g;i++)for(int j=i+1;j<g;j++){
        int ni=mv[i].node,nj=mv[j].node;
        if(ni==nj)return false;
        int ri=N.nodes[ni].resource,rj=N.nodes[nj].resource;
        if(ri>=0 && ri==rj)return false;
        if(old.node[i]==nj && old.node[j]==ni && ni!=nj)return false;
    }
    return true;
}

static double dist2xy(const Net&N,int u,int v){double dx=N.nodes[u].x-N.nodes[v].x,dy=N.nodes[u].y-N.nodes[v].y;return dx*dx+dy*dy;}

static GroupResult solve_group(const Net&N,const vector<int>&ids,const vector<Schedule>&ref,int H,
                               bool corridor,double budget,double rho,double cross_weight){
    auto tic=std::chrono::steady_clock::now(); GroupResult R; int g=ids.size(); if(g<1||g>MAXG)return R;
    vector<vector<int>> dists(g); for(int i=0;i<g;i++)dists[i]=bfs_dist(N,N.agents[ids[i]].goal);
    vector<vector<Label>> layers(H+1); vector<std::unordered_map<JState,int,JHash>> idx(H+1);
    JState st;st.g=g;for(int i=0;i<g;i++){st.prev[i]=-1;st.node[i]=N.agents[ids[i]].start;st.cool[i]=0;}
    layers[0].push_back({0,-1,st});idx[0][st]=0;R.generated=1;
    for(int t=0;t<H;t++){
        for(int li=0;li<(int)layers[t].size();li++){
            auto &lab=layers[t][li]; array<vector<Move>,MAXG> opts;
            for(int i=0;i<g;i++) opts[i]=moves_for(N,N.agents[ids[i]],lab.st,i,t,dists[i]);
            array<Move,MAXG> choice;
            std::function<void(int)> rec=[&](int q){
                if(q<g){for(auto&m:opts[q]){choice[q]=m;rec(q+1);}return;}
                R.transitions++;
                if(!hard_ok(N,lab.st,choice,g))return;
                JState ns;ns.g=g;double add=0,prox=0;
                for(int i=0;i<g;i++){
                    ns.prev[i]=choice[i].prev;ns.node[i]=choice[i].node;ns.cool[i]=choice[i].cool; add+=choice[i].cost;
                    auto&A=N.agents[ids[i]]; if(t+1>A.preferred && ns.node[i]!=A.goal)add+=A.tardy;
                    int rr=ref[ids[i]].node[std::min(t+1,(int)ref[ids[i]].node.size()-1)];
                    prox += A.prox*dist2xy(N,ns.node[i],rr);
                }
                if(corridor && rho*prox>budget+1e-12){R.screened++;return;}
                add += .5*rho*prox;
                for(int i=0;i<g;i++)for(int j=i+1;j<g;j++){
                    auto&A=N.agents[ids[i]];auto&B=N.agents[ids[j]];
                    double dx=N.nodes[ns.node[i]].x-N.nodes[ns.node[j]].x;
                    double dy=N.nodes[ns.node[i]].y-N.nodes[ns.node[j]].y;
                    double dd=std::sqrt(dx*dx+dy*dy);double des=.5*(A.desired+B.desired);
                    double w=.5*(A.spacing+B.spacing);double shortfall=std::max(0.0,des-dd);
                    add += .5*w*shortfall*shortfall;
                    double dm=(choice[i].moved?1.0:0.0)-(choice[j].moved?1.0:0.0);
                    add += .5*cross_weight*dm*dm;
                }
                double nc=lab.cost+add;auto it=idx[t+1].find(ns);
                if(it==idx[t+1].end()){int ni=layers[t+1].size();idx[t+1][ns]=ni;layers[t+1].push_back({nc,li,ns});R.generated++;}
                else if(nc<layers[t+1][it->second].cost-1e-12){layers[t+1][it->second].cost=nc;layers[t+1][it->second].pred=li;}
            };rec(0);
        }
        if(layers[t+1].empty())break;
    }
    int best=-1;double bc=INF;for(int i=0;i<(int)layers[H].size();i++){bool ok=true;for(int q=0;q<g;q++)if(layers[H][i].st.node[q]!=N.agents[ids[q]].goal)ok=false;if(ok&&layers[H][i].cost<bc){bc=layers[H][i].cost;best=i;}}
    if(best>=0){R.feasible=true;R.obj=bc;R.paths.assign(g,vector<int>(H+1));int cur=best;for(int t=H;t>=0;t--){auto&L=layers[t][cur];for(int q=0;q<g;q++)R.paths[q][t]=L.st.node[q];cur=L.pred;}}
    R.wall=std::chrono::duration<double>(std::chrono::steady_clock::now()-tic).count();return R;
}

static vector<vector<int>> partition_component(const vector<int>&comp,int maxg){vector<vector<int>>o;for(size_t i=0;i<comp.size();i+=maxg)o.emplace_back(comp.begin()+i,comp.begin()+std::min(comp.size(),i+maxg));return o;}

static string basename_of(string p){while(!p.empty()&&(p.back()=='/'||p.back()=='\\'))p.pop_back();auto q=p.find_last_of("/\\");return q==string::npos?p:p.substr(q+1);}

int main(int argc,char**argv){
    if(argc<3){std::cerr<<"usage: joint_timetable_solver <instance_dir> <output_dir> [--budget=V] [--rho=V] [--radius=V] [--max-group=3] [--full-limit=2] [--max-rounds=8]\n";return 2;}
    string idir=argv[1],odir=argv[2];double budget=18.0,rho=.18,radius=1.5,cross=.08;int maxg=3,full_limit=2,max_rounds=8;
    for(int i=3;i<argc;i++){string a=argv[i];if(a.rfind("--budget=",0)==0)budget=std::stod(a.substr(9));else if(a.rfind("--rho=",0)==0)rho=std::stod(a.substr(6));else if(a.rfind("--radius=",0)==0)radius=std::stod(a.substr(9));else if(a.rfind("--max-group=",0)==0)maxg=std::stoi(a.substr(12));else if(a.rfind("--full-limit=",0)==0)full_limit=std::stoi(a.substr(13));else if(a.rfind("--max-rounds=",0)==0)max_rounds=std::stoi(a.substr(13));}
    std::system(("mkdir -p '"+odir+"'").c_str());Net N=load_instance(idir);int H=0;for(auto&a:N.agents)H=std::max(H,a.deadline);
    vector<Schedule>S(N.agents.size());for(auto&a:N.agents)S[a.id]=independent_schedule(N,a,H);
    auto initHard=detect_conflicts(N,S,H,0,false);
    auto initialInteractions=detect_conflicts(N,S,H,radius,true);
    auto initialComps=components(N.agents.size(),initialInteractions);
    long long total_red_states=0,total_red_trans=0,total_full_states=0,total_full_trans=0,total_screen=0;
    double red_wall=0,full_wall=0;int solved_groups=0,failed_groups=0;
    struct Grow{int round,gid,size;bool redok,fullok;long long rs,rt,fs,ft,screen;double robj,fobj,rwall,fwall;};
    vector<Grow> grows; int gid=0;

    // Iterative conflict-localized repair. Small connected components are solved
    // jointly. Large components are decomposed into disjoint highest-conflict
    // pairs for the current round and reconsidered after schedules are updated.
    for(int round=0; round<max_rounds; ++round){
        auto hard=detect_conflicts(N,S,H,0,false);
        if(hard.empty()) break;
        std::map<std::pair<int,int>,int> pc;
        for(auto&c:hard){int a=std::min(c.a,c.b),b=std::max(c.a,c.b);pc[{a,b}]++;}
        vector<std::pair<int,std::pair<int,int>>> ranked;
        for(auto&kv:pc)ranked.push_back({kv.second,kv.first});
        std::sort(ranked.rbegin(),ranked.rend());
        auto hc=components(N.agents.size(),hard);
        vector<vector<int>> groups; vector<char> used(N.agents.size(),0);
        for(auto&comp:hc){
            if(comp.size()<=1) continue;
            if((int)comp.size()<=maxg){groups.push_back(comp);for(int a:comp)used[a]=1;continue;}
            std::set<int> inset(comp.begin(),comp.end());
            for(auto&rp:ranked){int a=rp.second.first,b=rp.second.second;
                if(inset.count(a)&&inset.count(b)&&!used[a]&&!used[b]){groups.push_back({a,b});used[a]=used[b]=1;}
            }
        }
        if(groups.empty()) break;
        int before=hard.size();
        for(auto&grp:groups){
            auto red=solve_group(N,grp,S,H,true,budget,rho,cross);GroupResult full;
            if(round==0 && (int)grp.size()<=full_limit) full=solve_group(N,grp,S,H,false,budget,rho,cross);
            grows.push_back({round,gid++,(int)grp.size(),red.feasible,full.feasible,red.generated,red.transitions,full.generated,full.transitions,red.screened,red.obj,full.obj,red.wall,full.wall});
            total_red_states+=red.generated;total_red_trans+=red.transitions;total_screen+=red.screened;red_wall+=red.wall;
            total_full_states+=full.generated;total_full_trans+=full.transitions;full_wall+=full.wall;
            if(red.feasible){solved_groups++;for(int q=0;q<(int)grp.size();q++){S[grp[q]].node=red.paths[q];S[grp[q]].feasible=true;}}
            else failed_groups++;
        }
        auto after=detect_conflicts(N,S,H,0,false);
        if((int)after.size()>=before) break;
    }
    auto finalHard=detect_conflicts(N,S,H,0,false);
    string name=basename_of(idir);
    {
        std::ofstream f(odir+"/summary.csv");
        f<<"instance,n_nodes,n_arcs,n_agents,horizon,initial_hard_conflicts,final_hard_conflicts,interaction_components,solved_joint_groups,failed_joint_groups,reduced_states,reduced_transitions,screened_candidates,reduced_wall_s,full_states_sampled,full_transitions_sampled,full_wall_s,budget,rho,coupling_radius\n";
        f<<name<<','<<N.nodes.size()<<','<<N.arcs.size()<<','<<N.agents.size()<<','<<H<<','<<initHard.size()<<','<<finalHard.size()<<','<<initialComps.size()<<','<<solved_groups<<','<<failed_groups<<','<<total_red_states<<','<<total_red_trans<<','<<total_screen<<','<<std::setprecision(9)<<red_wall<<','<<total_full_states<<','<<total_full_trans<<','<<full_wall<<','<<budget<<','<<rho<<','<<radius<<"\n";
    }
    {
        std::ofstream f(odir+"/components.csv");
        f<<"round,group_id,group_size,reduced_feasible,full_feasible,reduced_states,reduced_transitions,full_states,full_transitions,screened,reduced_objective,full_objective,reduced_wall_s,full_wall_s\n";
        for(auto&r:grows)f<<r.round<<','<<r.gid<<','<<r.size<<','<<(r.redok?1:0)<<','<<(r.fullok?1:0)<<','<<r.rs<<','<<r.rt<<','<<r.fs<<','<<r.ft<<','<<r.screen<<','<<r.robj<<','<<r.fobj<<','<<r.rwall<<','<<r.fwall<<"\n";
    }
    {
        std::ofstream f(odir+"/schedules.csv");f<<"agent_id,time,node_id,x,y,train_class\n";
        for(auto&a:N.agents)for(int t=0;t<=H;t++){int v=S[a.id].node[t];f<<a.id<<','<<t<<','<<v<<','<<N.nodes[v].x<<','<<N.nodes[v].y<<','<<a.train_class<<"\n";}
    }
    std::cerr<<name<<": agents="<<N.agents.size()<<" initial_conflicts="<<initHard.size()<<" final_conflicts="<<finalHard.size()<<" red_states="<<total_red_states<<" wall="<<red_wall<<"s\n";
    return 0;
}
