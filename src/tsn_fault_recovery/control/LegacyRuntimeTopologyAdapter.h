#ifndef __TSN_FAULT_RECOVERY_LEGACYRUNTIMETOPOLOGYADAPTER_H
#define __TSN_FAULT_RECOVERY_LEGACYRUNTIMETOPOLOGYADAPTER_H

#include <map>
#include <omnetpp.h>
#include "../model/NetworkGraph.h"
#include "ScheduleTypes.h"

namespace tsn_fault_recovery {

struct LegacyGraphCapture
{
    NetworkGraph graph;
    std::map<std::pair<std::string, std::string>, std::string> egressInterfaces;
};

class LegacyRuntimeTopologyAdapter
{
  public:
    static LegacyGraphCapture capture(omnetpp::cModule *network);
    static RoutePath compile(const LogicalRoute& logical, const std::string& destination,
            const LegacyGraphCapture& capture, omnetpp::cModule *network);
};

} // namespace tsn_fault_recovery
#endif
