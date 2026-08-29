#include "ForwardingRealizabilityValidator.h"

#include <map>
#include <sstream>
#include <utility>

namespace tsn_fault_recovery {

ForwardingValidationResult ForwardingRealizabilityValidator::validate(const std::vector<RouteDefinition>& routes)
{
    std::map<std::pair<std::string, std::string>, std::pair<std::string, std::string>> decisions;
    for (const auto& route : routes) {
        auto key = std::make_pair(route.switchPath, route.destinationPath);
        auto inserted = decisions.emplace(key, std::make_pair(route.egressInterface, route.flowId));
        if (!inserted.second && inserted.first->second.first != route.egressInterface) {
            std::ostringstream diagnostic;
            diagnostic << "destination-MAC forwarding conflict at switch " << route.switchPath
                       << " for destination " << route.destinationPath << ": flow "
                       << inserted.first->second.second << " requires " << inserted.first->second.first
                       << " while flow " << route.flowId << " requires " << route.egressInterface;
            return {false, diagnostic.str()};
        }
    }
    return {true, "destination-MAC forwarding decisions are realizable"};
}

} // namespace tsn_fault_recovery
