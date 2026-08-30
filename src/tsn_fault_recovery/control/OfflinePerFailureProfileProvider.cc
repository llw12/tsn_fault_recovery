#include "OfflinePerFailureProfileProvider.h"
#include "ProfileSerializer.h"

using namespace omnetpp;
namespace tsn_fault_recovery {
namespace {
cValueMap *mapAt(cValueMap *map, const char *key) { return check_and_cast<cValueMap *>(map->get(key).objectValue()); }
cValueArray *arrayAt(cValueMap *map, const char *key) { return check_and_cast<cValueArray *>(map->get(key).objectValue()); }
std::string textAt(cValueMap *map, const char *key) { return map->get(key).stringValue(); }
FaultProfileStatus parseStatus(const std::string& value) {
    if (value == "SAT") return FaultProfileStatus::SAT;
    if (value == "NO_AFFECTED_TT") return FaultProfileStatus::NO_AFFECTED_TT;
    if (value == "NO_ROUTE") return FaultProfileStatus::NO_ROUTE;
    if (value == "UNSAT") return FaultProfileStatus::UNSAT;
    if (value == "FORWARDING_CONFLICT") return FaultProfileStatus::FORWARDING_CONFLICT;
    if (value == "TIMEOUT") return FaultProfileStatus::TIMEOUT;
    if (value == "ERROR") return FaultProfileStatus::ERROR;
    throw cRuntimeError("Unknown offline profile status '%s'", value.c_str());
}
} // namespace

void OfflinePerFailureProfileProvider::preload(cValueMap *root,
        const std::string& expectedScenarioHash, const std::string& expectedSolverConfigHash) {
    if (!root) throw cRuntimeError("offline ProfileStore is not an object");
    if (textAt(root, "scenario_sha256") != expectedScenarioHash) throw cRuntimeError("offline ProfileStore scenario_sha256 is stale");
    if (textAt(root, "solver_config_hash") != expectedSolverConfigHash) throw cRuntimeError("offline ProfileStore solver_config_hash is stale");
    if (static_cast<int>(root->get("profile_schema_version").doubleValue()) != 2) throw cRuntimeError("unsupported offline profile schema version");
    entries.clear();
    faultToClass.clear();
    for (const auto& [candidate, value] : mapAt(root, "faults")->getFields()) {
        auto *item = check_and_cast<cValueMap *>(value.objectValue()); OfflineProfileEntry entry;
        entry.status = parseStatus(textAt(item, "status")); entry.diagnostic = textAt(item, "diagnostic");
        auto *affected = arrayAt(item, "affected_flows");
        for (int i = 0; i < affected->size(); ++i) entry.affectedFlowIds.push_back(affected->get(i).stringValue());
        if (entry.status == FaultProfileStatus::SAT) entry.profile = ProfileSerializer::parse(mapAt(item, "profile"), expectedScenarioHash);
        entries[candidate] = entry;
    }
}

void OfflinePerFailureProfileProvider::preloadExact(cValueMap *root,
        const std::string& expectedScenarioHash, const std::string& expectedSolverConfigHash) {
    preloadEquivalence(root, expectedScenarioHash, expectedSolverConfigHash, "exact-affected-set-equivalence");
}

void OfflinePerFailureProfileProvider::preloadEquivalence(cValueMap *root,
        const std::string& expectedScenarioHash, const std::string& expectedSolverConfigHash,
        const std::string& expectedStrategy) {
    if (!root) throw cRuntimeError("equivalence Class Store is not an object");
    if (textAt(root, "strategy") != expectedStrategy) throw cRuntimeError("equivalence Class Store strategy mismatch");
    if (textAt(root, "scenario_sha256") != expectedScenarioHash) throw cRuntimeError("equivalence Class Store scenario_sha256 is stale");
    if (textAt(root, "solver_config_hash") != expectedSolverConfigHash) throw cRuntimeError("equivalence Class Store solver_config_hash is stale");
    if (static_cast<int>(root->get("profile_schema_version").doubleValue()) != 2) throw cRuntimeError("unsupported equivalence profile schema version");
    entries.clear(); faultToClass.clear();
    for (const auto& [faultId, value] : mapAt(root, "fault_to_class")->getFields())
        faultToClass[faultId] = value.stringValue();
    for (const auto& [classId, value] : mapAt(root, "classes")->getFields()) {
        auto *item = check_and_cast<cValueMap *>(value.objectValue()); OfflineProfileEntry entry;
        entry.status = FaultProfileStatus::SAT; entry.diagnostic = "validated equivalence class";
        auto *affected = arrayAt(item, "affected_flows");
        for (int i = 0; i < affected->size(); ++i) entry.affectedFlowIds.push_back(affected->get(i).stringValue());
        entry.profile = ProfileSerializer::parse(mapAt(item, "profile"), expectedScenarioHash);
        entries[classId] = entry;
    }
}

const std::string& OfflinePerFailureProfileProvider::classForFault(const std::string& faultId) const {
    auto found = faultToClass.find(faultId);
    if (found == faultToClass.end()) throw cRuntimeError("equivalence Class Store has no class for fault '%s'", faultId.c_str());
    return found->second;
}

const OfflineProfileEntry& OfflinePerFailureProfileProvider::lookupClass(const std::string& classId) const {
    auto found = entries.find(classId);
    if (found == entries.end()) throw cRuntimeError("equivalence Class Store has no profile for class '%s'", classId.c_str());
    return found->second;
}

const OfflineProfileEntry& OfflinePerFailureProfileProvider::lookup(const std::string& faultId) const {
    auto found = entries.find(faultId);
    if (found == entries.end()) throw cRuntimeError("offline ProfileStore has no entry for fault '%s'", faultId.c_str());
    return found->second;
}
} // namespace tsn_fault_recovery
