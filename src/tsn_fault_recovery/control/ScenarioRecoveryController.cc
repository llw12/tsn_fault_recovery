#include "ScenarioRecoveryController.h"
#include <algorithm>
#include <chrono>
#include <fstream>
#include <set>
#include "AffectedFlowAnalyzer.h"
#include "BfsRouteSolver.h"
#include "GateScheduleCompiler.h"
#include "ProfileSerializer.h"
#include "ProfileSwitcher.h"
#include "TimeTickConverter.h"
#include "Z3ScheduleSolver.h"

using namespace omnetpp;
namespace tsn_fault_recovery {
Define_Module(ScenarioRecoveryController);

ProfileDefinition ScenarioRecoveryController::solveProfile(const std::string& id,const std::set<std::string>& disabled,
        const std::map<std::string,LogicalRoute> *preserved)
{
    auto totalStart=std::chrono::steady_clock::now(); auto routeStart=totalStart;
    BfsRouteSolver bfs; std::map<std::string,LogicalRoute> routes;
    for(const auto& flow:scenario.ttFlows) {
        auto existing=preserved?preserved->find(flow.flowId):std::map<std::string,LogicalRoute>::const_iterator{};
        if(preserved && existing!=preserved->end() && std::none_of(existing->second.linkPath.begin(),existing->second.linkPath.end(),[&](const std::string& link){return disabled.count(link);})) routes[flow.flowId]=existing->second;
        else routes[flow.flowId]=bfs.solve(scenario.graph,flow.flowId,flow.source,flow.destination,disabled);
    }
    auto routeEnd=std::chrono::steady_clock::now(); ScheduleRequest request; request.flows=scenario.ttFlows;
    for(const auto& flow:scenario.ttFlows) request.routeEgressPaths.push_back(adapter.egressPaths(routes.at(flow.flowId),scenario.graph));
    request.cycleTime=scenario.cycleTime; request.timeQuantum=scenario.timeQuantum; request.ingressMargin=scenario.ingressMargin;
    request.hopMargin=scenario.hopMargin; request.frameOverheadBytes=scenario.frameOverheadBytes; request.linkBitrate=scenario.linkBitrate; request.beTrafficClass=scenario.beTrafficClass;
    Z3ScheduleSolver solver; auto schedule=solver.solve(request); if(schedule.status!=ScheduleStatus::SAT) throw cRuntimeError("Scenario schedule is %s: %s",scheduleStatusName(schedule.status),schedule.diagnostic.c_str()); auto compilationStart=std::chrono::steady_clock::now();
    ProfileDefinition profile; profile.profileId=id;
    for(const auto& flow:scenario.ttFlows) { const auto& logical=routes.at(flow.flowId); profile.logicalRoutes.push_back(logical); auto entries=adapter.forwardingEntries(logical,scenario.graph,flow.destination); profile.routes.insert(profile.routes.end(),entries.begin(),entries.end()); EV_INFO<<"SCENARIO_ROUTE profile="<<id<<" flow="<<flow.flowId<<" nodes="; for(const auto& node:logical.nodePath)EV_INFO<<node<<" "; EV_INFO<<"links=";for(const auto&link:logical.linkPath)EV_INFO<<link<<" ";EV_INFO<<endl; }
    int ttClass=scenario.ttFlows.front().trafficClass; for(const auto& flow:scenario.ttFlows)if(flow.trafficClass!=ttClass)throw cRuntimeError("scheduler v1 requires one TT traffic class");
    int64_t cycleTicks=TimeTickConverter::exactTicks(scenario.cycleTime,scenario.timeQuantum,"cycleTime");
    profile.gateSchedules=GateScheduleCompiler::compile(schedule.windows,cycleTicks,scenario.timeQuantum,ttClass,scenario.beTrafficClass);
    routeWall=std::chrono::duration<double>(routeEnd-routeStart).count(); scheduleWall=schedule.wallTimeSeconds; profileCompilationWall=std::chrono::duration<double>(std::chrono::steady_clock::now()-compilationStart).count(); totalWall=std::chrono::duration<double>(std::chrono::steady_clock::now()-totalStart).count();
    return profile;
}

void ScenarioRecoveryController::initialize()
{
    initialEvent=new cMessage("scenarioInitialProfile"); faultEvent=new cMessage("scenarioFault"); activationEvent=new cMessage("scenarioRecoveryActivation");
    initialEvent->setSchedulingPriority(par("initialSchedulingPriority").intValue()); faultEvent->setSchedulingPriority(par("detectionSchedulingPriority").intValue()); activationEvent->setSchedulingPriority(par("activationSchedulingPriority").intValue());
    if(!par("enabled").boolValue())return; mode=par("mode").stringValue(); faultId=par("faultId").stringValue();
    if(mode!="precompute"&&mode!="no-recovery"&&mode!="online")throw cRuntimeError("NOT_IMPLEMENTED recovery mode '%s'",mode.c_str());
    scenario=ScenarioRuntimeAdapter::parseScenario(check_and_cast<cValueMap *>(par("scenario").objectValue())); adapter=ScenarioRuntimeAdapter::parsePortMap(check_and_cast<cValueMap *>(par("portMap").objectValue()));
    switcher=check_and_cast<ProfileSwitcher *>(getParentModule()->getSubmodule(par("activatorModule").stringValue())); scheduleAt(SIMTIME_ZERO,initialEvent);
    if(mode!="precompute")scheduleAt(scenario.failureTime,faultEvent);
}

void ScenarioRecoveryController::initializeProfile()
{
    if(mode=="precompute") { profile0=solveProfile("P0",{}); for(const auto&r:profile0.logicalRoutes)initialRoutes[r.flowId]=r; ProfileSerializer::write(profile0,scenario.sha256,par("profileOutputPath").stringValue()); writeFaultAnalysis(); recordScalar("scenario.precompute.routeWallTimeSeconds",routeWall);recordScalar("scenario.precompute.scheduleWallTimeSeconds",scheduleWall);recordScalar("scenario.precompute.profileCompilationWallTimeSeconds",profileCompilationWall);recordScalar("scenario.precompute.totalWallTimeSeconds",totalWall); endSimulation(); }
    else { profile0=ProfileSerializer::parse(check_and_cast<cValueMap *>(par("profile0").objectValue()),scenario.sha256); for(const auto&r:profile0.logicalRoutes)initialRoutes[r.flowId]=r; switcher->activateProfile(profile0); }
}

void ScenarioRecoveryController::handleFault()
{
    auto affected=AffectedFlowAnalyzer::affectedFlowIds(initialRoutes,faultId); std::set<std::string> affectedSet(affected.begin(),affected.end());
    for(const auto& flow:scenario.ttFlows)EV_INFO<<"SCENARIO_FLOW_AFFECTED fault="<<faultId<<" flow="<<flow.flowId<<" affected="<<(affectedSet.count(flow.flowId)?1:0)<<endl;
    recordScalar("scenario.affectedFlowCount",affected.size()); recordScalar("scenario.failureTime",scenario.failureTime.dbl());
    if(mode=="no-recovery")return;
    auto disabled=adapter.currentDisabledLinks(getParentModule()); disabled.insert(faultId);
    recoveryProfile=solveProfile("online_"+faultId,disabled,&initialRoutes); ProfileSerializer::write(recoveryProfile,scenario.sha256,par("recoveryProfileOutputPath").stringValue());
    recordScalar("scenario.online.routeWallTimeSeconds",routeWall);recordScalar("scenario.online.scheduleWallTimeSeconds",scheduleWall);recordScalar("scenario.online.profileCompilationWallTimeSeconds",profileCompilationWall);recordScalar("scenario.online.totalWallTimeSeconds",totalWall);recordScalar("scenario.online.simulatedSolverDelay",scenario.solverDelay.dbl()); scheduleAt(simTime()+scenario.solverDelay,activationEvent);
}

void ScenarioRecoveryController::activateRecovery() { auto result=switcher->activateProfile(recoveryProfile); recordScalar("scenario.online.activationTime",simTime().dbl());recordScalar("scenario.online.activationWallTimeSeconds",result.wallTimeSeconds); }

void ScenarioRecoveryController::writeFaultAnalysis() const
{
    std::ofstream out(par("faultAnalysisOutputPath").stringValue()); if(!out)throw cRuntimeError("Cannot write fault analysis"); out<<"{\n  \"scenario_sha256\": \""<<scenario.sha256<<"\",\n  \"faults\": {";
    for(size_t i=0;i<scenario.faultCandidates.size();++i) { const auto&fault=scenario.faultCandidates[i]; auto affected=AffectedFlowAnalyzer::affectedFlowIds(initialRoutes,fault); if(i)out<<","; out<<"\n    \""<<fault<<"\": [";for(size_t j=0;j<affected.size();++j){if(j)out<<", ";out<<"\""<<affected[j]<<"\"";}out<<"]"; }
    out<<"\n  }\n}\n";
}

void ScenarioRecoveryController::handleMessage(cMessage *msg) { if(msg==initialEvent)initializeProfile();else if(msg==faultEvent)handleFault();else if(msg==activationEvent)activateRecovery();else throw cRuntimeError("Unexpected ScenarioRecoveryController message"); }
ScenarioRecoveryController::~ScenarioRecoveryController(){cancelAndDelete(initialEvent);cancelAndDelete(faultEvent);cancelAndDelete(activationEvent);}
} // namespace tsn_fault_recovery
