#ifndef __TSN_FAULT_RECOVERY_SCHEDULESOLVER_H
#define __TSN_FAULT_RECOVERY_SCHEDULESOLVER_H

#include "ScheduleTypes.h"

namespace tsn_fault_recovery {

class ScheduleSolver
{
  public:
    virtual ~ScheduleSolver() = default;
    virtual ScheduleResult solve(const ScheduleRequest& request) = 0;
};

} // namespace tsn_fault_recovery

#endif
