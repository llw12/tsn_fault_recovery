#include "OnlineJointRecoveryController.h"

#include <chrono>
#include <sstream>

#include "BfsZ3JointProfileSolver.h"
#include "DeterministicJointProfileSolver.h"
#include "ProfileSwitcher.h"

using namespace omnetpp;

namespace tsn_fault_recovery {

namespace {

std::vector<std::string> splitWords(const char *text)
{
    std::istringstream stream(text);
    std::vector<std::string> values;
    std::string value;
    while (stream >> value)
        values.push_back(value);
    return values;
}

std::vector<AffectedFlow> parseAffectedFlows(cComponent *owner)
{
    auto ids = splitWords(owner->par("flowIds").stringValue());
    if (ids.empty()) {
        return {{"TT", owner->par("source").stringValue(), owner->par("destination").stringValue(),
                static_cast<int>(owner->par("ttPacketBytes").intValue()),
                static_cast<int>(owner->par("ttTrafficClass").intValue()), owner->par("cycleTime"),
                owner->par("cycleTime"), SIMTIME_ZERO}};
    }
    auto sources = splitWords(owner->par("flowSources").stringValue());
    auto destinations = splitWords(owner->par("flowDestinations").stringValue());
    auto packetBytes = splitWords(owner->par("flowPacketBytes").stringValue());
    auto classes = splitWords(owner->par("flowTrafficClasses").stringValue());
    auto periods = splitWords(owner->par("flowPeriods").stringValue());
    auto deadlines = splitWords(owner->par("flowDeadlines").stringValue());
    auto releases = splitWords(owner->par("flowReleaseOffsets").stringValue());
    if (sources.size() != ids.size() || destinations.size() != ids.size() || packetBytes.size() != ids.size()
            || classes.size() != ids.size() || periods.size() != ids.size() || deadlines.size() != ids.size()
            || releases.size() != ids.size())
        throw cRuntimeError("All flow configuration lists must have the same length");
    std::vector<AffectedFlow> flows;
    for (size_t i = 0; i < ids.size(); ++i)
        flows.push_back({ids[i], sources[i], destinations[i], std::stoi(packetBytes[i]), std::stoi(classes[i]),
                SimTime::parse(periods[i].c_str()), SimTime::parse(deadlines[i].c_str()),
                SimTime::parse(releases[i].c_str())});
    return flows;
}

} // namespace

Define_Module(OnlineJointRecoveryController);

void OnlineJointRecoveryController::initialize()
{
    detectionEvent = new cMessage("detectFaultAndSolve");
    activationEvent = new cMessage("activateOnlineProfile");
    detectionEvent->setSchedulingPriority(par("detectionSchedulingPriority").intValue());
    activationEvent->setSchedulingPriority(par("activationSchedulingPriority").intValue());
    std::string backend = par("solverBackend").stringValue();
    if (backend == "deterministic")
        solver = std::make_unique<DeterministicJointProfileSolver>();
    else if (backend == "z3")
        solver = std::make_unique<BfsZ3JointProfileSolver>();
    else
        throw cRuntimeError("Unknown solverBackend '%s'", backend.c_str());

    if (!par("enabled").boolValue())
        return;
    activator = dynamic_cast<ProfileSwitcher *>(getParentModule()->getSubmodule(par("activatorModule").stringValue()));
    if (activator == nullptr)
        throw cRuntimeError("Cannot resolve ProfileSwitcher '%s'", par("activatorModule").stringValue());
    scheduleAt(par("faultTime"), detectionEvent);
}

void OnlineJointRecoveryController::handleMessage(cMessage *msg)
{
    if (msg == detectionEvent)
        detectAndSolve();
    else if (msg == activationEvent)
        activate();
    else
        throw cRuntimeError("OnlineJointRecoveryController received an unexpected message");
}

void OnlineJointRecoveryController::detectAndSolve()
{
    solverStart = simTime();
    FaultEvent fault{simTime(), par("failedEndpointA").stringValue(), par("failedEndpointB").stringValue(),
            par("routeStart").stringValue(), par("destination").stringValue()};
    std::vector<AffectedFlow> affectedFlows = parseAffectedFlows(this);
    SolverInput input{affectedFlows, par("cycleTime"), par("timeQuantum"), par("ingressMargin"), par("hopMargin"), par("ttWindow"),
            static_cast<int>(par("frameOverheadBytes").intValue()), par("linkBitrate").doubleValueInUnit("bps"),
            static_cast<int>(par("beTrafficClass").intValue())};

    auto wallStart = std::chrono::steady_clock::now();
    solverOutput = solver->solve(getParentModule(), fault, input);
    auto wallEnd = std::chrono::steady_clock::now();
    solverEnd = simTime();
    solverWallTimeSeconds = std::chrono::duration<double>(wallEnd - wallStart).count();

    std::ostringstream path;
    for (size_t i = 0; i < solverOutput.nodePath.size(); ++i) {
        if (i != 0)
            path << "->";
        path << solverOutput.nodePath[i];
    }
    EV_INFO << "ONLINE_SOLVER profile=" << solverOutput.profile.profileId << " path=" << path.str()
            << " simulationStart=" << solverStart << " simulationEnd=" << solverEnd
            << " status=" << scheduleStatusName(solverOutput.scheduleStatus)
            << " objectiveTicks=" << solverOutput.objectiveTicks
            << " routeWallSeconds=" << solverOutput.routeSolverWallTimeSeconds
            << " scheduleWallSeconds=" << solverOutput.scheduleSolverWallTimeSeconds
            << " totalWallSeconds=" << solverWallTimeSeconds << endl;
    for (const auto& window : solverOutput.logicalWindows)
        EV_INFO << "SMT_WINDOW flow=" << window.flowId << " egress=" << window.egressInterfacePath
                << " class=" << window.trafficClass << " startTick=" << window.startTick
                << " endTick=" << window.endTick << endl;
    recordScalar("online.failureTime", par("faultTime").doubleValueInUnit("s"));
    recordScalar("online.solverStart", solverStart.dbl());
    recordScalar("online.solverEnd", solverEnd.dbl());
    recordScalar("online.solverWallTimeSeconds", solverWallTimeSeconds);
    recordScalar("online.routeSolverWallTimeSeconds", solverOutput.routeSolverWallTimeSeconds);
    recordScalar("online.scheduleSolverWallTimeSeconds", solverOutput.scheduleSolverWallTimeSeconds);
    recordScalar("online.scheduleStatus", solverOutput.scheduleStatus == ScheduleStatus::SAT ? 1 :
            solverOutput.scheduleStatus == ScheduleStatus::UNSAT ? 0 : -1);
    recordScalar("online.scheduleObjectiveTicks", solverOutput.objectiveTicks);
    recordScalar("online.simulatedSolverDelay", par("solverDelay").doubleValueInUnit("s"));
    recordScalar("online.routeHopCount", solverOutput.profile.routes.size());
    if (solverOutput.scheduleStatus != ScheduleStatus::SAT)
        throw cRuntimeError("Online schedule solver returned %s: %s",
                scheduleStatusName(solverOutput.scheduleStatus), solverOutput.diagnostic.c_str());
    scheduleAt(simTime() + par("solverDelay"), activationEvent);
}

void OnlineJointRecoveryController::activate()
{
    simtime_t start = simTime();
    ActivationResult result = activator->activateProfile(solverOutput.profile);
    recordScalar("online.activationStart", start.dbl());
    recordScalar("online.activationEnd", result.simulationEnd.dbl());
    recordScalar("online.activationWallTimeSeconds", result.wallTimeSeconds);
}

OnlineJointRecoveryController::~OnlineJointRecoveryController()
{
    cancelAndDelete(detectionEvent);
    cancelAndDelete(activationEvent);
}

} // namespace tsn_fault_recovery
