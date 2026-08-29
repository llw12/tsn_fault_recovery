#include "ScenarioRecoveryController.h"
#include <chrono>
#include <fstream>
#include <iomanip>
#include <set>
#include "AffectedFlowAnalyzer.h"
#include "ProfileSerializer.h"
#include "ProfileSwitcher.h"

using namespace omnetpp;
namespace tsn_fault_recovery {
namespace {
std::string quote(const std::string& value) {
    std::string result = "\"";
    for (char ch : value) {
        if (ch == '\\' || ch == '\"') result += '\\';
        if (ch == '\n') result += "\\n"; else result += ch;
    }
    return result + "\"";
}
int statusCode(FaultProfileStatus status) { return static_cast<int>(status); }
} // namespace

Define_Module(ScenarioRecoveryController);

void ScenarioRecoveryController::initialize() {
    initialEvent = new cMessage("scenarioInitialProfile"); faultEvent = new cMessage("scenarioFault"); activationEvent = new cMessage("scenarioRecoveryActivation");
    initialEvent->setSchedulingPriority(par("initialSchedulingPriority").intValue()); faultEvent->setSchedulingPriority(par("detectionSchedulingPriority").intValue()); activationEvent->setSchedulingPriority(par("activationSchedulingPriority").intValue());
    if (!par("enabled").boolValue()) return;
    mode = par("mode").stringValue(); faultId = par("faultId").stringValue();
    if (mode != "precompute" && mode != "precompute-per-failure" && mode != "no-recovery" && mode != "online" && mode != "offline-per-failure") throw cRuntimeError("NOT_IMPLEMENTED recovery mode '%s'", mode.c_str());
    scenario = ScenarioRuntimeAdapter::parseScenario(check_and_cast<cValueMap *>(par("scenario").objectValue()));
    adapter = ScenarioRuntimeAdapter::parsePortMap(check_and_cast<cValueMap *>(par("portMap").objectValue()));
    switcher = check_and_cast<ProfileSwitcher *>(getParentModule()->getSubmodule(par("activatorModule").stringValue()));
    if (mode == "offline-per-failure") loadOfflineStore();
    scheduleAt(SIMTIME_ZERO, initialEvent);
    if (mode != "precompute" && mode != "precompute-per-failure") scheduleAt(scenario.failureTime, faultEvent);
}

void ScenarioRecoveryController::recordComputation(const char *prefix, const ProfileComputationResult& result) {
    recordScalar((std::string(prefix) + ".routeWallTimeSeconds").c_str(), result.routeSolverWallSeconds);
    recordScalar((std::string(prefix) + ".scheduleWallTimeSeconds").c_str(), result.smtSolverWallSeconds);
    recordScalar((std::string(prefix) + ".profileCompilationWallTimeSeconds").c_str(), result.profileCompileWallSeconds);
    recordScalar((std::string(prefix) + ".totalWallTimeSeconds").c_str(), result.totalWallSeconds);
    recordScalar((std::string(prefix) + ".routeSolverInvocations").c_str(), result.routeSolverInvocations);
    recordScalar((std::string(prefix) + ".z3SolverInvocations").c_str(), result.z3SolverInvocations);
    recordScalar((std::string(prefix) + ".statusCode").c_str(), statusCode(result.status));
}

void ScenarioRecoveryController::initializeProfile() {
    if (mode == "precompute") {
        JointProfileComputer computer(scenario, adapter); auto result = computer.computeInitial("P0");
        if (result.status != FaultProfileStatus::SAT) throw cRuntimeError("Initial profile is %s: %s", faultProfileStatusName(result.status), result.diagnostic.c_str());
        profile0 = result.profile; for (const auto& route : profile0.logicalRoutes) initialRoutes[route.flowId] = route;
        ProfileSerializer::write(profile0, scenario.sha256, par("profileOutputPath").stringValue()); writeFaultAnalysis(); recordComputation("scenario.precompute", result); endSimulation(); return;
    }
    profile0 = ProfileSerializer::parse(check_and_cast<cValueMap *>(par("profile0").objectValue()), scenario.sha256);
    for (const auto& route : profile0.logicalRoutes) initialRoutes[route.flowId] = route;
    if (mode == "precompute-per-failure") { precomputePerFailure(); endSimulation(); return; }
    switcher->activateProfile(profile0);
}

void ScenarioRecoveryController::precomputePerFailure() {
    struct Row { std::string faultId; ProfileComputationResult result; double serializationWallSeconds = 0; long profileBytes = 0; std::string profileFile; };
    std::vector<Row> rows; JointProfileComputer computer(scenario, adapter); double totalSeconds = 0;
    for (const auto& candidate : scenario.faultCandidates) {
        Row row; row.faultId = candidate;
        try {
            row.result = computer.computeForFault("PF_" + candidate, candidate, initialRoutes);
            if (row.result.status == FaultProfileStatus::SAT) {
                row.profileFile = candidate + ".raw.json"; std::string path = std::string(par("perFailureProfileDirectory").stringValue()) + "/" + row.profileFile;
                auto start = std::chrono::steady_clock::now(); ProfileSerializer::write(row.result.profile, scenario.sha256, path);
                row.serializationWallSeconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
                std::ifstream input(path, std::ios::binary | std::ios::ate); row.profileBytes = input ? static_cast<long>(input.tellg()) : 0;
            }
        }
        catch (const std::exception& error) { row.result.status = FaultProfileStatus::ERROR; row.result.diagnostic = error.what(); }
        totalSeconds += row.result.totalWallSeconds + row.serializationWallSeconds; rows.push_back(row);
    }
    std::ofstream out(par("perFailureReportOutputPath").stringValue()); if (!out) throw cRuntimeError("Cannot write per-failure precompute report"); out << std::setprecision(17);
    out << "{\n  \"schema_version\": 1,\n  \"scenario_sha256\": " << quote(scenario.sha256) << ",\n  \"total_precompute_wall_s\": " << totalSeconds << ",\n  \"faults\": {";
    for (size_t i = 0; i < rows.size(); ++i) {
        const auto& row = rows[i]; if (i) out << ",";
        out << "\n    " << quote(row.faultId) << ": {\"status\": " << quote(faultProfileStatusName(row.result.status)) << ", \"affected_flows\": [";
        for (size_t j = 0; j < row.result.affectedFlowIds.size(); ++j) { if (j) out << ", "; out << quote(row.result.affectedFlowIds[j]); }
        out << "], \"route_solver_wall_us\": " << row.result.routeSolverWallSeconds * 1e6 << ", \"smt_solver_wall_us\": " << row.result.smtSolverWallSeconds * 1e6
            << ", \"profile_compile_wall_us\": " << row.result.profileCompileWallSeconds * 1e6 << ", \"serialization_wall_us\": " << row.serializationWallSeconds * 1e6
            << ", \"total_precompute_wall_us\": " << (row.result.totalWallSeconds + row.serializationWallSeconds) * 1e6 << ", \"objective\": " << row.result.scheduleObjectiveTicks
            << ", \"profile_bytes_raw\": " << row.profileBytes << ", \"profile_file_raw\": " << quote(row.profileFile) << ", \"diagnostic\": " << quote(row.result.diagnostic) << "}";
    }
    out << "\n  }\n}\n"; recordScalar("scenario.recoveryPrecompute.totalWallTimeSeconds", totalSeconds); recordScalar("scenario.recoveryPrecompute.candidateFaultCount", rows.size());
}

void ScenarioRecoveryController::loadOfflineStore() {
    auto start = std::chrono::steady_clock::now();
    offlineProvider.preload(check_and_cast<cValueMap *>(par("offlineProfileStore").objectValue()), scenario.sha256, par("solverConfigHash").stringValue());
    offlineProvider.lookup(faultId);
    offlineStoreLoadWallSeconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(); recordScalar("scenario.offline.storeLoadWallTimeSeconds", offlineStoreLoadWallSeconds);
}

void ScenarioRecoveryController::handleFault() {
    auto affected = AffectedFlowAnalyzer::affectedFlowIds(initialRoutes, faultId); std::set<std::string> affectedSet(affected.begin(), affected.end());
    for (const auto& flow : scenario.ttFlows) EV_INFO << "SCENARIO_FLOW_AFFECTED fault=" << faultId << " flow=" << flow.flowId << " affected=" << (affectedSet.count(flow.flowId) ? 1 : 0) << endl;
    recordScalar("scenario.affectedFlowCount", affected.size()); recordScalar("scenario.failureTime", scenario.failureTime.dbl());
    if (mode == "no-recovery") { recordScalar("scenario.runtime.routeSolverInvocations", 0); recordScalar("scenario.runtime.z3SolverInvocations", 0); return; }
    if (mode == "offline-per-failure") {
        auto lookupStart = std::chrono::steady_clock::now(); const auto& entry = offlineProvider.lookup(faultId); double lookupWall = std::chrono::duration<double>(std::chrono::steady_clock::now() - lookupStart).count();
        recordScalar("scenario.offline.lookupWallTimeSeconds", lookupWall); recordScalar("scenario.offline.simulatedLookupDelay", par("offlineLookupDelay").doubleValueInUnit("s"));
        recordScalar("scenario.runtime.routeSolverInvocations", 0); recordScalar("scenario.runtime.z3SolverInvocations", 0); recordScalar("scenario.recoveryStatusCode", statusCode(entry.status));
        EV_INFO << "OFFLINE_PROFILE_LOOKUP fault=" << faultId << " status=" << faultProfileStatusName(entry.status) << " wallSeconds=" << lookupWall << endl;
        if (entry.status == FaultProfileStatus::SAT) { recoveryProfile = entry.profile; scheduleAt(simTime() + par("offlineLookupDelay"), activationEvent); }
        return;
    }
    JointProfileComputer computer(scenario, adapter); auto result = computer.computeForFault("online_" + faultId, faultId, initialRoutes);
    recordComputation("scenario.online", result); recordScalar("scenario.runtime.routeSolverInvocations", result.routeSolverInvocations); recordScalar("scenario.runtime.z3SolverInvocations", result.z3SolverInvocations);
    recordScalar("scenario.recoveryStatusCode", statusCode(result.status)); recordScalar("scenario.online.simulatedSolverDelay", scenario.solverDelay.dbl());
    if (result.status == FaultProfileStatus::NO_AFFECTED_TT) return;
    if (result.status != FaultProfileStatus::SAT) { EV_WARN << "ONLINE_RECOVERY_UNAVAILABLE status=" << faultProfileStatusName(result.status) << " diagnostic=" << result.diagnostic << endl; return; }
    recoveryProfile = result.profile; ProfileSerializer::write(recoveryProfile, scenario.sha256, par("recoveryProfileOutputPath").stringValue()); scheduleAt(simTime() + scenario.solverDelay, activationEvent);
}

void ScenarioRecoveryController::activateRecovery() {
    auto result = switcher->activateProfile(recoveryProfile); recordScalar("scenario.activationTime", simTime().dbl()); recordScalar("scenario.activationWallTimeSeconds", result.wallTimeSeconds);
    if (mode == "online") { recordScalar("scenario.online.activationTime", simTime().dbl()); recordScalar("scenario.online.activationWallTimeSeconds", result.wallTimeSeconds); }
    else { recordScalar("scenario.offline.activationTime", simTime().dbl()); recordScalar("scenario.offline.activationWallTimeSeconds", result.wallTimeSeconds); }
}

void ScenarioRecoveryController::writeFaultAnalysis() const {
    std::ofstream out(par("faultAnalysisOutputPath").stringValue()); if (!out) throw cRuntimeError("Cannot write fault analysis"); out << "{\n  \"scenario_sha256\": " << quote(scenario.sha256) << ",\n  \"faults\": {";
    for (size_t i = 0; i < scenario.faultCandidates.size(); ++i) { const auto& fault = scenario.faultCandidates[i]; auto affected = AffectedFlowAnalyzer::affectedFlowIds(initialRoutes, fault); if (i) out << ","; out << "\n    " << quote(fault) << ": ["; for (size_t j = 0; j < affected.size(); ++j) { if (j) out << ", "; out << quote(affected[j]); } out << "]"; }
    out << "\n  }\n}\n";
}
void ScenarioRecoveryController::handleMessage(cMessage *msg) { if (msg == initialEvent) initializeProfile(); else if (msg == faultEvent) handleFault(); else if (msg == activationEvent) activateRecovery(); else throw cRuntimeError("Unexpected ScenarioRecoveryController message"); }
ScenarioRecoveryController::~ScenarioRecoveryController() { cancelAndDelete(initialEvent); cancelAndDelete(faultEvent); cancelAndDelete(activationEvent); }
} // namespace tsn_fault_recovery
