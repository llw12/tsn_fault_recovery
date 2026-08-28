#include "PipelineScheduleGenerator.h"

#include <omnetpp.h>

using namespace omnetpp;

namespace tsn_fault_recovery {

std::vector<GateScheduleDefinition> PipelineScheduleGenerator::generate(
        const std::vector<std::string>& egressInterfacePaths,
        simtime_t cycleTime,
        simtime_t ttWindow,
        int ttPacketBytes,
        int frameOverheadBytes,
        double linkBitrate,
        int ttTrafficClass,
        int beTrafficClass)
{
    if (egressInterfacePaths.empty())
        throw cRuntimeError("Cannot generate a GCL for an empty route");
    if (cycleTime <= SIMTIME_ZERO || ttWindow <= SIMTIME_ZERO || ttWindow >= cycleTime)
        throw cRuntimeError("Invalid cycle/window: cycle=%s window=%s", cycleTime.str().c_str(), ttWindow.str().c_str());
    if (ttTrafficClass < 0 || beTrafficClass < 0 || ttTrafficClass == beTrafficClass)
        throw cRuntimeError("TT and BE traffic classes must be distinct non-negative values");
    if (ttPacketBytes <= 0 || frameOverheadBytes < 0 || linkBitrate <= 0)
        throw cRuntimeError("Invalid packet size, frame overhead, or link bitrate");

    simtime_t serializationTime = SimTime((ttPacketBytes + frameOverheadBytes) * 8.0 / linkBitrate);
    if (serializationTime > ttWindow)
        throw cRuntimeError("TT frame serialization time %s exceeds window %s",
                serializationTime.str().c_str(), ttWindow.str().c_str());
    if (ttWindow * egressInterfacePaths.size() > cycleTime)
        throw cRuntimeError("Pipeline windows do not fit in the cycle");

    std::vector<GateScheduleDefinition> schedules;
    for (size_t hop = 0; hop < egressInterfacePaths.size(); ++hop) {
        simtime_t windowStart = ttWindow * hop;
        simtime_t offset = windowStart == SIMTIME_ZERO ? SIMTIME_ZERO : cycleTime - windowStart;
        std::vector<simtime_t> durations = {ttWindow, cycleTime - ttWindow};
        std::string gatePrefix = egressInterfacePaths[hop] + ".macLayer.queue.transmissionGate[";
        schedules.push_back({gatePrefix + std::to_string(ttTrafficClass) + "]", ttTrafficClass, true, offset, durations});
        schedules.push_back({gatePrefix + std::to_string(beTrafficClass) + "]", beTrafficClass, false, offset, durations});
    }
    return schedules;
}

} // namespace tsn_fault_recovery
