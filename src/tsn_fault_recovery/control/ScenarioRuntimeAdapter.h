#ifndef __TSN_FAULT_RECOVERY_SCENARIORUNTIMEADAPTER_H
#define __TSN_FAULT_RECOVERY_SCENARIORUNTIMEADAPTER_H

#include <map>
#include <utility>
#include <omnetpp.h>
#include "../model/NetworkGraph.h"
#include "ScheduleTypes.h"

namespace tsn_fault_recovery {

struct ScenarioData
{
    std::string name;
    std::string sha256;
    ForwardingModel forwardingModel = ForwardingModel::DESTINATION_MAC;
    NetworkGraph graph;
    std::vector<AffectedFlow> ttFlows;
    omnetpp::simtime_t cycleTime;
    omnetpp::simtime_t timeQuantum;
    omnetpp::simtime_t failureTime;
    omnetpp::simtime_t solverDelay;
    omnetpp::simtime_t ingressMargin;
    omnetpp::simtime_t hopMargin;
    int frameOverheadBytes = 0;
    int beTrafficClass = 0;
    double linkBitrate = 0;
    std::vector<std::string> faultCandidates;
    std::map<std::string, int> streamHandles;
};

struct PortBinding { std::string interfaceName; std::string egressPath; };

class ScenarioRuntimeAdapter
{
  private:
    std::map<std::pair<std::string,std::string>, PortBinding> bindings;
  public:
    ScenarioRuntimeAdapter() = default;
    explicit ScenarioRuntimeAdapter(std::map<std::pair<std::string,std::string>, PortBinding> values) : bindings(std::move(values)) {}
    static ScenarioData parseScenario(omnetpp::cValueMap *root);
    static ScenarioRuntimeAdapter parsePortMap(omnetpp::cValueMap *root);
    const PortBinding& binding(const std::string& linkId, const std::string& nodeId) const;
    std::set<std::string> currentDisabledLinks(omnetpp::cModule *network) const;
    std::vector<std::string> egressPaths(const LogicalRoute& route, const NetworkGraph& graph) const;
    std::vector<RouteDefinition> forwardingEntries(const LogicalRoute& route,
            const NetworkGraph& graph, const std::string& destination, int streamHandle = 0) const;
};

} // namespace tsn_fault_recovery
#endif
