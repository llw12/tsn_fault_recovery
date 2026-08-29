#include "DeterministicJointProfileSolver.h"

#include <chrono>

#include "BfsRouteSolver.h"
#include "LegacyRuntimeTopologyAdapter.h"
#include "PipelineScheduleGenerator.h"

using namespace omnetpp;

namespace tsn_fault_recovery {

SolverOutput DeterministicJointProfileSolver::solve(cModule *network, const FaultEvent& fault, const SolverInput& input)
{
    if (input.affectedFlows.size() != 1)
        throw cRuntimeError("The deterministic online backend currently supports exactly one affected TT flow, got %zu",
                input.affectedFlows.size());
    const AffectedFlow& ttFlow = input.affectedFlows.front();
    BfsRouteSolver routeSolver;
    auto routeStart = std::chrono::steady_clock::now();
    auto capture = LegacyRuntimeTopologyAdapter::capture(network);
    auto logical = routeSolver.solve(capture.graph, ttFlow.flowId, ttFlow.source, ttFlow.destination);
    RoutePath route = LegacyRuntimeTopologyAdapter::compile(logical, ttFlow.destination, capture, network);
    auto routeEnd = std::chrono::steady_clock::now();
    SolverOutput output;
    output.nodePath = route.nodePath;
    output.profile.profileId = "online-joint";
    output.profile.routes = route.routes;
    output.profile.logicalRoutes = {logical};
    auto scheduleStart = std::chrono::steady_clock::now();
    output.profile.gateSchedules = PipelineScheduleGenerator::generate(
            route.egressInterfacePaths, input.cycleTime, input.ttWindow, ttFlow.packetBytes,
            input.frameOverheadBytes, input.linkBitrate, ttFlow.trafficClass, input.beTrafficClass);
    auto scheduleEnd = std::chrono::steady_clock::now();
    output.scheduleStatus = ScheduleStatus::SAT;
    output.routeSolverWallTimeSeconds = std::chrono::duration<double>(routeEnd - routeStart).count();
    output.scheduleSolverWallTimeSeconds = std::chrono::duration<double>(scheduleEnd - scheduleStart).count();
    return output;
}

} // namespace tsn_fault_recovery
