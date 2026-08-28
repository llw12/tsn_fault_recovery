package tsn_fault_recovery.control;

simple ProfileSwitcher
{
    parameters:
        bool enabled = default(false);

        double switchTime @unit(s) = default(6ms);

        string switchPath = default("s1");
        string destinationPath = default("destination");

        string backupInterface = default("eth2");

        @class(ProfileSwitcher);
        @display("i=block/control;p=300,80");
}