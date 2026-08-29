# exp06 chart map

| Report segment | Analytical question | Family / type | Fields | Supported interpretation | Palette / non-color encoding | Source |
|---|---|---|---|---|---|---|
| Recovery and loss evidence | How does fault-to-first-success latency differ between Online and Offline Per-Failure for every relevant candidate? | Comparison / grouped bar | scenario, fault_id, online_recovery_us, offline_recovery_us, offline_status | Separates the 1 ms Online decision delay from the ideal preloaded Offline lower bound without hiding non-SAT status | Blue vs gold; Offline also uses hatch | `per_fault_comparison.csv` |
| Recovery and loss evidence | How many eligible TT packets are lost under each mode for every declared candidate, including no-action faults? | Comparison / grouped bar | scenario, fault_id, no_recovery_tt_lost, online_tt_lost, offline_tt_lost, offline_status | Shows recovery effectiveness and retains zero-loss/NO_AFFECTED_TT candidates | Neutral, blue, gold; Offline also uses hatch | `per_fault_comparison.csv` |

Both charts use zero-based quantitative axes, explicit units, direct fault labels,
quiet grid lines, and status annotations for any future non-SAT relevant fault.
