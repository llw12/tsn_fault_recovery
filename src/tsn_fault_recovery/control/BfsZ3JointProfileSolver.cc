#include "BfsZ3JointProfileSolver.h"

#include <chrono>

#include "BfsRouteSolver.h"
#include "GateScheduleCompiler.h"
#include "LegacyRuntimeTopologyAdapter.h"
#include "TimeTickConverter.h"
#include "Z3ScheduleSolver.h"

namespace tsn_fault_recovery {

SolverOutput BfsZ3JointProfileSolver::solve(omnetpp::cModule *network,
        const FaultEvent& fault, const SolverInput& input)
{
    BfsRouteSolver routeSolver;
    auto routeStart = std::chrono::steady_clock::now();
    auto capture = LegacyRuntimeTopologyAdapter::capture(network);
    std::vector<RoutePath> routes;
    std::vector<LogicalRoute> logicalRoutes;
    for (const auto& flow : input.affectedFlows) {
        auto logical = routeSolver.solve(capture.graph, flow.flowId, flow.source, flow.destination);
        routes.push_back(LegacyRuntimeTopologyAdapter::compile(logical, flow.destination, capture, network));
        logicalRoutes.push_back(logical);
    }
    auto routeEnd = std::chrono::steady_clock::now();

    ScheduleRequest request;
    request.flows = input.affectedFlows;
    for (const auto& route : routes)
        request.routeEgressPaths.push_back(route.egressInterfacePaths);
    request.cycleTime = input.cycleTime;
    request.timeQuantum = input.timeQuantum;
    request.ingressMargin = input.ingressMargin;
    request.hopMargin = input.hopMargin;
    request.frameOverheadBytes = input.frameOverheadBytes;
    request.linkBitrate = input.linkBitrate;
    request.beTrafficClass = input.beTrafficClass;

    Z3ScheduleSolver scheduleSolver;
    ScheduleResult schedule = scheduleSolver.solve(request);
    SolverOutput output;
    output.profile.profileId = "online-bfs-z3";
    for (const auto& route : routes)
        output.profile.routes.insert(output.profile.routes.end(), route.routes.begin(), route.routes.end());
    output.profile.logicalRoutes = logicalRoutes;
    output.nodePath = routes.front().nodePath;
    output.logicalWindows = schedule.windows;
    output.scheduleStatus = schedule.status;
    output.objectiveTicks = schedule.objectiveTicks;
    output.routeSolverWallTimeSeconds = std::chrono::duration<double>(routeEnd - routeStart).count();
    output.scheduleSolverWallTimeSeconds = schedule.wallTimeSeconds;
    output.diagnostic = schedule.diagnostic;
    if (schedule.status == ScheduleStatus::SAT) {
        int64_t cycleTicks = TimeTickConverter::exactTicks(input.cycleTime, input.timeQuantum, "cycleTime");
        int ttTrafficClass = input.affectedFlows.front().trafficClass;
        output.profile.gateSchedules = GateScheduleCompiler::compile(schedule.windows,
                cycleTicks, input.timeQuantum, ttTrafficClass, input.beTrafficClass);
    }
    return output;
}

} // namespace tsn_fault_recovery
