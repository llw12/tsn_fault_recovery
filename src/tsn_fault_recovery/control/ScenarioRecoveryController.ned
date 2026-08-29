package tsn_fault_recovery.control;
simple ScenarioRecoveryController
{
    parameters:
        bool enabled = default(false);
        string mode = default("no-recovery");
        string faultId = default("");
        string activatorModule = default("profileSwitcher");
        object scenario = default(object({}));
        object portMap = default(object({}));
        object profile0 = default(object({}));
        object offlineProfileStore = default(object({}));
        string solverConfigHash = default("");
        double offlineLookupDelay @unit(s) = default(0s);
        string profileOutputPath = default("profile0.json");
        string recoveryProfileOutputPath = default("recovery_profile.json");
        string faultAnalysisOutputPath = default("fault_analysis.json");
        string perFailureProfileDirectory = default("profiles/per_failure/raw");
        string perFailureReportOutputPath = default("profiles/per_failure/precompute_report.json");
        int initialSchedulingPriority = default(-200);
        int detectionSchedulingPriority = default(100);
        int activationSchedulingPriority = default(-100);
        @class(tsn_fault_recovery::ScenarioRecoveryController);
        @display("i=block/cogwheel");
}
