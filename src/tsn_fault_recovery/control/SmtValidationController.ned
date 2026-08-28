package tsn_fault_recovery.control;

simple SmtValidationController
{
    parameters:
        bool enabled = default(false);
        double activationTime @unit(s) = default(0s);
        int schedulingPriority = default(-100);
        string expectedStatus = default("SAT");
        string profileId = default("smt-validation");
        string activatorModule = default("profileSwitcher");
        string destination = default("destination");
        string routeSwitches = default("");
        string routeInterfaces = default("");
        string routeEgressInterfaces = default("");
        string flowIds = default("");
        string flowSources = default("");
        string flowDestinations = default("");
        string flowPacketBytes = default("");
        string flowTrafficClasses = default("");
        string flowPeriods = default("");
        string flowDeadlines = default("");
        string flowReleaseOffsets = default("");
        double cycleTime @unit(s) = default(1ms);
        double timeQuantum @unit(s) = default(1us);
        double ingressMargin @unit(s) = default(40us);
        double hopMargin @unit(s) = default(5us);
        int frameOverheadBytes = default(64);
        double linkBitrate @unit(bps) = default(100Mbps);
        int ttTrafficClass = default(1);
        int beTrafficClass = default(0);
        @class(tsn_fault_recovery::SmtValidationController);
        @display("i=block/cogwheel;p=570,80");
}
