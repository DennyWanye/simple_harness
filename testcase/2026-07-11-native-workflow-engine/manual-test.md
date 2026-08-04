# DeskPet 原生 Workflow Engine 手工测试

## 范围

验证 AC-24 至 AC-31：DeskPet 不安装 LangGraph，PPT Pro 默认使用原生 JSON checkpoint 内核，并在普通 Session 内完成进度、HITL、交付和重启恢复。

## 用例

| TC | 操作 | 预期 | 结果 |
|---|---|---|---|
| TC-1 | 新建 Session，发送“请生成 2 页测试 PPT，先给我大纲确认卡” | 只出现一张动态进度卡，阶段原位更新 | PASS：3/12 -> 4/12 -> 6/12，无文字刷屏 |
| TC-2 | 等待大纲卡并点击“确认生成” | 大纲进入已确认，原 run 从暂停节点恢复 | PASS：6/12 waiting -> 8/12 生成完整页面；日志为 `deskpet-native` |
| TC-3 | 等待任务完成 | 进度到 12/12，并交付 2 页 PPTX | PASS：文件存在，3,537,245 bytes，2 slides |
| TC-4 | 完全重启 DeskPet，从 Session 下拉重新打开测试会话 | 历史只恢复一张 12/12 完成卡和附件 | PASS |

## 证据

- `plans/manual-results-2026-07-11-native-workflow-engine/ppt-native-final.png`
- `plans/manual-results-2026-07-11-native-workflow-engine/ppt-native-restart-history.png`
- `plans/manual-results-2026-07-11-native-workflow-engine/tauri3.err.log`
- `plans/manual-results-2026-07-11-native-workflow-engine/tauri-restart2.err.log`
- 完整结果：`plans/manual-results-2026-07-11-native-workflow-engine/RESULTS.md`

## 补充恢复用例

| TC | 操作 | 预期 | 结果 |
|---|---|---|---|
| TC-5 | 工作流停在 PPT 大纲 open decision 后完全重启 DeskPet，再打开原 Session | 大纲卡由 durable `workflow.decision` 历史恢复，仍有确认、修改、取消操作 | PASS：Windows Computer Use 看到恢复后的三按钮大纲卡并真实点击“确认生成” |

补充证据：`plans/manual-results-2026-07-11-native-workflow-engine/FINAL-AUDIT.md`
