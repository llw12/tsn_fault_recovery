#include "AffectedFlowAnalyzer.h"
#include <algorithm>
namespace tsn_fault_recovery {
std::vector<std::string> AffectedFlowAnalyzer::affectedFlowIds(const std::map<std::string, LogicalRoute>& routes, const std::string& failedLinkId) {
    std::vector<std::string> result; for(const auto& [id,route]:routes) if(std::find(route.linkPath.begin(),route.linkPath.end(),failedLinkId)!=route.linkPath.end()) result.push_back(id); return result;
}
}
