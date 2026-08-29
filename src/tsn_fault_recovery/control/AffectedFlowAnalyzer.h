#ifndef __TSN_FAULT_RECOVERY_AFFECTEDFLOWANALYZER_H
#define __TSN_FAULT_RECOVERY_AFFECTEDFLOWANALYZER_H
#include <map>
#include "../model/NetworkGraph.h"
namespace tsn_fault_recovery { class AffectedFlowAnalyzer { public: static std::vector<std::string> affectedFlowIds(const std::map<std::string, LogicalRoute>& routes, const std::string& failedLinkId); }; }
#endif
