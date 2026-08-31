#ifndef __TSN_FAULT_RECOVERY_PROFILEDEFINITION_H
#define __TSN_FAULT_RECOVERY_PROFILEDEFINITION_H

#include <string>
#include <vector>

#include <omnetpp.h>
#include "../model/NetworkGraph.h"

namespace tsn_fault_recovery {

enum class ForwardingModel
{
    DESTINATION_MAC,
    STREAM_AWARE
};

inline const char *forwardingModelName(ForwardingModel model)
{
    return model == ForwardingModel::STREAM_AWARE ? "stream-aware" : "destination-mac";
}

struct RouteDefinition
{
    std::string switchPath;
    std::string destinationPath;
    std::string egressInterface;
    std::string flowId;
    std::string logicalLinkId;
    int streamHandle = 0;
};

struct GateScheduleDefinition
{
    std::string gatePath;
    int trafficClass = -1;
    bool initiallyOpen = false;
    omnetpp::simtime_t offset;
    std::vector<omnetpp::simtime_t> durations;
};

struct ProfileDefinition
{
    std::string profileId;
    ForwardingModel forwardingModel = ForwardingModel::DESTINATION_MAC;
    std::vector<RouteDefinition> routes;
    std::vector<GateScheduleDefinition> gateSchedules;
    std::vector<LogicalRoute> logicalRoutes;
};

struct ActivationResult
{
    std::string profileId;
    omnetpp::simtime_t simulationStart;
    omnetpp::simtime_t simulationEnd;
    double wallTimeSeconds = 0;
};

} // namespace tsn_fault_recovery

#endif
