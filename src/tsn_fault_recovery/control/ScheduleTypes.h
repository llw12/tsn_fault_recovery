#ifndef __TSN_FAULT_RECOVERY_SCHEDULETYPES_H
#define __TSN_FAULT_RECOVERY_SCHEDULETYPES_H

#include <cstdint>
#include <string>
#include <vector>

#include <omnetpp.h>

#include "ProfileDefinition.h"

namespace tsn_fault_recovery {

struct AffectedFlow
{
    std::string flowId;
    std::string source;
    std::string destination;
    int packetBytes = 0;
    int trafficClass = -1;
    omnetpp::simtime_t period;
    omnetpp::simtime_t deadline;
    omnetpp::simtime_t releaseOffset;
    // E2E deadline is the measurement contract; the reduced budget is the SMT constraint.
    omnetpp::simtime_t deadlineE2E;
    omnetpp::simtime_t scheduleDeadlineBudget;
};

struct RoutePath
{
    std::vector<std::string> nodePath;
    std::vector<std::string> egressInterfacePaths;
    std::vector<RouteDefinition> routes;
};

struct GateWindow
{
    std::string flowId;
    std::string egressInterfacePath;
    int trafficClass = -1;
    int64_t startTick = 0;
    int64_t endTick = 0;
};

enum class ScheduleStatus
{
    SAT,
    UNSAT,
    UNKNOWN
};

inline const char *scheduleStatusName(ScheduleStatus status)
{
    switch (status) {
        case ScheduleStatus::SAT: return "SAT";
        case ScheduleStatus::UNSAT: return "UNSAT";
        default: return "UNKNOWN";
    }
}

struct ScheduleRequest
{
    std::vector<AffectedFlow> flows;
    std::vector<std::vector<std::string>> routeEgressPaths;
    omnetpp::simtime_t cycleTime;
    omnetpp::simtime_t timeQuantum;
    omnetpp::simtime_t ingressMargin;
    omnetpp::simtime_t hopMargin;
    int frameOverheadBytes = 0;
    double linkBitrate = 0;
    int beTrafficClass = -1;
};

struct ScheduleResult
{
    ScheduleStatus status = ScheduleStatus::UNKNOWN;
    std::vector<GateWindow> windows;
    int64_t objectiveTicks = -1;
    double wallTimeSeconds = 0;
    std::string diagnostic;
};

} // namespace tsn_fault_recovery

#endif
