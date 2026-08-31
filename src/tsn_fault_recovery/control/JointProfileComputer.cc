#include "JointProfileComputer.h"

#include <algorithm>
#include <chrono>
#include <stdexcept>

#include "AffectedFlowAnalyzer.h"
#include "BfsRouteSolver.h"
#include "ForwardingRealizabilityValidator.h"
#include "GateScheduleCompiler.h"
#include "TimeTickConverter.h"
#include "Z3ScheduleSolver.h"

using namespace omnetpp;

namespace tsn_fault_recovery {

const char *faultProfileStatusName(FaultProfileStatus status)
{
    switch (status) {
        case FaultProfileStatus::SAT: return "SAT";
        case FaultProfileStatus::NO_AFFECTED_TT: return "NO_AFFECTED_TT";
        case FaultProfileStatus::NO_ROUTE: return "NO_ROUTE";
        case FaultProfileStatus::UNSAT: return "UNSAT";
        case FaultProfileStatus::FORWARDING_CONFLICT: return "FORWARDING_CONFLICT";
        case FaultProfileStatus::TIMEOUT: return "TIMEOUT";
        default: return "ERROR";
    }
}

ProfileComputationResult JointProfileComputer::computeInitial(const std::string& profileId) const
{
    std::vector<std::string> allFlows;
    for (const auto& flow : scenario.ttFlows)
        allFlows.push_back(flow.flowId);
    return compute(profileId, {}, nullptr, allFlows);
}

ProfileComputationResult JointProfileComputer::computeInitialWithRoutes(const std::string& profileId,
        const std::map<std::string, LogicalRoute>& frozenRoutes) const
{
    std::vector<std::string> allFlows;
    for (const auto& flow : scenario.ttFlows) {
        allFlows.push_back(flow.flowId);
        auto route = frozenRoutes.find(flow.flowId);
        if (route == frozenRoutes.end())
            throw cRuntimeError("missing frozen primary route for flow '%s'", flow.flowId.c_str());
        if (route->second.nodePath.empty() || route->second.nodePath.front() != flow.source ||
                route->second.nodePath.back() != flow.destination ||
                route->second.linkPath.size() + 1 != route->second.nodePath.size())
            throw cRuntimeError("invalid frozen primary route for flow '%s'", flow.flowId.c_str());
    }
    if (frozenRoutes.size() != scenario.ttFlows.size())
        throw cRuntimeError("frozen primary route set does not match TT flow set");
    return compute(profileId, {}, &frozenRoutes, allFlows);
}

ProfileComputationResult JointProfileComputer::computeForFault(const std::string& profileId,
        const std::string& faultId, const std::map<std::string, LogicalRoute>& initialRoutes) const
{
    auto affected = AffectedFlowAnalyzer::affectedFlowIds(initialRoutes, faultId);
    if (affected.empty()) {
        ProfileComputationResult result;
        result.status = FaultProfileStatus::NO_AFFECTED_TT;
        result.diagnostic = "fault is not on any healthy TT route";
        return result;
    }
    return compute(profileId, {faultId}, &initialRoutes, affected);
}

ProfileComputationResult JointProfileComputer::computeForDisabledLinks(const std::string& profileId,
        const std::set<std::string>& disabledLinks,
        const std::map<std::string, LogicalRoute>& initialRoutes,
        const std::vector<std::string>& affectedFlowIds) const
{
    if (disabledLinks.empty())
        throw cRuntimeError("shared Profile synthesis requires at least one disabled link");
    if (affectedFlowIds.empty())
        throw cRuntimeError("shared Profile synthesis requires a non-empty affected-flow set");
    std::set<std::string> expected;
    for (const auto& [flowId, route] : initialRoutes)
        if (std::any_of(route.linkPath.begin(), route.linkPath.end(),
                [&](const std::string& link) { return disabledLinks.count(link); }))
            expected.insert(flowId);
    std::set<std::string> declared(affectedFlowIds.begin(), affectedFlowIds.end());
    if (expected != declared)
        throw cRuntimeError("shared Profile affected-flow set is inconsistent with union disabled links");
    return compute(profileId, disabledLinks, &initialRoutes, affectedFlowIds);
}

ProfileComputationResult JointProfileComputer::compute(const std::string& profileId,
        const std::set<std::string>& disabledLinks,
        const std::map<std::string, LogicalRoute> *preservedRoutes,
        const std::vector<std::string>& affectedFlowIds) const
{
    ProfileComputationResult result;
    result.affectedFlowIds = affectedFlowIds;
    auto totalStart = std::chrono::steady_clock::now();
    auto routeStart = totalStart;
    std::map<std::string, LogicalRoute> routes;
    BfsRouteSolver bfs;
    try {
        for (const auto& flow : scenario.ttFlows) {
            auto existing = preservedRoutes ? preservedRoutes->find(flow.flowId) :
                    std::map<std::string, LogicalRoute>::const_iterator{};
            if (preservedRoutes && existing != preservedRoutes->end() &&
                    std::none_of(existing->second.linkPath.begin(), existing->second.linkPath.end(),
                            [&](const std::string& link) { return disabledLinks.count(link); })) {
                routes[flow.flowId] = existing->second;
            }
            else {
                ++result.routeSolverInvocations;
                routes[flow.flowId] = bfs.solve(scenario.graph, flow.flowId, flow.source,
                        flow.destination, disabledLinks);
            }
        }
    }
    catch (const std::runtime_error& error) {
        result.status = FaultProfileStatus::NO_ROUTE;
        result.diagnostic = error.what();
        result.routeSolverWallSeconds = std::chrono::duration<double>(
                std::chrono::steady_clock::now() - routeStart).count();
        result.totalWallSeconds = std::chrono::duration<double>(
                std::chrono::steady_clock::now() - totalStart).count();
        return result;
    }
    result.routeSolverWallSeconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - routeStart).count();

    ScheduleRequest request;
    request.flows = scenario.ttFlows;
    for (const auto& flow : scenario.ttFlows)
        request.routeEgressPaths.push_back(adapter.egressPaths(routes.at(flow.flowId), scenario.graph));
    request.cycleTime = scenario.cycleTime;
    request.timeQuantum = scenario.timeQuantum;
    request.ingressMargin = scenario.ingressMargin;
    request.hopMargin = scenario.hopMargin;
    request.frameOverheadBytes = scenario.frameOverheadBytes;
    request.linkBitrate = scenario.linkBitrate;
    request.beTrafficClass = scenario.beTrafficClass;
    request.solverTimeoutMs = solverTimeoutMs;

    Z3ScheduleSolver solver;
    ++result.z3SolverInvocations;
    auto schedule = solver.solve(request);
    result.smtSolverWallSeconds = schedule.wallTimeSeconds;
    result.scheduleObjectiveTicks = schedule.objectiveTicks;
    if (schedule.status != ScheduleStatus::SAT) {
        result.status = schedule.status == ScheduleStatus::UNSAT ? FaultProfileStatus::UNSAT :
                (schedule.diagnostic.find("timeout") != std::string::npos ? FaultProfileStatus::TIMEOUT : FaultProfileStatus::ERROR);
        result.diagnostic = schedule.diagnostic;
        result.totalWallSeconds = std::chrono::duration<double>(
                std::chrono::steady_clock::now() - totalStart).count();
        return result;
    }

    auto compileStart = std::chrono::steady_clock::now();
    result.profile.profileId = profileId;
    for (const auto& flow : scenario.ttFlows) {
        const auto& logical = routes.at(flow.flowId);
        result.profile.logicalRoutes.push_back(logical);
        auto entries = adapter.forwardingEntries(logical, scenario.graph, flow.destination);
        result.profile.routes.insert(result.profile.routes.end(), entries.begin(), entries.end());
    }
    auto forwarding = ForwardingRealizabilityValidator::validate(result.profile.routes);
    if (!forwarding.valid) {
        result.status = FaultProfileStatus::FORWARDING_CONFLICT;
        result.diagnostic = forwarding.diagnostic;
        result.profileCompileWallSeconds = std::chrono::duration<double>(
                std::chrono::steady_clock::now() - compileStart).count();
        result.totalWallSeconds = std::chrono::duration<double>(
                std::chrono::steady_clock::now() - totalStart).count();
        return result;
    }
    int ttClass = scenario.ttFlows.front().trafficClass;
    for (const auto& flow : scenario.ttFlows)
        if (flow.trafficClass != ttClass)
            throw cRuntimeError("scheduler v1 requires one TT traffic class");
    int64_t cycleTicks = TimeTickConverter::exactTicks(scenario.cycleTime,
            scenario.timeQuantum, "cycleTime");
    result.profile.gateSchedules = GateScheduleCompiler::compile(schedule.windows, cycleTicks,
            scenario.timeQuantum, ttClass, scenario.beTrafficClass);
    result.profileCompileWallSeconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - compileStart).count();
    result.totalWallSeconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - totalStart).count();
    result.status = FaultProfileStatus::SAT;
    result.diagnostic = schedule.diagnostic;
    return result;
}

} // namespace tsn_fault_recovery
