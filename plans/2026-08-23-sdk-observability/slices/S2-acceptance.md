# S2 验收：Correlation、状态转换与健康快照

父验收：`../acceptance.md`。基线为 S1 已验收 commit。

| MUST AC | 本 slice 决定性出口 |
|---|---|
| AC-OBS-3 | Host→Harness→Memory→restart correlation 可重建且跨身份 rebase |
| AC-OBS-4 | outbox/recall/context/recovery 状态转换事件完整 |
| AC-OBS-6 | 两 SDK `diagnostics_snapshot()` 稳定、有界、只读 |
| AC-OBS-9 | 仅凭 correlation+snapshot 定位故障组件与恢复结果 |
| AC-OBS-11 | 状态异常含 event/stage/outcome/code/duration/correlation |
| AC-OBS-12 | 无正文情况下区分主要故障类别 |

适用性：`input_sensitive=false`、`llm_payload_driven=false`、`stateful_init=true`。required obligations：TO-A3/A4/A6/A9/A11/A12、TO-R2/R3/R4/R5/R6。退出：跨进程 recovery、snapshot query denylist、根因重建和 DB backup/restore gate 全绿。
