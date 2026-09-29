#include "model.hpp"
#include <deque>
#include <functional>
#include <memory>
#include <numeric>
#include <queue>
#include <set>
#include <unordered_map>
#include <unordered_set>

namespace bottleneck { namespace {
using Clock=std::chrono::steady_clock;
double elapsed(Clock::time_point t){return std::chrono::duration<double>(Clock::now()-t).count();}
struct Expr {int v;Time offset;};
struct Edge {int u,v;Time w;bool operator<(const Edge& e)const{return std::tie(u,v,w)<std::tie(e.u,e.v,e.w);} };
struct Occupation {int train,resource;Expr a,b;bool holding;};
struct Conflict {int resource;Time at;std::vector<Occupation> tasks;};
struct Graph {
    int count=1;
    std::vector<Edge> base;
    std::vector<std::vector<Expr>> entries;
    std::vector<std::vector<Occupation>> resources;
    std::vector<std::vector<int>> regions;
    explicit Graph(const Model& m,bool macro):entries(m.trains.size()),resources(2*m.sections+1){
        // Exact elimination of no-wait chain variables. This is the run-block
        // foundation of idp_exact.cpp, extended to retain finite holding resources.
        for(size_t i=0;i<m.trains.size();++i){const auto& t=m.trains[i];Expr prev{0,0};int previous_seg=-1;
            for(int k=0;k<m.sections;++k){int seg=t.direction==1?k:m.sections-1-k;
                int node=t.direction==1?seg:seg+1;Expr a;
                if(k && macro && !m.berths[node])a={prev.v,prev.offset+t.run[previous_seg]};
                else {a={count++,0};if(!k)base.push_back({0,a.v,t.release});
                    else {base.push_back({prev.v,a.v,prev.offset+t.run[previous_seg]});
                        if(!m.berths[node])base.push_back({a.v,prev.v,-prev.offset-t.run[previous_seg]});}}
                entries[i].push_back(a);
                resources[seg].push_back({int(i),seg,a,{a.v,a.offset+t.run[seg]+m.headway},false});
                if(k && m.berths[node])resources[m.sections+node].push_back({int(i),m.sections+node,
                    {prev.v,prev.offset+t.run[previous_seg]},a,true});
                prev=a;previous_seg=seg;
            }
        }
        std::vector<int> block;
        for(int s=0;s<m.sections;++s){block.push_back(s);if(s==m.sections-1 || m.berths[s+1]){regions.push_back(block);block.clear();}}
    }
    Schedule expand(const Model& m,const std::vector<Time>& x)const{
        Schedule out(m.trains.size());for(size_t i=0;i<m.trains.size();++i)for(int k=0;k<m.sections;++k){
            int s=m.trains[i].direction==1?k:m.sections-1-k;auto a=entries[i][k];Time t=x[a.v]+a.offset;
            out[i].push_back({int(i),k,s,t,t+m.trains[i].run[s]});}return out;
    }
    Time cost(const Model& m,const std::vector<Time>& x)const{
        Time result=0;for(size_t i=0;i<m.trains.size();++i){auto a=entries[i].back();int seg=m.trains[i].direction==1?m.sections-1:0;
            result+=x[a.v]+a.offset+m.trains[i].run[seg]-m.trains[i].release-running(m.trains[i]);}return result;
    }
};
Time value(Expr e,const std::vector<Time>& x){return x[e.v]+e.offset;}

// Bellman longest-path DP on temporal difference constraints. Every variable is
// reachable from its release. A change on pass |V| proves a positive cycle.
bool evaluate(const Graph& g,const std::vector<Edge>& extra,std::vector<Time>& x,Time horizon,Metrics& m){
    if(x.empty())x.assign(g.count,0);
    for(int pass=0;pass<g.count;++pass){bool changed=false;
        auto scan=[&](const std::vector<Edge>& edges){for(auto e:edges){++m.relax_attempts;
            Time proposed=x[e.u]+e.w;if(proposed>x[e.v]){x[e.v]=proposed;++m.label_updates;changed=true;}}};
        scan(g.base);scan(extra);
        if(*std::max_element(x.begin(),x.end())>horizon)return false;
        if(!changed)return true;
    }return false;
}
int capacity(const Model& m,int r){return r<m.sections?m.tracks[r]:m.berths[r-m.sections];}

// Macro kernel: local feasibility query on a joint entrance-time signature.
// All interior times are determined by that signature, so F_b is exactly 0 or
// infinity. Boundary holding is a separate factor and never disappears.
Conflict local_sweep(const Model& m,const Graph& g,int r,const std::vector<Time>& x){
    std::vector<std::pair<Time,int>> starts;
    for(size_t k=0;k<g.resources[r].size();++k){auto o=g.resources[r][k];if(value(o.a,x)<value(o.b,x))starts.push_back({value(o.a,x),int(k)});}
    std::sort(starts.begin(),starts.end());
    for(auto [t,k]:starts){(void)k;std::vector<Occupation> active;
        for(auto o:g.resources[r])if(value(o.a,x)<=t && t<value(o.b,x))active.push_back(o);
        if(int(active.size())>capacity(m,r)){active.resize(capacity(m,r)+1);return {r,t,active};}}
    return {-1,0,{}};
}
// Uncontracted oracle uses direct subset intersections, not the macro sweep.
Conflict oracle_intersection(const Model& m,const Graph& g,int r,const std::vector<Time>& x){
    const auto& all=g.resources[r];int need=capacity(m,r)+1;Conflict best{-1,0,{}};std::vector<Occupation> group;
    std::function<void(int)> visit=[&](int at){if(int(group.size())==need){Time lo=0,hi=INT64_MAX;
            for(auto o:group){lo=std::max(lo,value(o.a,x));hi=std::min(hi,value(o.b,x));}
            if(lo<hi && (best.resource<0 || lo<best.at))best={r,lo,group};return;}
        for(int k=at;k<=int(all.size())-(need-int(group.size()));++k){auto o=all[k];
            if(value(o.a,x)>=value(o.b,x))continue;group.push_back(o);visit(k+1);group.pop_back();}};
    if(need>0 && need<=int(all.size()))visit(0);return best;
}
struct Library {
    const Model& model;const Graph& graph;bool enabled;
    std::unordered_map<std::string,Conflict> table;
    Library(const Model& m,const Graph& g,bool e):model(m),graph(g),enabled(e){}
    Conflict query(int region,const std::vector<Time>& x,Metrics& stats){
        // Cache is instance-local: model, capacities, runtimes, region partition
        // are immutable. Exact string key includes all boundary movement times.
        std::string key=std::to_string(region);
        for(int r:graph.regions[region])for(auto o:graph.resources[r])key+='|'+std::to_string(value(o.a,x));
        if(enabled){auto found=table.find(key);if(found!=table.end()){++stats.cache_hits;return found->second;}}
        ++stats.cache_misses;Conflict best{-1,0,{}};
        for(int r:graph.regions[region]){auto c=local_sweep(model,graph,r,x);if(c.resource>=0 && (best.resource<0 || std::tie(c.at,c.resource)<std::tie(best.at,best.resource)))best=c;}
        // Eviction changes reuse only; it never removes a feasible action.
        if(enabled){if(table.size()>=100000)table.clear();table.emplace(std::move(key),best);}
        stats.cache_entries=std::max<uint64_t>(stats.cache_entries,table.size());return best;
    }
};
Conflict detect(const Model& m,const Graph& g,const std::vector<Time>& x,bool macro,Library& lib,Metrics& stats){
    Conflict best{-1,0,{}};auto take=[&](Conflict c){if(c.resource>=0 && (best.resource<0 || std::tie(c.at,c.resource)<std::tie(best.at,best.resource)))best=c;};
    if(macro){for(size_t b=0;b<g.regions.size();++b)take(lib.query(int(b),x,stats));
        for(int j=1;j<m.sections;++j)if(m.berths[j])take(local_sweep(m,g,m.sections+j,x));}
    else for(size_t r=0;r<g.resources.size();++r)if(!g.resources[r].empty())take(oracle_intersection(m,g,int(r),x));
    return best;
}
std::vector<Edge> branches(const Conflict& c){
    std::vector<Edge> out;
    // For C+1 positive half-open intervals to stop sharing a point, at least one
    // ordered pair must separate. A holding interval can also become EMPTY.
    // Omitting that alternative incorrectly treats zero-duration dwell as a job.
    for(auto a:c.tasks)for(auto b:c.tasks)if(a.train!=b.train)out.push_back({a.b.v,b.a.v,a.b.offset-b.a.offset});
    for(auto a:c.tasks)if(a.holding)out.push_back({a.b.v,a.a.v,a.b.offset-a.a.offset});
    std::sort(out.begin(),out.end());out.erase(std::unique(out.begin(),out.end(),[](auto a,auto b){return a.u==b.u&&a.v==b.v&&a.w==b.w;}),out.end());return out;
}
bool append(std::vector<Edge>& edges,Edge e){
    for(auto& a:edges)if(a.u==e.u && a.v==e.v){if(a.w>=e.w)return false;a.w=e.w;std::sort(edges.begin(),edges.end());return true;}
    edges.push_back(e);std::sort(edges.begin(),edges.end());return true;
}
std::string identity(const std::vector<Edge>& es){std::string s;for(auto e:es)s+=std::to_string(e.u)+","+std::to_string(e.v)+","+std::to_string(e.w)+";";return s;}
struct Node {std::vector<Edge> edges;std::vector<Time> times;Time lb;uint64_t id;int depth;};
struct Order {bool operator()(const std::shared_ptr<Node>& a,const std::shared_ptr<Node>& b)const{
    if(a->lb!=b->lb)return a->lb>b->lb;if(a->depth!=b->depth)return a->depth<b->depth;return a->id>b->id;}};
} // namespace

Metrics solve(const Model& m,const Options& opt){
    Metrics result;auto setup=Clock::now();Graph graph(m,opt.macro);Library library(m,graph,opt.cache);
    std::vector<int> order(m.trains.size());std::iota(order.begin(),order.end(),0);
    std::stable_sort(order.begin(),order.end(),[&](int a,int b){return m.trains[a].release<m.trains[b].release;});
    auto seed=serial_seed(m,order);auto check=validate(m,seed);if(!check.ok)throw std::runtime_error("serial seed failed validation");
    result.ub=check.wait;result.best=seed;
    // Tighten the feasible seed by serializing direction groups in both orders.
    for(int direction:{1,-1}){std::stable_sort(order.begin(),order.end(),[&](int a,int b){
        if(m.trains[a].direction!=m.trains[b].direction)return m.trains[a].direction==direction;
        return m.trains[a].release<m.trains[b].release;});auto candidate=serial_seed(m,order);auto v=validate(m,candidate);
        if(v.ok && v.wait<result.ub){result.ub=v.wait;result.best=candidate;}}
    for(auto& tr:m.trains)result.horizon=std::max(result.horizon,tr.release+running(tr)+result.ub);
    std::priority_queue<std::shared_ptr<Node>,std::vector<std::shared_ptr<Node>>,Order> queue;
    auto root=std::make_shared<Node>();root->lb=0;root->id=0;root->depth=0;
    if(!evaluate(graph,{},root->times,result.horizon,result))throw std::runtime_error("unconstrained physical chain failed");
    root->lb=graph.cost(m,root->times);queue.push(root);result.generated=1;
    // Validated no-stop fleet seeds: preserve a common train order on all
    // tracks, but permit following trains to pipeline across different sections.
    // These extra equalities/orderings are NEVER inherited by the proof root.
    for(int direction:{1,-1})for(int slow_first:{0,1}){
        std::iota(order.begin(),order.end(),0);
        std::stable_sort(order.begin(),order.end(),[&](int a,int b){
            if(m.trains[a].direction!=m.trains[b].direction)return m.trains[a].direction==direction;
            if(slow_first && running(m.trains[a])!=running(m.trains[b]))return running(m.trains[a])>running(m.trains[b]);
            return m.trains[a].release<m.trains[b].release;});
        std::vector<Edge> seed_edges;
        for(int r=0;r<m.sections;++r)for(size_t k=1;k<order.size();++k){auto a=graph.resources[r][order[k-1]],b=graph.resources[r][order[k]];
            append(seed_edges,{a.b.v,b.a.v,a.b.offset-b.a.offset});}
        for(int j=1;j<m.sections;++j)for(auto a:graph.resources[m.sections+j])append(seed_edges,{a.b.v,a.a.v,a.b.offset-a.a.offset});
        std::vector<Time> times;if(evaluate(graph,seed_edges,times,result.horizon,result)){
            auto candidate=graph.expand(m,times);auto valid=validate(m,candidate);
            if(valid.ok && valid.wait<result.ub){result.ub=valid.wait;result.best=std::move(candidate);}}
    }
    // Deterministic primal-only dive. Discarding alternatives here never prunes
    // the proof frontier, which still starts at the unrestricted root below.
    for(int tie=0;tie<2 && elapsed(setup)<5;++tie){Node current=*root;
        for(int step=0;step<2000 && elapsed(setup)<5;++step){auto c=detect(m,graph,current.times,opt.macro,library,result);
            if(c.resource<0){if(current.lb<result.ub){result.ub=current.lb;result.best=graph.expand(m,current.times);}break;}
            bool found=false;Node next;auto options=branches(c);if(tie)std::reverse(options.begin(),options.end());
            for(auto e:options){Node trial;trial.edges=current.edges;if(!append(trial.edges,e))continue;
                if(opt.macro)trial.times=current.times;
                if(!evaluate(graph,trial.edges,trial.times,result.horizon,result))continue;
                trial.lb=graph.cost(m,trial.times);if(trial.lb>=result.ub)continue;
                if(!found || trial.lb<next.lb){next=std::move(trial);found=true;}}
            if(!found)break;current=std::move(next);
        }
    }
    std::unordered_set<std::string> visited;visited.insert("");result.precompute_seconds=elapsed(setup);
    auto start=Clock::now();bool stopped=false;
    while(!queue.empty()){
        if(queue.top()->lb>=result.ub)break;
        if((opt.seconds>0 && elapsed(start)>=opt.seconds) || (opt.max_nodes && result.generated>=opt.max_nodes)){stopped=true;break;}
        auto node=queue.top();queue.pop();++result.expanded;
        auto conflict=detect(m,graph,node->times,opt.macro,library,result);
        if(conflict.resource<0){result.ub=node->lb;result.best=graph.expand(m,node->times);continue;}
        for(auto e:branches(conflict)){
            auto child=std::make_shared<Node>();child->edges=node->edges;if(!append(child->edges,e))throw std::runtime_error("non-covering branch: no new restriction");
            if(!visited.insert(identity(child->edges)).second)continue;
            child->id=result.generated++;child->depth=node->depth+1;
            // Macro warm-start is valid: child only adds lower-bound constraints.
            // Oracle starts all physical variables from zero, independently.
            if(opt.macro)child->times=node->times;
            if(!evaluate(graph,child->edges,child->times,result.horizon,result)){++result.infeasible;continue;}
            child->lb=graph.cost(m,child->times);
            if(child->lb<node->lb)throw std::runtime_error("non-monotone lower bound");
            if(child->lb>=result.ub){++result.bound_pruned;continue;}queue.push(std::move(child));
        }
        result.peak_frontier=std::max<uint64_t>(result.peak_frontier,queue.size());
    }
    result.search_seconds=elapsed(start);result.lb=queue.empty()?result.ub:std::min(result.ub,queue.top()->lb);
    result.status=result.lb==result.ub?"PROVEN_OPTIMAL":(stopped && opt.max_nodes && result.generated>=opt.max_nodes?"NODE_LIMIT":"TIME_LIMIT");
    auto vt=Clock::now();auto final=validate(m,result.best);result.validation_seconds=elapsed(vt);
    if(!final.ok || final.wait!=result.ub)throw std::runtime_error("independent final validation failed");return result;
}

void write_metrics(const Model& m,const Options& o,const Metrics& r,const std::string& path){
    std::ofstream f(path);if(!f)throw std::runtime_error("cannot write metrics");
    f<<"key\tvalue\nmodel\tFINITE_BERTH_FIXED_ROUTE_INTEGER_V1\nname\t"<<m.name<<"\nengine\t"<<(o.macro?"MACRO_DP_BB":"UNCONTRACTED_BELLMAN_BB")
     <<"\nstatus\t"<<r.status<<"\nobjective\tTOTAL_ORIGIN_AND_ENROUTE_WAIT\nLB\t"<<r.lb<<"\nUB\t"<<r.ub
     <<"\ngap_percent\t"<<(r.ub?100.0*(r.ub-r.lb)/r.ub:0)<<"\nhorizon\t"<<r.horizon
     <<"\nprecompute_seconds\t"<<r.precompute_seconds<<"\nsearch_seconds\t"<<r.search_seconds<<"\nvalidation_seconds\t"<<r.validation_seconds
     <<"\ntotal_seconds\t"<<r.precompute_seconds+r.search_seconds+r.validation_seconds
     <<"\nnodes_generated\t"<<r.generated<<"\nnodes_expanded\t"<<r.expanded<<"\nbound_pruned\t"<<r.bound_pruned
     <<"\ninfeasible_nodes\t"<<r.infeasible<<"\nrelax_attempts\t"<<r.relax_attempts<<"\nlabel_updates\t"<<r.label_updates
     <<"\ncache_hits\t"<<r.cache_hits<<"\ncache_misses\t"<<r.cache_misses<<"\npeak_cache_entries\t"<<r.cache_entries
     <<"\npeak_frontier\t"<<r.peak_frontier<<"\nvalidation\tPASS\nthreads\t1\ncache\t"<<o.cache
     <<"\npattern_domain\tCOMPLETE_IMPLICIT_BOUNDARY_DOMAIN\nmaterialized_patterns\tINCUMBENT_ONLY\nproof_scope\tFULL_DECLARED_MODEL_NOT_REAL_OPERATIONS\n";
}
void write_patterns(const Model& m,const Schedule& s,const std::string& path){
    std::ofstream f(path);f<<"region\ttrain\tdirection\tentry\texit\tinternal_wait\tpattern_status\n";
    int region=0,start=0;for(int end=1;end<=m.sections;++end)if(end==m.sections || m.berths[end]){
        for(size_t i=0;i<m.trains.size();++i){Time a=INT64_MAX,b=0;for(auto x:s[i])if(x.segment>=start && x.segment<end){a=std::min(a,x.start);b=std::max(b,x.end);}
            f<<region<<'\t'<<m.trains[i].id<<'\t'<<m.trains[i].direction<<'\t'<<a<<'\t'<<b<<"\t0\tJOINT_INCUMBENT_FEASIBLE\n";}
        start=end;++region;}
}
void write_templates(const Model& m,Time horizon,const std::string& path){
    std::ofstream f(path);f<<"region\ttrain\tdirection\tsegment\tentry_offset\texit_offset\tprotected_exit_offset\tentry_domain\n";
    int region=0,lo=0;for(int hi=1;hi<=m.sections;++hi)if(hi==m.sections || m.berths[hi]){
        for(auto& tr:m.trains){Time offset=0;for(int k=0;k<hi-lo;++k){int seg=tr.direction==1?lo+k:hi-1-k;
            f<<region<<'\t'<<tr.id<<'\t'<<tr.direction<<'\t'<<seg<<'\t'<<offset<<'\t'<<offset+tr.run[seg]<<'\t'
             <<offset+tr.run[seg]+m.headway<<"\tALL_INTEGER_ENTRIES_0_TO_"<<horizon<<'\n';offset+=tr.run[seg];}}
        lo=hi;++region;}
}
Schedule connect_patterns(const Model& m,const std::string& path){
    std::vector<std::pair<int,int>> regions;int lo=0;
    for(int hi=1;hi<=m.sections;++hi)if(hi==m.sections || m.berths[hi]){regions.push_back({lo,hi});lo=hi;}
    std::map<std::string,int> ids;for(size_t i=0;i<m.trains.size();++i)ids[m.trains[i].id]=int(i);
    std::ifstream file(path);if(!file)throw std::runtime_error("cannot read patterns");std::string line;std::getline(file,line);
    Schedule out(m.trains.size());std::set<std::pair<int,int>> seen;
    while(std::getline(file,line)){auto r=split(line);if(r.size()!=7 || !ids.count(r[1]))throw std::runtime_error("invalid pattern row");
        int b=std::stoi(r[0]),i=ids.at(r[1]);if(b<0||b>=int(regions.size())||!seen.insert({b,i}).second)throw std::runtime_error("duplicate/unknown region");
        const auto& tr=m.trains[i];if(std::stoi(r[2])!=tr.direction || std::stoll(r[5])!=0)throw std::runtime_error("pattern direction/wait mismatch");
        auto [a,z]=regions[b];Time t=std::stoll(r[3]);for(int k=0;k<z-a;++k){int seg=tr.direction==1?a+k:z-1-k;
            int index=tr.direction==1?seg:m.sections-1-seg;out[i].push_back({i,index,seg,t,t+tr.run[seg]});t+=tr.run[seg];}
        if(t!=std::stoll(r[4]))throw std::runtime_error("pattern exit does not match physical template");}
    if(seen.size()!=regions.size()*m.trains.size())throw std::runtime_error("incomplete pattern assignment");
    for(auto& s:out)std::sort(s.begin(),s.end(),[](auto a,auto b){return a.index<b.index;});
    auto v=validate(m,out);if(!v.ok)throw std::runtime_error("pattern connection violates physical constraints: "+v.errors.front());return out;
}
void write_hotspots(const Model& m,const Schedule& schedule,const std::string& path){
    std::ofstream f(path);f<<"source\tresource_kind\tresource_id\tstart\tend\tload\tcapacity\tclassification\ttrains\n";
    for(int source=0;source<2;++source){Schedule s=schedule;
        if(source==0)for(size_t i=0;i<m.trains.size();++i){Time t=m.trains[i].release;for(auto& x:s[i]){x.start=t;x.end=t+m.trains[i].run[x.segment];t=x.end;}}
        for(int resource=0;resource<2*m.sections+1;++resource){int cap=capacity(m,resource);if(cap<=0)continue;
            std::vector<std::tuple<Time,Time,int>> occ;std::set<Time> ends;
            for(size_t i=0;i<s.size();++i)for(size_t k=0;k<s[i].size();++k){auto x=s[i][k];Time a=0,b=0;
                if(resource<m.sections && x.segment==resource){a=x.start;b=x.end+m.headway;}
                else if(resource>=m.sections && k){int node=m.trains[i].direction==1?x.segment:x.segment+1;
                    if(resource==m.sections+node){a=s[i][k-1].end;b=x.start;}}
                if(a<b){occ.emplace_back(a,b,int(i));ends.insert(a);ends.insert(b);}}
            std::vector<Time> points(ends.begin(),ends.end());
            for(size_t k=0;k+1<points.size();++k){std::vector<int> active;for(auto [a,b,i]:occ)if(a<=points[k] && points[k]<b)active.push_back(i);
                f<<(source?"incumbent":"free_run")<<'\t'<<(resource<m.sections?"track":"berth")<<'\t'
                 <<(resource<m.sections?resource:resource-m.sections)<<'\t'<<points[k]<<'\t'<<points[k+1]<<'\t'<<active.size()<<'\t'<<cap<<'\t'
                 <<(int(active.size())>cap?"OVERLOAD":int(active.size())==cap?"SATURATED":"SLACK")<<'\t';
                for(size_t j=0;j<active.size();++j)f<<(j?",":"")<<m.trains[active[j]].id;f<<'\n';}
        }
    }
}
} // namespace bottleneck
