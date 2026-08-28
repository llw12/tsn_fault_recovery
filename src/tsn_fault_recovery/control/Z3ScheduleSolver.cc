#include "Z3ScheduleSolver.h"

#include <cctype>
#include <algorithm>
#include <chrono>
#include <map>
#include <set>
#include <sstream>

#include <z3++.h>

#include "TimeTickConverter.h"

using namespace omnetpp;

namespace tsn_fault_recovery {

namespace {

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
        int64_t deadline = TimeTickConverter::exactTicks(request.flows[f].deadline, request.timeQuantum, "deadline");
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

} // namespace

ScheduleResult Z3ScheduleSolver::solve(const ScheduleRequest& request)
{
    auto wallStart = std::chrono::steady_clock::now();
    if (request.flows.empty() || request.flows.size() != request.routeEgressPaths.size())
        throw cRuntimeError("SMT request must contain one fixed route per affected flow");
    int64_t cycleTicks = TimeTickConverter::exactTicks(request.cycleTime, request.timeQuantum, "cycleTime");
    int64_t ingressTicks = TimeTickConverter::exactTicks(request.ingressMargin, request.timeQuantum, "ingressMargin");
    int64_t marginTicks = TimeTickConverter::exactTicks(request.hopMargin, request.timeQuantum, "hopMargin");
    std::set<std::string> flowIds;
    std::vector<int64_t> txTicks;
    for (size_t f = 0; f < request.flows.size(); ++f) {
        const auto& flow = request.flows[f];
        if (flow.flowId.empty() || !flowIds.insert(flow.flowId).second)
            throw cRuntimeError("Affected flow IDs must be non-empty and unique");
        if (flow.source.empty() || flow.destination.empty() || flow.trafficClass < 0 || request.routeEgressPaths[f].empty())
            throw cRuntimeError("Flow '%s' has incomplete identity or route data", flow.flowId.c_str());
        if (TimeTickConverter::exactTicks(flow.period, request.timeQuantum, "period") != cycleTicks)
            throw cRuntimeError("Flow '%s' period must equal the single-cycle hyperperiod", flow.flowId.c_str());
        TimeTickConverter::exactTicks(flow.deadline, request.timeQuantum, "deadline");
        TimeTickConverter::exactTicks(flow.releaseOffset, request.timeQuantum, "releaseOffset");
        txTicks.push_back(TimeTickConverter::serializationTicks(flow.packetBytes,
                request.frameOverheadBytes, request.linkBitrate, request.timeQuantum));
    }

    z3::context context;
    z3::optimize optimizer(context);
    std::vector<std::vector<z3::expr>> starts;
    for (size_t f = 0; f < request.flows.size(); ++f) {
        std::vector<z3::expr> flowStarts;
        for (size_t h = 0; h < request.routeEgressPaths[f].size(); ++h)
            flowStarts.push_back(context.int_const(variableName(request.flows[f].flowId, h).c_str()));
        starts.push_back(flowStarts);
    }

    z3::expr maxCompletion = context.int_const("maxCompletion");
    optimizer.add(maxCompletion >= 0);
    z3::expr totalCompletion = integer(context, 0);
    for (size_t f = 0; f < request.flows.size(); ++f) {
        int64_t release = TimeTickConverter::exactTicks(request.flows[f].releaseOffset, request.timeQuantum, "releaseOffset");
        int64_t deadline = TimeTickConverter::exactTicks(request.flows[f].deadline, request.timeQuantum, "deadline");
        for (size_t h = 0; h < starts[f].size(); ++h) {
            optimizer.add(starts[f][h] >= 0);
            optimizer.add(starts[f][h] + integer(context, txTicks[f]) <= integer(context, cycleTicks));
            if (h == 0)
                optimizer.add(starts[f][h] >= integer(context, release + ingressTicks));
            else
                optimizer.add(starts[f][h] >= starts[f][h - 1] + integer(context, txTicks[f] + marginTicks));
        }
        z3::expr completion = starts[f].back() + integer(context, txTicks[f]);
        optimizer.add(completion - integer(context, release) <= integer(context, deadline));
        optimizer.add(maxCompletion >= completion);
        totalCompletion = totalCompletion + completion;
    }

    for (size_t leftFlow = 0; leftFlow < request.flows.size(); ++leftFlow) {
        for (size_t leftHop = 0; leftHop < starts[leftFlow].size(); ++leftHop) {
            for (size_t rightFlow = leftFlow; rightFlow < request.flows.size(); ++rightFlow) {
                size_t firstRightHop = rightFlow == leftFlow ? leftHop + 1 : 0;
                for (size_t rightHop = firstRightHop; rightHop < starts[rightFlow].size(); ++rightHop) {
                    if (request.routeEgressPaths[leftFlow][leftHop] != request.routeEgressPaths[rightFlow][rightHop])
                        continue;
                    optimizer.add(starts[leftFlow][leftHop] + integer(context, txTicks[leftFlow]) <= starts[rightFlow][rightHop]
                            || starts[rightFlow][rightHop] + integer(context, txTicks[rightFlow]) <= starts[leftFlow][leftHop]);
                }
            }
        }
    }

    optimizer.minimize(maxCompletion);
    optimizer.minimize(totalCompletion);
    std::vector<size_t> stableFlowOrder(request.flows.size());
    for (size_t i = 0; i < stableFlowOrder.size(); ++i)
        stableFlowOrder[i] = i;
    std::sort(stableFlowOrder.begin(), stableFlowOrder.end(), [&](size_t left, size_t right) {
        return request.flows[left].flowId < request.flows[right].flowId;
    });
    for (size_t f : stableFlowOrder)
        for (const auto& start : starts[f])
            optimizer.minimize(start);

    ScheduleResult output;
    z3::check_result status = optimizer.check();
    if (status == z3::sat) {
        output.status = ScheduleStatus::SAT;
        z3::model model = optimizer.get_model();
        if (!model.eval(maxCompletion, true).is_numeral_i64(output.objectiveTicks))
            throw cRuntimeError("Z3 returned a non-integer objective");
        for (size_t f = 0; f < request.flows.size(); ++f) {
            for (size_t h = 0; h < starts[f].size(); ++h) {
                int64_t startTick;
                if (!model.eval(starts[f][h], true).is_numeral_i64(startTick))
                    throw cRuntimeError("Z3 returned a non-integer window start");
                output.windows.push_back({request.flows[f].flowId, request.routeEgressPaths[f][h],
                        request.flows[f].trafficClass, startTick, startTick + txTicks[f]});
            }
        }
        std::sort(output.windows.begin(), output.windows.end(), [](const GateWindow& left, const GateWindow& right) {
            if (left.egressInterfacePath != right.egressInterfacePath)
                return left.egressInterfacePath < right.egressInterfacePath;
            if (left.startTick != right.startTick)
                return left.startTick < right.startTick;
            return left.flowId < right.flowId;
        });
        output.diagnostic = "lexicographic optimum found";
    }
    else if (status == z3::unsat) {
        output.status = ScheduleStatus::UNSAT;
        output.diagnostic = explainUnsat(request, cycleTicks, ingressTicks, marginTicks, txTicks);
    }
    else {
        output.status = ScheduleStatus::UNKNOWN;
        output.diagnostic = "Z3 Optimize returned UNKNOWN";
    }
    output.wallTimeSeconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - wallStart).count();
    return output;
}

} // namespace tsn_fault_recovery
