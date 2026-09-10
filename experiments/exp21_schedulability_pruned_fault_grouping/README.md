# exp21: schedulability-pruned fault grouping

Run `./run.sh --qualification` for the deterministic necessary-condition gate,
`./run.sh --quick` for the M_RING quick census, or `./run.sh` for the formal
six-scenario sequential campaign.  This experiment invokes the external
AdvancedFlowScheduler backend but does not run OMNeT++ or INET.

`./run.sh --resume` verifies the saved implementation/scenario/catalog/P0/
backend/filter contract before continuing.  A contract mismatch is a hard
failure rather than permission to combine incompatible artifacts.
