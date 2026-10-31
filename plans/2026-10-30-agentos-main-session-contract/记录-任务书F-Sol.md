# 任务书 F 执行记录（Sol）

日期：2026-10-31（Asia/Taipei）。任务书第 10 节优先；用户要求第 3 节之外不改，问题按第 9 节自行处理并记录。

## 身份与边界

- 先进入 `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness`，再执行任务书原命令 `git worktree add ../simple_harness-sol-F -b sol/F-action-tools main`。
- 基线：`fa8c96559481c4e0c4d58dd084348cb515e8d8f6`，当前 main，含 A/B/C/D/E。
- 工作树：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-F`；分支：`sol/F-action-tools`。
- 集中执行：目录、授权、输入队列、收件箱相互依赖，采用单一工作树串行实现。独立子代理仅做只读正确性/最小化审阅与本地 probe。
- 本任务书视为已批准 plan，第 5/6/7 节就是验收依据。复用其 I5/I6/E2/R2/P1/T7，不另写或更改验收表。
- 原主树的未跟踪 `.archify/` 保留。未推送、未合并、不跑全量、不碰密钥。
- 收尾检查时主树另出现外部未跟踪 `sdk/simple-harness-sdk/scripts/acceptance/measure_session_tree_load.py`，不是本轮创建，未读取、修改、提交或删除；main 的基线提交仍未移动。

## 改了什么

1. `OperationSpec` 仅加 `tool_roles: tuple[str, ...] = ()`；空角色不暴露，未修改其执行/查询语义或旧 JSON 投影。
2. 新增 `runtime/action_tools.py`：操作参数适配、稳定动作槽位、参数绑定、强制审批卡片投影、审批后输入与已有回执观测。执行/claim/lookup 只调用现有 `EffectLedger.execute()`，没有另写效果执行器。
3. Host 装配连接器时按等级与角色登记按需动作工具，目录 `source="action"`。只允许 main/child；任务角色仍走申请单，子角色仍受主角色交集限制。
4. Session 通过现有授权入口处理动作，能力发现增加 `action_tools`；批准/拒绝后排队 `action_followup`，原 `_run_inputs` 拿锁和检查屏障后消费。批准已落账但未入队的间隙由 inbox catch-up 补队列。
5. 动作结果以稳定事件号进 `source="action_result"` 的收件箱，`instruction=False`；结果与队列完成同事务。下一句附资料、只读自动汇总、子结果镜像复用原路。无委派的动作通知不借用不存在的任务结果预算。
6. 任务动作只观察已核对且匹配的回执，在所属会话的 `effects` 中按原 `idempotency_key` 记账；未改任务申请单、审阅、批准或执行器。
7. 提示词仅追加任务书指定段落；续办收窄的工具名单没有改动。文档只修改 §2d 与 §4b 后的小节。
8. 新测试全部在 `test_action_tools.py`，既有测试与断言没有修改。

## 验收映射与可复跑命令

以下命令均在工作树的 `sdk/simple-harness-sdk/` 运行。真宿主使用可控连接器与 `ScriptedProvider`，是 SDK 真实宿主栈集成测试，不是原生 UI、付费模型或桌面应用验收。

| 条款 | 新增自动化场景 |
|---|---|
| I5 | 稳定槽位、换 run/call 重放、参数冲突、pending 时异参不再发卡；超限后旧回执仍能回放 |
| I6 | 恶意 inbox 正文不能绕过 always approval；真实续办搜索/加载/直接调动作均无法执行 |
| E2 | 抛异常且 lookup 暂无回执→unknown；重试不再 execute；lookup 恢复后 committed |
| R2 | 父/子角色交集、超等级不入目录、动作不进任务工具、参数 schema 覆盖 |
| P1 | manual/always 两档的批准和拒绝、唯一跟进输入、唯一通知、下一句附结果；子发起父批准与父镜像；批准落账后恢复队列；取消 Goal 阻止交接 |
| T7 | 真任务 `action:` 仍完成申请单/审阅/批准/执行链，并和主会话动作将回执记入同一份 effects |
| 授权限度 | 强制人工审批保留宿主的风险限制、revision 与 expires_at，不因人工批准扩大权限 |

```bash
uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream ../../.local-test-evidence/2026-10-05/f2-upstream/evidence.json
uv run --frozen python -m pytest tests/orchestrator/host/test_action_tools.py tests/orchestrator/host/test_host_connectors.py tests/orchestrator/host/test_tool_catalog.py tests/orchestrator/host/test_authorization_port.py tests/orchestrator/host/test_session_tree.py tests/orchestrator/host/test_continuation.py tests/test_public_api.py -q -p no:cacheprovider
```

上游证据由主树现有同目录复制到 ignored 证据目录，未改内容。部署清单每次源码变化后重生成，本地用于真宿主验证，不提交，交付前恢复工作树原清单。

### 已运行结果

- 原主树独立基线（上列命令不含新增文件）：**285 passed / 0 failed，121.53 秒**。
- 最初在实现工作树启动基线后过早开始编辑，导致后续子进程复制到变化中的源码，出现 **281 passed / 4 failed**。此轮明确作废为基线，不归因于主树，不用来证明新增回归；保留原始日志。
- 先红：新增 10 个场景全部因缺 `tool_roles` 失败，**0 passed / 10 failed，0.84 秒**；随后提交测试 `d4a26cd35`。
- 首轮实现：9 passed / 1 failed；暴露无任务通知误结算 `session-result:None`。修正按无委派通知处理后，10 passed / 0 failed，6.29 秒。
- 后续新增测试曾暴露测试夹具缺少 Goal 的预算/完成条件、delegate 的 criteria 必须字符串，以及 `approvals()` 只返回待批项。均按既有生产接口修正新夹具：T7 改为读取任务动作绑定的原 approval request 与 decision，而不是把已批准的请求当成待批项。没有改变或放宽“必须存在真实批准事实”的预期。
- 实现完成后的指定定向整组：**301 passed / 0 failed，147.93 秒**（新增 16 + 原指定组 285）。
- 提交态复验：在 `83724572b15e6396d96055e9cedb9ed6dea6a74e` 再生成清单并重跑同组，**301 passed / 0 failed，149.79 秒，exit 0**。此后仅回写本记录，源码与测试未再改动。
- 最终清单：`deployment_id=f7aac048955664cd83341e20f0d9ece32a6bfcfcc35b1bb0c960408b474d6381`，856 个来源文件；已保存 ignored 副本并恢复工作树原清单，未进入提交。复跑真宿主前仍需按上列命令生成。

质量命令（改动的 9 个 Python 文件）：`uv run --frozen ruff check <文件列表>`、`uv run --frozen ruff format --check <同列表>`。format 为 **9 files already formatted**。check 只有 `runtime/connectors.py` 原有注释 E501（主树第 128 行，工作树加字段后第 129 行，101 > 100），主树同命令复现；新增代码没有 lint 错误。因该文件车道只准增加字段，没有修原注释。

## 独立审阅与修复

- 审阅对象为相对上述 main 的累计 diff，覆盖授权、幂等、恢复、取消、角色与预算。
- P1：强制人工审批替换 allow 时会丢掉端口限制与期限。已在发卡前检查原限制，并在批准后保留 constraints/revision/expires_at；加入决定性测试，独立 probe 复验 L1 限制和过期 allow 均无卡片/无执行。
- P2：达到上限后按整次 attempt 的路由总数检查，会拒绝旧调用回放。已改按当前稳定 slot 检查；测试断言第五次被拒、c0 原回执仍 committed，真实执行总数仍 4。
- 独立真实宿主 probe 复验“子发起→父批准→子执行→父镜像”通过。当前审核切面无剩余 P0/P1；最小化意见为复用现有执行/对账与串行队列，不为消除卡片投影重复越界改授权模块。
- 可选 P2：动作卡片目前带 target_ref/params_hash，缺少 `to` 等参数摘要，接入 UI 如只读卡片事件不便展示实际目的地；本轮未增加 UI 或扩大生产改动。
- 完成度审计：13 个测试函数、参数化合计 16 场景，六条不变量都有实际断言；限定覆盖审计 PASS。精度边界：续办测试直接断言 provider 工具集无动作、无实际执行，未逐项解析搜索返回体；E2 验证 lookup 恢复 committed/唯一效果，没有逐字段再比 receipt；T7 验证申请单/批准请求/decision/receipt，未另查 review 记录。既有任务审阅路径没有改动，原指定回归另行覆盖。

## 拿不准的、未做的

1. **未完成：正式 `HostSettings.max_actions_per_turn` 字段。** 任务书 §4.3 要求它，但真实定义在 `host/settings.py`，§3 未开放，用户明确只改车道内文件。按 §9 自决保留车道：适配器采用 `getattr(settings, "max_actions_per_turn", 4)` 的默认上限并受 SessionPolicy 约束；标准 `HostSettings` 仍不能用该关键字配置。不能把“默认上限生效”报告成“配置字段已完成”。
2. `OperationSpec` 真实参数字段为 `required_params`，`param_schema` 已存在，故只增加 `tool_roles`，不再造 `params` 或重复 schema 字段。
3. `DomainTool.NAME_PATTERN` 不允许点号，按 §10 采用 `<connector>__<operation>`，未放宽命名规则。
4. `authorization.py` 不在车道，且原 `_apply()` 写死 `request_kind="authorization"`。在允许的 Session 授权子类中调用动作适配的卡片投影；沿用原授权请求/绑定/grants/respond，仅重复动作卡片投影，不复制 claim/执行/lookup。
5. task effects 的追加采用原任务回执的只读观测，没有改车道外 ActionExecutor/CommitService。任务中的申请单与执行权限不因本轮改变。
6. `ARCHITECTURE/` 不在车道，未修改；架构状态由审核合并方回写。本轮不是发版，不声明桌面消费者、真实模型或 UI 验收。
7. 没有修原 connectors.py E501，没有修改既有断言，没有提交清单，没有推送、合并、发版、安装或运行全量回归。
8. `always_needs_approval` 对宿主的 allow 强制增加人工批准；宿主明确 deny 仍然拒绝，不把 deny 转为人能代批的 allow，以保留 §4.2 的拒绝语义和端口给定限制。

## 提交、证据与终态

- 红灯提交：`d4a26cd35`。
- 实现提交与最终测试身份：`83724572b15e6396d96055e9cedb9ed6dea6a74e`。
- 原始日志与独立审阅位于 `.local-test-evidence/2026-10-31/sol-f/`，永久 ignored；本文件只保留文字结论、命令、计数、索引与 SHA-256。
- 主要证据索引（以下文件均相对于该目录）：

| 文件 | SHA-256 |
|---|---|
| `sol-f-pristine-baseline.log` | `468d7745283d9c395fae5891d7ec04ef294e914992acc2a6caf33f6a755ecd31` |
| `sol-f-red.log` | `6971a0a0271777ddfb79a5b7fa03cfc7076f32f490e1172f9b2f0947384b626c` |
| `sol-f-regression1.log` | `e1df898ba8c0a4e975a99d1f976db3dcd4de102d70b4d7760cd9f1f3fbc6b2dd` |
| `review.md` | `599d5840ce0db7fce05ea0669e2c89c5d716c1cc583378e1c686ec45b1527781` |
| `sol-f-final-regression.log` | `7669b9ac083db1f9e7e82bacdc7e4bc03db2b9145f27ea4c11a41857fc27b7d3` |
| `sol-f-final-manifest.log` | `9f137dec262094b06d824c760bc38fc402a24646043e01c9568e9dba686afbe4` |
| `tested-taskgraph-deployment-manifest.json` | `74081437a154859fc4ea572d6500edf8b017db8ae018048deb8464b333838542` |
| `sol-f-final-lint.log` | `f255b986653759581a554bfa3af46a9c8cb1f17f128be99866a8a2675b793d09` |
| `sol-f-final-format.log` | `2ce9758b9f8abd876ec3e76c589e099e8b05377b1e7ea2331e81ee80f07bdff8` |

- 自我批评：首轮基线未等源码快照稳定便开始编辑，污染了子进程恢复测试；真正有效的门是独立主树基线和授权约束反例。沿用任务书的明确授权，未制造用户等待点。
- 收尾 DoD：车道审计通过、既有测试没有变化、提交态 301 项定向测试全绿、format 全绿、独立审阅无 P0/P1；正式配置字段与继承的 lint 红灯仍未达成，不宣称整任务完成。没有机器 receipt。部署清单恢复后，只剩本记录待提交；文档提交不改变被测代码身份。

VERDICT: BLOCKED — 任务书 F 车道内定向测试 301 通过；正式 max_actions_per_turn 字段未完成，Ruff 原有 E501 未解决 — 2026-10-31 — 83724572b15e6396d96055e9cedb9ed6dea6a74e

## F2 补修

执行日期：2026-10-31（客户端 Asia/Taipei）；F2 任务书标注日期为 2026-11-01。本节依据用户明确指令，在同一工作树、同一 `sol/F-action-tools` 分支继续。F2 覆盖上节仍记录为 BLOCKED 的配置字段和 lint，不改写历史结果。

### 并入 main 与测试基线

- 先进入 `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-F/sdk/simple-harness-sdk`，确认原 HEAD 为 `4a31b143b960de6e4b1a53dc3089d40b0d9da4ae`、工作树干净。
- 执行 `git merge main`；并入的 main 为 `2eeca3818`，含 `668266aa7` 的原始事件尾游标修复。合并提交为 `935ec0a14d7e4ee388691de7e16f5dd1d8d96a40`，自动合并无冲突，保留 F 动作接线与 main 游标补尾两边改动。
- 合并后先生成清单，再完整执行 F2 §4 指定的十个测试文件：**358 passed / 0 failed，232.37 秒**。基线期间没有修改生产源码。
- F2 新增十个场景先红：**9 failed / 1 passed / 16 deselected，9.50 秒**。已有卡片键集合测试通过，但不掩盖九项新行为失败。红灯提交：`fed093532`。
- 集中串行修改依赖链，独立代理只做事务方案与代码审阅/probe。测试对象为 SDK 宿主，不新增 UI 或真实付费模型验收，也不引入机器 receipt。

### 逐条改动

1. **§1 配置与 E501**：正式加入 `HostSettings.max_actions_per_turn: int = 4`，校验为正整数，配置映射可读取；适配器直接读取它与既有 `max_action_level`。原 connectors.py 注释折为两行，未改连接器协议。
2. **§2.1 空闲扫描与审批入队**：从 `inbox.catch_up()` 删除动作恢复，只在 `Session.recover()` 首次恢复段运行一次，FakeHost 的子打开分支也经过首次恢复。`responded()` 先读已有记录，缺失才进入事务。
3. **§2.1 原子边界的必要细化**：实际的授权单位于根 Session 库，子输入队列位于另一份 SQLite WAL 库；两次独立事务不能当成跨库原子提交。采用根库权威 `action_followups` 受理记录：在原 `SessionAuthorization.respond()` 的既有事务中，通过内部 `_on_decided` 回调将授权决定与待执行输入一起提交；主会话 `queued_inputs` 也在同一事务中写入。子队列是根库受理记录提交后的幂等投影，首次重开或重放 respond 均能补同一 `input_id`，不重置已经完成的队列。投影未完成也计作在途工作，归档不能藏掉已受理动作。没有 ATTACH、跨库锁或另造效果执行器。
4. **授权文件车道解释**：§2.3 明确开放 `_apply` 的改动；§2.1 同时要求 respond 决定与入队共享提交。原 respond 自行开事务，没有可插入的提交内接缝，因此增加内部回调是满足 §2.1 的必要最小改动；没有复制批准校验或修改公开 Session 命令。独立事务审阅认可上述边界，记录明确不宣称子库和根库的跨库原子性。
5. **§2.2 终态任务观察**：将 `observe_task_actions` 移到“原始事件尾 ≤ 游标”跳过判断之后；已有 effect 先按任务幂等键只读检查，不重复解析回执或开写事务。保留 main 最后一页把游标补至原始事件尾的修复。
6. **§2.3 单一审批投影**：给 `SessionAuthorization._apply` 加 `request_kind="authorization"` 默认参数，动作路径传 `"action"`。删除复制的待批行、绑定、编号、版本和 `needs_response` 事件创建逻辑。
7. **§2.4 直接约定**：`action_tools.py` 直接访问 `host._session_handles`、`settings.max_action_level/max_actions_per_turn`、`host.store.list_actions`，不再用缺失属性回退。`getattr(receipt, field)` 仅用于遍历核对既有回执字段，没有默认值，不是兼容回退。
8. **§3 参数、拒绝与绑定**：缺 target、缺必填参数先返回，不占动作槽位；开始新一次执行/授权前清掉上次 `denied_reason`，端口离线不会沿用旧拒绝原因；待批/拒绝状态按持久 `authorization_key → authorization_bindings → authorization_requests` 定点读取，不扫全表。
9. **§3 类型与目录**：`ContinuationContext.delegation_id/mission_id` 改为 `str | None`；注册时不登记只声明 child 的动作，使目录与可调用权限一致；补目录断言并更新文档的正式配置与恢复边界。
10. **测试替身**：`test_session.py` 的 FakeHost 补执行库的 `list_actions/read_view/transaction/事件尾` 约定，将直接修改的替身状态同步为单调事件；finish 也使用单调序号。main 自带隐藏事件尾测试的局部 MissionStore 补空动作查询。既有断言全部保留，没有改表、放宽断言或在生产路径补测试兼容分支。

### 决定性验证与命令

新增测试仍在 `test_action_tools.py`，覆盖：正式配置与坏值、无效参数不占槽位、旧拒绝原因清除、授权请求定点查询、动作/普通卡片行键一致、审批事务失败整笔回滚、50 条已批准历史闲置 3.05 秒至少 12 拍无授权扫描且零 BEGIN、child-only 不登记、终态任务两条动作五次 catch-up 只查询一次、子投影前失败后重开补执行且不能提前归档。

首次新增/原动作测试小组为 **26 passed / 0 failed，38.00 秒**；之后又加强了已记账回执预查与投影未完成的归档断言，最终组以提交态复验为准。

以下命令均在该工作树 `sdk/simple-harness-sdk/` 运行：

```bash
uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream ../../.local-test-evidence/2026-10-05/f2-upstream/evidence.json
uv run --frozen python -m pytest tests/orchestrator/host/test_action_tools.py tests/orchestrator/host/test_host_connectors.py tests/orchestrator/host/test_tool_catalog.py tests/orchestrator/host/test_authorization_port.py tests/orchestrator/host/test_session_tree.py tests/orchestrator/host/test_continuation.py tests/orchestrator/host/test_session.py tests/orchestrator/host/test_owner_and_close.py tests/orchestrator/host/test_host_recovery.py tests/test_public_api.py -q -p no:cacheprovider
uv run --frozen ruff check src/agent_orchestrator/runtime/{action_tools,connectors}.py src/agent_orchestrator/host/{authorization,session,settings,inbox,continuation,host}.py tests/orchestrator/host/{test_session,test_action_tools}.py
uv run --frozen ruff format --check src/agent_orchestrator/runtime/{action_tools,connectors}.py src/agent_orchestrator/host/{authorization,session,settings,inbox,continuation,host}.py tests/orchestrator/host/{test_session,test_action_tools}.py
```

- 首次指定回归：**367 passed / 1 failed，243.87 秒**。唯一失败为旧 `test_polling_unchanged_mission_does_not_reproject_or_write` 的局部 SimpleNamespace 执行库缺少 `list_actions`；补齐该替身方法，没有修改断言或增加生产回退。
- 加强本地已完成队列的只读预查后，重跑 F2 十场景与上述 polling 案例：**11 passed / 45 deselected，10.81 秒**；旧完成队列重放原回执一致、仍为唯一 done、真实效果仍为 1、BEGIN 为 0。
- 实现提交与最终被测代码：`c4880175ffa1bc400d000f919d0c2584e900a96f`。在此提交上重新生成清单并完整执行十文件复验：**368 passed / 0 failed，243.87 秒，exit 0**；此后生产源码与测试保持不变。
- 最终质量检查：**ruff check 全绿；ruff format --check 为 10 files already formatted**，包含原 connectors.py E501 的修复。
- 独立审阅：累计 diff 与条款覆盖 PASS，无剩余 P0/P1。独立子集 **5 passed / 21 deselected，8.40 秒**；另以真实子进程在根 COMMIT 后、子投影前 `os._exit(77)`，重开后 approved/outbox=1/子队列 done/效果=1，重放仍唯一。子回调写根受理后抛错会整笔回滚为 pending/outbox=0/childqueue=0；未投影时取消 session_tree，重开后队列 cancelled、效果 0。最终本地队列预查也有独立零 BEGIN 复验。
- 原始证据目录：`.local-test-evidence/2026-10-31/sol-f2/`；保留 baseline/red/green/regression/final、清单与审阅日志，全部 ignored，索引与 SHA-256 见下表。
- 被测清单 `deployment_id=332962d1e3ff28166fc8df3d7ab562d8613b236ddf00bbbca83d33f78e7df7fa`，856 个来源文件；ignored 副本已保留，交付前已恢复工作树原清单，未提交清单。之后只有本记录更新，不改变被测代码。
- 本轮没有修改命令表、池编号、既有工具说明、任务申请单/审阅/执行器、连接器协议；只把 main 并入当前工作树，未将本分支合回 main、未推送、不跑全量。`ARCHITECTURE/` 按 F2 §5 留给审核人合并时回写。
- 自我批评：先等完整基线结束再改生产源码，避免 F 首轮的快照污染。事务结构核对发现父子分库，先用独立反例澄清原子边界，再选择根库受理与子队列投影；不把两个 WAL 提交包装成“同事务”。

证据 SHA-256（均相对于 `.local-test-evidence/2026-10-31/sol-f2/`）：

| 文件 | SHA-256 |
|---|---|
| `baseline.log` | `7848c2e607c893970fd5db5bd9b113c4a97506db89ab66e18da32620e164adea` |
| `red.log` | `770b80a82203c97a5023e1acfd87c13eea5185835bded87a8163f6f96c10855d` |
| `green.log` | `403717f05e23ec8e6a75b42baa8473c18d411555fef8453adbec36bf35e6c7c9` |
| `regression.log` | `b3668144729ef8d9a5e7691950f040e027c165fcd13ce516757ee32fdabfdc64` |
| `focused.log` | `fc2ada1bf3db001d00505957e56ad248785f5fcb17b9fbe6b140c31fe6e457f2` |
| `transaction-review.md` | `1054bca2eb2921cfc941b1a13eb085f2918bed1c12a11ed4f23e9112e35da002` |
| `review.md` | `547bb22d1ffd50f3e21eab3bd116fbfff53eeff667809bc2be3facd702689545` |
| `final.log` | `40b7401f337c7e905b1f00649366e2e0ca86fd1b42bf8e74f5fc407626d6ea45` |
| `final-manifest.log` | `07b94ee7e3a493bdbcbfd2a0433f3a7af541602b818c9cd09ad3c6e2f800bdad` |
| `tested-taskgraph-deployment-manifest.json` | `93e001c00f2832a3733067e78c1dd011f39059b9d5df708bc188ede2e9b963de` |
| `final-lint.log` | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |
| `final-format.log` | `5b9f70ad96522f492ef846065642c069b5627379aff2ab3f74acd2e3b9ecfe34` |

F2 DoD：§1 两项原 BLOCKED 均解除；§2 四项阻断补修与§3 小项完成，新增决定性测试、独立审阅与提交态十文件回归全绿；Ruff/format 含 connectors.py 全绿；部署清单恢复；只提交授权车道和记录，未推送、未合回 main。完成判定依据本节验收与证据，无机器 receipt。

VERDICT: PASS — F2 补修完成，提交态 368 项全绿、Ruff/format 全绿、独立审阅无剩余 P0/P1 — 2026-10-31 — c4880175ffa1bc400d000f919d0c2584e900a96f
