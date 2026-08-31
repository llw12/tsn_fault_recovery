package tsn_fault_recovery.control;

simple SmtScalabilityBenchmark
{
    parameters:
        object scenario;
        object portMap;
        string caseId;
        string solverMode; // PRODUCTION_OPTIMIZE or BENCHMARK_FEASIBILITY_ONLY
        string disabledLinks = default("");
        string affectedFlowIds = default("");
        int solverTimeoutMs = default(30000);
        string reportOutputPath;
        @class(tsn_fault_recovery::SmtScalabilityBenchmark);
}
