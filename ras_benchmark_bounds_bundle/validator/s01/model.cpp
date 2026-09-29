#include "model.hpp"
#include <filesystem>
#include <iomanip>
#include <numeric>
#include <set>

namespace bottleneck {
std::vector<std::string> split(const std::string& s, char sep) {
    std::vector<std::string> a; std::istringstream in(s); std::string x;
    while (std::getline(in, x, sep)) { if (!x.empty() && x.back() == '\r') x.pop_back(); a.push_back(x); }
    return a;
}
Time running(const Train& t) { return std::accumulate(t.run.begin(), t.run.end(), Time(0)); }
Model example(int n, int spacing, bool hetero) {
    Model m; m.name = "n" + std::to_string(n) + "_s" + std::to_string(spacing) + (hetero ? "_mixed" : "_uniform");
    m.tracks.assign(6, 1); m.berths = {0,0,1,0,1,0,0};
    for (int i = 0; i < n; ++i) m.trains.push_back({"T" + std::to_string(i), i % 2 ? -1 : 1,
        Time(i / 2) * spacing, std::vector<Time>(6, hetero && (i / 2) % 2 ? 7 : 5)});
    return m;
}
Model read_model(const std::string& dir) {
    Model m; std::string line;
    std::ifstream p(dir + "/params.tsv"); if (!p) throw std::runtime_error("missing params.tsv");
    std::getline(p,line);
    while (std::getline(p,line)) { auto r=split(line); if(r.size()!=2) throw std::runtime_error("invalid params row");
        if(r[0]=="name") m.name=r[1]; else if(r[0]=="sections") m.sections=std::stoi(r[1]);
        else if(r[0]=="headway") m.headway=std::stoll(r[1]);
        else throw std::runtime_error("unknown parameter: " + r[0]); }
    if(m.sections<1 || m.headway<0) throw std::runtime_error("invalid sections/headway");
    m.tracks.assign(m.sections,0); m.berths.assign(m.sections+1,0);
    std::ifstream resources(dir+"/resources.tsv"); if(!resources) throw std::runtime_error("missing resources.tsv");
    std::getline(resources,line); std::set<std::pair<std::string,int>> seen;
    while(std::getline(resources,line)){ auto r=split(line); if(r.size()!=3) throw std::runtime_error("invalid resource row");
        int i=std::stoi(r[1]), cap=std::stoi(r[2]); if(!seen.insert({r[0],i}).second) throw std::runtime_error("duplicate resource");
        if(r[0]=="track" && i>=0 && i<m.sections && cap>0) m.tracks[i]=cap;
        else if(r[0]=="berth" && i>0 && i<m.sections && cap>0) m.berths[i]=cap;
        else throw std::runtime_error("invalid resource"); }
    for(int c:m.tracks) if(c<=0) throw std::runtime_error("missing track");
    std::ifstream trains(dir+"/trains.tsv"); if(!trains) throw std::runtime_error("missing trains.tsv");
    std::getline(trains,line); std::set<std::string> ids;
    while(std::getline(trains,line)){ auto r=split(line); if(r.size()!=4) throw std::runtime_error("invalid train row");
        Train t{r[0],std::stoi(r[1]),std::stoll(r[2]),{}};
        for(auto x:split(r[3],',')) t.run.push_back(std::stoll(x));
        if(!ids.insert(t.id).second || (t.direction!=1 && t.direction!=-1) || t.release<0 || int(t.run.size())!=m.sections)
            throw std::runtime_error("invalid train");
        for(Time x:t.run) if(x<=0) throw std::runtime_error("running time must be positive integer");
        m.trains.push_back(t); }
    if(m.trains.empty()) throw std::runtime_error("no trains");
    return m;
}
void write_model(const Model& m,const std::string& dir){
    std::filesystem::create_directories(dir);
    std::ofstream p(dir+"/params.tsv"); p<<"key\tvalue\nname\t"<<m.name<<"\nsections\t"<<m.sections<<"\nheadway\t"<<m.headway<<'\n';
    std::ofstream r(dir+"/resources.tsv"); r<<"kind\tid\tcapacity\n";
    for(int s=0;s<m.sections;++s) r<<"track\t"<<s<<'\t'<<m.tracks[s]<<'\n';
    for(int j=1;j<m.sections;++j) if(m.berths[j]) r<<"berth\t"<<j<<'\t'<<m.berths[j]<<'\n';
    std::ofstream t(dir+"/trains.tsv"); t<<"id\tdirection\trelease\trun_by_physical_segment\n";
    for(auto& x:m.trains){t<<x.id<<'\t'<<x.direction<<'\t'<<x.release<<'\t';
        for(int s=0;s<m.sections;++s)t<<(s?",":"")<<x.run[s];t<<'\n';}
}
Schedule serial_seed(const Model& m,const std::vector<int>& order){
    Schedule out(m.trains.size()); Time clear=0;
    for(int i:order){const auto& tr=m.trains[i]; Time t=std::max(clear,tr.release);
        for(int k=0;k<m.sections;++k){int s=tr.direction==1?k:m.sections-1-k;
            out[i].push_back({i,k,s,t,t+tr.run[s]});t+=tr.run[s];}clear=t+m.headway;}
    return out;
}
void write_schedule(const Model& m,const Schedule& s,const std::string& path){
    std::ofstream f(path); if(!f)throw std::runtime_error("cannot write schedule");
    f<<"train\tindex\tsegment\tstart\tend\n";
    for(const auto& train:s)for(auto x:train)f<<m.trains[x.train].id<<'\t'<<x.index<<'\t'<<x.segment<<'\t'<<x.start<<'\t'<<x.end<<'\n';
}
Schedule read_schedule(const Model& m,const std::string& path){
    std::ifstream f(path);if(!f)throw std::runtime_error("cannot read schedule"); std::string line;std::getline(f,line);
    Schedule s(m.trains.size()); std::map<std::string,int> id;for(size_t i=0;i<m.trains.size();++i)id[m.trains[i].id]=int(i);
    while(std::getline(f,line)){auto r=split(line);if(r.size()!=5 || !id.count(r[0]))throw std::runtime_error("invalid schedule row");
        int i=id.at(r[0]);s[i].push_back({i,std::stoi(r[1]),std::stoi(r[2]),std::stoll(r[3]),std::stoll(r[4])});}
    for(auto& a:s)std::sort(a.begin(),a.end(),[](auto x,auto y){return x.index<y.index;});return s;
}
std::string fingerprint(const Model& m){ // identity text, no hash collisions in caches
    std::ostringstream s;s<<"physical-v1|"<<m.sections<<'|'<<m.headway;
    for(int c:m.tracks)s<<'|'<<c;for(int c:m.berths)s<<'|'<<c;
    for(auto& t:m.trains){s<<'|'<<t.id<<':'<<t.direction<<':'<<t.release;for(Time p:t.run)s<<','<<p;}return s.str();
}
} // namespace bottleneck
