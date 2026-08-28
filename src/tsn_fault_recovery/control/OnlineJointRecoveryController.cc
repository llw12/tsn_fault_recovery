#include "OnlineJointRecoveryController.h"

#include <chrono>
#include <sstream>

#include "DeterministicJointProfileSolver.h"
#include "ProfileSwitcher.h"

using namespace omnetpp;

namespace tsn_fault_recovery {

Define_Module(OnlineJointRecoveryController);

void OnlineJointRecoveryController::initialize()
{
    detectionEvent = new cMessage("detectFaultAndSolve");
    activationEvent = new cMessage("activateOnlineProfile");
    detectionEvent->setSchedulingPriority(par("detectionSchedulingPriority").intValue());
    activationEvent->setSchedulingPriority(par("activationSchedulingPriority").intValue());
    solver = std::make_unique<DeterministicJointProfileSolver>();

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
    // The current experiment has one configured TT flow. This explicit set is
    // the extension point for later fault-to-affected-flow classification.
    std::vector<AffectedFlow> affectedFlows = {{"TT", static_cast<int>(par("ttPacketBytes").intValue()),
            static_cast<int>(par("ttTrafficClass").intValue())}};
    SolverInput input{affectedFlows, par("cycleTime"), par("ttWindow"),
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
            << " wallSeconds=" << solverWallTimeSeconds << endl;
    recordScalar("online.failureTime", par("faultTime").doubleValueInUnit("s"));
    recordScalar("online.solverStart", solverStart.dbl());
    recordScalar("online.solverEnd", solverEnd.dbl());
    recordScalar("online.solverWallTimeSeconds", solverWallTimeSeconds);
    recordScalar("online.simulatedSolverDelay", par("solverDelay").doubleValueInUnit("s"));
    recordScalar("online.routeHopCount", solverOutput.profile.routes.size());
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
