#include <sstream>

#include <omnetpp.h>

#include "GateScheduleCompiler.h"
#include "ProfileSwitcher.h"
#include "TimeTickConverter.h"
#include "Z3ScheduleSolver.h"

using namespace omnetpp;

namespace tsn_fault_recovery {

namespace {

std::vector<std::string> splitWords(const char *text)
{
    std::istringstream stream(text);
    std::vector<std::string> words;
    std::string word;
    while (stream >> word)
        words.push_back(word);
    return words;
}

std::vector<AffectedFlow> parseFlows(cComponent *owner)
{
    auto ids = splitWords(owner->par("flowIds").stringValue());
    auto sources = splitWords(owner->par("flowSources").stringValue());
    auto destinations = splitWords(owner->par("flowDestinations").stringValue());
    auto bytes = splitWords(owner->par("flowPacketBytes").stringValue());
    auto classes = splitWords(owner->par("flowTrafficClasses").stringValue());
    auto periods = splitWords(owner->par("flowPeriods").stringValue());
    auto deadlines = splitWords(owner->par("flowDeadlines").stringValue());
    auto releases = splitWords(owner->par("flowReleaseOffsets").stringValue());
    if (ids.empty() || sources.size() != ids.size() || destinations.size() != ids.size()
            || bytes.size() != ids.size() || classes.size() != ids.size() || periods.size() != ids.size()
            || deadlines.size() != ids.size() || releases.size() != ids.size())
        throw cRuntimeError("SMT validation flow lists must have equal non-zero length");
    std::vector<AffectedFlow> flows;
    for (size_t i = 0; i < ids.size(); ++i)
        flows.push_back({ids[i], sources[i], destinations[i], std::stoi(bytes[i]), std::stoi(classes[i]),
                SimTime::parse(periods[i].c_str()), SimTime::parse(deadlines[i].c_str()),
                SimTime::parse(releases[i].c_str())});
    return flows;
}

} // namespace

class SmtValidationController : public cSimpleModule
{
  private:
    cMessage *solveEvent = nullptr;

  protected:
    virtual void initialize() override
    {
        solveEvent = new cMessage("solveAndActivateSmtProfile");
        solveEvent->setSchedulingPriority(par("schedulingPriority").intValue());
        if (par("enabled").boolValue())
            scheduleAt(par("activationTime"), solveEvent);
    }

    virtual void handleMessage(cMessage *message) override
    {
        if (message != solveEvent)
            throw cRuntimeError("SmtValidationController received an unexpected message");
        auto flows = parseFlows(this);
        auto egresses = splitWords(par("routeEgressInterfaces").stringValue());
        ScheduleRequest request{flows, std::vector<std::vector<std::string>>(flows.size(), egresses),
                par("cycleTime"), par("timeQuantum"), par("ingressMargin"), par("hopMargin"),
                static_cast<int>(par("frameOverheadBytes").intValue()),
                par("linkBitrate").doubleValueInUnit("bps"), static_cast<int>(par("beTrafficClass").intValue())};
        Z3ScheduleSolver solver;
        ScheduleResult result = solver.solve(request);
        std::string expected = par("expectedStatus").stringValue();
        EV_INFO << "SMT_VALIDATION status=" << scheduleStatusName(result.status)
                << " objectiveTicks=" << result.objectiveTicks << " wallSeconds=" << result.wallTimeSeconds
                << " diagnostic=" << result.diagnostic << endl;
        for (const auto& window : result.windows)
            EV_INFO << "SMT_WINDOW flow=" << window.flowId << " egress=" << window.egressInterfacePath
                    << " class=" << window.trafficClass << " startTick=" << window.startTick
                    << " endTick=" << window.endTick << endl;
        recordScalar("smt.status", result.status == ScheduleStatus::SAT ? 1 : result.status == ScheduleStatus::UNSAT ? 0 : -1);
        recordScalar("smt.objectiveTicks", result.objectiveTicks);
        recordScalar("smt.solverWallTimeSeconds", result.wallTimeSeconds);
        recordScalar("smt.windowCount", result.windows.size());
        if (expected != scheduleStatusName(result.status))
            throw cRuntimeError("Expected SMT status %s, got %s: %s", expected.c_str(),
                    scheduleStatusName(result.status), result.diagnostic.c_str());
        if (result.status != ScheduleStatus::SAT)
            return;

        auto switches = splitWords(par("routeSwitches").stringValue());
        auto interfaces = splitWords(par("routeInterfaces").stringValue());
        if (switches.size() != interfaces.size() || switches.size() != egresses.size())
            throw cRuntimeError("Validation route switches/interfaces/egresses must have equal length");
        ProfileDefinition profile;
        profile.profileId = par("profileId").stringValue();
        for (size_t i = 0; i < switches.size(); ++i)
            profile.routes.push_back({switches[i], par("destination").stringValue(), interfaces[i]});
        int64_t cycleTicks = TimeTickConverter::exactTicks(par("cycleTime"), par("timeQuantum"), "cycleTime");
        profile.gateSchedules = GateScheduleCompiler::compile(result.windows, cycleTicks, par("timeQuantum"),
                static_cast<int>(par("ttTrafficClass").intValue()), static_cast<int>(par("beTrafficClass").intValue()));
        auto *activator = dynamic_cast<ProfileSwitcher *>(getParentModule()->getSubmodule(par("activatorModule").stringValue()));
        if (activator == nullptr)
            throw cRuntimeError("Cannot resolve validation ProfileSwitcher");
        activator->activateProfile(profile);
    }

  public:
    virtual ~SmtValidationController()
    {
        cancelAndDelete(solveEvent);
    }
};

Define_Module(SmtValidationController);

} // namespace tsn_fault_recovery
