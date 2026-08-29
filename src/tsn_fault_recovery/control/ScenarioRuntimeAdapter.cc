#include "ScenarioRuntimeAdapter.h"

using namespace omnetpp;
namespace tsn_fault_recovery {
namespace {
cValueMap *mapAt(cValueMap *map, const char *key) { return check_and_cast<cValueMap *>(map->get(key).objectValue()); }
cValueArray *arrayAt(cValueMap *map, const char *key) { return check_and_cast<cValueArray *>(map->get(key).objectValue()); }
std::string textAt(cValueMap *map, const char *key) { return map->get(key).stringValue(); }
double numberAt(cValueMap *map, const char *key) { return map->get(key).doubleValue(); }
}

ScenarioData ScenarioRuntimeAdapter::parseScenario(cValueMap *root)
{
    if (!root) throw cRuntimeError("scenario parameter is not an object");
    ScenarioData data;
    data.name=textAt(root,"scenario_name"); data.sha256=textAt(root,"scenario_sha256");
    auto simulation=mapAt(root,"simulation");
    data.cycleTime=SimTime(numberAt(simulation,"cycle_time_s"));
    data.timeQuantum=SimTime(numberAt(simulation,"time_quantum_s"));
    data.failureTime=SimTime(numberAt(simulation,"failure_time_s"));
    data.solverDelay=SimTime(numberAt(simulation,"solver_delay_s"));
    auto scheduling=mapAt(root,"scheduling");
    data.ingressMargin=SimTime(numberAt(scheduling,"ingress_margin_s"));
    data.hopMargin=SimTime(numberAt(scheduling,"hop_margin_s"));
    data.frameOverheadBytes=static_cast<int>(numberAt(scheduling,"frame_overhead_bytes"));
    data.beTrafficClass=static_cast<int>(numberAt(scheduling,"be_traffic_class"));
    auto nodes=arrayAt(root,"nodes");
    for(int i=0;i<nodes->size();++i) { auto node=check_and_cast<cValueMap *>(nodes->get(i).objectValue()); data.graph.addNode({textAt(node,"id"),textAt(node,"type")=="switch"?NodeType::SWITCH:NodeType::END_SYSTEM}); }
    auto links=arrayAt(root,"links");
    for(int i=0;i<links->size();++i) { auto link=check_and_cast<cValueMap *>(links->get(i).objectValue()); double bitrate=numberAt(link,"bitrate_bps"); if(data.linkBitrate==0)data.linkBitrate=bitrate; else if(data.linkBitrate!=bitrate)throw cRuntimeError("scheduler v1 requires one common link bitrate"); data.graph.addLink({textAt(link,"id"),textAt(link,"endpoint_a"),textAt(link,"endpoint_b"),bitrate,numberAt(link,"propagation_delay_s")}); }
    auto flows=arrayAt(root,"tt_flows");
    for(int i=0;i<flows->size();++i) {
        auto flow=check_and_cast<cValueMap *>(flows->get(i).objectValue());
        AffectedFlow value{textAt(flow,"id"),textAt(flow,"source"),textAt(flow,"destination"),
            static_cast<int>(numberAt(flow,"packet_size_bytes")),static_cast<int>(numberAt(flow,"traffic_class")),
            SimTime(numberAt(flow,"period_s")),SimTime(numberAt(flow,"schedule_deadline_budget_s")),
            SimTime(numberAt(flow,"release_offset_s"))};
        value.deadlineE2E=SimTime(numberAt(flow,"deadline_e2e_s"));
        value.scheduleDeadlineBudget=SimTime(numberAt(flow,"schedule_deadline_budget_s"));
        data.ttFlows.push_back(value);
    }
    auto faults=arrayAt(root,"fault_candidates"); for(int i=0;i<faults->size();++i) data.faultCandidates.push_back(faults->get(i).stringValue());
    return data;
}

ScenarioRuntimeAdapter ScenarioRuntimeAdapter::parsePortMap(cValueMap *root)
{
    ScenarioRuntimeAdapter result; auto links=mapAt(root,"links");
    for(const auto& [linkId,value]:links->getFields()) {
        auto link=check_and_cast<cValueMap *>(value.objectValue());
        for(const char *side:{"a","b"}) { auto endpoint=mapAt(link,side); std::string node=textAt(endpoint,"node"); result.bindings[{linkId,node}]={textAt(endpoint,"interface"),textAt(endpoint,"egress_path")}; }
    }
    return result;
}

const PortBinding& ScenarioRuntimeAdapter::binding(const std::string& linkId,const std::string& nodeId) const
{
    auto it=bindings.find({linkId,nodeId}); if(it==bindings.end()) throw cRuntimeError("No port binding for link '%s' at node '%s'",linkId.c_str(),nodeId.c_str()); return it->second;
}

std::set<std::string> ScenarioRuntimeAdapter::currentDisabledLinks(cModule *network) const
{
    std::set<std::string> disabled;
    for(const auto& [key,port]:bindings) {
        const auto& [linkId,nodeId]=key; cModule *node=network->getSubmodule(nodeId.c_str());
        int index=std::stoi(port.interfaceName.substr(3)); cGate *output=node?node->gate("ethg$o",index):nullptr;
        cChannel *channel=output?output->findTransmissionChannel():nullptr;
        if(!output||!output->getNextGate()||!channel||channel->isDisabled())disabled.insert(linkId);
    }
    return disabled;
}

std::vector<std::string> ScenarioRuntimeAdapter::egressPaths(const LogicalRoute& route,const NetworkGraph& graph) const
{
    std::vector<std::string> result;
    for(size_t hop=0;hop<route.linkPath.size();++hop) if(graph.getNode(route.nodePath[hop]).type==NodeType::SWITCH) result.push_back(binding(route.linkPath[hop],route.nodePath[hop]).egressPath);
    return result;
}

std::vector<RouteDefinition> ScenarioRuntimeAdapter::forwardingEntries(const LogicalRoute& route,const NetworkGraph& graph,const std::string& destination) const
{
    std::vector<RouteDefinition> result;
    for(size_t hop=0;hop<route.linkPath.size();++hop) {
        const auto& node=route.nodePath[hop]; if(graph.getNode(node).type!=NodeType::SWITCH) continue;
        const auto& port=binding(route.linkPath[hop],node); result.push_back({node,destination,port.interfaceName,route.flowId,route.linkPath[hop]});
    }
    return result;
}
} // namespace tsn_fault_recovery
