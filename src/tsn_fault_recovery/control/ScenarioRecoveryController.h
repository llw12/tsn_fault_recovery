#ifndef __TSN_FAULT_RECOVERY_SCENARIORECOVERYCONTROLLER_H
#define __TSN_FAULT_RECOVERY_SCENARIORECOVERYCONTROLLER_H
#include <map>
#include <omnetpp.h>
#include "ProfileDefinition.h"
#include "ScenarioRuntimeAdapter.h"
namespace tsn_fault_recovery {
class ProfileSwitcher;
class ScenarioRecoveryController : public omnetpp::cSimpleModule {
  private:
    omnetpp::cMessage *initialEvent=nullptr,*faultEvent=nullptr,*activationEvent=nullptr;
    ScenarioData scenario;
    ScenarioRuntimeAdapter adapter;
    ProfileSwitcher *switcher=nullptr;
    ProfileDefinition profile0,recoveryProfile;
    std::map<std::string,LogicalRoute> initialRoutes;
    std::string mode,faultId;
    double routeWall=0,scheduleWall=0,profileCompilationWall=0,totalWall=0;
    ProfileDefinition solveProfile(const std::string& id,const std::set<std::string>& disabled,
            const std::map<std::string,LogicalRoute> *preserved=nullptr);
    void initializeProfile();
    void handleFault();
    void activateRecovery();
    void writeFaultAnalysis() const;
  protected:
    virtual void initialize() override;
    virtual void handleMessage(omnetpp::cMessage *msg) override;
  public:
    virtual ~ScenarioRecoveryController();
};
}
#endif
