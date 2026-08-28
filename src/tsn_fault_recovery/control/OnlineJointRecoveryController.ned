package tsn_fault_recovery.control;

simple OnlineJointRecoveryController
{
    parameters:
        bool enabled = default(false);
        double faultTime @unit(s) = default(5ms);
        double solverDelay @unit(s) = default(1ms);
        int detectionSchedulingPriority = default(100);
        int activationSchedulingPriority = default(-100);
        string failedEndpointA = default("s1");
        string failedEndpointB = default("s2");
        string routeStart = default("s1");
        string destination = default("destination");
        string activatorModule = default("profileSwitcher");
        string solverBackend = default("deterministic"); // deterministic or z3
        double cycleTime @unit(s) = default(1ms);
        double timeQuantum @unit(s) = default(1us);
        double ingressMargin @unit(s) = default(0us);
        double hopMargin @unit(s) = default(5us);
        double ttWindow @unit(s) = default(100us);
        int ttPacketBytes = default(200);
        int frameOverheadBytes = default(64);
        double linkBitrate @unit(bps) = default(100Mbps);
        int ttTrafficClass = default(1);
        int beTrafficClass = default(0);
        string source = default("source");
        string flowIds = default("");
        string flowSources = default("");
        string flowDestinations = default("");
        string flowPacketBytes = default("");
        string flowTrafficClasses = default("");
        string flowPeriods = default("");
        string flowDeadlines = default("");
        string flowReleaseOffsets = default("");
        @class(tsn_fault_recovery::OnlineJointRecoveryController);
        @display("i=block/cogwheel;p=390,80");
}
