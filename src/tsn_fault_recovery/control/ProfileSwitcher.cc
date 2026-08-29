#include "ProfileSwitcher.h"

#include <chrono>
#include <sstream>

#include "PipelineScheduleGenerator.h"
#include "inet/linklayer/ethernet/common/MacForwardingTable.h"
#include "inet/networklayer/common/InterfaceTable.h"
#include "inet/networklayer/common/NetworkInterface.h"
#include "inet/queueing/gate/PeriodicGate.h"

using namespace omnetpp;
using namespace inet;
using namespace inet::queueing;

namespace tsn_fault_recovery {

namespace {

struct ResolvedRoute
{
    const RouteDefinition *definition;
    MacForwardingTable *macTable;
    NetworkInterface *egressInterface;
    MacAddress destinationMac;
    int oldInterfaceId;
};

struct GateParameters
{
    bool initiallyOpen;
    simtime_t offset;
    std::vector<simtime_t> durations;
};

struct ResolvedGate
{
    const GateScheduleDefinition *definition;
    PeriodicGate *gate;
    GateParameters oldParameters;
};

std::vector<std::string> splitWords(const char *text)
{
    std::vector<std::string> words;
    std::istringstream stream(text);
    std::string word;
    while (stream >> word)
        words.push_back(word);
    return words;
}

GateParameters readGateParameters(PeriodicGate *gate)
{
    GateParameters result;
    result.initiallyOpen = gate->par("initiallyOpen").boolValue();
    result.offset = SimTime(gate->par("offset").doubleValueInUnit("s"));
    auto *durations = check_and_cast<cValueArray *>(gate->par("durations").objectValue());
    for (int i = 0; i < durations->size(); ++i)
        result.durations.push_back(SimTime(durations->get(i).doubleValueInUnit("s")));
    return result;
}

bool sameDurations(const std::vector<simtime_t>& left, const std::vector<simtime_t>& right)
{
    return left == right;
}

std::string durationsText(const std::vector<simtime_t>& durations)
{
    std::ostringstream stream;
    stream << "[";
    for (size_t i = 0; i < durations.size(); ++i) {
        if (i != 0)
            stream << ",";
        stream << durations[i];
    }
    stream << "]";
    return stream.str();
}

} // namespace

Define_Module(ProfileSwitcher);

void ProfileSwitcher::initialize()
{
    switchEvent = new cMessage("activateProfile");
    switchEvent->setSchedulingPriority(par("activationSchedulingPriority").intValue());

    if (!par("enabled").boolValue()) {
        EV_INFO << "ProfileSwitcher disabled" << endl;
        return;
    }

    scheduledProfile = buildScheduledProfile();
    validateProfile(scheduledProfile);
    scheduleAt(par("switchTime"), switchEvent);
    EV_INFO << "Profile '" << scheduledProfile.profileId << "' scheduled at " << par("switchTime")
            << " with priority " << switchEvent->getSchedulingPriority() << endl;
}

void ProfileSwitcher::handleMessage(cMessage *msg)
{
    if (msg != switchEvent)
        throw cRuntimeError("ProfileSwitcher received unexpected message");
    activateProfile(scheduledProfile);
}

ProfileDefinition ProfileSwitcher::buildScheduledProfile() const
{
    ProfileDefinition profile;
    profile.profileId = par("profileId").stringValue();

    auto switchPaths = splitWords(par("profileRouteSwitches").stringValue());
    auto interfaces = splitWords(par("profileRouteInterfaces").stringValue());
    if (switchPaths.empty()) {
        switchPaths.push_back(par("switchPath").stringValue());
        interfaces.push_back(par("backupInterface").stringValue());
    }
    if (switchPaths.size() != interfaces.size())
        throw cRuntimeError("profileRouteSwitches and profileRouteInterfaces must contain the same number of values");
    for (size_t i = 0; i < switchPaths.size(); ++i)
        profile.routes.push_back({switchPaths[i], par("destinationPath").stringValue(), interfaces[i]});

    if (par("activateGclProfile").boolValue()) {
        auto egressPaths = splitWords(par("profileEgressInterfaces").stringValue());
        profile.gateSchedules = PipelineScheduleGenerator::generate(
                egressPaths, par("cycleTime"), par("ttWindow"), par("ttPacketBytes").intValue(),
                par("frameOverheadBytes").intValue(), par("linkBitrate").doubleValueInUnit("bps"),
                par("ttTrafficClass").intValue(), par("beTrafficClass").intValue());
    }
    return profile;
}

void ProfileSwitcher::validateProfile(const ProfileDefinition& profile) const
{
    if (profile.profileId.empty())
        throw cRuntimeError("Profile ID must not be empty");
    if (profile.routes.empty())
        throw cRuntimeError("Profile '%s' contains no forwarding entries", profile.profileId.c_str());
    for (const auto& route : profile.routes) {
        if (route.switchPath.empty() || route.destinationPath.empty() || route.egressInterface.empty())
            throw cRuntimeError("Profile '%s' contains an incomplete route entry", profile.profileId.c_str());
    }
    for (const auto& gate : profile.gateSchedules) {
        if (gate.gatePath.empty() || gate.trafficClass < 0 || gate.durations.empty() || gate.durations.size() % 2 != 0)
            throw cRuntimeError("Profile '%s' contains an invalid gate entry", profile.profileId.c_str());
    }
}

ActivationResult ProfileSwitcher::activateProfile(const ProfileDefinition& profile)
{
    validateProfile(profile);
    auto wallStart = std::chrono::steady_clock::now();
    simtime_t simulationStart = simTime();
    cModule *network = getParentModule();
    std::vector<ResolvedRoute> routes;
    std::vector<ResolvedGate> gates;

    // Resolve every target before mutating any data-plane state.
    for (const auto& definition : profile.routes) {
        cModule *switchModule = network->getModuleByPath(definition.switchPath.c_str());
        cModule *destinationModule = network->getModuleByPath(definition.destinationPath.c_str());
        if (switchModule == nullptr || destinationModule == nullptr)
            throw cRuntimeError("Cannot resolve route modules '%s' and '%s'", definition.switchPath.c_str(), definition.destinationPath.c_str());
        auto *macTable = check_and_cast<MacForwardingTable *>(switchModule->getSubmodule("macTable"));
        auto *interfaceTable = check_and_cast<InterfaceTable *>(switchModule->getSubmodule("interfaceTable"));
        auto *egress = interfaceTable->findInterfaceByName(definition.egressInterface.c_str());
        if (egress == nullptr)
            throw cRuntimeError("Cannot find interface '%s' on '%s'", definition.egressInterface.c_str(), definition.switchPath.c_str());
        auto *destinationTable = check_and_cast<InterfaceTable *>(destinationModule->getSubmodule("interfaceTable"));
        auto *destinationInterface = destinationTable->findFirstNonLoopbackInterface();
        if (destinationInterface == nullptr)
            throw cRuntimeError("Destination '%s' has no non-loopback interface", definition.destinationPath.c_str());
        MacAddress destinationMac = destinationInterface->getMacAddress();
        routes.push_back({&definition, macTable, egress, destinationMac,
                macTable->getUnicastAddressForwardingInterface(destinationMac, 0)});
    }
    for (const auto& definition : profile.gateSchedules) {
        auto *gateModule = network->getModuleByPath(definition.gatePath.c_str());
        auto *gate = dynamic_cast<PeriodicGate *>(gateModule);
        if (gate == nullptr)
            throw cRuntimeError("Cannot resolve PeriodicGate '%s'", definition.gatePath.c_str());
        gates.push_back({&definition, gate, readGateParameters(gate)});
    }

    EV_INFO << "PROFILE_ACTIVATION_BEGIN profile=" << profile.profileId << " time=" << simTime() << endl;
    for (const auto& route : routes) {
        int newId = route.egressInterface->getInterfaceId();
        route.macTable->setUnicastAddressForwardingInterface(newId, route.destinationMac, 0);
        int readback = route.macTable->getUnicastAddressForwardingInterface(route.destinationMac, 0);
        if (readback != newId)
            throw cRuntimeError("Forwarding readback failed for '%s'", route.definition->switchPath.c_str());
        EV_INFO << "PROFILE_ROUTE switch=" << route.definition->switchPath << " destination=" << route.destinationMac
                << " flow=" << route.definition->flowId << " logicalLink=" << route.definition->logicalLinkId
                << " oldInterfaceId=" << route.oldInterfaceId << " newInterface=" << route.definition->egressInterface
                << " newInterfaceId=" << newId << endl;
    }

    for (const auto& resolved : gates) {
        const auto& definition = *resolved.definition;
        auto *newDurations = new cValueArray();
        for (simtime_t duration : definition.durations)
            newDurations->add(cValue(duration.dbl(), "s"));
        cPar& durationsPar = resolved.gate->par("durations");
        durationsPar.copyIfShared();
        durationsPar.setObjectValue(newDurations);
        resolved.gate->par("initiallyOpen").setBoolValue(definition.initiallyOpen);
        resolved.gate->par("offset").setDoubleValue(definition.offset.dbl());

        GateParameters readback = readGateParameters(resolved.gate);
        if (readback.initiallyOpen != definition.initiallyOpen || readback.offset != definition.offset ||
                !sameDurations(readback.durations, definition.durations))
            throw cRuntimeError("GCL readback failed for '%s'", definition.gatePath.c_str());
        EV_INFO << "PROFILE_GCL module=" << definition.gatePath << " trafficClass=" << definition.trafficClass
                << " oldInitiallyOpen=" << resolved.oldParameters.initiallyOpen
                << " oldOffset=" << resolved.oldParameters.offset
                << " oldDurations=" << durationsText(resolved.oldParameters.durations)
                << " newInitiallyOpen=" << readback.initiallyOpen << " newOffset=" << readback.offset
                << " newDurations=" << durationsText(readback.durations) << " readback=OK" << endl;
    }

    auto wallEnd = std::chrono::steady_clock::now();
    ActivationResult result{profile.profileId, simulationStart, simTime(),
            std::chrono::duration<double>(wallEnd - wallStart).count()};
    recordScalar("profileActivation.simulationStart", result.simulationStart.dbl());
    recordScalar("profileActivation.simulationEnd", result.simulationEnd.dbl());
    recordScalar("profileActivation.wallTimeSeconds", result.wallTimeSeconds);
    recordScalar("profileActivation.routeCount", profile.routes.size());
    recordScalar("profileActivation.gateCount", profile.gateSchedules.size());
    EV_INFO << "PROFILE_ACTIVATION_END profile=" << profile.profileId << " time=" << simTime()
            << " wallSeconds=" << result.wallTimeSeconds << endl;
    return result;
}

ProfileSwitcher::~ProfileSwitcher()
{
    cancelAndDelete(switchEvent);
}

} // namespace tsn_fault_recovery
