#include "GateScheduleCompiler.h"

#include <algorithm>
#include <map>

using namespace omnetpp;

namespace tsn_fault_recovery {

namespace {

struct Interval
{
    int64_t start;
    int64_t end;
};

std::string gatePath(const std::string& egress, int trafficClass)
{
    return egress + ".macLayer.queue.transmissionGate[" + std::to_string(trafficClass) + "]";
}

} // namespace

std::vector<GateScheduleDefinition> GateScheduleCompiler::compile(const std::vector<GateWindow>& windows,
        int64_t cycleTicks, simtime_t quantum, int ttTrafficClass, int beTrafficClass)
{
    if (windows.empty() || cycleTicks <= 0 || quantum <= SIMTIME_ZERO)
        throw cRuntimeError("Cannot compile an empty or invalid gate schedule");
    if (ttTrafficClass < 0 || beTrafficClass < 0 || ttTrafficClass == beTrafficClass)
        throw cRuntimeError("TT and BE classes must be distinct non-negative values");

    std::map<std::string, std::vector<Interval>> grouped;
    for (const auto& window : windows) {
        if (window.trafficClass != ttTrafficClass)
            throw cRuntimeError("Window for flow '%s' uses unexpected traffic class %d",
                    window.flowId.c_str(), window.trafficClass);
        if (window.startTick < 0 || window.endTick <= window.startTick || window.endTick > cycleTicks)
            throw cRuntimeError("Invalid window [%lld,%lld) for '%s'",
                    static_cast<long long>(window.startTick), static_cast<long long>(window.endTick), window.flowId.c_str());
        grouped[window.egressInterfacePath].push_back({window.startTick, window.endTick});
    }

    std::vector<GateScheduleDefinition> definitions;
    simtime_t cycleTime = quantum * cycleTicks;
    for (auto& [egress, intervals] : grouped) {
        std::sort(intervals.begin(), intervals.end(), [](const Interval& left, const Interval& right) {
            return left.start < right.start || (left.start == right.start && left.end < right.end);
        });
        std::vector<Interval> merged;
        for (const auto& interval : intervals) {
            if (!merged.empty() && interval.start < merged.back().end)
                throw cRuntimeError("Overlapping SMT windows reached compiler for '%s'", egress.c_str());
            if (!merged.empty() && interval.start == merged.back().end)
                merged.back().end = interval.end;
            else
                merged.push_back(interval);
        }

        std::vector<simtime_t> durations;
        for (size_t i = 0; i < merged.size(); ++i) {
            durations.push_back(quantum * (merged[i].end - merged[i].start));
            int64_t nextStart = i + 1 < merged.size() ? merged[i + 1].start : cycleTicks + merged.front().start;
            int64_t closedTicks = nextStart - merged[i].end;
            if (closedTicks <= 0)
                throw cRuntimeError("TT windows consume the complete cycle on '%s'; BE complement is not representable",
                        egress.c_str());
            durations.push_back(quantum * closedTicks);
        }
        simtime_t offset = merged.front().start == 0 ? SIMTIME_ZERO : quantum * (cycleTicks - merged.front().start);
        GateScheduleDefinition tt{gatePath(egress, ttTrafficClass), ttTrafficClass, true, offset, durations};
        GateScheduleDefinition be{gatePath(egress, beTrafficClass), beTrafficClass, false, offset, durations};
        validateComplement(tt, be, cycleTime);
        definitions.push_back(tt);
        definitions.push_back(be);
    }
    return definitions;
}

void GateScheduleCompiler::validateComplement(const GateScheduleDefinition& tt,
        const GateScheduleDefinition& be, simtime_t cycleTime)
{
    if (!tt.initiallyOpen || be.initiallyOpen || tt.offset != be.offset || tt.durations != be.durations)
        throw cRuntimeError("TT/BE schedules are not exact complements");
    if (tt.durations.empty() || tt.durations.size() % 2 != 0)
        throw cRuntimeError("PeriodicGate duration count must be positive and even");
    simtime_t sum = SIMTIME_ZERO;
    for (simtime_t duration : tt.durations) {
        if (duration <= SIMTIME_ZERO)
            throw cRuntimeError("PeriodicGate durations must be strictly positive");
        sum += duration;
    }
    if (sum != cycleTime)
        throw cRuntimeError("PeriodicGate durations sum to %s instead of cycle %s",
                sum.str().c_str(), cycleTime.str().c_str());
}

} // namespace tsn_fault_recovery
