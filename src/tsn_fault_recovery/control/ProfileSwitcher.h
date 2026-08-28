#ifndef __TSN_FAULT_RECOVERY_PROFILESWITCHER_H
#define __TSN_FAULT_RECOVERY_PROFILESWITCHER_H

#include <omnetpp.h>

#include "ProfileDefinition.h"

namespace tsn_fault_recovery {

class ProfileSwitcher : public omnetpp::cSimpleModule
{
  private:
    omnetpp::cMessage *switchEvent = nullptr;
    ProfileDefinition scheduledProfile;

  protected:
    virtual void initialize() override;
    virtual void handleMessage(omnetpp::cMessage *msg) override;
    ProfileDefinition buildScheduledProfile() const;
    void validateProfile(const ProfileDefinition& profile) const;

  public:
    virtual ~ProfileSwitcher();
    ActivationResult activateProfile(const ProfileDefinition& profile);
};

} // namespace tsn_fault_recovery

#endif
