#ifndef __TSN_FAULT_RECOVERY_ONLINEJOINTRECOVERYCONTROLLER_H
#define __TSN_FAULT_RECOVERY_ONLINEJOINTRECOVERYCONTROLLER_H

#include <memory>

#include <omnetpp.h>

#include "JointProfileSolver.h"

namespace tsn_fault_recovery {

class ProfileSwitcher;

class OnlineJointRecoveryController : public omnetpp::cSimpleModule
{
  private:
    omnetpp::cMessage *detectionEvent = nullptr;
    omnetpp::cMessage *activationEvent = nullptr;
    std::unique_ptr<JointProfileSolver> solver;
    ProfileSwitcher *activator = nullptr;
    SolverOutput solverOutput;
    omnetpp::simtime_t solverStart;
    omnetpp::simtime_t solverEnd;
    double solverWallTimeSeconds = 0;

  protected:
    virtual void initialize() override;
    virtual void handleMessage(omnetpp::cMessage *msg) override;
    void detectAndSolve();
    void activate();

  public:
    virtual ~OnlineJointRecoveryController();
};

} // namespace tsn_fault_recovery

#endif
