#include "BfsZ3JointProfileSolver.h"

#include <chrono>

#include "BfsRouteSolver.h"
#include "GateScheduleCompiler.h"
#include "TimeTickConverter.h"
#include "Z3ScheduleSolver.h"

namespace tsn_fault_recovery {

SolverOutput BfsZ3JointProfileSolver::solve(omnetpp::cModule *network,
        const FaultEvent& fault, const SolverInput& input)
{
    BfsRouteSolver routeSolver;
    auto routeStart = std::chrono::steady_clock::now();
    RoutePath route = routeSolver.solve(network, fault);
    auto routeEnd = std::chrono::steady_clock::now();

    ScheduleRequest request;
    request.flows = input.affectedFlows;
    request.routeEgressPaths.assign(input.affectedFlows.size(), route.egressInterfacePaths);
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
    output.profile.routes = route.routes;
    output.nodePath = route.nodePath;
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
