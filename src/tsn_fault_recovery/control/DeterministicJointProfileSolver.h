#ifndef __TSN_FAULT_RECOVERY_DETERMINISTICJOINTPROFILESOLVER_H
#define __TSN_FAULT_RECOVERY_DETERMINISTICJOINTPROFILESOLVER_H

#include "JointProfileSolver.h"

namespace tsn_fault_recovery {

class DeterministicJointProfileSolver : public JointProfileSolver
{
  public:
    virtual SolverOutput solve(omnetpp::cModule *network, const FaultEvent& fault, const SolverInput& input) override;
};

} // namespace tsn_fault_recovery

#endif
