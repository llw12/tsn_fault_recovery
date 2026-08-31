#ifndef __TSN_FAULT_RECOVERY_PROFILESERIALIZER_H
#define __TSN_FAULT_RECOVERY_PROFILESERIALIZER_H
#include <map>
#include <omnetpp.h>
#include "ProfileDefinition.h"
namespace tsn_fault_recovery {
class ProfileSerializer {
  public:
    static void write(const ProfileDefinition& profile, const std::string& scenarioHash, const std::string& path);
    static ProfileDefinition parse(omnetpp::cValueMap *root, const std::string& expectedScenarioHash);
    static std::map<std::string, LogicalRoute> parseLogicalRoutes(omnetpp::cValueMap *root);
};
}
#endif
