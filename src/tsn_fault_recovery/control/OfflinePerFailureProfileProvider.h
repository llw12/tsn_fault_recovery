#ifndef __TSN_FAULT_RECOVERY_OFFLINEPERFAILUREPROFILEPROVIDER_H
#define __TSN_FAULT_RECOVERY_OFFLINEPERFAILUREPROFILEPROVIDER_H

#include <map>
#include <string>
#include <vector>
#include <omnetpp.h>
#include "JointProfileComputer.h"
#include "ProfileDefinition.h"

namespace tsn_fault_recovery {

struct OfflineProfileEntry {
    FaultProfileStatus status = FaultProfileStatus::ERROR;
    ProfileDefinition profile;
    std::vector<std::string> affectedFlowIds;
    std::string diagnostic;
};

class OfflinePerFailureProfileProvider {
  private:
    std::map<std::string, OfflineProfileEntry> entries;
    std::map<std::string, std::string> faultToClass;
  public:
    void preload(omnetpp::cValueMap *root, const std::string& expectedScenarioHash,
            const std::string& expectedSolverConfigHash);
    const OfflineProfileEntry& lookup(const std::string& faultId) const;
    void preloadExact(omnetpp::cValueMap *root, const std::string& expectedScenarioHash,
            const std::string& expectedSolverConfigHash);
    void preloadEquivalence(omnetpp::cValueMap *root, const std::string& expectedScenarioHash,
            const std::string& expectedSolverConfigHash, const std::string& expectedStrategy);
    const std::string& classForFault(const std::string& faultId) const;
    const OfflineProfileEntry& lookupClass(const std::string& classId) const;
    size_t size() const { return entries.size(); }
};

} // namespace tsn_fault_recovery
#endif
