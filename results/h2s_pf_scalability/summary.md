# exp16 H2S per-failure scalability

## A

PF recovery semantics qualification = `True`；affected-only rerouting、unaffected exact route lock、all-TT joint rescheduling 均通过。

## B

共发现 2365 个候选物理故障。

## C

各尺度候选数：S1=50, S2=98, S3=50, S4=257, S5=284, S6=515, S7=663, F150_TT100=50, F150_TT250=50, F150_TT500=50, F150_TT750=133, F150_TT1000=165。

## D

affected-flow 数的 min/median/max：S1=1/15.0/21, S2=1/39.0/49, S3=195/195.0/195, S4=1/109/125, S5=1/197.0/224, S6=1/234/261, S7=1/467/528, F150_TT100=47/47.0/47, F150_TT250=110/110.0/110, F150_TT500=195/195.0/195, F150_TT750=1/11/248, F150_TT1000=1/30/262。

## E

各尺度 median PF 为 S1=23.965 ms, S2=48.891 ms, S3=153.909 ms, S4=332.141 ms, S5=1004.956 ms, S6=3272.389 ms, S7=6587.301 ms, F150_TT100=53.967 ms, F150_TT250=98.425 ms, F150_TT500=158.387 ms, F150_TT750=188.940 ms, F150_TT1000=215.625 ms；总体尺度中位数范围 23.965–6587.301 ms，逐尺度 p95/max 见 scale_summary.csv。

## F

PF/P0 的 median/p95/max 比率逐尺度记录在 scale_summary.csv；这衡量故障约束与 route lock 的额外成本。

## G

H2S primary 成功 482/588。

## H

CELF fallback 成功贡献 0 个 Profile。

## I

STRUCTURAL_NO_ROUTE = 0。

## J

HEURISTIC_NOT_FOUND = 106；它不代表不可行证明。

## K

实测样本有效 Profile coverage = 482/588 (81.97%)。

## L

分层加权或 FULL 的 PF 串行工作量合计 5986012.605 ms。

## M

8 workers 的逐尺度 LPT makespan 合计 751058.849 ms。

## N

16 workers 的逐尺度 LPT makespan 合计 379598.550 ms。

## O

成功 Profile 平均大小逐尺度见 scale_summary.csv。

## P

包含 P0 的估计完整 Profile Store 合计 8353321956 bytes。

## Q

峰值求解 RSS 最大值为 218451968 bytes。

## R

F150 100→1000 TT 的 PF 成本、覆盖、串行量与 8-worker 投影见 fixed_topology_tt_sweep.csv。

## S

compute 证据已与覆盖、存储和内存联合评估，正式 verdict 为 `PF_COVERAGE_LIMITED`。

## T

storage 证据的实测/估计规范 JSON 与 gzip 字节数见 profile_storage.csv；verdict 为 `PF_COVERAGE_LIMITED`。

## U

heuristic coverage 已独立于结构断路统计；verdict 为 `PF_COVERAGE_LIMITED`。

## V

当前是否支持 fault grouping 必须服从 `PF_COVERAGE_LIMITED`：若为 PF_CHEAP_AND_HIGH_COVERAGE，则没有仅为减少离线求解次数而分组的强证据。
