#include "model.hpp"

namespace bottleneck {
// Independent endpoint-sweep validator. No solver constraints, labels, or conflict code.
Validation validate(const Model& m,const Schedule& schedule){
    Validation v;auto fail=[&](const std::string& s){v.ok=false;v.errors.push_back(s);};
    if(schedule.size()!=m.trains.size()){fail("train count");return v;}
    std::vector<std::vector<std::pair<Time,int>>> events(2*m.sections+1);
    auto occupy=[&](int r,Time a,Time b){if(a<b){events[r].push_back({a,1});events[r].push_back({b,-1});}};
    for(size_t i=0;i<m.trains.size();++i){const auto& t=m.trains[i];const auto& a=schedule[i];
        if(int(a.size())!=m.sections){fail(t.id+": missing/extra movement");continue;}
        Time ready=t.release;
        for(int k=0;k<m.sections;++k){const auto& x=a[k];int seg=t.direction==1?k:m.sections-1-k;
            if(x.train!=int(i)||x.index!=k||x.segment!=seg)fail(t.id+": route/index");
            if(x.end-x.start!=t.run[seg])fail(t.id+": runtime");
            if(x.start<ready)fail(t.id+": continuity/release");
            Time wait=x.start-ready;v.wait+=wait;
            int node=t.direction==1?seg:seg+1;
            if(k && wait>0){if(m.berths[node]<=0)fail(t.id+": forbidden wait");
                occupy(m.sections+node,ready,x.start);}
            occupy(seg,x.start,x.end+m.headway);ready=x.end;
        }
    }
    for(size_t r=0;r<events.size();++r){auto& e=events[r];std::sort(e.begin(),e.end());int active=0;
        int cap=r<size_t(m.sections)?m.tracks[r]:m.berths[r-m.sections];
        for(auto [t,delta]:e){active+=delta;if(active>cap){fail("resource "+std::to_string(r)+" exceeds capacity at "+std::to_string(t));break;}}
    }
    return v;
}
} // namespace bottleneck
