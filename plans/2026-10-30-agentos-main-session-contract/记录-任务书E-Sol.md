# 任务书 E 执行记录

日期：2026-10-31。工作树 `../simple_harness-sol-E`，分支 `sol/E-authorization-goal`。
基线：当前 main `5f1a11c1b`（已含 A/B/C/D/D2 及金丝雀修正）。未推送、未合并。

## 范围与执行依据

任务书 E §4/§5 为验收真相，§10 优先：续办只走 ContinuationPolicy；取消在持有 send lock 后重读队列、扣额前检查。只修改 §3 车道与本记录；不按通用 AGENTS/skill 扩写架构、oracle、journal 等其他文件。任务书 §9 已授权范围内技术取舍，不停等确认。

执行分工：授权及效果账本、Goal 及恢复报告、owner 由独立代理准备测试和实现；主代理负责会话生产接线、取消、关闭、文档与集成验证。独立 review 交叉审查非本人实现。此任务为 SDK 库接口，以 FakeHost 账本测试和 ScriptedProvider 真宿主测试验收，不涉及 UI。

## 先红后绿

实现前冻结的预期：A1 默认/自定义授权在受理和派发检查，异常超时拒绝、人工批准绑定摘要且不复用到新参数；A2 根授权撤销让子队列执行拒绝；I7 取消先持久屏障，派发/续办/队列均检查；O1 owner 持久代次及失锁拒绝；C1 默认关闭拒活、强制关闭保留任务、归档不删数据；R1 恢复报告纯事实；G1 根级唯一 Goal、版本历史及结构收口；E1 claim 与权威回执对账不盲重放。

红测试（源码未改）：
- `test_authorization_port.py`：缺 authorization 模块，1 collection error。
- `test_goals.py`：17 failed（缺宿主 Goal API/恢复报告）。
- `test_owner_and_close.py`：6 failed（缺代次、冲突码及失锁拒绝）。
- `test_cancel_scope.py`：6 failed（缺范围取消、force 和命令 schema）。

## 基线与验证

所有测试从 `sdk/simple-harness-sdk` 执行，使用 `uv run --frozen python -m pytest ... -q -p no:cacheprovider`，不跑全量。
真宿主前已执行 `uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream ../../.local-test-evidence/2026-10-05/f2-upstream/evidence.json`；清单不提交。本地证据 `../../.local-test-evidence/2026-10-31/sol-E/`。
基线指定八文件测试进行中。

## 拿不准的

- 同步原生命令端口与 async AuthorizationPort：保持命令端口协议，在线程内只运行宿主回调，账本仍由会话线程提交，最多等待 5 秒。
- 默认关闭拒活与 Host.shutdown：宿主进入 closing 时仍走资源释放语义；显式 Session.close 默认核活，force=True 明确释放资源且保留业务责任。
- 未指定的预算维度不另起模型计数；Goal 登记正整数预算，模型计量继续读取既有会话及 continuation_usage。

## 结果

实施中；未宣称完成。

## 实现与审查补记

- 授权：可信字段、超时失败关闭、allow缓存/持久撤销、人工审批绑定参数及Goal版本，默认auto_mode端口；同步命令端口保留，回调只在线程内运行，账本仍由会话线程写入。异步受理等待期间Goal更新则拒绝本次受理（stale_version），不让旧授权覆盖新版关系/输入。
- 取消：根账本持久屏障，消费队列在拿锁重读后检查；取消排队输入不会扣续办额度。子树迟到结果从delegation定位实际子身份，审计after_cancel且不续办；各范围独立收敛，已提交语音只标意图并保留handed_off/played事实。创建Mission先claim，回执丢失后按原幂等键只读查任务库，不能确定就unknown，禁止用再次创建探测。待审批随范围持久取消。
- Goal：根唯一未收口目标，规格版本和历史；子关系/委派/输入/结果冻结原版本（显式None也不会借新Goal）。收口核任务、通知、审批、排队责任和未知效果；可信排除释放未执行续办，混合批保留未排除资料。
- Owner：锁旁代次原子递增，公开冲突错误含代次；旧owner拒绝后续写入，在线owner_lost不向失锁库写假持久事件。原start未持锁实例仍保持既有unavailable行为；公开open/Session.open报owner_conflict。
- 关闭/归档：close默认拒活、force资源停机保留责任；Host关闭保持停机语义。归档不删库，归档子不阻断父恢复。
- 恢复：报告读会话和真实任务快照，包括D表、待审批、失绑定语音、claimed但Mission关联丢失的未知交接。
- EffectLedger：会话效果接口预备，claim后在真正handoff前再查授权/owner/屏障；交接前拒绝记not_committed，交接后如实对账；缺回执保持unknown，不重放。宿主动作等级上限和回调约束同时生效。

独立代码审查由非本人模块执行者交叉完成。发现并修复的P1均有决定性自动测试：子树迟到续办、显式None冻结、归档子恢复、Mission成功/关联前崩溃、授权期间Goal更新、Goal收口后审批重放、Python3.11调度缝隙的claim后handoff前屏障、未知Mission在报告/Goal中遗漏、跨取消范围语音责任串入。未修改任务/工具/连接器协议。

既有测试改动仅两处，均为新能力字段的断言：
1. `test_session_tree.py::test_capabilities_are_local_and_errors_do_not_disclose_details` 的unsupported由goals/authorization_port/steer改为只剩steer。
2. `test_continuation.py` 能力发现断言同上。
未改既有关闭清理调用，未改池编号；命令表只加cancel，提示词只追加一段。

## 可信基线

最初baseline.log在源码变动期间仍在运行，其清单校验结果不可作为基线，已弃用。
重新用`git archive 5f1a11c1b:sdk/simple-harness-sdk`在ignored证据目录导出快照，打印两个包的__file__确认来自该快照，重生成快照清单后在同进程pytest跑八指定文件：**287 passed in 312.06s，exit 0**。
日志 `baseline-frozen.log` SHA-256：`a651ffbcf50cea3444f4335bb826440ead48fca31910a20d9ae38b1c307904ee`。
快照清单SHA-256：`7ed5489a457a91e9d9bccf16a2845944a53390c21f08a49114220f0fa08f7d27`。

## 验收进行中与约束冲突

新真实宿主冒烟（取消模型命令 + owner/关闭/归档新文件）首轮 **18 passed in 10.13s**。此后补了取消范围语音隔离，已重生成855项源码清单（deployment_id `06657f0c1b311698e3468b5dc562e98f118c2065921dae21c75ae295a17d867b`），开始完整指定回归及新增四文件。
源码稳定后改动文件ruff check、ruff format --check（21文件）与git diff --check通过；最后结果在后文回写。

已确认任务书内部存在关闭测试冲突：§4.4/§10明确要求未消费结果时close()拒活；既有`test_policy_context_and_fail_closed_decisions`四种情形保留未消费结果却无参数close()清理，因此4项在清理时报active_work。§3仅允许改现有测试中新字段直接相关的断言，不允许把这些调用改为force=True，也不能为绿测在实现中放行关闭。依§9采用合理做法：实现按新合同，保留老测试，记录精确失败，不声称§7全绿。继续执行其余所有指定验证。

## 未做

未接入任务书F动作工具、未增加运行期工具注册、未改任务审批链、工具层或连接器协议；未迁移旧库、未发版、未推送、未合并。部署清单只在本地生成，不提交。

## 提交态验证与可复跑命令

- 红测试提交：`4a06b5b47`。
- 实现提交（被测源码 HEAD）：`6a4e65af0`。
- 该HEAD四新增测试文件：**104 passed in 11.90s，exit 0**，日志`new-head.log`（前一轮104 passed in 11.36s，同源码）。组成：authorization 44、cancel 16、goals 27、owner/close 17。
- 原有第二宿主让位行为定向回归：**1 passed in 4.48s**，日志`lock-regression.log`。
- 独立完成度审计覆盖A1/A2/I7/O1/C1/R1/G1/E1，无已知P0/P1遗留；验收结论仍须受下述指定回归结果约束，不能把新增104绿替代原回归全绿。

执行目录：`sdk/simple-harness-sdk`。以下均加`-q -p no:cacheprovider`，所有真宿主执行前先重生成清单：

```sh
uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream ../../.local-test-evidence/2026-10-05/f2-upstream/evidence.json
uv run --frozen python -m pytest tests/orchestrator/host/test_authorization_port.py tests/orchestrator/host/test_cancel_scope.py tests/orchestrator/host/test_goals.py tests/orchestrator/host/test_owner_and_close.py -q -p no:cacheprovider
uv run --frozen python -m pytest tests/orchestrator/host/test_session_tree.py tests/orchestrator/host/test_continuation.py tests/orchestrator/host/test_session.py tests/orchestrator/host/test_session_replay.py tests/orchestrator/host/test_host_sessions.py tests/orchestrator/host/test_host_connectors.py tests/orchestrator/host/test_host_recovery.py tests/test_public_api.py -q -p no:cacheprovider
uv run --frozen python -m pytest tests/orchestrator/host/test_orchestration_host.py::test_a_second_host_on_the_same_directory_stands_aside -q -p no:cacheprovider
uv run --frozen ruff check src/agent_orchestrator/host/{authorization,goals,recovery_report,session,sessions,delegation,inbox,session_tree,continuation,host,instance_lock,settings,session_chat,__init__}.py src/agent_orchestrator/public_api.py tests/orchestrator/host/test_{authorization_port,cancel_scope,goals,owner_and_close,session_tree,continuation}.py
uv run --frozen ruff format --check src/agent_orchestrator/host/{authorization,goals,recovery_report,session,sessions,delegation,inbox,session_tree,continuation,host,instance_lock,settings,session_chat,__init__}.py src/agent_orchestrator/public_api.py tests/orchestrator/host/test_{authorization_port,cancel_scope,goals,owner_and_close,session_tree,continuation}.py
```

证据均在工作树根`.local-test-evidence/2026-10-31/sol-E/`，不提交原始日志。`acceptance-first.log`及`acceptance.log`是中途因发现P1或脚本夹具形状错误中止的试跑，不作最终完整验收证据；实际完成输出取`new-head.log`与`regression.log`。

自我批评：首次并行推进污染了仍在运行的基线清单校验，已改成不可变git archive快照重跑。决定性崩溃/授权等待/调度间隙测试与独立审查拦住实际缺陷；机械要求旧测试全绿与禁止改其清理调用相冲突，必须保留精确失败事实。

## 指定回归首轮与实际缺陷修正

`regression.log`：**255 passed /35 failed，274.68s**。33项直接在无参数close清理时报active_work；另1项是旧cancel_task要求“尚未派发也先创建Mission再取消”，与新屏障禁止取消后派发相冲突；最后1项发现child_send重复查询已终态历史，是实际性能/行为回归，已修。

另外由独立复现实锤修正：用户已成功消费结果，但continuation_results残留pending会使close永久拒活，hold/deny也会在恢复报告误报责任。现在用户提交消费事实时，同事务把相应续办标applied并绑定实际回合，已完全消费的未执行批次同步done。追加enqueue/hold/deny三条决定性测试；child_send追加已终态不轮询测试。未放宽关闭门或修改旧测试清理调用。将对新HEAD重新生成清单并跑全部指定文件。
