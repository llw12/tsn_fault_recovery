package tsn_fault_recovery.control;

simple StreamForwardingRecorder
{
    parameters:
        bool enabled = default(false);
        string switches = default("");
        string flowIds = default("");
        string streamHandles = default("");
        string outputPath = default("");
        @class(tsn_fault_recovery::StreamForwardingRecorder);
        @display("i=block/table;p=710,80");
}
