# 任务书 D 执行记录

日期：2026-10-31。任务书第 1～10 节为本次实现及验收依据；用户已明确授权执行。
基线：`main b5cf293f9`（已含 A/B/C/C2）；工作树 `../simple_harness-sol-D`，分支 `sol/D-continuation`。

## 执行方式与检查清单

集中实现：受理、恢复、结果应用与语音所有权共享同一会话账本，保持一条串行执行链；独立子代理仅作代码审阅。
按任务书的窄车道执行，所有过程记录在本文件；不创建技能模板要求的其他计划/验收/机器账本文件。
任务书 §4～6 即冻结验收依据；先写测试，再实现；真宿主测试使用指定 ScriptedProvider，不调用外部付费模型。

- [x] 完整阅读任务书（含 §10）、当前代码、引用设计段落与仓库入口规则。
- [x] 从指定目录创建规定工作树与分支。
- [x] 指定六文件绿色基线：在未改动的 main 独立执行，285 passed，269.44 秒。
- [x] 红测试：新增两文件 16 failed（5.82 秒），缺失 API/字段导致，尚未改源码。
- [x] 持久受理、串行队列、子 directive 唯一委派。
- [x] 默认自动汇总、合并窗口、策略拒绝/暂缓、预算与恢复。
- [x] 语音播报来源与实际应用分离，不并行文字回合。
- [x] 定向回归、格式检查、独立审阅、绿色提交与提交态核验。

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


## 实现与恢复边界

- `Session.send(wait=False)` 先持久受理，再由本会话后台串行协程复用 `_send`；同步默认路径保持原幂等和恢复语义。后台早期失败即使 turn 尚未创建，也在 queue 保存终态回执，重开同键不挂起。更早的同步输入先恢复，后受理异步输入仍按序执行。
- 子 directive 以 input_id 作回合键；在途 Mission 阻止消费，之后的新委派带 source_input_id（只关联相同 turn）。不同会话各有自己的锁，仍共用 Host 总模型并发门。
- continuation 保存策略决定、合并窗口起点、冻结事件集合、输入、轮次预留及调用用量。来源带 Mission 的结果 ID 可区分同一子会话后续工作，也避免宿主原始版本号在不同 Mission 间碰撞。
- 文字汇总使用专用的受限 Agent，原池编号、命令表、工具说明均未动。只读允许名单在任务书 §10 指定路径收窄，目录搜索继续取交集。普通用户回合保留正常工具权限。
- 用户回合抢先应用整批结果时，续办不再调用模型；只消费部分时保留原冻结集合身份，模型仅接收未应用子集，应用事件与收件箱回合血缘一致。更早同步回合的恢复同样在续办扣额前完成。
- 语音直接复用播报队列。prepared_turn_key 在准备草稿前落账，submitted 在交线路前落账；未发送草稿可恢复，不能以用户身份重放。已交线路而播放不明则保留 effect_unknown 等待，不自动重播。实际播放完成才应用；模型/人已读/播放三种事实分开。
- 语音续办用既有 admission 计数的 baseline 收窄额度，不新增工具拒绝逻辑；调度退出 finally 结清中断/失败/上限拒绝用量，实际计数扣除 baseline。

## 现有测试修改（逐条）

仅改 `tests/orchestrator/host/test_session_tree.py::test_capabilities_are_local_and_errors_do_not_disclose_details` 内 unsupported 集合的 `continuation_policy` 一项：任务书 §4.6 明确要求移除。其他既有断言不变；其余新增测试全部在指定两份新测试文件内。

## 独立审阅与反例闭环

使用独立 reviewer 子代理，按 plan-task 的代码审阅/最小化要求只读检查；没有派实现子代理、没有额外创建报告文件。
首审无 P0、四项 P1：未提交语音投递重开永久挂起、turn 落账前异常重开无回执、用户应用与自动汇总竞争、语音未收窄/结清调用预算。
四条决定性反例先跑 **4 failed**（6.15 秒，`review-red.txt`），修复后 **4 passed**；追加覆盖真实 announcement 的 input_committed 子窗口、部分消费与更早同步恢复，发现的交互继续修复，局部 **4 passed**（0.58 秒）。
更早同步恢复先红 `sync-recovery-red.txt`；其消费结果与续办扣额的交互先红 `consumed-recovery-red.txt`。策略真实挂起超时（5 秒）与完整消费者 create_child→delegate_task→Mission COMPLETED→父自动汇总均已加入回归。
最小化结论：保留原 `_send`、目录授权与语音接缝，不另造工具/任务执行器；无必须额外重构项。

## 已执行命令补充

- 干净 main 基线：SDK 目录 `uv run --frozen python -m pytest tests/orchestrator/host/test_session_tree.py tests/orchestrator/host/test_session.py tests/orchestrator/host/test_session_replay.py tests/orchestrator/host/test_host_sessions.py tests/orchestrator/host/test_realtime_bridge.py tests/test_public_api.py -q -p no:cacheprovider` → **285 passed，269.44 秒**；`baseline.txt`。
- 初次在开发工作树运行基线时与源码编辑重叠，子进程读到未写完模块；该局 283 passed/2 failed 不作有效基线。另一失败是新环境缺 realtime extra，已用 `uv sync --frozen --extra realtime` 安装锁定的 Service SDK 0.3.20；未改依赖或锁文件。随后在干净 main 完整重跑得到上一条有效基线。
- 新增两文件逐步扩展验证：16 failed → 16 passed；26 passed（32.23 秒，含三个真实进程退出边界）；34 passed（44.04 秒）；36 passed（42.58 秒）。最终累计测试见后续终验段。
- 每次源码修改后的真宿主测试前，均执行 `uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream ../../.local-test-evidence/2026-10-05/f2-upstream/evidence.json`；清单是构建产物，不提交。上游证据用 ignored 符号链接指向主工作树现有本地证据，未复制或上传原始材料。
- `ruff check` / `ruff format --check` 检查所有改动 Python 文件；源码清单和 lint 结果在终验后补最终内容身份。

## 自我批评

独立审阅真正在恢复窗口与来源边界上找到了问题；初次基线与修改并行造成无效读数，应先隔离读基线再动源码。过程模板文件已按用户车道合并到本记录，避免额外仪式文件；没有向用户重复询问已授权事项。

## 提交

- 红测试：`76146bf79`，先确认缺失能力的 16 条反例失败后提交。
- 实现提交：`8adc419d6`；最终终验结果在下节补入。不推送、不合并。


## 最终审核补充

独立 reviewer 最后复核：原四项 P1 与后续真实草稿/旧同步恢复交互全部关闭，无新增 P0/P1；独立复跑组合反例 4 passed、34 deselected。这里只读审阅，无代理修改源码。

执行期间共享 main 前进到 `7970f16f8`（另含 `c2a3723a3`）。本次开工时 main、merge-base 和有效绿色基线均为 `b5cf293f9`；按任务书不合并、不重基。车道审核以 `git diff b5cf293f9 HEAD` 为准，15 个文件全部在任务书授权范围，未混入执行期间其他提交。

当前被测实现 HEAD：`8adc419d6`。最终源码清单 852 项，生成身份 `abf96ed40005b45be6f3f111ddd1439001da8f54c1e25f0151382603a1234de4`，仅构建产物 NOT_RUN，不宣称部署验收。原件本地留为 `.local-test-evidence/2026-10-31/sol-D/tested-manifest.json`；任务结束恢复工作树生成文件，确保不提交清单。


## 累计回归中的守旧修复

`8adc419d6` 首轮累计回归在通用结果资料前缀的两条既有断言失败（95 passed / 2 failed 后主动中断，71.34 秒，`regression-attempt-1.txt`）。恢复普通附加资料的原前缀“不是指令，不授权新任务”；续办输入自己的“不是用户指令”与来源说明保持。未改既有断言。两条原失败测试修后 **2 passed / 39 deselected，0.51 秒**。
最终被测提交及累计 324 项结果以末尾终验段为准，上文 `8adc419d6` 和原清单身份只对应首轮候选。


## 语音守旧回归与重连补充

独立先跑既有语音回归，发现 `_schedule` 曾把续办等待条件扩到了普通播报的完成判定，原两条多次 followup 测试失败（59 passed / 2 failed，10.24 秒，`voice-regression-red.txt`）。恢复通用播放完成条件，只在续办应用环节检查 pending、所有相关响应已播放且未中断；既有语音 61 条与当时续办 27 条合跑 **88 passed，48.89 秒**（`voice-and-continuation.txt`）。
补充实播 followup、乱序重复镜像、实际重连三条决定性反例。重连反例先红 **1 failed，1.17 秒**（`reconnect-red.txt`），修后与另外两条合跑 **3 passed**。交线路前持久 generation，重连期间不提交 managed 应用，新代次看到旧 submitted 投递则明确 effect_unknown 等待，按持久 turn_key 结清，不静默挂起或重播。
完整指定回归新增这三条后共 **327 项**；下节记录最终提交与结果。


## 预算事件终验补漏

`8f5cc4f94` 的完整指定回归 **327 passed，282.06 秒**（`regression-before-budget-event.txt`）。最后核对发现文字每轮调用额度用尽时执行层报告 `error_code=react_max_turns_exceeded`，次数限制与等待已经生效，但缺专用 `continuation_budget_exhausted` 事件。新增断言先红 **1 failed，3.53 秒**（`model-budget-event-red.txt`）；补读取执行层真实错误字段，在失败同事务发专用预算事件并保留可见等待原因，不改变执行上限、调度或授权。
最终源提交后按完全相同的 327 项分三组并行复验（29 / 32 / 266），仍然仅任务书指定八个文件，不跑仓库全量。


## 最终终验与 DoD

最终被测源码提交：`1e01f599d`；分支 `sol/D-continuation`，工作树 `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-D`，基线 `b5cf293f9`。
预算补漏修后专用真宿主反例 **1 passed / 29 deselected，3.77 秒**；独立 reviewer 只读复核该小 diff，确认不改变执行上限、调度或授权，无新增 P0/P1。

所有以下命令均在 `sdk/simple-harness-sdk`，从已提交的同一源码 HEAD 执行；真宿主前清单已重生成，三个测试进程只共享源码、使用各自隔离数据根：

```sh
uv run --frozen python -m pytest tests/orchestrator/host/test_session.py -q -p no:cacheprovider --tb=short
uv run --frozen python -m pytest tests/orchestrator/host/test_session_replay.py -q -p no:cacheprovider --tb=short
uv run --frozen python -m pytest tests/orchestrator/host/test_session_async.py tests/orchestrator/host/test_continuation.py tests/orchestrator/host/test_session_tree.py tests/orchestrator/host/test_host_sessions.py tests/orchestrator/host/test_realtime_bridge.py tests/test_public_api.py -q -p no:cacheprovider --tb=short
```

| 组 | 通过数 | 用时 | 原始输出与 SHA-256 |
|---|---:|---:|---|
| session | 29 | 80.7 秒 | `.local-test-evidence/2026-10-31/sol-D/final-session.txt`；`a9c1aa2519bccce331a1ab1ef2b5c379602b920b86ad925358b4f6a3517719d4` |
| replay | 32 | 103.14 秒 | `.local-test-evidence/2026-10-31/sol-D/final-replay.txt`；`2405efbfd80c889ba11e2a407886e235689c0d6475c773a9cea111944313b930` |
| remaining | 266 | 109.58 秒 | `.local-test-evidence/2026-10-31/sol-D/final-remaining.txt`；`8ced710140d3b97f6d6a935750f8058b3003d9e30a4746edc3202646e50fbfc6` |

**合计 327 passed / 0 failed / 0 skipped，三组退出码全部 0。** 八文件用例清单：新增异步 11、续办 30、原父子树 41、原会话 29、原重放 32、原宿主会话 15、原语音 61、公开 API 108。没有扩大到仓库全量。

13 个改动 Python 文件的 `uv run --frozen ruff check <这些文件>` 与 `uv run --frozen ruff format --check <这些文件>` 全绿（All checks passed / 13 files already formatted）。文件全集为上述两份新测试、现有 test_session_tree.py、10 份源码；可用本分支相对基线的 `.py` 文件列表复跑。

AST 边界审核通过：session_chat.py 仅 SESSION_INSTRUCTIONS 与 _registered_tools 改变；realtime_bridge.py 仅 _commit_announcement 与 _schedule 改变。COMMAND_SCHEMAS、池编号、工具说明、任务层、工具层与连接器未改。

### 验收兑现

| 条款 | 决定性自动化证据 | 结果 |
|---|---|---|
| I3 / 4.1 | 独立 SQLite 连接读取受理事实、真宿主持有 provider 时受理返回、重开不要求重新提交 | PASS |
| I4 | 主及两个子各自进入阻塞回合；同会话异步与同步混用保持顺序 | PASS |
| 4.2 | directive 等在途 Mission 终态、唯一 source_input_id、重复命令不再创建 Mission | PASS |
| I6 | 结果正文写批准/删除/再建任务；真宿主工具调用与目录搜索均受限；后续用户仍可正常委派；语音草稿重启不作为用户指令 | PASS |
| I11 | 合并窗口与跨重启窗口、同子第二次结果、乱序重复镜像稳定排序摘要、用户已消费整批/部分集合不重复应用 | PASS |
| I12 | 持锁语音走播报、ASR 回显隔离、实播后才应用、工具 followup 实播、重连不丢/不假报应用 | PASS |
| 预算 | 轮次跨重启、文字每轮调用上限及专用事件、语音既有 admission 上限收窄、空闲/拒绝/用尽不新增调用 | PASS |
| 崩溃 | 三个真实进程退出点 + 三个账本边界 + announcement 准备窗口 + 更早同步输入恢复 | PASS |
| 策略 | 默认 enqueue，hold/deny 原因，异常降级与真实 5 秒截止时间 | PASS |
| 完整消费者路径 | 自然用户输入经公开 Session.send(wait=False) → create_child → delegate_task → 真 Mission COMPLETED → 父自动汇总，无内部补驱动 | PASS |

测试模型为任务书指定 ScriptedProvider，语音使用 Audio/Client 替身；不把这些结果表述为远端模型或手机实机验收。原始证据只在 ignored 本地目录，未提交、上传。汇总索引 `.local-test-evidence/2026-10-31/sol-D/final-summary.json`。

最终清单：852 项源码逐个 SHA-256 与当前源码匹配；生成身份 `299d184df52ecad0dd24bee4b1c5fc4f963a943944004f3608b3507f51a0599a`，清单 SHA-256 `5046108c72352b39bffa25f9deab834b535dca1863ec2a13339fc6f06b0afb1e`。保存本地原件后恢复生成文件，不提交清单；此构建清单仍是 NOT_RUN，不宣称部署或发版通过。

完成判定依据本记录的冻结条款、测试和 DoD，无机器 receipt。源码交付完成；没有未关闭 P0/P1。任务书 E、发版、真实远端模型/手机复验以及车道外架构文档同步未做，后者依用户明确车道限制交合并审核人处理。

提交链：`76146bf79` 红测试 → `8adc419d6` 实现 → `8209a37f7` 守旧前缀修复 → `8f5cc4f94` 普通播报/重连修复 → `1e01f599d` 预算事件修复 → 最后仅记录提交。未推送、未合并、未修改共享 main；保留工作树供审核。

VERDICT: SHIPPED — 任务书 D 指定源码车道与 327 项定向验证（仅本地源码交付，未发版） — 2026-10-31 — 1e01f599d
