#ifndef __TSN_FAULT_RECOVERY_PIPELINESCHEDULEGENERATOR_H
#define __TSN_FAULT_RECOVERY_PIPELINESCHEDULEGENERATOR_H

#include <string>
#include <vector>

#include "ProfileDefinition.h"

namespace tsn_fault_recovery {

class PipelineScheduleGenerator
{
  public:
    static std::vector<GateScheduleDefinition> generate(
            const std::vector<std::string>& egressInterfacePaths,
            omnetpp::simtime_t cycleTime,
            omnetpp::simtime_t ttWindow,
            int ttPacketBytes,
            int frameOverheadBytes,
            double linkBitrate,
            int ttTrafficClass,
            int beTrafficClass);
};

} // namespace tsn_fault_recovery

#endif
