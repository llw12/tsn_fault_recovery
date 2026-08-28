#ifndef __TSN_FAULT_RECOVERY_GATESCHEDULECOMPILER_H
#define __TSN_FAULT_RECOVERY_GATESCHEDULECOMPILER_H

#include "ScheduleTypes.h"

namespace tsn_fault_recovery {

class GateScheduleCompiler
{
  public:
    static std::vector<GateScheduleDefinition> compile(const std::vector<GateWindow>& windows,
            int64_t cycleTicks, omnetpp::simtime_t quantum, int ttTrafficClass, int beTrafficClass);
    static void validateComplement(const GateScheduleDefinition& tt, const GateScheduleDefinition& be,
            omnetpp::simtime_t cycleTime);
};

} // namespace tsn_fault_recovery

#endif
