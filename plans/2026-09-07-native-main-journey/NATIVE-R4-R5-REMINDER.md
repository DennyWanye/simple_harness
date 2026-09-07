# 原生时间提醒旅程 r4/r5（Host main 22592ec5 后端源码 + bundle d94e93dc，H0.7.10 / M0.6.20 / S0.3.13，gpt-5.6-luna，auto 模式）

2026-09-07，taiwan Mac。前端 bundle 未重建（无前端改动）；后端由源码加载，包含当日「auto 模式不弹授权提示」改动（`22592ec5`）。全新隔离 userdata；luna 预检 200，未回退。

## 通过

| 步骤 | 结果 |
|---|---|
| 创建 | 用户「北京时间 2026年09月07日 17:36 提醒我检查银杏测试记录，只提醒这一次。」→ 助手措辞正确（"可以处理…只提醒一次"，未声称已排程）。后台真实模型提取 prospective：`prospective_records` 1 行，`trigger_kind=time`，`due_at=1788773760`；state.db `prospective_scheduler_registrations` prepared/applied，`user_version=54` |
| 到期调度 | 17:36:02 `prospective_runtime_time_applied count=1`；`prospective_timer_events` prepared→claimed→handed_off→applied；SDK `prospective_trigger_events` 1 |
| 呈现 + ACK | 到期后发「38 加 19 等于多少？」：occurrence claimed→presented；模型调用 `prospective_ack`，**auto 模式下零授权提示**（`product_policy_user_confirmation` 0 次）自动授权并 settled=acknowledged；回答「38 加 19 等于 57。另外，刚才那条一次性提醒已确认。」；历史出现独立「提醒」卡片「提醒用户检查银杏测试记录」 |
| 冷重启（r5） | 正常退出（parent 0，remaining=[]，峰 1.6 GiB）→ 同 userdata 重开 → 历史与提醒卡片原位保留 → 发「72 减 28 等于多少？」只回「44」，无新工具调用/无新 occurrence；`companion.db reminders` 0 行（走的是 Prospective 链，不是遗留 Companion 调度器） |

## 观察

- 提醒卡片排在本轮助手回答之后（r22 记录为"排在新问题之前"），呈现顺序与旧记录不同，建议明确产品口径。
- 聊天区仍渲染 prospective_ack 原始回执 JSON（F02）。
- 事件触发来源仍为用户延期项 F01；递归提醒为 backlog。

## 原始证据（ignored）

| 路径 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-_d9play_/launch.json` | 00fe572bd6da33c66b326feb1020f34ceb0684d91e819d03123265dd16958ee2 |
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-_d9play_/native.log` | 418784c7c26272ab4cfc6f471e39794b931e93f1e6420fbb06fafb1908784348 |
| `.local-test-evidence/2026-09-07/native-d94e93dc/resource-r4/resource.json` | ee70b6ade487816d8645862b52c41d7546039575bf85dd3eb4a33c55a9dc52a7 |
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-amir8dbt/launch.json` | 3af1b0c95ecd8649eec8b4784936ccf34909ee9b6618dd5f672fae2eb6b804e1 |
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-amir8dbt/native.log` | f0ecd71660c0fe224dbbdb840e08a98eeea64bae9d3deffee8756909034e0d56 |
| `.local-test-evidence/2026-09-07/native-d94e93dc/resource-r5/resource.json` | 5dd5e87b237a1f46d43a6ba5889889d68f4c1411526d0cfa63a6c6ebb7af1009 |
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-_d9play_/userdata/data/state.db`（退出后快照） | 7eb64e299b68d123456a8296552c2e841593e198bc27c25442ec92c41d22256a |
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-_d9play_/userdata/data/human_memory_v7.db` | 43e7a6e46dcb500b03ad524a083fcdc7096a12ee06a797969ee1d04c5b5bd08f |
