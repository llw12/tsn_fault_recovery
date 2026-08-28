package tsn_fault_recovery.control;

simple ProfileSwitcher
{
    parameters:
        bool enabled = default(false);

        double switchTime @unit(s) = default(6ms);

        string switchPath = default("s1");
        string destinationPath = default("destination");

        string backupInterface = default("eth2");

        int activationSchedulingPriority = default(-100);
        string profileId = default("manual-route-only");
        string profileRouteSwitches = default("");
        string profileRouteInterfaces = default("");

        bool activateGclProfile = default(false);
        string profileEgressInterfaces = default("");
        double cycleTime @unit(s) = default(1ms);
        double ttWindow @unit(s) = default(100us);
        int ttPacketBytes = default(200);
        int frameOverheadBytes = default(64);
        double linkBitrate @unit(bps) = default(100Mbps);
        int ttTrafficClass = default(1);
        int beTrafficClass = default(0);

        @class(tsn_fault_recovery::ProfileSwitcher);
        @display("i=block/control;p=300,80");
}
