#ifndef __TSN_FAULT_RECOVERY_Z3SCHEDULESOLVER_H
#define __TSN_FAULT_RECOVERY_Z3SCHEDULESOLVER_H

#include "ScheduleSolver.h"

namespace tsn_fault_recovery {

class Z3ScheduleSolver : public ScheduleSolver
{
  public:
    virtual ScheduleResult solve(const ScheduleRequest& request) override;
    // Experiment-only entry point. Runtime recovery controllers never call this.
    ScheduleResult solveFeasibilityOnly(const ScheduleRequest& request);
};

} // namespace tsn_fault_recovery

#endif
