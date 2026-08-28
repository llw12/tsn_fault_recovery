#ifndef __TSN_FAULT_RECOVERY_JOINTPROFILESOLVER_H
#define __TSN_FAULT_RECOVERY_JOINTPROFILESOLVER_H

#include <string>

#include <omnetpp.h>

#include "ProfileDefinition.h"

namespace tsn_fault_recovery {

struct FaultEvent
{
    omnetpp::simtime_t detectionTime;
    std::string failedEndpointA;
    std::string failedEndpointB;
    std::string routeStart;
    std::string destination;
};

struct AffectedFlow
{
    std::string flowId;
    int packetBytes;
    int trafficClass;
};

struct SolverInput
{
    std::vector<AffectedFlow> affectedFlows;
    omnetpp::simtime_t cycleTime;
    omnetpp::simtime_t ttWindow;
    int frameOverheadBytes;
    double linkBitrate;
    int beTrafficClass;
};

struct SolverOutput
{
    ProfileDefinition profile;
    std::vector<std::string> nodePath;
};

class JointProfileSolver
{
  public:
    virtual ~JointProfileSolver() = default;
    virtual SolverOutput solve(omnetpp::cModule *network, const FaultEvent& fault, const SolverInput& input) = 0;
};

} // namespace tsn_fault_recovery

#endif
