#include "ProfileSerializer.h"
#include <fstream>
#include <iomanip>

using namespace omnetpp;
namespace tsn_fault_recovery {
namespace {
std::string quote(const std::string& value) { std::string out="\""; for(char ch:value) { if(ch=='\\'||ch=='\"') out+='\\'; out+=ch; } return out+"\""; }
cValueArray *arrayAt(cValueMap *map,const char *key) { return check_and_cast<cValueArray *>(map->get(key).objectValue()); }
cValueMap *objectAt(cValueArray *array,int index) { return check_and_cast<cValueMap *>(array->get(index).objectValue()); }
std::string textAt(cValueMap *map,const char *key) { return map->get(key).stringValue(); }
}

void ProfileSerializer::write(const ProfileDefinition& profile,const std::string& scenarioHash,const std::string& path)
{
    std::ofstream out(path); if(!out) throw cRuntimeError("Cannot write profile '%s'",path.c_str()); out<<std::setprecision(17);
    out<<"{\n  \"schema_version\": 1,\n  \"scenario_sha256\": "<<quote(scenarioHash)<<",\n  \"profile_id\": "<<quote(profile.profileId)<<",\n  \"logical_routes\": [";
    for(size_t i=0;i<profile.logicalRoutes.size();++i) { const auto&r=profile.logicalRoutes[i]; if(i) out<<","; out<<"\n    {\"flow_id\": "<<quote(r.flowId)<<", \"node_path\": ["; for(size_t j=0;j<r.nodePath.size();++j){if(j)out<<", ";out<<quote(r.nodePath[j]);} out<<"], \"link_path\": ["; for(size_t j=0;j<r.linkPath.size();++j){if(j)out<<", ";out<<quote(r.linkPath[j]);} out<<"]}"; }
    out<<"\n  ],\n  \"routes\": [";
    for(size_t i=0;i<profile.routes.size();++i) { const auto&r=profile.routes[i]; if(i)out<<","; out<<"\n    {\"flow_id\": "<<quote(r.flowId)<<", \"switch\": "<<quote(r.switchPath)<<", \"destination\": "<<quote(r.destinationPath)<<", \"interface\": "<<quote(r.egressInterface)<<", \"logical_link\": "<<quote(r.logicalLinkId)<<"}"; }
    out<<"\n  ],\n  \"gate_schedules\": [";
    for(size_t i=0;i<profile.gateSchedules.size();++i) { const auto&g=profile.gateSchedules[i]; if(i)out<<","; out<<"\n    {\"gate_path\": "<<quote(g.gatePath)<<", \"traffic_class\": "<<g.trafficClass<<", \"initially_open\": "<<(g.initiallyOpen?"true":"false")<<", \"offset_s\": "<<g.offset.dbl()<<", \"durations_s\": ["; for(size_t j=0;j<g.durations.size();++j){if(j)out<<", ";out<<g.durations[j].dbl();} out<<"]}"; }
    out<<"\n  ]\n}\n";
}

ProfileDefinition ProfileSerializer::parse(cValueMap *root,const std::string& expectedHash)
{
    if(!root) throw cRuntimeError("profile0 is not an object");
    if(textAt(root,"scenario_sha256")!=expectedHash) throw cRuntimeError("profile scenario hash does not match scenario input");
    ProfileDefinition profile; profile.profileId=textAt(root,"profile_id");
    auto logical=arrayAt(root,"logical_routes");
    for(int i=0;i<logical->size();++i) { auto item=objectAt(logical,i); LogicalRoute r; r.flowId=textAt(item,"flow_id"); auto nodes=arrayAt(item,"node_path"); for(int j=0;j<nodes->size();++j)r.nodePath.push_back(nodes->get(j).stringValue()); auto links=arrayAt(item,"link_path"); for(int j=0;j<links->size();++j)r.linkPath.push_back(links->get(j).stringValue()); profile.logicalRoutes.push_back(r); }
    auto routes=arrayAt(root,"routes");
    for(int i=0;i<routes->size();++i) { auto item=objectAt(routes,i); profile.routes.push_back({textAt(item,"switch"),textAt(item,"destination"),textAt(item,"interface"),textAt(item,"flow_id"),textAt(item,"logical_link")}); }
    auto gates=arrayAt(root,"gate_schedules");
    for(int i=0;i<gates->size();++i) { auto item=objectAt(gates,i); GateScheduleDefinition g; g.gatePath=textAt(item,"gate_path"); g.trafficClass=static_cast<int>(item->get("traffic_class").doubleValue()); g.initiallyOpen=item->get("initially_open").boolValue(); g.offset=SimTime(item->get("offset_s").doubleValue()); auto durations=arrayAt(item,"durations_s"); for(int j=0;j<durations->size();++j)g.durations.push_back(SimTime(durations->get(j).doubleValue())); profile.gateSchedules.push_back(g); }
    return profile;
}
} // namespace tsn_fault_recovery
