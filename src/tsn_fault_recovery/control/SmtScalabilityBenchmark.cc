#include "SmtScalabilityBenchmark.h"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <fstream>
#include <iomanip>
#include <map>
#include <set>
#include <sstream>

#include "AffectedFlowAnalyzer.h"
#include "BfsRouteSolver.h"
#include "GateScheduleCompiler.h"
#include "ScenarioRuntimeAdapter.h"
#include "TimeTickConverter.h"
#include "Z3ScheduleSolver.h"

using namespace omnetpp;

namespace tsn_fault_recovery {
namespace {

std::string quote(const std::string& value)
{
    std::string result = "\"";
    for (char ch : value) {
        if (ch == '\\' || ch == '"') result += '\\';
        if (ch == '\n') result += "\\n"; else result += ch;
    }
    return result + "\"";
}

std::vector<std::string> splitWords(const char *text)
{
    std::istringstream input(text); std::vector<std::string> result; std::string word;
    while (input >> word) result.push_back(word);
    return result;
}

std::string classifiedStatus(const ScheduleResult& result)
{
    if (result.status != ScheduleStatus::UNKNOWN) return scheduleStatusName(result.status);
    std::string reason = result.reasonUnknown;
    std::transform(reason.begin(), reason.end(), reason.begin(), [](unsigned char ch) { return std::tolower(ch); });
    const bool budgetExpired = reason.find("timeout") != std::string::npos || reason.find("canceled") != std::string::npos;
    return budgetExpired ? "TIMEOUT" : "UNKNOWN_OTHER";
}

void writeStringArray(std::ostream& out, const std::vector<std::string>& values)
{
    out << "[";
    for (size_t i = 0; i < values.size(); ++i) { if (i) out << ", "; out << quote(values[i]); }
    out << "]";
}

} // namespace

Define_Module(SmtScalabilityBenchmark);

void SmtScalabilityBenchmark::initialize()
{
    const auto scenario = ScenarioRuntimeAdapter::parseScenario(check_and_cast<cValueMap *>(par("scenario").objectValue()));
    const auto adapter = ScenarioRuntimeAdapter::parsePortMap(check_and_cast<cValueMap *>(par("portMap").objectValue()));
    const std::string mode = par("solverMode").stringValue();
    if (mode != "PRODUCTION_OPTIMIZE" && mode != "BENCHMARK_FEASIBILITY_ONLY")
        throw cRuntimeError("Unknown SMT benchmark solver mode '%s'", mode.c_str());
    const std::vector<std::string> disabledValues = splitWords(par("disabledLinks").stringValue());
    const std::set<std::string> disabled(disabledValues.begin(), disabledValues.end());
    const std::vector<std::string> affectedValues = splitWords(par("affectedFlowIds").stringValue());
    const std::set<std::string> affected(affectedValues.begin(), affectedValues.end());

    BfsRouteSolver bfs;
    std::map<std::string, LogicalRoute> healthy;
    for (const auto& flow : scenario.ttFlows)
        healthy[flow.flowId] = bfs.solve(scenario.graph, flow.flowId, flow.source, flow.destination);
    if (!disabled.empty()) {
        std::set<std::string> expected;
        for (const auto& [flowId, route] : healthy)
            if (std::any_of(route.linkPath.begin(), route.linkPath.end(), [&](const std::string& link) { return disabled.count(link); }))
                expected.insert(flowId);
        if (expected != affected)
            throw cRuntimeError("Benchmark affected-flow set is inconsistent with union disabled links");
    }

    auto routeStart = std::chrono::steady_clock::now();
    std::map<std::string, LogicalRoute> routes;
    std::string noRouteDiagnostic;
    try {
        for (const auto& flow : scenario.ttFlows) {
            if (!disabled.empty() && !affected.count(flow.flowId)) routes[flow.flowId] = healthy.at(flow.flowId);
            else routes[flow.flowId] = bfs.solve(scenario.graph, flow.flowId, flow.source, flow.destination, disabled);
        }
    }
    catch (const std::runtime_error& error) { noRouteDiagnostic = error.what(); }
    double routeSeconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - routeStart).count();

    ScheduleResult result;
    double compileSeconds = 0;
    std::vector<std::vector<std::string>> egressPaths;
    if (noRouteDiagnostic.empty()) {
        ScheduleRequest request;
        request.flows = scenario.ttFlows;
        for (const auto& flow : scenario.ttFlows) {
            egressPaths.push_back(adapter.egressPaths(routes.at(flow.flowId), scenario.graph));
            request.routeEgressPaths.push_back(egressPaths.back());
        }
        request.cycleTime = scenario.cycleTime; request.timeQuantum = scenario.timeQuantum;
        request.ingressMargin = scenario.ingressMargin; request.hopMargin = scenario.hopMargin;
        request.frameOverheadBytes = scenario.frameOverheadBytes; request.linkBitrate = scenario.linkBitrate;
        request.beTrafficClass = scenario.beTrafficClass; request.solverTimeoutMs = par("solverTimeoutMs").intValue();
        Z3ScheduleSolver solver;
        result = mode == "PRODUCTION_OPTIMIZE" ? solver.solve(request) : solver.solveFeasibilityOnly(request);
        if (mode == "PRODUCTION_OPTIMIZE" && result.status == ScheduleStatus::SAT) {
            auto compileStart = std::chrono::steady_clock::now();
            int64_t cycleTicks = TimeTickConverter::exactTicks(scenario.cycleTime, scenario.timeQuantum, "cycleTime");
            int ttClass = scenario.ttFlows.front().trafficClass;
            (void)GateScheduleCompiler::compile(result.windows, cycleTicks, scenario.timeQuantum, ttClass, scenario.beTrafficClass);
            compileSeconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - compileStart).count();
        }
    }

    int64_t routeTotalHops = 0; size_t routeMaxHops = 0;
    if (noRouteDiagnostic.empty()) for (const auto& [flowId, route] : routes) {
        routeTotalHops += route.linkPath.size(); routeMaxHops = std::max(routeMaxHops, route.linkPath.size());
    }
    std::ofstream out(par("reportOutputPath").stringValue());
    if (!out) throw cRuntimeError("Cannot write SMT scalability report");
    out << std::setprecision(17) << "{\n";
    out << "  \"case_id\": " << quote(par("caseId").stringValue()) << ",\n";
    out << "  \"mode\": " << quote(mode) << ",\n";
    out << "  \"scenario_sha256\": " << quote(scenario.sha256) << ",\n";
    out << "  \"disabled_links\": "; writeStringArray(out, disabledValues); out << ",\n";
    out << "  \"affected_flow_ids\": "; writeStringArray(out, affectedValues); out << ",\n";
    if (!noRouteDiagnostic.empty()) {
        out << "  \"status\": \"NO_ROUTE\",\n  \"reason_unknown\": \"\",\n";
        out << "  \"diagnostic\": " << quote(noRouteDiagnostic) << ",\n";
    }
    else {
        out << "  \"status\": " << quote(classifiedStatus(result)) << ",\n";
        out << "  \"reason_unknown\": " << quote(result.reasonUnknown) << ",\n";
        out << "  \"diagnostic\": " << quote(result.diagnostic) << ",\n";
    }
    out << "  \"route_total_hops\": " << routeTotalHops << ",\n";
    out << "  \"route_mean_hops\": " << (routes.empty() ? 0 : static_cast<double>(routeTotalHops) / routes.size()) << ",\n";
    out << "  \"route_max_hops\": " << routeMaxHops << ",\n";
    out << "  \"route_wall_ms\": " << routeSeconds * 1000 << ",\n";
#define FIELD(name) out << "  \"" #name "\": " << result.name << ",\n"
    FIELD(activeTtFlowCount); FIELD(controlledHopCount); FIELD(egressCount);
    FIELD(contentedEgressCount); FIELD(sharedEgressCount); FIELD(maxFlowsPerEgress);
    FIELD(meanFlowsPerUsedEgress); FIELD(contentionPairCount); FIELD(startTimeVarCount);
    FIELD(orderingBoolVarCount); FIELD(otherAuxVarCount); FIELD(totalSymbolicVarCount);
    FIELD(cycleBoundConstraintCount); FIELD(releaseConstraintCount); FIELD(hopPrecedenceConstraintCount);
    FIELD(deadlineConstraintCount); FIELD(nonOverlapConstraintCount); FIELD(otherHardConstraintCount);
    FIELD(totalHardConstraintCount); FIELD(objectiveCount); FIELD(objectiveTicks);
#undef FIELD
    out << "  \"model_build_wall_ms\": " << result.modelBuildWallSeconds * 1000 << ",\n";
    out << "  \"z3_check_wall_ms\": " << result.z3CheckWallSeconds * 1000 << ",\n";
    out << "  \"model_extract_wall_ms\": " << result.modelExtractWallSeconds * 1000 << ",\n";
    out << "  \"schedule_compile_wall_ms\": " << compileSeconds * 1000 << ",\n";
    out << "  \"total_solver_pipeline_wall_ms\": " << (routeSeconds + result.wallTimeSeconds + compileSeconds) * 1000 << ",\n";
    out << "  \"objective_values\": [";
    for (size_t i = 0; i < result.objectiveValues.size(); ++i) { if (i) out << ", "; out << result.objectiveValues[i]; }
    out << "],\n  \"complete_routes\": {";
    size_t routeIndex = 0;
    for (const auto& [flowId, route] : routes) {
        if (routeIndex++) out << ","; out << "\n    " << quote(flowId) << ": "; writeStringArray(out, route.linkPath);
    }
    out << (routes.empty() ? "" : "\n  ") << "},\n  \"complete_node_routes\": {";
    routeIndex = 0;
    for (const auto& [flowId, route] : routes) {
        if (routeIndex++) out << ","; out << "\n    " << quote(flowId) << ": "; writeStringArray(out, route.nodePath);
    }
    out << (routes.empty() ? "" : "\n  ") << "},\n  \"z3_statistics\": {";
    size_t statIndex = 0;
    for (const auto& [key, value] : result.z3Statistics) {
        if (statIndex++) out << ","; out << "\n    " << quote(key) << ": " << quote(value);
    }
    out << (result.z3Statistics.empty() ? "" : "\n  ") << "}\n}\n";
}

void SmtScalabilityBenchmark::handleMessage(cMessage *)
{
    throw cRuntimeError("SmtScalabilityBenchmark does not accept messages");
}

} // namespace tsn_fault_recovery
