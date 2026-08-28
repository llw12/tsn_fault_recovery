#include <omnetpp.h>

#include "inet/linklayer/ethernet/common/MacForwardingTable.h"
#include "inet/networklayer/common/InterfaceTable.h"
#include "inet/networklayer/common/NetworkInterface.h"

using namespace omnetpp;
using namespace inet;

class ProfileSwitcher : public cSimpleModule
{
  private:
    cMessage *switchEvent = nullptr;

  protected:
    virtual void initialize() override;
    virtual void handleMessage(cMessage *msg) override;

    void switchProfile();

  public:
    virtual ~ProfileSwitcher();
};

Define_Module(ProfileSwitcher);


void ProfileSwitcher::initialize()
{
    switchEvent = new cMessage("switchProfile");

    if (!par("enabled").boolValue()) {
        EV_INFO << "ProfileSwitcher disabled" << endl;
        return;
    }

    simtime_t switchTime = par("switchTime");

    EV_INFO << "ProfileSwitcher scheduled at "
            << switchTime << endl;

    scheduleAt(switchTime, switchEvent);
}


void ProfileSwitcher::handleMessage(cMessage *msg)
{
    if (msg == switchEvent) {
        switchProfile();
    }
    else {
        throw cRuntimeError(
            "ProfileSwitcher received unexpected message");
    }
}


void ProfileSwitcher::switchProfile()
{
    const char *switchPath =
        par("switchPath").stringValue();

    const char *destinationPath =
        par("destinationPath").stringValue();

    const char *backupInterfaceName =
        par("backupInterface").stringValue();


    // ------------------------------------------------------
    // 1. Resolve S1 and Destination
    // ------------------------------------------------------

    cModule *network = getParentModule();

    cModule *switchModule =
        network->getModuleByPath(switchPath);

    cModule *destinationModule =
        network->getModuleByPath(destinationPath);


    if (switchModule == nullptr)
        throw cRuntimeError(
            "Cannot find switch module '%s'",
            switchPath);

    if (destinationModule == nullptr)
        throw cRuntimeError(
            "Cannot find destination module '%s'",
            destinationPath);


    // ------------------------------------------------------
    // 2. Obtain S1 MAC forwarding table
    // ------------------------------------------------------

    auto *macTable =
        check_and_cast<MacForwardingTable *>(
            switchModule->getSubmodule("macTable"));


    // ------------------------------------------------------
    // 3. Obtain S1 interface table
    // ------------------------------------------------------

    auto *switchInterfaceTable =
        check_and_cast<InterfaceTable *>(
            switchModule->getSubmodule("interfaceTable"));


    NetworkInterface *backupInterface =
        switchInterfaceTable->findInterfaceByName(
            backupInterfaceName);


    if (backupInterface == nullptr)
        throw cRuntimeError(
            "Cannot find backup interface '%s' on '%s'",
            backupInterfaceName,
            switchPath);


    int backupInterfaceId =
        backupInterface->getInterfaceId();


    // ------------------------------------------------------
    // 4. Obtain Destination MAC address
    // ------------------------------------------------------

    auto *destinationInterfaceTable =
        check_and_cast<InterfaceTable *>(
            destinationModule->getSubmodule(
                "interfaceTable"));


    NetworkInterface *destinationInterface =
        destinationInterfaceTable
            ->findFirstNonLoopbackInterface();


    if (destinationInterface == nullptr)
        throw cRuntimeError(
            "Destination has no non-loopback interface");


    MacAddress destinationMac =
        destinationInterface->getMacAddress();


    // ------------------------------------------------------
    // 5. Read current route
    // ------------------------------------------------------

    int oldInterfaceId =
        macTable
            ->getUnicastAddressForwardingInterface(
                destinationMac,
                0);


    EV_INFO
        << "\n=====================================\n"
        << "PROFILE SWITCH at t=" << simTime() << "\n"
        << "Destination MAC: "
        << destinationMac << "\n"
        << "Old interface ID: "
        << oldInterfaceId << "\n"
        << "New interface: "
        << backupInterfaceName << "\n"
        << "New interface ID: "
        << backupInterfaceId << "\n"
        << "=====================================\n";


    // ------------------------------------------------------
    // 6. Switch forwarding entry:
    //
    //      S1 → S2
    //
    // becomes
    //
    //      S1 → S3
    //
    // ------------------------------------------------------

    macTable
        ->setUnicastAddressForwardingInterface(
            backupInterfaceId,
            destinationMac,
            0);


    // ------------------------------------------------------
    // 7. Verify
    // ------------------------------------------------------

    int newInterfaceId =
        macTable
            ->getUnicastAddressForwardingInterface(
                destinationMac,
                0);


    if (newInterfaceId != backupInterfaceId)
        throw cRuntimeError(
            "Profile switch verification failed");


    EV_INFO
        << "Profile switch SUCCESS: "
        << destinationMac
        << " -> "
        << backupInterfaceName
        << endl;
}


ProfileSwitcher::~ProfileSwitcher()
{
    cancelAndDelete(switchEvent);
}
