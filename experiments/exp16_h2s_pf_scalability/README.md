# exp16 H2S per-failure scalability

This campaign measures offline per-physical-link PF generation with H2S primary,
CELF fallback, affected-only rerouting, exact healthy-route locks for unaffected
TT streams, and joint rescheduling of every TT stream. It reuses provenance-
checked exp15 P0 profiles and does not invoke OMNeT++, INET, PF grouping, or any
older campaign.

Run `./experiments/exp16_h2s_pf_scalability/run.sh --quick`, then
`--qualification`, then the script without a mode flag for the formal campaign.
