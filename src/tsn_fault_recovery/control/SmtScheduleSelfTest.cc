#include <map>

#include <omnetpp.h>

#include "GateScheduleCompiler.h"
#include "TimeTickConverter.h"
#include "Z3ScheduleSolver.h"

using namespace omnetpp;

namespace tsn_fault_recovery {

namespace {

void require(bool condition, const char *message)
{
    if (!condition)
        throw cRuntimeError("SMT self-test failed: %s", message);
}

AffectedFlow flow(const char *id, int bytes, const char *deadline, const char *release = "0us")
{
    return {id, "source", "destination", bytes, 1, SimTime::parse("1ms"),
            SimTime::parse(deadline), SimTime::parse(release)};
}

ScheduleRequest request(std::vector<AffectedFlow> flows,
        std::vector<std::vector<std::string>> routes, double bitrate = 100e6)
{
    return {flows, routes, SimTime::parse("1ms"), SimTime::parse("1us"), SIMTIME_ZERO,
            SimTime::parse("5us"), 64, bitrate, 0};
}

const GateWindow& findWindow(const ScheduleResult& result, const std::string& flowId, const std::string& egress)
{
    for (const auto& window : result.windows)
        if (window.flowId == flowId && window.egressInterfacePath == egress)
            return window;
    throw cRuntimeError("SMT self-test could not find window %s/%s", flowId.c_str(), egress.c_str());
}

bool sameWindows(const std::vector<GateWindow>& left, const std::vector<GateWindow>& right)
{
    if (left.size() != right.size())
        return false;
    for (size_t i = 0; i < left.size(); ++i)
        if (left[i].flowId != right[i].flowId || left[i].egressInterfacePath != right[i].egressInterfacePath
                || left[i].trafficClass != right[i].trafficClass || left[i].startTick != right[i].startTick
                || left[i].endTick != right[i].endTick)
            return false;
    return true;
}

} // namespace

class SmtScheduleSelfTest : public cSimpleModule
{
  protected:
    virtual void initialize() override
    {
        Z3ScheduleSolver solver;
        const std::vector<std::string> route = {"s1.eth[1]", "s2.eth[1]", "s4.eth[2]"};

        // 1. Single-flow SAT.
        auto singleRequest = request({flow("TT1", 200, "300us")}, {route});
        auto single = solver.solve(singleRequest);
        require(single.status == ScheduleStatus::SAT, "single flow must be SAT");

        // 2. Two flows sharing a link never overlap.
        auto shared = solver.solve(request({flow("TT1", 200, "400us"), flow("TT2", 300, "500us")},
                {route, route}));
        require(shared.status == ScheduleStatus::SAT, "two shared-link flows must be SAT");
        for (const auto& egress : route) {
            const auto& first = findWindow(shared, "TT1", egress);
            const auto& second = findWindow(shared, "TT2", egress);
            require(first.endTick <= second.startTick || second.endTick <= first.startTick,
                    "shared-link windows overlap");
        }

        // 3. Hop precedence includes serialization plus the 5 tick margin.
        int64_t tx = TimeTickConverter::serializationTicks(200, 64, 100e6, SimTime::parse("1us"));
        for (size_t hop = 1; hop < route.size(); ++hop) {
            const auto& previous = findWindow(single, "TT1", route[hop - 1]);
            const auto& current = findWindow(single, "TT1", route[hop]);
            require(current.startTick >= previous.startTick + tx + 5, "hop precedence is not preserved");
        }

        // 4. The last completion respects the configured deadline.
        const auto& last = findWindow(single, "TT1", route.back());
        require(last.endTick <= 300, "single-flow deadline is violated");

        // 5. More serialization demand than one cycle is UNSAT.
        auto capacity = solver.solve(request({flow("BIG1", 7000, "1ms"), flow("BIG2", 7000, "1ms")},
                {{"shared.eth[0]"}, {"shared.eth[0]"}}));
        require(capacity.status == ScheduleStatus::UNSAT, "impossible capacity must be UNSAT");

        // 6. A deadline below the no-contention lower bound is UNSAT.
        auto deadline = solver.solve(request({flow("TIGHT", 200, "50us")}, {route}));
        require(deadline.status == ScheduleStatus::UNSAT, "impossible deadline must be UNSAT");

        // 7-9. Compiled PeriodicGate schedule invariants and complement.
        auto gates = GateScheduleCompiler::compile(shared.windows, 1000, SimTime::parse("1us"), 1, 0);
        require(gates.size() == route.size() * 2, "compiler must emit TT and BE gates per egress");
        for (size_t i = 0; i < gates.size(); i += 2) {
            simtime_t sum = SIMTIME_ZERO;
            for (simtime_t duration : gates[i].durations)
                sum += duration;
            require(sum == SimTime::parse("1ms"), "gate durations must sum to one cycle");
            require(gates[i].durations.size() % 2 == 0, "gate duration count must be even");
            GateScheduleCompiler::validateComplement(gates[i], gates[i + 1], SimTime::parse("1ms"));
        }

        // 10. Identical input has an identical optimized model.
        auto repeated = solver.solve(singleRequest);
        require(repeated.status == ScheduleStatus::SAT && repeated.objectiveTicks == single.objectiveTicks
                && sameWindows(repeated.windows, single.windows), "solver output is not deterministic");

        // 11-15. Instrumentation is exact and feasibility uses the same hard model without objectives.
        require(single.startTimeVarCount == 3 && single.otherAuxVarCount == 1
                && single.orderingBoolVarCount == 0 && single.totalSymbolicVarCount == 4,
                "symbolic variable counters are incorrect");
        require(single.cycleBoundConstraintCount == 6 && single.releaseConstraintCount == 1
                && single.hopPrecedenceConstraintCount == 2 && single.deadlineConstraintCount == 1
                && single.nonOverlapConstraintCount == 0 && single.otherHardConstraintCount == 2
                && single.totalHardConstraintCount == 12, "hard-constraint counters are incorrect");
        require(shared.nonOverlapConstraintCount == 3 && shared.contentionPairCount == 3,
                "contention/non-overlap counters are incorrect");
        require(single.objectiveCount == 5, "production objective count changed");
        auto feasible = solver.solveFeasibilityOnly(singleRequest);
        require(feasible.status == ScheduleStatus::SAT && feasible.objectiveCount == 0
                && feasible.windows.empty() && feasible.totalHardConstraintCount == single.totalHardConstraintCount,
                "feasibility-only mode is not using the same hard constraints");

        recordScalar("testsPassed", 15);
        EV_INFO << "SMT_SCHEDULE_SELF_TEST PASS tests=15" << endl;
    }

    virtual void handleMessage(cMessage *) override
    {
        throw cRuntimeError("SmtScheduleSelfTest does not accept messages");
    }
};

Define_Module(SmtScheduleSelfTest);

} // namespace tsn_fault_recovery
