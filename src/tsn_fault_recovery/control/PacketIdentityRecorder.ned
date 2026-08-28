package tsn_fault_recovery.control;

simple PacketIdentityRecorder
{
    parameters:
        bool enabled = default(false);
        string flowIds = default("");
        string sourceModules = default("");
        string destinationModules = default("");
        @class(tsn_fault_recovery::PacketIdentityRecorder);
        @display("i=block/table;p=480,80");
}
