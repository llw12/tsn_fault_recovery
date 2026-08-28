#ifndef __TSN_FAULT_RECOVERY_BFSZ3JOINTPROFILESOLVER_H
#define __TSN_FAULT_RECOVERY_BFSZ3JOINTPROFILESOLVER_H

#include "JointProfileSolver.h"

namespace tsn_fault_recovery {

// Routing is selected by BFS. Z3 optimizes TAS windows only on that fixed path.
class BfsZ3JointProfileSolver : public JointProfileSolver
{
  public:
    virtual SolverOutput solve(omnetpp::cModule *network, const FaultEvent& fault, const SolverInput& input) override;
};

} // namespace tsn_fault_recovery

#endif
