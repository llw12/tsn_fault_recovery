#ifndef __TSN_FAULT_RECOVERY_FORWARDINGREALIZABILITYVALIDATOR_H
#define __TSN_FAULT_RECOVERY_FORWARDINGREALIZABILITYVALIDATOR_H

#include <string>
#include <vector>

#include "ProfileDefinition.h"

namespace tsn_fault_recovery {

struct ForwardingValidationResult
{
    bool valid = true;
    std::string diagnostic;
};

class ForwardingRealizabilityValidator
{
  public:
    static ForwardingValidationResult validate(const std::vector<RouteDefinition>& routes);
};

} // namespace tsn_fault_recovery

#endif
