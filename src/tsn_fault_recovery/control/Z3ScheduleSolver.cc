#include "Z3ScheduleSolver.h"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <iomanip>
#include <map>
#include <set>
#include <sstream>

#include <z3++.h>

#include "TimeTickConverter.h"

using namespace omnetpp;

namespace tsn_fault_recovery {
namespace {

using Clock = std::chrono::steady_clock;

simtime_t schedulingDeadline(const AffectedFlow& flow)
{
    return flow.scheduleDeadlineBudget > SIMTIME_ZERO ? flow.scheduleDeadlineBudget : flow.deadline;
}

z3::expr integer(z3::context& context, int64_t value)
{
    return context.int_val(std::to_string(value).c_str());
}

std::string variableName(const std::string& flowId, size_t hop)
{
    std::string result = "x_";
    for (char ch : flowId)
        result += std::isalnum(static_cast<unsigned char>(ch)) ? ch : '_';
    return result + "_" + std::to_string(hop);
}

std::string explainUnsat(const ScheduleRequest& request, int64_t cycleTicks,
        int64_t ingressTicks, int64_t marginTicks, const std::vector<int64_t>& txTicks)
{
    for (size_t f = 0; f < request.flows.size(); ++f) {
        int64_t release = TimeTickConverter::exactTicks(request.flows[f].releaseOffset, request.timeQuantum, "releaseOffset");
        int64_t deadline = TimeTickConverter::exactTicks(schedulingDeadline(request.flows[f]), request.timeQuantum, "scheduleDeadlineBudget");
        int64_t hops = request.routeEgressPaths[f].size();
        int64_t minimumCompletion = release + ingressTicks + hops * txTicks[f]
                + std::max<int64_t>(0, hops - 1) * marginTicks;
        if (minimumCompletion - release > deadline)
            return "minimum route serialization plus hop margins exceeds deadline for flow " + request.flows[f].flowId;
    }
    std::map<std::string, int64_t> occupancy;
    for (size_t f = 0; f < request.flows.size(); ++f)
        for (const auto& egress : request.routeEgressPaths[f])
            occupancy[egress] += txTicks[f];
    for (const auto& [egress, ticks] : occupancy)
        if (ticks > cycleTicks)
            return "required TT serialization exceeds cycle capacity on " + egress;
    return "combined cycle, precedence, non-overlap, and deadline constraints are inconsistent";
}

void collectStatistics(const z3::stats& statistics, ScheduleResult& output)
{
    for (unsigned i = 0; i < statistics.size(); ++i) {
        std::ostringstream value;
        value << std::setprecision(17);
        if (statistics.is_uint(i)) value << statistics.uint_value(i);
        else value << statistics.double_value(i);
        output.z3Statistics[statistics.key(i)] = value.str();
    }
}

class HardConstraintModelBuilder
{
  public:
    const ScheduleRequest& request;
    z3::context& context;
    int64_t cycleTicks;
    int64_t ingressTicks;
    int64_t marginTicks;
    std::vector<int64_t> txTicks;
    std::vector<std::vector<z3::expr>> starts;
    z3::expr maxCompletion;
    z3::expr totalCompletion;
    ScheduleResult& metrics;

    HardConstraintModelBuilder(const ScheduleRequest& request, z3::context& context, ScheduleResult& metrics) :
        request(request), context(context),
        cycleTicks(TimeTickConverter::exactTicks(request.cycleTime, request.timeQuantum, "cycleTime")),
        ingressTicks(TimeTickConverter::exactTicks(request.ingressMargin, request.timeQuantum, "ingressMargin")),
        marginTicks(TimeTickConverter::exactTicks(request.hopMargin, request.timeQuantum, "hopMargin")),
        maxCompletion(context.int_const("maxCompletion")), totalCompletion(integer(context, 0)), metrics(metrics)
    {
        validateAndCreateVariables();
        collectStructureMetrics();
    }

    template<typename Add>
    void addHardConstraints(Add add)
    {
        add(maxCompletion >= 0); ++metrics.otherHardConstraintCount;
        for (size_t f = 0; f < request.flows.size(); ++f) {
            int64_t release = TimeTickConverter::exactTicks(request.flows[f].releaseOffset, request.timeQuantum, "releaseOffset");
            int64_t deadline = TimeTickConverter::exactTicks(schedulingDeadline(request.flows[f]), request.timeQuantum, "scheduleDeadlineBudget");
            for (size_t h = 0; h < starts[f].size(); ++h) {
                add(starts[f][h] >= 0);
                add(starts[f][h] + integer(context, txTicks[f]) <= integer(context, cycleTicks));
                metrics.cycleBoundConstraintCount += 2;
                if (h == 0) {
                    add(starts[f][h] >= integer(context, release + ingressTicks));
                    ++metrics.releaseConstraintCount;
                }
                else {
                    add(starts[f][h] >= starts[f][h - 1] + integer(context, txTicks[f] + marginTicks));
                    ++metrics.hopPrecedenceConstraintCount;
                }
            }
            z3::expr completion = starts[f].back() + integer(context, txTicks[f]);
            add(completion - integer(context, release) <= integer(context, deadline));
            ++metrics.deadlineConstraintCount;
            add(maxCompletion >= completion);
            ++metrics.otherHardConstraintCount;
            totalCompletion = totalCompletion + completion;
        }
        for (size_t lf = 0; lf < request.flows.size(); ++lf) {
            for (size_t lh = 0; lh < starts[lf].size(); ++lh) {
                for (size_t rf = lf; rf < request.flows.size(); ++rf) {
                    size_t firstRightHop = rf == lf ? lh + 1 : 0;
                    for (size_t rh = firstRightHop; rh < starts[rf].size(); ++rh) {
                        if (request.routeEgressPaths[lf][lh] != request.routeEgressPaths[rf][rh]) continue;
                        add(starts[lf][lh] + integer(context, txTicks[lf]) <= starts[rf][rh]
                                || starts[rf][rh] + integer(context, txTicks[rf]) <= starts[lf][lh]);
                        ++metrics.nonOverlapConstraintCount;
                    }
                }
            }
        }
        metrics.totalHardConstraintCount = metrics.cycleBoundConstraintCount + metrics.releaseConstraintCount
                + metrics.hopPrecedenceConstraintCount + metrics.deadlineConstraintCount
                + metrics.nonOverlapConstraintCount + metrics.otherHardConstraintCount;
    }

  private:
    void validateAndCreateVariables()
    {
        if (request.flows.empty() || request.flows.size() != request.routeEgressPaths.size())
            throw cRuntimeError("SMT request must contain one fixed route per affected flow");
        std::set<std::string> flowIds;
        for (size_t f = 0; f < request.flows.size(); ++f) {
            const auto& flow = request.flows[f];
            if (flow.flowId.empty() || !flowIds.insert(flow.flowId).second)
                throw cRuntimeError("Affected flow IDs must be non-empty and unique");
            if (flow.source.empty() || flow.destination.empty() || flow.trafficClass < 0 || request.routeEgressPaths[f].empty())
                throw cRuntimeError("Flow '%s' has incomplete identity or route data", flow.flowId.c_str());
            if (TimeTickConverter::exactTicks(flow.period, request.timeQuantum, "period") != cycleTicks)
                throw cRuntimeError("Flow '%s' period must equal the single-cycle hyperperiod", flow.flowId.c_str());
            TimeTickConverter::exactTicks(schedulingDeadline(flow), request.timeQuantum, "scheduleDeadlineBudget");
            TimeTickConverter::exactTicks(flow.releaseOffset, request.timeQuantum, "releaseOffset");
            txTicks.push_back(TimeTickConverter::serializationTicks(flow.packetBytes,
                    request.frameOverheadBytes, request.linkBitrate, request.timeQuantum));
            std::vector<z3::expr> flowStarts;
            for (size_t h = 0; h < request.routeEgressPaths[f].size(); ++h)
                flowStarts.push_back(context.int_const(variableName(flow.flowId, h).c_str()));
            starts.push_back(flowStarts);
        }
    }

    void collectStructureMetrics()
    {
        metrics.activeTtFlowCount = request.flows.size();
        std::map<std::string, std::set<std::string>> egressFlows;
        for (size_t f = 0; f < request.flows.size(); ++f) {
            metrics.controlledHopCount += request.routeEgressPaths[f].size();
            for (const auto& egress : request.routeEgressPaths[f])
                egressFlows[egress].insert(request.flows[f].flowId);
        }
        metrics.egressCount = egressFlows.size();
        int64_t flowUses = 0;
        for (const auto& [egress, flows] : egressFlows) {
            int count = flows.size(); flowUses += count;
            metrics.maxFlowsPerEgress = std::max(metrics.maxFlowsPerEgress, count);
            if (count > 1) { ++metrics.contentedEgressCount; ++metrics.sharedEgressCount; }
            metrics.contentionPairCount += static_cast<int64_t>(count) * (count - 1) / 2;
        }
        metrics.meanFlowsPerUsedEgress = egressFlows.empty() ? 0 : static_cast<double>(flowUses) / egressFlows.size();
        metrics.startTimeVarCount = metrics.controlledHopCount;
        metrics.orderingBoolVarCount = 0;
        metrics.otherAuxVarCount = 1;
        metrics.totalSymbolicVarCount = metrics.startTimeVarCount + metrics.otherAuxVarCount;
    }
};

std::vector<size_t> stableFlowOrder(const ScheduleRequest& request)
{
    std::vector<size_t> result(request.flows.size());
    for (size_t i = 0; i < result.size(); ++i) result[i] = i;
    std::sort(result.begin(), result.end(), [&](size_t left, size_t right) {
        return request.flows[left].flowId < request.flows[right].flowId;
    });
    return result;
}

void extractModel(const ScheduleRequest& request, HardConstraintModelBuilder& builder,
        const z3::model& model, ScheduleResult& output, bool emitWindows)
{
    if (!model.eval(builder.maxCompletion, true).is_numeral_i64(output.objectiveTicks))
        throw cRuntimeError("Z3 returned a non-integer objective");
    int64_t totalCompletionTicks;
    if (model.eval(builder.totalCompletion, true).is_numeral_i64(totalCompletionTicks))
        output.objectiveValues.push_back(totalCompletionTicks);
    for (size_t f : stableFlowOrder(request)) {
        for (size_t h = 0; h < builder.starts[f].size(); ++h) {
            int64_t startTick;
            if (!model.eval(builder.starts[f][h], true).is_numeral_i64(startTick))
                throw cRuntimeError("Z3 returned a non-integer window start");
            output.objectiveValues.push_back(startTick);
            if (emitWindows)
                output.windows.push_back({request.flows[f].flowId, request.routeEgressPaths[f][h],
                        request.flows[f].trafficClass, startTick, startTick + builder.txTicks[f]});
        }
    }
    output.objectiveValues.insert(output.objectiveValues.begin(), output.objectiveTicks);
    if (emitWindows)
        std::sort(output.windows.begin(), output.windows.end(), [](const GateWindow& left, const GateWindow& right) {
            if (left.egressInterfacePath != right.egressInterfacePath) return left.egressInterfacePath < right.egressInterfacePath;
            if (left.startTick != right.startTick) return left.startTick < right.startTick;
            return left.flowId < right.flowId;
        });
}

ScheduleResult solveRequest(const ScheduleRequest& request, bool optimize)
{
    auto wallStart = Clock::now();
    ScheduleResult output;
    z3::context context;
    auto buildStart = Clock::now();
    HardConstraintModelBuilder builder(request, context, output);
    if (optimize) {
        z3::optimize solver(context);
        if (request.solverTimeoutMs > 0) {
            z3::params parameters(context); parameters.set("timeout", static_cast<unsigned>(request.solverTimeoutMs)); solver.set(parameters);
        }
        builder.addHardConstraints([&](const z3::expr& constraint) { solver.add(constraint); });
        solver.minimize(builder.maxCompletion);
        solver.minimize(builder.totalCompletion);
        for (size_t f : stableFlowOrder(request)) for (const auto& start : builder.starts[f]) solver.minimize(start);
        output.objectiveCount = 2 + output.startTimeVarCount;
        output.modelBuildWallSeconds = std::chrono::duration<double>(Clock::now() - buildStart).count();
        auto checkStart = Clock::now(); z3::check_result status = solver.check();
        output.z3CheckWallSeconds = std::chrono::duration<double>(Clock::now() - checkStart).count();
        collectStatistics(solver.statistics(), output);
        if (status == z3::sat) {
            output.status = ScheduleStatus::SAT; auto extractStart = Clock::now();
            extractModel(request, builder, solver.get_model(), output, true);
            output.modelExtractWallSeconds = std::chrono::duration<double>(Clock::now() - extractStart).count();
            output.diagnostic = "lexicographic optimum found";
        }
        else if (status == z3::unsat) {
            output.status = ScheduleStatus::UNSAT;
            output.diagnostic = explainUnsat(request, builder.cycleTicks, builder.ingressTicks, builder.marginTicks, builder.txTicks);
        }
        else {
            output.status = ScheduleStatus::UNKNOWN;
            output.reasonUnknown = Z3_optimize_get_reason_unknown(context, solver);
            output.diagnostic = "Z3 Optimize returned UNKNOWN: " + output.reasonUnknown;
        }
    }
    else {
        z3::solver solver(context);
        if (request.solverTimeoutMs > 0) {
            z3::params parameters(context); parameters.set("timeout", static_cast<unsigned>(request.solverTimeoutMs)); solver.set(parameters);
        }
        builder.addHardConstraints([&](const z3::expr& constraint) { solver.add(constraint); });
        output.objectiveCount = 0;
        output.modelBuildWallSeconds = std::chrono::duration<double>(Clock::now() - buildStart).count();
        auto checkStart = Clock::now(); z3::check_result status = solver.check();
        output.z3CheckWallSeconds = std::chrono::duration<double>(Clock::now() - checkStart).count();
        collectStatistics(solver.statistics(), output);
        if (status == z3::sat) {
            output.status = ScheduleStatus::SAT; auto extractStart = Clock::now();
            extractModel(request, builder, solver.get_model(), output, false);
            output.modelExtractWallSeconds = std::chrono::duration<double>(Clock::now() - extractStart).count();
            output.objectiveTicks = -1; output.objectiveValues.clear();
            output.diagnostic = "hard constraints are satisfiable (benchmark-only)";
        }
        else if (status == z3::unsat) {
            output.status = ScheduleStatus::UNSAT;
            output.diagnostic = explainUnsat(request, builder.cycleTicks, builder.ingressTicks, builder.marginTicks, builder.txTicks);
        }
        else {
            output.status = ScheduleStatus::UNKNOWN; output.reasonUnknown = solver.reason_unknown();
            output.diagnostic = "Z3 Solver returned UNKNOWN: " + output.reasonUnknown;
        }
    }
    output.wallTimeSeconds = std::chrono::duration<double>(Clock::now() - wallStart).count();
    return output;
}

} // namespace

ScheduleResult Z3ScheduleSolver::solve(const ScheduleRequest& request) { return solveRequest(request, true); }
ScheduleResult Z3ScheduleSolver::solveFeasibilityOnly(const ScheduleRequest& request) { return solveRequest(request, false); }

} // namespace tsn_fault_recovery
