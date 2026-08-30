#ifndef __TSN_FAULT_RECOVERY_SCENARIORECOVERYCONTROLLER_H
#define __TSN_FAULT_RECOVERY_SCENARIORECOVERYCONTROLLER_H

#include <map>
#include <omnetpp.h>
#include "JointProfileComputer.h"
#include "OfflinePerFailureProfileProvider.h"
#include "ProfileDefinition.h"
#include "ScenarioRuntimeAdapter.h"

namespace tsn_fault_recovery {
class ProfileSwitcher;

class ScenarioRecoveryController : public omnetpp::cSimpleModule {
  private:
    omnetpp::cMessage *initialEvent = nullptr, *faultEvent = nullptr, *activationEvent = nullptr;
    ScenarioData scenario;
    ScenarioRuntimeAdapter adapter;
    ProfileSwitcher *switcher = nullptr;
    ProfileDefinition profile0, recoveryProfile;
    std::map<std::string, LogicalRoute> initialRoutes;
    OfflinePerFailureProfileProvider offlineProvider;
    std::string mode, faultId;
    double offlineStoreLoadWallSeconds = 0;
    void initializeProfile();
    void precomputePerFailure();
    void precomputeExactGroup();
    void loadOfflineStore();
    void loadExactStore();
    void loadApproxStore();
    void handleFault();
    void activateRecovery();
    void writeFaultAnalysis() const;
    void recordComputation(const char *prefix, const ProfileComputationResult& result);
  protected:
    virtual void initialize() override;
    virtual void handleMessage(omnetpp::cMessage *msg) override;
  public:
    virtual ~ScenarioRecoveryController();
};
} // namespace tsn_fault_recovery
#endif
