#include "ForwardingRealizabilityValidator.h"

#include <map>
#include <sstream>
#include <utility>

namespace tsn_fault_recovery {

ForwardingValidationResult ForwardingRealizabilityValidator::validate(const std::vector<RouteDefinition>& routes,
        ForwardingModel model)
{
    std::map<std::pair<std::string, std::string>, std::pair<std::string, std::string>> decisions;
    for (const auto& route : routes) {
        const std::string& identity = model == ForwardingModel::STREAM_AWARE ? route.flowId : route.destinationPath;
        if (model == ForwardingModel::STREAM_AWARE && identity.empty())
            return {false, "stream-aware forwarding entry has an empty flowId"};
        auto key = std::make_pair(route.switchPath, identity);
        auto inserted = decisions.emplace(key, std::make_pair(route.egressInterface, route.flowId));
        if (!inserted.second && inserted.first->second.first != route.egressInterface) {
            std::ostringstream diagnostic;
            diagnostic << (model == ForwardingModel::STREAM_AWARE ? "stream-aware forwarding conflict" : "destination-MAC forwarding conflict")
                       << " at switch " << route.switchPath << " for "
                       << (model == ForwardingModel::STREAM_AWARE ? "flow " : "destination ") << identity << ": flow "
                       << inserted.first->second.second << " requires " << inserted.first->second.first
                       << " while flow " << route.flowId << " requires " << route.egressInterface;
            return {false, diagnostic.str()};
        }
    }
    return {true, std::string(forwardingModelName(model)) + " forwarding decisions are realizable"};
}

} // namespace tsn_fault_recovery
