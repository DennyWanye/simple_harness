# S1 验收：安全事件协议与本地 sinks

父验收：`../acceptance.md`。本 slice 不改变父验收，只冻结首个 release unit。

| MUST AC | 本 slice 决定性出口 |
|---|---|
| AC-OBS-1 | Harness/Memory 使用同一 V1 envelope 与 golden schema |
| AC-OBS-2 | 两 SDK public composition 与 Host 可注入 sink；failure 不影响业务 |
| AC-OBS-5 | 正文/API key/nested exception canary 在所有 S1 sink 不出现 |
| AC-OBS-7 | JSONL/ring 有界、权限/轮转/溢出正确 |
| AC-OBS-8 | schema/sink/privacy 自动化契约通过 |
| AC-OBS-11 | S1 error event 必备稳定字段，不以自由文本为唯一证据 |

适用性：`input_sensitive=false`、`llm_payload_driven=false`、`stateful_init=true`。required obligations：TO-A1/A2/A5/A7/A8/A11、TO-R1/R2/R3/R5/R6。退出：两个 SDK candidate wheel + Host sink wiring 的相关测试与 affected smoke 全绿。
