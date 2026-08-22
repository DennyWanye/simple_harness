# S3 验收：Diagnostic bundle 与优化闭环

父验收：`../acceptance.md`。基线为 S2 已验收 commit；本 slice 负责最终发布。

| MUST AC | 本 slice 决定性出口 |
|---|---|
| AC-OBS-5 | 最终 wheel/Host/bundle 全链 canary 不泄漏 |
| AC-OBS-7 | bundle 只收有界 SDK export/snapshot |
| AC-OBS-8 | 三仓最终日志契约与 exact-wheel 自动化通过 |
| AC-OBS-9 | bundle 中 correlation 时间线足以定位注入故障 |
| AC-OBS-10 | latency/error/retry/queue/recall/context 代理指标可聚合比较 |
| AC-OBS-12 | 隐私约束下六类故障根因可区分 |

适用性：`input_sensitive=false`、`llm_payload_driven=false`、`stateful_init=true`。required obligations：TO-A5/A7/A8/A9/A10/A12、TO-R1/R2/R3/R4/R5/R6。退出：最终 Harness 0.4.0/Memory 0.5.0 exact wheel、Host revendor、critical/affected/full smoke、独立审计与 README release runbook 全部通过。
