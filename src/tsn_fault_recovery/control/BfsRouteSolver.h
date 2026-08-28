#ifndef __TSN_FAULT_RECOVERY_BFSROUTESOLVER_H
#define __TSN_FAULT_RECOVERY_BFSROUTESOLVER_H

#include "RouteSolver.h"

namespace tsn_fault_recovery {

class BfsRouteSolver : public RouteSolver
{
  public:
    virtual RoutePath solve(omnetpp::cModule *network, const FaultEvent& fault) override;
};

} // namespace tsn_fault_recovery

#endif
