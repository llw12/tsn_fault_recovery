#ifndef __TSN_FAULT_RECOVERY_ROUTESOLVER_H
#define __TSN_FAULT_RECOVERY_ROUTESOLVER_H

#include <omnetpp.h>

#include "ScheduleTypes.h"

namespace tsn_fault_recovery {

struct FaultEvent;

class RouteSolver
{
  public:
    virtual ~RouteSolver() = default;
    virtual RoutePath solve(omnetpp::cModule *network, const FaultEvent& fault) = 0;
};

} // namespace tsn_fault_recovery

#endif
