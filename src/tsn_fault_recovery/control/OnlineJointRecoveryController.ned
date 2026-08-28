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
        double cycleTime @unit(s) = default(1ms);
        double ttWindow @unit(s) = default(100us);
        int ttPacketBytes = default(200);
        int frameOverheadBytes = default(64);
        double linkBitrate @unit(bps) = default(100Mbps);
        int ttTrafficClass = default(1);
        int beTrafficClass = default(0);
        @class(tsn_fault_recovery::OnlineJointRecoveryController);
        @display("i=block/cogwheel;p=390,80");
}
