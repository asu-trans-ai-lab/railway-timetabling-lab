#include "model.hpp"
#include <filesystem>
#include <iostream>
#include <sys/resource.h>

using namespace bottleneck;
int main(int argc,char** argv){try{
    if(argc<2)throw std::runtime_error("usage: bottleneck generate|solve|validate --data DIR [--out DIR] [--trains N --spacing S --mixed 0|1] [--engine macro|oracle --seconds 300 --cache 0|1 --add-track ID --add-berth ID]");
    std::map<std::string,std::string> a;for(int i=2;i<argc;i+=2){if(i+1>=argc)throw std::runtime_error("missing flag value");a[argv[i]]=argv[i+1];}
    auto arg=[&](std::string key,std::string def=""){return a.count(key)?a.at(key):def;};
    std::string command=argv[1],dir=arg("--data");if(dir.empty())throw std::runtime_error("--data required");
    if(command=="generate"){auto m=example(std::stoi(arg("--trains","4")),std::stoi(arg("--spacing","5")),arg("--mixed","0")=="1");
        if(m.trains.empty())throw std::runtime_error("positive train count required");write_model(m,dir);return 0;}
    Model m=read_model(dir);
    if(a.count("--add-track")){int s=std::stoi(a.at("--add-track"));if(s<0||s>=m.sections)throw std::runtime_error("invalid track intervention");++m.tracks[s];}
    if(a.count("--add-berth")){int n=std::stoi(a.at("--add-berth"));if(n<=0||n>=m.sections||!m.berths[n])throw std::runtime_error("berth intervention requires existing siding");++m.berths[n];}
    if(command=="validate"){auto v=validate(m,read_schedule(m,arg("--schedule")));std::cout<<"validation\t"<<(v.ok?"PASS":"FAIL")<<"\nwait\t"<<v.wait<<'\n';
        for(auto& e:v.errors)std::cout<<e<<'\n';return v.ok?0:2;}
    if(command=="connect"){auto s=connect_patterns(m,arg("--patterns"));auto v=validate(m,s);
        if(arg("--out").empty())throw std::runtime_error("--out schedule path required");write_schedule(m,s,arg("--out"));
        std::cout<<"validation\tPASS\nwait\t"<<v.wait<<'\n';return 0;}
    if(command!="solve")throw std::runtime_error("unknown command");
    std::string out=arg("--out");if(out.empty())throw std::runtime_error("--out required");
    std::filesystem::create_directories(out);Options opt;std::string engine=arg("--engine","macro");
    if(engine!="macro"&&engine!="oracle")throw std::runtime_error("invalid engine");
    opt.macro=engine=="macro";opt.cache=arg("--cache","1")=="1";opt.seconds=std::stod(arg("--seconds","300"));opt.max_nodes=std::stoull(arg("--max-nodes","0"));
    auto result=solve(m,opt);write_metrics(m,opt,result,out+"/metrics.tsv");write_schedule(m,result.best,out+"/schedule.tsv");
    write_patterns(m,result.best,out+"/patterns.tsv");write_templates(m,result.horizon,out+"/templates.tsv");write_hotspots(m,result.best,out+"/hotspots.tsv");
    write_model(m,out+"/input");std::ofstream identity(out+"/model_identity.txt");identity<<fingerprint(m)<<'\n';
    struct rusage usage{};getrusage(RUSAGE_SELF,&usage);std::ofstream metrics(out+"/metrics.tsv",std::ios::app);
#ifdef __APPLE__
    metrics<<"peak_rss_bytes\t"<<usage.ru_maxrss<<'\n';
#else
    metrics<<"peak_rss_bytes\t"<<usage.ru_maxrss*1024LL<<'\n';
#endif
    std::cout<<m.name<<'\t'<<engine<<'\t'<<result.status<<'\t'<<result.lb<<'\t'<<result.ub<<'\t'<<result.search_seconds<<'\n';return 0;
}catch(const std::exception& e){std::cerr<<"ERROR: "<<e.what()<<'\n';return 2;}}
