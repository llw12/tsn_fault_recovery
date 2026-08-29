#ifndef __TSN_FAULT_RECOVERY_ROUTESOLVER_H
#define __TSN_FAULT_RECOVERY_ROUTESOLVER_H

#include <set>
#include "../model/NetworkGraph.h"

namespace tsn_fault_recovery {

class RouteSolver
{
  public:
    virtual ~RouteSolver() = default;
    virtual LogicalRoute solve(const NetworkGraph& graph, const std::string& flowId,
            const std::string& source, const std::string& destination,
            const std::set<std::string>& disabledLinks = {}) const = 0;
};

} // namespace tsn_fault_recovery

#endif
