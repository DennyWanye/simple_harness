# 任务书 D 执行记录

日期：2026-10-31。任务书第 1～10 节为本次实现及验收依据；用户已明确授权执行。
基线：`main b5cf293f9`（已含 A/B/C/C2）；工作树 `../simple_harness-sol-D`，分支 `sol/D-continuation`。

## 执行方式与检查清单

集中实现：受理、恢复、结果应用与语音所有权共享同一会话账本，保持一条串行执行链；独立子代理仅作代码审阅。
按任务书的窄车道执行，所有过程记录在本文件；不创建技能模板要求的其他计划/验收/机器账本文件。
任务书 §4～6 即冻结验收依据；先写测试，再实现；真宿主测试使用指定 ScriptedProvider，不调用外部付费模型。

- [x] 完整阅读任务书（含 §10）、当前代码、引用设计段落与仓库入口规则。
- [x] 从指定目录创建规定工作树与分支。
- [ ] 指定六文件绿色基线。
- [x] 红测试：新增两文件 16 failed（5.82 秒），缺失 API/字段导致，尚未改源码。
- [ ] 持久受理、串行队列、子 directive 唯一委派。
- [ ] 默认自动汇总、合并窗口、策略拒绝/暂缓、预算与恢复。
- [ ] 语音播报来源与实际应用分离，不并行文字回合。
- [ ] 定向回归、格式检查、独立审阅、绿色提交与提交态核验。

## 预先冻结的验收映射

| 条款 | 预期与决定性验证 |
|---|---|
| I3 / 4.1 | wait=False 在持久 queue + input_accepted 后返回；独立 SQLite 连接能读到；同 key 不重复，异文冲突 |
| I4 | 同会话两个输入顺序提交，混用 wait=True/False 仍串行；主与两子各自阻塞的三个回合能同时进入 |
| 4.2 | Mission 在途时 directive 不调用模型；终态后执行，以 input_id 关联唯一新委派，重放不再建 Mission |
| I6 | 结果正文中的批准/删除/再建任务只是资料；绑定工具允许名单只含查询/只读；ToolSearch 不得重新发现写命令 |
| I11 | 窗口合并冻结 event_id 列表；重复/乱序镜像不增加逻辑输入；同一子的第二个结果独立回流 |
| I12 | voice 持锁时结果走播报，无文字模型；来源 task_result 随上下文；回显不当 ASR；完成实际播放才应用 |
| 预算 | 每输入原子预扣轮次/调用额度，实际用量持久；重启不重置；用尽可见等待；空闲无新增调用 |
| 崩溃 | inbox_written / continuation_queued / continuation_applied 后中断重开，结果不丢、输入不重、已提交模型不重跑 |
| 策略 | 默认 enqueue；hold/deny 不调模型并留原因；异常/超时最多 5 秒后 hold(policy_unavailable) |

## 命令与结果

红测试命令（SDK 目录）：`uv run --frozen python -m pytest tests/orchestrator/host/test_session_async.py tests/orchestrator/host/test_continuation.py -q -p no:cacheprovider` → **16 failed**，5.82 秒。原始输出 `.local-test-evidence/2026-10-31/sol-D/red.txt`。首次重定向目录不存在，未启动测试；创建 ignored 证据目录后用上述命令正式执行。

## 拿不准的

- 指定车道与仓库 AGENTS 的架构文档同步要求冲突：遵循用户明确“只改车道”指令，架构同步交审核人合并时处理。
- 通用 skill 的独立 acceptance/journal 文件要求与用户指定记录位置冲突：以任务书不变量和测试要求为验收标准，过程和终态统一写本文件。
- continuation_max_model_calls_per_turn 默认用 None 表示继承 SessionPolicy.max_model_calls，以保持不同会话策略的准确上限。

## 没做的

任务书 E、工具/任务/连接器层、命令表、池编号、部署清单提交、推送、合并及全量回归均不在本次范围。
