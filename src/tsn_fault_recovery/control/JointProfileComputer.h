#ifndef __TSN_FAULT_RECOVERY_JOINTPROFILECOMPUTER_H
#define __TSN_FAULT_RECOVERY_JOINTPROFILECOMPUTER_H

#include <map>
#include <set>
#include <string>
#include <vector>

#include "ProfileDefinition.h"
#include "ScenarioRuntimeAdapter.h"

namespace tsn_fault_recovery {

enum class FaultProfileStatus
{
    SAT,
    NO_AFFECTED_TT,
    NO_ROUTE,
    UNSAT,
    FORWARDING_CONFLICT,
    TIMEOUT,
    ERROR
};

const char *faultProfileStatusName(FaultProfileStatus status);

struct ProfileComputationResult
{
    FaultProfileStatus status = FaultProfileStatus::ERROR;
    ProfileDefinition profile;
    std::vector<std::string> affectedFlowIds;
    std::string diagnostic;
    int64_t scheduleObjectiveTicks = -1;
    double routeSolverWallSeconds = 0;
    double smtSolverWallSeconds = 0;
    double profileCompileWallSeconds = 0;
    double totalWallSeconds = 0;
    int routeSolverInvocations = 0;
    int z3SolverInvocations = 0;
};

class JointProfileComputer
{
  private:
    const ScenarioData& scenario;
    const ScenarioRuntimeAdapter& adapter;
    int solverTimeoutMs;

  public:
    JointProfileComputer(const ScenarioData& scenario, const ScenarioRuntimeAdapter& adapter,
            int solverTimeoutMs = 0) : scenario(scenario), adapter(adapter), solverTimeoutMs(solverTimeoutMs) {}

    ProfileComputationResult computeInitial(const std::string& profileId) const;
    ProfileComputationResult computeInitialWithRoutes(const std::string& profileId,
            const std::map<std::string, LogicalRoute>& frozenRoutes) const;
    ProfileComputationResult computeForFault(const std::string& profileId, const std::string& faultId,
            const std::map<std::string, LogicalRoute>& initialRoutes) const;
    ProfileComputationResult computeForDisabledLinks(const std::string& profileId,
            const std::set<std::string>& disabledLinks,
            const std::map<std::string, LogicalRoute>& initialRoutes,
            const std::vector<std::string>& affectedFlowIds) const;

  private:
    ProfileComputationResult compute(const std::string& profileId, const std::set<std::string>& disabledLinks,
            const std::map<std::string, LogicalRoute> *preservedRoutes,
            const std::vector<std::string>& affectedFlowIds) const;
};

} // namespace tsn_fault_recovery

#endif
