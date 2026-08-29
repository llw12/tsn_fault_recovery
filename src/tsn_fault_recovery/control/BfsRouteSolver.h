#ifndef __TSN_FAULT_RECOVERY_BFSROUTESOLVER_H
#define __TSN_FAULT_RECOVERY_BFSROUTESOLVER_H

#include "RouteSolver.h"

namespace tsn_fault_recovery {

class BfsRouteSolver : public RouteSolver
{
  public:
    virtual LogicalRoute solve(const NetworkGraph& graph, const std::string& flowId,
            const std::string& source, const std::string& destination,
            const std::set<std::string>& disabledLinks = {}) const override;
};

} // namespace tsn_fault_recovery

#endif
